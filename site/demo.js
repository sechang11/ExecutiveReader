/**
 * The live demo.
 *
 * Runs the same Kokoro model the extension uses, in the visitor's browser. The
 * point is that voice quality is the whole pitch and nobody believes a claim
 * about it, so this plays the real thing rather than a recording someone would
 * reasonably assume was cherry-picked.
 *
 * Two constraints shape it. Nothing downloads until the button is pressed,
 * because spending 88 MB of someone's bandwidth on a page they are browsing is
 * rude. And it degrades to an honest message rather than a broken control when
 * the browser cannot run it.
 *
 * The extension's own modules are reused rather than reimplemented, so the demo
 * cannot drift from the product and quietly sound better or worse than it.
 */

const $ = (id) => document.getElementById(id);

const HF = 'https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main';
const MODEL = `${HF}/onnx/model_quantized.onnx`;
const VOICE = `${HF}/voices/af_heart.bin`;
const SAMPLE_RATE = 24_000;
const STYLE_DIM = 256;

let engine = null;
let busy = false;

function status(message) {
  $('demo-status').textContent = message;
}

function fail(message) {
  $('demo-progress').hidden = false;
  status(message);
  $('demo-play').disabled = false;
  $('demo-play').textContent = 'Download voice and play';
}

/** Fetch with progress, so an 88 MB wait is not indistinguishable from a hang. */
async function fetchWithProgress(url, onProgress) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} fetching the voice model`);
  const total = Number(res.headers.get('content-length')) || null;
  const reader = res.body.getReader();
  const chunks = [];
  let loaded = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.byteLength;
    onProgress(loaded, total);
  }
  return new Blob(chunks).arrayBuffer();
}

/**
 * Load the runtime, the model and one voice.
 *
 * The extension vendors its own copy of the ONNX runtime because Manifest V3
 * forbids remote code. A web page has no such restriction, so this loads it
 * from a CDN — the one place the demo legitimately differs from the product.
 */
async function boot() {
  status('Loading the runtime…');
  const ort = await import('https://cdn.jsdelivr.net/npm/onnxruntime-web@1.29.0/dist/ort.webgpu.bundle.min.mjs');
  ort.env.wasm.wasmPaths = 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.29.0/dist/';
  ort.env.wasm.numThreads = 1;
  ort.env.logLevel = 'error';

  const [vocab, dict, homographs] = await Promise.all([
    fetch('assets/vocab.json').then((r) => r.json()),
    fetch('assets/cmudict.txt.gz')
      .then((r) => new Response(r.body.pipeThrough(new DecompressionStream('gzip'))).text()),
    fetch('assets/homographs.json').then((r) => r.json()),
  ]);

  const g2p = await import('./vendor/g2p-en.js');
  const tok = await import('./vendor/tokenize.js');
  g2p.loadDict(dict);
  g2p.loadHomographs(homographs);
  tok.loadVocab(vocab);

  status('Downloading the voice model, about 88 MB…');
  const model = await fetchWithProgress(MODEL, (loaded, total) => {
    const pct = total ? Math.round((loaded / total) * 100) : null;
    if (pct === null) $('demo-bar').removeAttribute('value');
    else $('demo-bar').value = pct;
    status(pct === null
      ? `${(loaded / 1e6).toFixed(0)} MB downloaded`
      : `${pct}% — ${(loaded / 1e6).toFixed(0)} of ${(total / 1e6).toFixed(0)} MB`);
  });

  status('Downloading the voice…');
  const voice = new Float32Array(await fetch(VOICE).then((r) => r.arrayBuffer()));

  status('Starting the engine…');
  let session = null;
  let backend = 'wasm';
  for (const ep of ['webgpu', 'wasm']) {
    try {
      session = await ort.InferenceSession.create(model, { executionProviders: [ep] });
      backend = ep;
      break;
    } catch (e) {
      if (ep === 'wasm') throw e;
    }
  }

  return { ort, session, voice, g2p, tok, backend };
}

async function speak(text, speed) {
  const { ort, session, voice, g2p, tok } = engine;

  const { ipa } = g2p.phonemize(text);
  const { ids } = tok.tokenize(ipa);
  if (!ids.length) throw new Error('nothing to read');

  const frames = voice.length / STYLE_DIM;
  const frame = tok.styleIndex(ids.length, frames);
  const style = voice.slice(frame * STYLE_DIM, (frame + 1) * STYLE_DIM);

  const tokens = BigInt64Array.from([0n, ...ids.map(BigInt), 0n]);
  const out = await session.run({
    input_ids: new ort.Tensor('int64', tokens, [1, tokens.length]),
    style: new ort.Tensor('float32', style, [1, STYLE_DIM]),
    speed: new ort.Tensor('float32', Float32Array.from([speed]), [1]),
  });

  const pcm = out.waveform.data;
  const ctx = new AudioContext();
  const buffer = ctx.createBuffer(1, pcm.length, SAMPLE_RATE);
  buffer.copyToChannel(pcm, 0);
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);
  source.start();
  source.onended = () => ctx.close();
  return pcm.length / SAMPLE_RATE;
}

$('demo-speed').addEventListener('input', (e) => {
  $('demo-speed-out').textContent = `${Number(e.target.value).toFixed(1)}×`;
});

$('demo-play').addEventListener('click', async () => {
  if (busy) return;
  busy = true;
  $('demo-play').disabled = true;
  $('demo-progress').hidden = false;

  try {
    if (!engine) {
      engine = await boot();
      $('demo-play').textContent = 'Play';
      $('demo-note').textContent = engine.backend === 'webgpu'
        ? 'Running on your GPU. Nothing you type leaves this tab.'
        : 'Running on your processor, which is slower than the GPU path. Nothing you type leaves this tab.';
    }
    status('Generating…');
    const seconds = await speak($('demo-text').value, Number($('demo-speed').value));
    status(`${seconds.toFixed(1)} seconds of speech, generated here.`);
  } catch (e) {
    // An honest message beats a control that silently does nothing. WebGPU
    // absence is not the usual cause; a blocked download or an old browser is.
    fail(`Could not run the demo in this browser: ${e.message}. The extension itself may still work.`);
    console.error(e);
    engine = null;
  } finally {
    busy = false;
    $('demo-play').disabled = false;
  }
});
