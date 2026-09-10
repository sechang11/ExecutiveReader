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
 * cannot drift from the product and quietly sound better or worse than it. That
 * now includes the inference worker: an earlier version called onnxruntime on
 * the page's own thread, which froze the tab for minutes on the WebAssembly
 * path while the extension — which has always used the worker — did not. A demo
 * that behaves worse than the product it advertises argues against it.
 */

const $ = (id) => document.getElementById(id);

const HF = 'https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main';
const MODEL = `${HF}/onnx/model_quantized.onnx`;
const VOICE = `${HF}/voices/af_heart.bin`;
const ORT = 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.29.0/dist/ort.webgpu.bundle.min.mjs';
const ORT_DIR = 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.29.0/dist/';
const SAMPLE_RATE = 24_000;
const STYLE_DIM = 256;

/**
 * Bytes as mebibytes.
 *
 * The page says "about 88 MB" and the model is 92,000,000 bytes, so a decimal
 * formatter made the progress readout contradict the sentence above it. Both
 * numbers were right; only one unit can be shown.
 */
const mib = (bytes) => (bytes / 1024 / 1024).toFixed(0);

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

/**
 * Fetch with progress, so an 88 MB wait is not indistinguishable from a hang.
 *
 * Progress is reported at most every 200 ms. Reporting every chunk means tens
 * of thousands of layout-invalidating writes during the download, which is
 * itself a source of the stutter the progress bar exists to explain away.
 */
