/**
 * Copy shared/*.json into the extension package.
 *
 * A Chrome extension can only load files inside its own folder, but `shared/`
 * is deliberately outside it so the Python side can read the same files. This
 * copies them in. It is not a build step in the interesting sense — no
 * transpiling, no bundling, no minifying — so what a store reviewer reads is
 * still exactly what runs.
 *
 * Run: node tools/sync-shared.mjs
 */

import { readdirSync, readFileSync, writeFileSync, mkdirSync, copyFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = join(root, 'shared');
const dest = join(root, 'extension', 'src', 'shared');

const FINGERPRINT = 'fingerprint.json';

mkdirSync(dest, { recursive: true });

const sha = (text) => `sha256:${createHash('sha256').update(text, 'utf8').digest('hex').slice(0, 16)}`;

/**
 * Strip every underscore-prefixed key, recursively, and sort what remains.
 *
 * The fingerprint answers one question: would this file produce different
 * spoken text than before? Documentation keys cannot, so hashing them produces
 * spurious invalidation. That is not hypothetical — renaming the project
 * changed a `_comment` mentioning a file path, which changed the raw hash of
 * `abbreviations.json`, which would have marked every stored reading position
 * as "rules changed" over an edit that altered no rule at all.
 *
 * Sorting keys means reformatting or reordering a file is also a non-event.
 * A false positive here only costs confidence in a position, never correctness,
 * but confidence is the entire point of the stamp.
 */
function semantic(value) {
  if (Array.isArray(value)) return value.map(semantic);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value)
        .filter((k) => !k.startsWith('_'))
        .sort()
        .map((k) => [k, semantic(value[k])]),
    );
  }
  return value;
}

let failed = false;
/** @type {Record<string, string>} */
const hashes = {};

const names = readdirSync(src)
  .filter((f) => f.endsWith('.json') && f !== FINGERPRINT)
  .sort(); // stable order, so the combined hash is deterministic

for (const name of names) {
  const text = readFileSync(join(src, name), 'utf8');
  let parsed;
  try {
    parsed = JSON.parse(text); // never ship a file that would throw at runtime
  } catch (e) {
    console.error(`invalid JSON in shared/${name}: ${e.message}`);
    failed = true;
    continue;
  }

  /**
   * Only files that change the *text of a sentence* belong in the fingerprint.
   *
   * Stored reading positions hold normalized sentence text, so segmentation and
   * normalization rules invalidate them. Pronunciation does not: it runs at
   * synthesis, per word, and never touches the stored string. Pagination and
   * site rules do not either.
   *
   * This was a real bug. Adding one per-site pagination selector — which cannot
   * change a single spoken word — changed the fingerprint and marked every
   * saved position as "rules changed", quietly downgrading confidence
   * everywhere for no reason.
   *
   * The flag is required rather than inferred. A new shared file that forgets
   * to declare itself fails here, loudly, instead of silently landing on
   * whichever default we guessed.
   */
  if (typeof parsed._affects_stored_text !== 'boolean') {
    console.error(
      `shared/${name} does not declare "_affects_stored_text". Set it true if `
      + 'editing this file changes the text of a sentence, false otherwise.',
    );
    failed = true;
    continue;
  }
  if (parsed._affects_stored_text) hashes[name] = sha(JSON.stringify(semantic(parsed)));

  // Written with the line endings .gitattributes asks for, whatever the source
  // happens to have on disk. A script editing shared/*.json on Windows leaves
  // CRLF behind, git considers the file unmodified because it normalises on
  // commit, and the copy then differs from its original in bytes while being
  // identical in content. That made the drift test fail for the wrong reason,
  // telling the reader to re-run this very script.
  writeFileSync(join(dest, name), text.split('\r\n').join('\n'));
  console.log(`synced ${name}${parsed._affects_stored_text ? ' (fingerprinted)' : ''}`);
}

if (failed) process.exit(1);

/**
 * Content-derived, with no timestamp, so re-running with no edits produces no
 * diff and the fingerprint cannot drift from what it describes.
 *
 * Why this exists: stored reading positions record the sentence text they were
 * captured from. Editing these rule files rewrites that text, so an anchor
 * saved yesterday may no longer match verbatim today. Adding "w/" to
 * expansions did exactly that. Stamping a position with the rules that produced
 * it lets a reader know to go straight to fuzzy matching instead of expecting
 * an exact hit and silently landing on sentence zero.
 */
const fingerprinted = names.filter((n) => hashes[n]);
if (!fingerprinted.length) {
  console.error('no shared file affects stored text; the fingerprint would be meaningless');
  process.exit(1);
}

