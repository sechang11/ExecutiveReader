/**
 * The voice model download and its cache.
 *
 * This is the module a first-run user waits on for 88 MB, and the one the
 * settings screen asks whether that download can be removed. Both answers are
 * invisible when wrong: a cache that never hits re-downloads silently, and an
 * evict that misses leaves someone carrying storage they were told was gone.
 *
 * Node has Blob, Response and ReadableStream, so only `caches` and `fetch` need
 * standing in. Nothing in the module under test is replaced.
 */

import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

/** A CacheStorage that keeps bodies in memory, keyed by URL like the real one. */
function fakeCaches() {
  const stores = new Map();
  return {
    stores,
    async open(name) {
      if (!stores.has(name)) stores.set(name, new Map());
      const entries = stores.get(name);
      return {
        async match(url) {
          const buf = entries.get(url);
          return buf ? new Response(buf) : undefined;
        },
        async put(url, response) {
          entries.set(url, await response.arrayBuffer());
        },
      };
    },
    async delete(name) {
      return stores.delete(name);
    },
  };
}

/** A fetch that streams a body in fixed-size chunks, so progress is observable. */
function fakeFetch({ bytes = 1024, chunk = 256, status = 200, contentLength = true } = {}) {
  const calls = [];
  const fn = async (url, opts) => {
    calls.push({ url, opts });
    if (status !== 200) return new Response(null, { status });

    const data = new Uint8Array(bytes).map((_, i) => i % 251);
    let at = 0;
    const body = new ReadableStream({
      pull(controller) {
        if (at >= data.length) { controller.close(); return; }
        controller.enqueue(data.slice(at, at + chunk));
        at += chunk;
      },
    });
    const headers = contentLength ? { 'content-length': String(bytes) } : {};
    return new Response(body, { status: 200, headers });
  };
  fn.calls = calls;
  return fn;
}

let store;

beforeEach(async () => {
  globalThis.caches = fakeCaches();
  globalThis.fetch = fakeFetch();
  // Fresh per test: the module holds no state itself, but the cache name it
  // closes over is shared, and a stale globalThis.caches would leak across.
  store = await import(`../src/engines/kokoro/model-store.js?t=${Math.random()}`);
});

test('the announced sizes are the ones the download reports', () => {
  // These numbers reach a first-run prompt before anything is fetched, so they
  // are a promise rather than a note. 92,361,116 bytes is 88 MiB, which is the
  // figure the privacy policy and the welcome page both state.
  assert.equal(store.MODEL_BYTES, 92_361_116);
  assert.ok(Math.round(store.MODEL_BYTES / 1024 / 1024) === 88);
  assert.equal(store.VOICE_BYTES, 522_240);
  assert.equal(store.VOICE_BYTES % (store.STYLE_DIM * 4), 0, 'a voice pack is whole frames');
});

test('a download reports cumulative progress against the real total', async () => {
  globalThis.fetch = fakeFetch({ bytes: 1000, chunk: 250 });
  const seen = [];

  await store.loadModel((loaded, total) => seen.push([loaded, total]));

  assert.deepEqual(seen, [[250, 1000], [500, 1000], [750, 1000], [1000, 1000]]);
});

test('a missing content-length gives a null total rather than a wrong one', async () => {
  // Callers must render an indeterminate bar. A zero or a guess here would show
  // a progress bar that is confidently wrong, which is worse than none.
  globalThis.fetch = fakeFetch({ bytes: 512, chunk: 512, contentLength: false });
  const seen = [];

  await store.loadModel((loaded, total) => seen.push([loaded, total]));

  assert.deepEqual(seen, [[512, null]]);
});

test('the second load comes from the cache and does not fetch again', async () => {
  const f = fakeFetch({ bytes: 400 });
  globalThis.fetch = f;

  const first = await store.loadModel();
  const second = await store.loadModel();

  assert.equal(f.calls.length, 1, 'a cache that never hits re-downloads 88 MB in silence');
  assert.equal(first.byteLength, 400);
  assert.deepEqual(new Uint8Array(second), new Uint8Array(first));
});

test('a cached load reports no progress, because nothing is downloading', async () => {
  await store.loadModel();
  let called = false;
  await store.loadModel(() => { called = true; });
  assert.equal(called, false);
});

test('a failed response names the status rather than caching an error page', async () => {
  globalThis.fetch = fakeFetch({ status: 503 });

  await assert.rejects(() => store.loadModel(), /503/);
  // The interesting half: a 503 body must not become the cached "model".
  assert.deepEqual(await store.status(), { model: false, voices: {} });
});

test('a voice pack is measured in frames, not assumed', async () => {
  globalThis.fetch = fakeFetch({ bytes: store.STYLE_DIM * 4 * 3, chunk: 4096 });

  const { floats, frames } = await store.loadVoice('af_heart');

  assert.equal(frames, 3);
  assert.equal(floats.length, store.STYLE_DIM * 3);
});

test('a wrongly shaped voice pack is refused rather than reshaped', async () => {
  // Truncating to a whole frame would produce a voice that speaks, badly, with
  // no indication anything was wrong.
  globalThis.fetch = fakeFetch({ bytes: store.STYLE_DIM * 4 + 4, chunk: 4096 });

  await assert.rejects(() => store.loadVoice('af_heart'), /is not a multiple of 256/);
});

test('a style frame is a copy, so handing it to the runtime cannot detach the pack', () => {
  const floats = new Float32Array(store.STYLE_DIM * 2).map((_, i) => i);

  const frame = store.styleFrame(floats, 1);
  frame[0] = -1;

  assert.equal(frame.length, store.STYLE_DIM);
  assert.equal(floats[store.STYLE_DIM], store.STYLE_DIM, 'the parent pack must be untouched');
  assert.notEqual(frame.buffer, floats.buffer, 'a subarray would share the parent buffer');
});

test('status reports each voice separately, not just whether any exist', async () => {
  globalThis.fetch = fakeFetch({ bytes: store.STYLE_DIM * 4 });
  await store.loadVoice('af_heart');

  const s = await store.status(['af_heart', 'am_michael']);

  assert.equal(s.model, false, 'a downloaded voice does not mean a downloaded model');
  assert.deepEqual(s.voices, { af_heart: true, am_michael: false });
});

test('evicting removes everything, and status then agrees', async () => {
  await store.loadModel();
  globalThis.fetch = fakeFetch({ bytes: store.STYLE_DIM * 4 });
  await store.loadVoice('af_heart');
  assert.deepEqual(await store.status(['af_heart']), { model: true, voices: { af_heart: true } });

  await store.evict();

  assert.deepEqual(await store.status(['af_heart']), { model: false, voices: { af_heart: false } });
});

test('the abort signal reaches fetch', async () => {
  // Without this the cancel button on a first-run download does nothing, and
  // the only symptom is a progress bar that keeps moving after you stop it.
  const f = fakeFetch();
  globalThis.fetch = f;
  const controller = new AbortController();

  await store.loadModel(undefined, controller.signal);

  assert.equal(f.calls[0].opts.signal, controller.signal);
});