async function fetchWithProgress(url, onProgress) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} fetching the voice model`);
  const total = Number(res.headers.get('content-length')) || null;
  const reader = res.body.getReader();
  const chunks = [];
  let loaded = 0;
  let lastReport = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.byteLength;
    const now = Date.now();
    if (now - lastReport >= 200) { lastReport = now; onProgress(loaded, total); }
  }
  onProgress(loaded, total);
  return new Blob(chunks).arrayBuffer();
}

/**
 * Load the runtime, the model and one voice.
 *
 * The extension vendors its own copy of the ONNX runtime because Manifest V3
 * forbids remote code. A web page has no such restriction, so this loads it
 * from a CDN — the one place the demo legitimately differs from the product.
 */
/** Ask the worker to do one thing, and wait for that answer rather than any answer. */
function callWorker(worker, type, payload, transfer = []) {
  const id = Math.random().toString(36).slice(2);
  return new Promise((resolve, reject) => {
    const onMessage = (e) => {
      if (e.data.id !== id) return;
      worker.removeEventListener('message', onMessage);
      if (e.data.ok) resolve(e.data.result);
      else reject(new Error(e.data.error));
    };
    worker.addEventListener('message', onMessage);
    worker.postMessage({ id, type, payload }, transfer);
  });
}

/**
 * Load the phonemizer here, and the model into a worker.
 *
 * Text work is microseconds and belongs on this thread, where it can touch the
 * page. Inference is seconds of solid compute and belongs off it. The split
 * matches the extension's, which is the point: the worker file below is the
 * extension's, copied by tools/sync-shared.mjs rather than rewritten.
 */
async function boot() {
  status('Reading the pronunciation dictionary…');

  const [vocab, dict, homographs, abbreviations, normalization] = await Promise.all([
    fetch('assets/vocab.json').then((r) => r.json()),
    fetch('assets/cmudict.txt.gz')
      .then((r) => new Response(r.body.pipeThrough(new DecompressionStream('gzip'))).text()),
    fetch('assets/homographs.json').then((r) => r.json()),
    fetch('assets/abbreviations.json').then((r) => r.json()),
    fetch('assets/normalization.json').then((r) => r.json()),
  ]);

  const g2p = await import('./vendor/g2p-en.js');
  const tok = await import('./vendor/tokenize.js');
  const seg = await import('./vendor/segment.js');
  const norm = await import('./vendor/normalize.js');
  g2p.loadDict(dict);
  g2p.loadHomographs(homographs);
  tok.loadVocab(vocab);
  seg.loadAbbreviations(abbreviations);
  norm.loadNormalization(normalization);

  status('Downloading the voice model, about 88 MB…');
  const model = await fetchWithProgress(MODEL, (loaded, total) => {
    const pct = total ? Math.round((loaded / total) * 100) : null;
    if (pct === null) $('demo-bar').removeAttribute('value');
    else $('demo-bar').value = pct;
    status(pct === null
      ? `${mib(loaded)} MB downloaded`
      : `${pct}% — ${mib(loaded)} of ${mib(total)} MB`);
  });

  status('Downloading the voice…');
  const voice = new Float32Array(await fetch(VOICE).then((r) => r.arrayBuffer()));

  status('Starting the engine…');
  const worker = new Worker('vendor/kokoro-worker.js', { type: 'module' });

  // The extension vendors its own copy of the runtime because Manifest V3
  // forbids remote code. A web page has no such restriction, so these point at
  // a CDN — the one place the demo legitimately differs from the product.
  const { backend } = await callWorker(worker, 'init', { ortUrl: ORT, wasmPath: ORT_DIR, model }, [model]);

  return { worker, voice, g2p, tok, seg, norm, backend };
}

/**
 * Synthesize and play one sentence.
 *
 * Per sentence rather than per paragraph, because Kokoro's cost grows faster
 * than the token count: measured on the WebAssembly path, "Hello." took seven
 * seconds while the two-sentence default text had not finished after six
 * minutes. The extension has always segmented first for the same reason, so
 * this is one more place the demo was not running what it advertised.
 */
async function synthesize(sentence, speed) {
  const { worker, voice, g2p, tok, norm } = engine;

  const { ipa } = g2p.phonemize(norm.normalize(sentence));
  const { ids } = tok.tokenize(ipa);
  if (!ids.length) return null;

  const frames = voice.length / STYLE_DIM;
  const frame = tok.styleIndex(ids.length, frames);
  const style = voice.slice(frame * STYLE_DIM, (frame + 1) * STYLE_DIM);

  return callWorker(worker, 'synth', {
    ids, style, speed, words: [], sampleRate: SAMPLE_RATE,
  });
}

/** Play one buffer and resolve when it has finished, so sentences do not overlap. */
function play(ctx, pcm) {
  const buffer = ctx.createBuffer(1, pcm.length, SAMPLE_RATE);
  buffer.copyToChannel(pcm, 0);
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);
  return new Promise((resolve) => {
    source.onended = resolve;
    source.start();
  });
}

/**
 * Read the whole passage, generating the next sentence while the current one
 * plays. On a slow machine that overlap is the difference between a wait and a
 * reading, and it is how the extension behaves too.
 * @returns {Promise<number>} seconds of speech produced
 */
async function speak(text, speed, onSentence) {
  const sentences = engine.seg.segment(text).map((s) => s.text);
  if (!sentences.length) throw new Error('nothing to read');

  const ctx = new AudioContext();
  let total = 0;
  let playing = Promise.resolve();
  let next = synthesize(sentences[0], speed);

  try {
    for (let i = 0; i < sentences.length; i++) {
      onSentence?.(i, sentences.length);
      const result = await next;
      next = i + 1 < sentences.length ? synthesize(sentences[i + 1], speed) : null;
      if (!result) continue;
      total += result.duration;
      await playing;
      playing = play(ctx, result.pcm);
    }
    await playing;
  } finally {
    // Await the outstanding synthesis before closing, or a rejected promise
    // from a torn-down context surfaces as an unhandled rejection.
    await Promise.allSettled([next]);
    ctx.close();
  }
  return total;
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
    // Synthesis on the WebAssembly path is slow enough that a static message
    // reads as a hang. On a machine with no GPU adapter, "Hello." took seven
    // seconds. A ticking counter, the sentence number, and a line naming the
    // cause are the difference between waiting and giving up.
    const started = Date.now();
    const slow = engine.backend !== 'webgpu';
    let at = { i: 0, n: 1 };
    const tick = () => {
      const elapsed = Math.round((Date.now() - started) / 1000);
      const where = at.n > 1 ? ` Sentence ${at.i + 1} of ${at.n}.` : '';
      status(slow
        ? `Generating on your processor, ${elapsed}s so far.${where} A machine with a GPU is far quicker.`
        : `Generating, ${elapsed}s.${where}`);
    };
    tick();
    const timer = setInterval(tick, 1000);
    let seconds;
    try {
      seconds = await speak($('demo-text').value, Number($('demo-speed').value),
        (i, n) => { at = { i, n }; tick(); });
    } finally {
      clearInterval(timer);
    }
    status(`${seconds.toFixed(1)} seconds of speech, generated here in ${Math.round((Date.now() - started) / 1000)}s.`);
  } catch (e) {
    // An honest message beats a control that silently does nothing. WebGPU
    // absence is not the usual cause; a blocked download or an old browser is.
    fail(`Could not run the demo in this browser: ${e.message}. The extension itself may still work.`);
    console.error(e);
    engine?.worker?.terminate();
    engine = null;
  } finally {
    busy = false;
    $('demo-play').disabled = false;
  }
});
