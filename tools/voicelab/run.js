/**
 * Synthesize variants of a sentence and report how long each one is.
 *
 * Written to settle a specific disagreement — whether collapsing "..." to "."
 * throws away a pause the model can voice — with a measurement rather than an
 * inference from the vocabulary file. The desktop half measured the same
 * question on the Windows system voice; this is the other engine.
 *
 * Runs the extension's real worker, phonemizer and tokenizer.
 */

const HF = 'https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main';
const ORT = 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.29.0/dist/ort.webgpu.bundle.min.mjs';
const ORT_DIR = 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.29.0/dist/';
const SAMPLE_RATE = 24_000;
const STYLE_DIM = 256;

/** Variants to compare. The first is the baseline the rest are measured against. */
const CASES = [
  'Wait. What happened?',
  'Wait... What happened?',
  'Wait… What happened?',
  'Wait, What happened?',
  'Wait What happened?',
  'As shown by Smith the result holds.',
  'As shown by Smith [1] the result holds.',
];

const status = (m) => { document.getElementById('status').textContent = m; };

function call(worker, type, payload, transfer = []) {
  const id = Math.random().toString(36).slice(2);
  return new Promise((resolve, reject) => {
    const on = (e) => {
      if (e.data.id !== id) return;
      worker.removeEventListener('message', on);
      if (e.data.ok) resolve(e.data.result); else reject(new Error(e.data.error));
    };
    worker.addEventListener('message', on);
    worker.postMessage({ id, type, payload }, transfer);
  });
}

status('Loading the dictionary…');
const [vocab, dict, homographs] = await Promise.all([
  fetch('../../site/assets/vocab.json').then((r) => r.json()),
  fetch('../../site/assets/cmudict.txt.gz')
    .then((r) => new Response(r.body.pipeThrough(new DecompressionStream('gzip'))).text()),
  fetch('../../site/assets/homographs.json').then((r) => r.json()),
]);

const g2p = await import('../../extension/src/engines/kokoro/g2p-en.js');
const tok = await import('../../extension/src/engines/kokoro/tokenize.js');
g2p.loadDict(dict);
g2p.loadHomographs(homographs);
tok.loadVocab(vocab);

status('Downloading the model… (cached after the first run)');
const model = await fetch(`${HF}/onnx/model_quantized.onnx`).then((r) => r.arrayBuffer());
const voice = new Float32Array(await fetch(`${HF}/voices/af_heart.bin`).then((r) => r.arrayBuffer()));

status('Starting the engine…');
const worker = new Worker('../../extension/src/engines/kokoro/worker.js', { type: 'module' });
const { backend } = await call(worker, 'init', { ortUrl: ORT, wasmPath: ORT_DIR, model }, [model]);

const body = document.querySelector('#out tbody');
let baseline = null;

for (const text of CASES) {
  status(`${backend}: ${text}`);
  const { ipa } = g2p.phonemize(text);
  const { ids } = tok.tokenize(ipa);
  const frames = voice.length / STYLE_DIM;
  const frame = tok.styleIndex(ids.length, frames);
  const style = voice.slice(frame * STYLE_DIM, (frame + 1) * STYLE_DIM);

  const { duration } = await call(worker, 'synth', {
    ids, style, speed: 1, words: [], sampleRate: SAMPLE_RATE,
  });

  if (baseline === null) baseline = duration;
  const row = document.createElement('tr');
  row.innerHTML = '<td></td><td class="n"></td><td class="n"></td><td class="n"></td>';
  row.children[0].textContent = JSON.stringify(text);
  row.children[1].textContent = String(ids.length);
  row.children[2].textContent = duration.toFixed(3);
  row.children[3].textContent = `${duration - baseline >= 0 ? '+' : ''}${(duration - baseline).toFixed(3)}`;
  body.append(row);
  window.__voicelab = (window.__voicelab ?? []);
  window.__voicelab.push({ text, tokens: ids.length, duration });
}

status(`done, on ${backend}`);
window.__voicelabDone = true;
