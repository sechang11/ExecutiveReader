/**
 * Reading history: what you read, and where you stopped.
 *
 * Storage is split by size and by access pattern. Metadata — URL, title,
 * position, timestamps — lives in `chrome.storage.local`, which is small,
 * synchronous to query, and easy to enumerate for the history list. Article
 * bodies live in IndexedDB, because `chrome.storage.local` caps at ten megabytes
 * without the unlimited-storage permission and a few hundred articles would blow
 * through that. See spec section 7.
 *
 * Names here are deliberate. A rename has to migrate every one of them, and the
 * desktop half already shipped a bug where the folder moved but the database
 * file inside it did not, silently starting with empty history while every log
 * line reported success. See the renaming table near the top of the spec.
 */

/**
 * Deliberately still the old product name. **Do not rename this.**
 *
 * It is the IndexedDB database name, so changing it does not migrate anything —
 * it opens a different, empty database and every saved reading position becomes
 * unreachable. Nothing reports that: the extension simply starts with no
 * history and looks like it is working.
 *
 * A branding change is not a reason to make someone's data disappear. If this
 * ever must move, it needs a migration that opens the old name, copies, and
 * only then deletes.
 */
const DB_NAME = 'earmark-history';
const DB_VERSION = 1;
const STORE_BODIES = 'bodies';
const META_PREFIX = 'hist:';
const INDEX_KEY = 'hist-index';

/** Defaults; both configurable, per spec section 7. */
const MAX_ENTRIES = 500;
const MAX_AGE_DAYS = 90;

/**
 * @typedef {{
 *   id: string, url: string, title: string, favicon: string|null,
 *   firstReadAt: number, lastReadAt: number, secondsListened: number,
 *   voiceKey: string|null, total: number,
 *   anchor: import('./anchor.js').Anchor|null,
 * }} Entry
 */

/** One entry per URL, so re-reading updates rather than accumulating. */
export function idFor(url) {
  // Fragment and tracking parameters do not identify a different article.
  try {
    const u = new URL(url);
    u.hash = '';
    for (const p of [...u.searchParams.keys()]) {
      if (/^(utm_|fbclid|gclid|mc_|ref$|source$)/i.test(p)) u.searchParams.delete(p);
    }
    return u.toString();
  } catch {
    return url;
  }
}

// ------------------------------------------------------------------ bodies

/** @returns {Promise<IDBDatabase>} */
function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_BODIES)) {
        db.createObjectStore(STORE_BODIES);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

/** @param {string} id @param {string[]} sentences */
export async function putBody(id, sentences) {
  const db = await openDb();
  try {
    await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_BODIES, 'readwrite');
      tx.objectStore(STORE_BODIES).put(sentences, id);
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
  } finally {
    db.close(); // unconditional: a stuck connection blocks the next upgrade
  }
}

/** @param {string} id @returns {Promise<string[]|null>} */
export async function getBody(id) {
  const db = await openDb();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_BODIES, 'readonly');
      const req = tx.objectStore(STORE_BODIES).get(id);
      req.onsuccess = () => resolve(req.result ?? null);
      req.onerror = () => reject(req.error);
    });
  } finally {
    db.close();
  }
}

/** @param {string[]} ids */
export async function deleteBodies(ids) {
  if (!ids.length) return;
  const db = await openDb();
  try {
    await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_BODIES, 'readwrite');
      const store = tx.objectStore(STORE_BODIES);
      for (const id of ids) store.delete(id);
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

// ---------------------------------------------------------------- metadata

const metaKey = (id) => META_PREFIX + id;

/** @returns {Promise<string[]>} ids, most recently read first */
async function readIndex() {
  const got = await chrome.storage.local.get(INDEX_KEY);
  return got[INDEX_KEY] ?? [];
}

/** @param {string[]} ids */
async function writeIndex(ids) {
  await chrome.storage.local.set({ [INDEX_KEY]: ids });
}

/** @param {string} id @returns {Promise<Entry|null>} */
export async function getEntry(id) {
  const got = await chrome.storage.local.get(metaKey(id));
  return got[metaKey(id)] ?? null;
}

/**
 * Record or update a position. Called often, so it touches only the index and
 * one metadata key; the body is written separately and only when it changed.
 *
 * @param {Partial<Entry> & {url: string}} patch
 * @returns {Promise<Entry>}
 */
export async function record(patch) {
  const id = idFor(patch.url);
  const now = Date.now();
  const existing = await getEntry(id);

  /** @type {Entry} */
  const entry = {
    id,
    url: patch.url,
    title: patch.title ?? existing?.title ?? patch.url,
    favicon: patch.favicon ?? existing?.favicon ?? null,
    firstReadAt: existing?.firstReadAt ?? now,
    lastReadAt: now,
    secondsListened: (existing?.secondsListened ?? 0) + (patch.secondsListened ?? 0),
    voiceKey: patch.voiceKey ?? existing?.voiceKey ?? null,
    total: patch.total ?? existing?.total ?? 0,
    anchor: patch.anchor ?? existing?.anchor ?? null,
  };

  await chrome.storage.local.set({ [metaKey(id)]: entry });

  const ids = await readIndex();
  await writeIndex([id, ...ids.filter((x) => x !== id)]);
  return entry;
}

/** @param {number} [limit] @returns {Promise<Entry[]>} */
export async function list(limit = 100) {
  const ids = (await readIndex()).slice(0, limit);
  if (!ids.length) return [];
  const got = await chrome.storage.local.get(ids.map(metaKey));
  // An id in the index with no metadata is a torn write, not an entry.
  return ids.map((id) => got[metaKey(id)]).filter(Boolean);
}

/** @param {string} id */
export async function remove(id) {
  await chrome.storage.local.remove(metaKey(id));
  await writeIndex((await readIndex()).filter((x) => x !== id));
  await deleteBodies([id]);
}

export async function clear() {
  const ids = await readIndex();
  await chrome.storage.local.remove([...ids.map(metaKey), INDEX_KEY]);
  await deleteBodies(ids);
}

/**
 * Drop entries past the retention limits.
 *
 * Bodies are deleted alongside their metadata rather than on a separate
 * schedule, because an orphaned body is invisible: nothing lists it, nothing
 * reads it, and it counts against quota forever.
 *
 * @param {{maxEntries?: number, maxAgeDays?: number}} [opts]
 * @returns {Promise<number>} how many entries were removed
 */
export async function prune(opts = {}) {
  const maxEntries = opts.maxEntries ?? MAX_ENTRIES;
  const maxAgeDays = opts.maxAgeDays ?? MAX_AGE_DAYS;
  const cutoff = Date.now() - maxAgeDays * 86_400_000;

  const ids = await readIndex();
  if (!ids.length) return 0;

  const got = await chrome.storage.local.get(ids.map(metaKey));
  const keep = [];
  const drop = [];

  ids.forEach((id, position) => {
    const entry = got[metaKey(id)];
    if (!entry) { drop.push(id); return; } // torn write
    if (position >= maxEntries || entry.lastReadAt < cutoff) drop.push(id);
    else keep.push(id);
  });

  if (!drop.length) return 0;
  await chrome.storage.local.remove(drop.map(metaKey));
  await writeIndex(keep);
  await deleteBodies(drop);
  return drop.length;
}
