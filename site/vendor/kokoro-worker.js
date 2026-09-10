/**
 * Kokoro inference, in a worker.
 *
 * Off the main thread because synthesis is hundreds of milliseconds of solid
 * compute per sentence, and the offscreen document is also running the audio
 * graph. Blocking that thread produces gaps between sentences, which is the one
 * defect a reader cannot hide.
 *
 * WebGPU where available, WASM otherwise. The difference is roughly an order of
 * magnitude, so the fallback is genuinely usable only for short passages, and
 * the engine reports which backend it got so the UI can be honest about it.
 */

/* eslint-env worker */

/** @type {any} */ let ort = null;
/** @type {any} */ let session = null;
let backend = 'none';

/** Voice packs, kept as raw floats and sliced per utterance. */
const voices = new Map();

/**
 * Whether a WebGPU adapter actually exists, as opposed to the API existing.
 *
 * Wrapped because requestAdapter can reject on some builds, and because a
 * pre-flight check that itself hangs would reintroduce the problem it prevents.
 */
async function hasGpuAdapter() {
  if (!globalThis.navigator?.gpu) return false;
  try {
    const adapter = await Promise.race([
      navigator.gpu.requestAdapter(),
      new Promise((resolve) => setTimeout(() => resolve(null), 3000)),
    ]);
    return !!adapter;
  } catch {
    return false;
  }
}

/**
 * @param {{ortUrl: string, wasmPath: string, model: ArrayBuffer}} msg
 */
async function init({ ortUrl, wasmPath, model }) {
  ort = await import(ortUrl);

  // Point the runtime at the vendored binary. Left unset it looks for a CDN,
  // which the extension content-security-policy blocks, and the failure is a
  // silent hang rather than an error.
  ort.env.wasm.wasmPaths = wasmPath;
  ort.env.wasm.numThreads = 1; // cross-origin isolation is not available here
  ort.env.logLevel = 'error';

  // Try the GPU first, then fall back. A WebGPU session can fail to build on
  // drivers that advertise support, so this must be a real attempt rather than
  // a capability check.
  //
  // One capability check earns its place ahead of that attempt. `navigator.gpu`
  // exists on machines with no usable adapter — a virtual machine, a browser
  // with the feature flagged on but no driver behind it — and on those the
  // WebGPU session build does not fail, it stalls. Measured in a browser where
  // requestAdapter() resolves null in one millisecond, the session attempt was
  // still going after eight minutes. Asking for the adapter first turns that
  // into an instant fallback to WebAssembly.
  const eps = (await hasGpuAdapter()) ? ['webgpu', 'wasm'] : ['wasm'];
  for (const ep of eps) {
    try {
      session = await ort.InferenceSession.create(model, { executionProviders: [ep] });
      backend = ep;
      break;
    } catch (e) {
      if (ep === 'wasm') throw e;
    }
  }
  return { backend };
}

/**
 * Distribute a sentence's duration across its words.
 *
 * Kokoro emits no alignment, so timings are estimated from how many phonemes
 * each word has. That is approximate, and good enough to drive a highlight that
 * tracks the voice; it is not good enough to claim as real timing data, which
 * is why the engine reports supportsWordBoundaries as false.
 *
 * @param {{word: string, phonemes: number}[]} words
 * @param {number} duration seconds
 */
function estimateTimings(words, duration) {
  const total = words.reduce((n, w) => n + Math.max(w.phonemes, 1), 0);
  if (!total) return [];
  let at = 0;
  return words.map((w) => {
    const share = (Math.max(w.phonemes, 1) / total) * duration;
    const timing = { charStart: w.charStart, charEnd: w.charEnd, timeStart: at, timeEnd: at + share };
    at += share;
    return timing;
  });
}

/**
 * @param {{ids: number[], style: Float32Array, speed: number,
 *          words: object[], sampleRate: number}} msg
 */
async function synth({ ids, style, speed, words, sampleRate }) {
  if (!session) throw new Error('synthesize before init');

  const tokens = BigInt64Array.from([0n, ...ids.map(BigInt), 0n]);
  const feeds = {
    input_ids: new ort.Tensor('int64', tokens, [1, tokens.length]),
    style: new ort.Tensor('float32', style, [1, style.length]),
    // Speed is a synthesis parameter, so 2.5x keeps its pitch. Resampling
    // finished audio instead is what makes fast reading sound like a chipmunk.
    speed: new ort.Tensor('float32', Float32Array.from([speed]), [1]),
  };

  const out = await session.run(feeds);
  const pcm = out.waveform.data;
  const duration = pcm.length / sampleRate;

  return {
    pcm,
    duration,
    timings: words?.length ? estimateTimings(words, duration) : [],
  };
}

self.onmessage = async (e) => {
  const { id, type, payload } = e.data;
  try {
    let result;
    if (type === 'init') result = await init(payload);
    else if (type === 'synth') result = await synth(payload);
    else if (type === 'voice') { voices.set(payload.id, payload.floats); result = { ok: true }; }
    else if (type === 'dispose') {
      await session?.release?.();
      session = null;
      voices.clear();
      result = { ok: true };
    } else throw new Error(`unknown message ${type}`);

    // Transfer the audio rather than copying it: a sentence is a megabyte or
    // two of float32 and this runs for every sentence.
    const transfer = result.pcm instanceof Float32Array ? [result.pcm.buffer] : [];
    self.postMessage({ id, ok: true, result }, transfer);
  } catch (err) {
    self.postMessage({ id, ok: false, error: String(err?.message ?? err) });
  }
};
