/**
 * Fetching and caching Kokoro's weights.
 *
 * The runtime is code and is vendored, because Manifest V3 forbids loading code
 * from the network. The weights are data, so they are fetched on first use
 * instead of shipped: 88 MB of model plus about 510 KB per voice, against a
 * package a reviewer has to download in one piece.
 *
 * Storage is the Cache API rather than IndexedDB. The model arrives as a
 * Response and is consumed by the runtime as an ArrayBuffer, so a store that
 * speaks Response natively avoids a copy through a JavaScript object, and
 * eviction under storage pressure is a re-download rather than data loss.
 */

const CACHE = 'earmark-kokoro-v1';

const HOST = 'https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main';
const MODEL_URL = `${HOST}/onnx/model_quantized.onnx`;
const voiceUrl = (id) => `${HOST}/voices/${id}.bin`;

/** Announced before download so a first-run prompt can state a real number. */
export const MODEL_BYTES = 92_361_116;
export const VOICE_BYTES = 522_240;

/** A voice pack is 510 frames of 256 float32. Derived, not assumed, so a
 *  differently shaped pack is caught rather than reshaped into nonsense. */
export const STYLE_DIM = 256;

/** @param {string} url @returns {Promise<boolean>} */
async function cached(url) {
  const cache = await caches.open(CACHE);
  return Boolean(await cache.match(url));
}

/**
 * Fetch with progress, storing the result.
 *
 * Progress needs the body streamed rather than awaited whole, because an 88 MB
 * download with no feedback is indistinguishable from a hang. Content-Length is
 * absent on some responses, so `total` may be null and callers must render an
 * indeterminate state rather than dividing by it.
 *
 * @param {string} url
 * @param {(loaded: number, total: number|null) => void} [onProgress]
 * @param {AbortSignal} [signal]
 */
async function download(url, onProgress, signal) {
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`${res.status} fetching ${url}`);

  const total = Number(res.headers.get('content-length')) || null;
  const reader = res.body.getReader();
  const chunks = [];
  let loaded = 0;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.byteLength;
    onProgress?.(loaded, total);
  }

  const blob = new Blob(chunks);
  const cache = await caches.open(CACHE);
  // Store a fresh Response: the original body is already consumed.
  await cache.put(url, new Response(blob, {
    headers: { 'content-type': 'application/octet-stream', 'content-length': String(blob.size) },
  }));
  return blob.arrayBuffer();
}

/**
 * @param {string} url
 * @param {(loaded: number, total: number|null) => void} [onProgress]
 * @param {AbortSignal} [signal]
 * @returns {Promise<ArrayBuffer>}
 */
async function load(url, onProgress, signal) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(url);
  if (hit) return hit.arrayBuffer();
  return download(url, onProgress, signal);
}

/** @param {(loaded: number, total: number|null) => void} [onProgress] */
export function loadModel(onProgress, signal) {
  return load(MODEL_URL, onProgress, signal);
}

/** @param {string} voiceId */
export async function loadVoice(voiceId, onProgress, signal) {
  const buf = await load(voiceUrl(voiceId), onProgress, signal);
  const floats = new Float32Array(buf);
  if (floats.length % STYLE_DIM !== 0) {
    throw new Error(`voice ${voiceId}: ${floats.length} floats is not a multiple of ${STYLE_DIM}`);
  }
  return { floats, frames: floats.length / STYLE_DIM };
}

/**
 * One frame of a voice pack, copied rather than subarrayed.
 *
 * The runtime takes ownership of tensor data, and a subarray shares the parent
 * buffer, so handing one over can leave the whole pack detached and every later
 * frame unreadable.
 *
 * @param {Float32Array} floats @param {number} index
 */
export function styleFrame(floats, index) {
  const start = index * STYLE_DIM;
  return floats.slice(start, start + STYLE_DIM);
}

/** What is already downloaded, for the settings screen. */
export async function status(voiceIds = []) {
  return {
    model: await cached(MODEL_URL),
    voices: Object.fromEntries(
      await Promise.all(voiceIds.map(async (id) => [id, await cached(voiceUrl(id))])),
    ),
  };
}

/** Free the space. Users who tried neural voices and went back to system ones
 *  should not be left carrying 88 MB they cannot see or remove. */
export async function evict() {
  return caches.delete(CACHE);
}
