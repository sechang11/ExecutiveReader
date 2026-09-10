/**
 * The execution-provider ladder in the Kokoro worker.
 *
 * "Try WebGPU, fall back to WebAssembly" is only a fallback if the failure is
 * prompt. On a host where `navigator.gpu` exists with no adapter behind it, the
 * WebGPU session build does not reject — it stalls. Measured in a browser where
 * requestAdapter() resolved null in one millisecond, the build was still going
 * after eight minutes, with the demo stuck on "Starting the engine…".
 *
 * The fix is one adapter request ahead of the ladder. These tests pin both
 * halves of it: no adapter means WebGPU is never attempted, and an adapter
 * means it still is, because an adapter that exists can still fail to build a
 * session and only a real attempt finds that out.
 */

import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const extRoot = new URL('../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const ORT_URL = pathToFileURL(path.join(extRoot, 'test', 'fixtures', 'fake-ort.mjs')).href;

const ort = await import(ORT_URL);

/**
 * Load the worker with a fake `self`, and hand back a way to call it.
 *
 * The worker is written for a Worker global scope: it installs `self.onmessage`
 * and answers with `self.postMessage`. Supplying both is the whole of the
 * adaptation — no part of the module under test is replaced.
 */
async function loadWorker({ adapter }) {
  const replies = [];
  globalThis.self = {
    postMessage: (m) => replies.push(m),
    set onmessage(fn) { this._onmessage = fn; },
    get onmessage() { return this._onmessage; },
  };
  globalThis.navigator = adapter === undefined
    ? undefined
    : { gpu: { requestAdapter: async () => adapter } };

  // A fresh module instance per test: the worker holds its session in a
  // module-level variable, so a cached copy would carry one test into the next.
  const mod = `${pathToFileURL(path.join(extRoot, 'src', 'engines', 'kokoro', 'worker.js')).href}?t=${Math.random()}`;
  await import(mod);

  let id = 0;
  const send = async (type, payload) => {
    const mine = `m${id++}`;
    await globalThis.self.onmessage({ data: { id: mine, type, payload } });
    return replies.find((r) => r.id === mine);
  };
  return { send, replies };
}

const model = new ArrayBuffer(8);

beforeEach(() => ort.reset());

test('with no GPU adapter, WebGPU is never attempted', async () => {
  // The hang is set on webgpu deliberately: if the ladder tried it at all, this
  // test would time out rather than fail, which is exactly the bug's signature.
  ort.hangs.add('webgpu');
  const { send } = await loadWorker({ adapter: null });

  const reply = await send('init', { ortUrl: ORT_URL, wasmPath: '/wasm/', model });

  assert.equal(reply.ok, true, reply.error);
  assert.equal(reply.result.backend, 'wasm');
  assert.deepEqual(ort.calls, ['wasm'], 'webgpu must not be attempted without an adapter');
});

test('with no WebGPU API at all, WebAssembly is used', async () => {
  const { send } = await loadWorker({ adapter: undefined });
  const reply = await send('init', { ortUrl: ORT_URL, wasmPath: '/wasm/', model });
  assert.equal(reply.result.backend, 'wasm');
  assert.deepEqual(ort.calls, ['wasm']);
});

test('with an adapter, WebGPU is tried first', async () => {
  const { send } = await loadWorker({ adapter: {} });
  const reply = await send('init', { ortUrl: ORT_URL, wasmPath: '/wasm/', model });
  assert.equal(reply.result.backend, 'webgpu');
  assert.deepEqual(ort.calls, ['webgpu']);
});

test('an adapter that cannot build a session still falls back', async () => {
  // The reason the ladder is a real attempt rather than a capability check:
  // drivers advertise support they cannot deliver.
  ort.throws.add('webgpu');
  const { send } = await loadWorker({ adapter: {} });
  const reply = await send('init', { ortUrl: ORT_URL, wasmPath: '/wasm/', model });
  assert.equal(reply.result.backend, 'wasm');
  assert.deepEqual(ort.calls, ['webgpu', 'wasm']);
});

test('a WebAssembly failure is reported rather than swallowed', async () => {
  ort.throws.add('wasm');
  const { send } = await loadWorker({ adapter: null });
  const reply = await send('init', { ortUrl: ORT_URL, wasmPath: '/wasm/', model });
  assert.equal(reply.ok, false);
  assert.match(reply.error, /wasm unavailable/);
});

test('synthesizing before init fails with a usable message', async () => {
  const { send } = await loadWorker({ adapter: null });
  const reply = await send('synth', { ids: [1], style: new Float32Array(256), speed: 1, sampleRate: 24000 });
  assert.equal(reply.ok, false);
  assert.match(reply.error, /synthesize before init/);
});

test('synthesis returns audio and a duration derived from the sample rate', async () => {
  const { send } = await loadWorker({ adapter: null });
  await send('init', { ortUrl: ORT_URL, wasmPath: '/wasm/', model });

  const reply = await send('synth', {
    ids: [1, 2, 3], style: new Float32Array(256), speed: 1, words: [], sampleRate: 24_000,
  });

  assert.equal(reply.ok, true, reply.error);
  assert.equal(reply.result.pcm.length, 2400);
  assert.equal(reply.result.duration, 0.1);
  assert.deepEqual(reply.result.timings, [], 'no words in means no timings out');
});

test('word timings are estimated across the sentence and cover it exactly', async () => {
  const { send } = await loadWorker({ adapter: null });
  await send('init', { ortUrl: ORT_URL, wasmPath: '/wasm/', model });

  const words = [
    { word: 'a', phonemes: 1, charStart: 0, charEnd: 1 },
    { word: 'bcd', phonemes: 3, charStart: 2, charEnd: 5 },
  ];
  const reply = await send('synth', {
    ids: [1], style: new Float32Array(256), speed: 1, words, sampleRate: 24_000,
  });

  const timings = reply.result.timings;
  assert.equal(timings.length, 2);
  assert.equal(timings[0].timeStart, 0);
  // Shares are proportional to phoneme count: one part then three.
  assert.ok(Math.abs(timings[0].timeEnd - 0.025) < 1e-9);
  assert.ok(Math.abs(timings[1].timeEnd - 0.1) < 1e-9, 'the last word ends when the audio does');
  assert.equal(timings[0].timeEnd, timings[1].timeStart, 'no gap between words');
});

test('an unknown message is refused rather than ignored', async () => {
  const { send } = await loadWorker({ adapter: null });
  const reply = await send('sing', {});
  assert.equal(reply.ok, false);
  assert.match(reply.error, /unknown message sing/);
});
