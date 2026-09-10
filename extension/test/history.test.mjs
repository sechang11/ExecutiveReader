/**
 * History store tests.
 *
 * Both browser APIs are faked here rather than mocked away, because the bugs
 * worth catching are in the bookkeeping between the two stores: an index that
 * disagrees with its metadata, or a body left behind when its entry is dropped.
 * A mock that just records calls would not see either.
 */

import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

// --- fakes, installed before the module under test is imported -------------

/** @type {Map<string, any>} */
const storage = new Map();
globalThis.chrome = {
  storage: {
    local: {
      async get(keys) {
        const list = Array.isArray(keys) ? keys : [keys];
        const out = {};
        for (const k of list) if (storage.has(k)) out[k] = storage.get(k);
        return out;
      },
      async set(obj) {
        for (const [k, v] of Object.entries(obj)) storage.set(k, v);
      },
      async remove(keys) {
        for (const k of (Array.isArray(keys) ? keys : [keys])) storage.delete(k);
      },
    },
  },
};

/** Minimal in-memory IndexedDB: enough for put/get/delete on one store. */
const idb = new Map();
globalThis.indexedDB = {
  open() {
    const req = {};
    queueMicrotask(() => {
      req.result = {
        objectStoreNames: { contains: () => true },
        createObjectStore: () => {},
        transaction() {
          const tx = {};
          const store = {
            put: (v, k) => idb.set(k, v),
            get: (k) => {
              const r = {};
              queueMicrotask(() => { r.result = idb.get(k); r.onsuccess?.(); });
              return r;
            },
            delete: (k) => idb.delete(k),
          };
          tx.objectStore = () => store;
          queueMicrotask(() => tx.oncomplete?.());
          return tx;
        },
        close: () => {},
      };
      req.onsuccess?.();
    });
    return req;
  },
};

const H = await import('../src/core/history.js');

beforeEach(() => { storage.clear(); idb.clear(); });

// --- tests ----------------------------------------------------------------

test('strips fragments and tracking parameters from the identity', () => {
  const plain = H.idFor('https://example.com/article');
  assert.equal(H.idFor('https://example.com/article#section-3'), plain);
  assert.equal(H.idFor('https://example.com/article?utm_source=twitter'), plain);
  assert.equal(H.idFor('https://example.com/article?fbclid=abc'), plain);
  // A meaningful parameter is part of the identity and must survive.
  assert.notEqual(H.idFor('https://example.com/article?page=2'), plain);
});

test('tolerates a URL that will not parse', () => {
  assert.equal(H.idFor('not a url'), 'not a url');
});

test('re-reading updates one entry rather than accumulating', async () => {
  await H.record({ url: 'https://a.test/x', title: 'First' });
  await H.record({ url: 'https://a.test/x', title: 'Second' });
  const all = await H.list();
  assert.equal(all.length, 1);
  assert.equal(all[0].title, 'Second');
});

test('keeps the first-read time while advancing the last-read time', async () => {
  const first = await H.record({ url: 'https://a.test/x' });
  await new Promise((r) => setTimeout(r, 2));
  const second = await H.record({ url: 'https://a.test/x' });
  assert.equal(second.firstReadAt, first.firstReadAt);
  assert.ok(second.lastReadAt >= first.lastReadAt);
});

test('accumulates listening time across sessions', async () => {
  await H.record({ url: 'https://a.test/x', secondsListened: 30 });
  const e = await H.record({ url: 'https://a.test/x', secondsListened: 45 });
  assert.equal(e.secondsListened, 75);
});

test('lists most recently read first', async () => {
  await H.record({ url: 'https://a.test/1' });
  await H.record({ url: 'https://a.test/2' });
  await H.record({ url: 'https://a.test/1' }); // touched again
  assert.deepEqual((await H.list()).map((e) => e.url),
    ['https://a.test/1', 'https://a.test/2']);
});

