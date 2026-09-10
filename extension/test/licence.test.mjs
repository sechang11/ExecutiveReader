/**
 * Licence guard.
 *
 * The extension ships nothing copyleft, deliberately: eSpeak would give better
 * pronunciation and seven more languages and is GPL-3.0, which would make the
 * whole extension GPL and foreclose a proprietary tier later.
 *
 * That is a property of what is vendored, and a property nothing checks is a
 * property that lapses. Someone adds a phonemizer for convenience, it works
 * beautifully, and the licence position is gone with no visible symptom.
 *
 * Two lessons from the desktop half are built in here, both learned the
 * expensive way:
 *
 * 1. **Scan everything, not the convenient subset.** The first version of this
 *    file read only text-shaped files and so examined 6% of vendored bytes,
 *    skipping the 26 MB WebAssembly binary entirely — the single largest thing
 *    shipped. A check that quietly ignores most of its subject is worse than no
 *    check, because it reads as assurance.
 * 2. **Reasoning finds only the problem you are already thinking about.** The
 *    desktop half found eSpeak by reasoning about the speech pipeline, and then
 *    found an Affero-GPL EPUB library two layers away only by scanning the
 *    whole dependency list. Enumerate; do not reason.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync, existsSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import path from 'node:path';

const root = new URL('../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const vendorDir = path.join(root, 'vendor');

/** Text meaning a file carries, or embeds, a copyleft licence. */
const COPYLEFT = [
  /GNU GENERAL PUBLIC LICENSE/i,
  /GNU LESSER GENERAL PUBLIC/i,
  /GNU AFFERO GENERAL PUBLIC/i,
  /\bAGPL-3/i,
  /\bGPL-[23]\.0(?:-or-later|-only)?\b/i,
];

/** eSpeak, under any of the names it travels under. */
const ESPEAK = [/eSpeakNGWorker/, /espeakng_loader/, /espeak_VOICE/, /piper-phonemize/i];

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else out.push(p);
  }
  return out;
}

/**
 * Every vendored file as searchable text.
 *
 * Binaries are read as latin1 rather than skipped: embedded licence text is
 * plain ASCII inside a wasm blob and searches perfectly well. Compressed files
 * are decompressed, since a gzip hides its contents from any string search.
 */
function readable(file) {
  const buf = readFileSync(file);
  if (/\.gz$/i.test(file)) {
    try {
      return gunzipSync(buf).toString('latin1');
    } catch {
      return buf.toString('latin1');
    }
  }
  return buf.toString('latin1');
}

const vendored = existsSync(vendorDir) ? walk(vendorDir) : [];
/** Our own notes name these licences in order to explain avoiding them. */
const isOurNote = (f) => /^NOTICE/i.test(path.basename(f));
const subjects = vendored.filter((f) => !isOurNote(f));

test('the scan has a subject, and covers essentially all of it', () => {
  // Guards the guard. An empty vendor directory, or a filter that quietly
  // excludes the big files, would otherwise pass everything below.
  assert.ok(subjects.length >= 8, `only ${subjects.length} vendored files found`);

  const total = subjects.reduce((n, f) => n + statSync(f).size, 0);
  assert.ok(total > 20_000_000, `only ${(total / 1e6).toFixed(1)} MB in scope`);
});

test('no vendored file carries or embeds a copyleft licence', () => {
  const offenders = [];
  for (const file of subjects) {
    const text = readable(file);
    for (const pattern of COPYLEFT) {
      if (pattern.test(text)) offenders.push(`${path.relative(root, file)} matches ${pattern}`);
    }
  }
  assert.deepEqual(offenders, []);
});

test('eSpeak is not vendored, under any of its names', () => {
  // The trap is that a permissive wrapper can carry a copyleft payload: the npm
  // `phonemizer` declares Apache-2.0 and embeds eSpeak as WebAssembly.
  const byName = subjects.filter((f) => /espeak|phonemizer/i.test(path.basename(f)));
  assert.deepEqual(byName.map((f) => path.relative(root, f)), []);

  const bySymbol = [];
  for (const file of subjects) {
    const text = readable(file);
    for (const pattern of ESPEAK) {
      if (pattern.test(text)) bySymbol.push(`${path.relative(root, file)} matches ${pattern}`);
    }
  }
  assert.deepEqual(bySymbol, []);
});

test('every vendored dependency states its licence', () => {
  const dirs = readdirSync(vendorDir).filter((d) => statSync(path.join(vendorDir, d)).isDirectory());
  assert.ok(dirs.length >= 3, `only ${dirs.length} vendor directories`);

  for (const d of dirs) {
    const files = readdirSync(path.join(vendorDir, d));
    assert.ok(
      files.some((f) => /^LICENSE|^NOTICE/i.test(f)),
      `vendor/${d} ships no LICENSE or NOTICE, so its terms are unrecorded`,
    );
  }
});

test('binary dependencies carry checksums', () => {
  // A vendored binary nobody can verify is a supply-chain hole: swapping it
  // would be undetectable.
  for (const d of ['onnxruntime', 'pdfjs']) {
    const dir = path.join(vendorDir, d);
    if (!existsSync(dir)) continue;
    assert.ok(readdirSync(dir).includes('SHA256SUMS'), `vendor/${d} has no SHA256SUMS`);
  }
});

test('vendor/ is the whole third-party surface', () => {
  // If a package dependency ever appears, this scan stops being complete and
  // the licence position moves somewhere nothing is looking. Failing here
  // forces the question to be answered rather than skipped.
  const pkg = JSON.parse(readFileSync(path.join(root, 'package.json'), 'utf8'));
  assert.deepEqual(pkg.dependencies ?? {}, {},
    'a runtime dependency is third-party code this scan does not examine');
  assert.deepEqual(pkg.devDependencies ?? {}, {});
});