const fingerprint = {
  _comment: 'Generated by tools/sync-shared.mjs. Do not edit by hand.',
  _purpose: 'Stamp stored reading positions with the rules version that produced them, so a later rules change is detectable rather than silent.',
  _covers: 'Only files declaring _affects_stored_text. Pronunciation, pagination and site rules are excluded: none of them changes the text a position is stored against.',
  combined: sha(fingerprinted.map((n) => `${n}:${hashes[n]}`).join('\n')),
  files: hashes,
};

const serialized = `${JSON.stringify(fingerprint, null, 2)}\n`;
writeFileSync(join(src, FINGERPRINT), serialized);
writeFileSync(join(dest, FINGERPRINT), serialized);
console.log(`fingerprint ${fingerprint.combined}`);

/**
 * Directories of shared data that are not JSON.
 *
 * The pronunciation dictionary is canonical in `shared/` rather than inside the
 * extension, because the desktop app needs it too and a deliverable that cannot
 * be built without reaching into another deliverable's package directory is the
 * wrong shape. A Chrome extension can only load files inside its own folder, so
 * the copy is the price — the same price `shared/*.json` already pays, and
 * CMUdict never changes, so it is paid once.
 */
const DIRS = [
  { from: 'cmudict', to: join(root, 'extension', 'vendor', 'cmudict') },
  { from: 'kokoro', to: join(root, 'extension', 'vendor', 'kokoro') },
];

/**
 * Individual files the site needs, copied rather than referenced.
 *
 * GitHub Pages serves only what is committed, so the demo cannot reach up into
 * `shared/` or `extension/` at runtime. Making these derived rather than
 * hand-copied is the same reasoning as everywhere else: a third copy of the
 * dictionary that drifts would have the demo pronouncing words differently from
 * the product it is advertising, which is worse than not having a demo.
 */
const SITE_FILES = [
  ['shared/cmudict/cmudict.txt.gz', 'site/assets/cmudict.txt.gz'],
  ['shared/cmudict/homographs.json', 'site/assets/homographs.json'],
  ['shared/kokoro/vocab.json', 'site/assets/vocab.json'],
  ['extension/src/engines/kokoro/g2p-en.js', 'site/vendor/g2p-en.js'],
  ['extension/src/engines/kokoro/tokenize.js', 'site/vendor/tokenize.js'],
  ['extension/src/engines/kokoro/worker.js', 'site/vendor/kokoro-worker.js'],
  ['extension/src/core/segment.js', 'site/vendor/segment.js'],
  ['extension/src/core/normalize.js', 'site/vendor/normalize.js'],
  ['shared/abbreviations.json', 'site/assets/abbreviations.json'],
  ['shared/normalization.json', 'site/assets/normalization.json'],
  ['extension/assets/icon-128.png', 'site/assets/icon-128.png'],
];

for (const { from, to: toDir } of DIRS) {
  const fromDir = join(src, from);
  if (!existsSync(fromDir)) {
    console.error(`missing shared/${from}`);
    process.exit(1);
  }
  mkdirSync(toDir, { recursive: true });

  let copied = 0;
  for (const name of readdirSync(fromDir)) {
    copyFileSync(join(fromDir, name), join(toDir, name));
    copied++;
  }
  if (!copied) {
    console.error(`shared/${from} is empty; the extension would ship without it`);
    process.exit(1);
  }

  // Confirm the files landed where the extension will look for them. The first
  // version of this loop reported success while writing one directory too high,
  // because the destination was built relative to the JSON output path rather
  // than to the extension root. A copy that logs success and lands elsewhere is
  // indistinguishable from a working one until something tries to load it.
  const landed = readdirSync(toDir);
  if (landed.length < copied) {
    console.error(`copied ${copied} files but ${toDir} holds ${landed.length}`);
    process.exit(1);
  }
  console.log(`synced ${from}/ -> ${toDir} (${copied} files)`);
}

for (const [from, to] of SITE_FILES) {
  const src_ = join(root, from);
  const dest_ = join(root, to);
  if (!existsSync(src_)) {
    console.error(`missing ${from}`);
    process.exit(1);
  }
  mkdirSync(dirname(dest_), { recursive: true });
  // Same reasoning as the JSON above, and the same exception: a line-ending
  // conversion applied to a gzip or a PNG corrupts it silently, so anything
  // binary is copied rather than rewritten.
  if (/\.(gz|png|onnx|bin|wasm)$/.test(src_)) {
    copyFileSync(src_, dest_);
  } else {
    writeFileSync(dest_, readFileSync(src_, 'utf8').split('\r\n').join('\n'));
  }
}
console.log(`synced ${SITE_FILES.length} files into site/`);