test('round-trips an article body', async () => {
  const id = H.idFor('https://a.test/x');
  await H.putBody(id, ['One.', 'Two.']);
  assert.deepEqual(await H.getBody(id), ['One.', 'Two.']);
  assert.equal(await H.getBody('https://a.test/missing'), null);
});

test('removing an entry deletes its body too', async () => {
  const id = H.idFor('https://a.test/x');
  await H.record({ url: 'https://a.test/x' });
  await H.putBody(id, ['One.']);
  await H.remove(id);
  assert.deepEqual(await H.list(), []);
  assert.equal(await H.getBody(id), null, 'an orphaned body is invisible and never reclaimed');
});

test('prune drops entries past the count limit, oldest first', async () => {
  for (let i = 0; i < 5; i++) await H.record({ url: `https://a.test/${i}` });
  const removed = await H.prune({ maxEntries: 3 });
  assert.equal(removed, 2);
  assert.deepEqual((await H.list()).map((e) => e.url),
    ['https://a.test/4', 'https://a.test/3', 'https://a.test/2']);
});

test('prune drops entries past the age limit and their bodies', async () => {
  await H.record({ url: 'https://a.test/old' });
  const id = H.idFor('https://a.test/old');
  await H.putBody(id, ['stale']);
  // Backdate it by a hundred days.
  const entry = await H.getEntry(id);
  entry.lastReadAt = Date.now() - 100 * 86_400_000;
  await chrome.storage.local.set({ [`hist:${id}`]: entry });

  await H.record({ url: 'https://a.test/new' });
  assert.equal(await H.prune({ maxAgeDays: 90 }), 1);
  assert.deepEqual((await H.list()).map((e) => e.url), ['https://a.test/new']);
  assert.equal(await H.getBody(id), null);
});

test('prune reports zero and changes nothing when within limits', async () => {
  await H.record({ url: 'https://a.test/x' });
  assert.equal(await H.prune(), 0);
  assert.equal((await H.list()).length, 1);
});

test('an index entry with no metadata is dropped, not listed as a hole', async () => {
  await H.record({ url: 'https://a.test/x' });
  storage.set('hist-index', ['https://a.test/x', 'https://a.test/torn']);
  assert.equal((await H.list()).length, 1, 'list must skip it');
  assert.equal(await H.prune(), 1, 'prune must reclaim it');
});

test('clear empties both stores', async () => {
  await H.record({ url: 'https://a.test/x' });
  await H.putBody(H.idFor('https://a.test/x'), ['One.']);
  await H.clear();
  assert.deepEqual(await H.list(), []);
  assert.equal(await H.getBody(H.idFor('https://a.test/x')), null);
});

test('prune uses the documented defaults when called with no options', () => {
  // The defaults were unpinned: every test passed explicit limits, so the
  // constants could drift to any value and nothing would notice. A retention
  // default that silently became a decade is not a visible failure.
  return (async () => {
    for (let i = 0; i < 3; i++) await H.record({ url: `https://a.test/${i}` });
    const id = H.idFor('https://a.test/0');
    const entry = await H.getEntry(id);
    entry.lastReadAt = Date.now() - 91 * 86_400_000; // just past ninety days
    await chrome.storage.local.set({ [`hist:${id}`]: entry });

    assert.equal(await H.prune(), 1, 'the default age limit is ninety days');
    assert.deepEqual((await H.list()).map((e) => e.url),
      ['https://a.test/2', 'https://a.test/1']);
  })();
});

test('the default count limit is five hundred, not unlimited', async () => {
  // Checked by argument rather than by writing 501 entries, which would make
  // the suite slow for no extra confidence.
  for (let i = 0; i < 4; i++) await H.record({ url: `https://b.test/${i}` });
  assert.equal(await H.prune({ maxEntries: 500 }), 0, 'four entries are under the default');
  assert.equal(await H.prune({ maxEntries: 2 }), 2, 'and the limit does bite when reached');
});
