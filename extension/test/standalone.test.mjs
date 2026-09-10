/**
 * Packaging integrity.
 *
 * The pronunciation dictionary is canonical in `shared/` and copied into the
 * extension, because the desktop half needs it too and a deliverable that
 * cannot be built without reaching into another deliverable's package directory
 * is the wrong shape.
 *
 * A copy has one failure mode: it stops being made, or is made somewhere else,
 * and nothing notices until a user loads the extension and neural voices are
 * silently absent. That already happened once — the sync loop reported success
 * while writing one directory too high, because its destination was relative to
 * the JSON output path rather than to the extension root.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, statSync, readdirSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import path from 'node:path';

const extRoot = new URL('../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const repoRoot = path.join(extRoot, '..');
const sharedRoot = path.join(repoRoot, 'shared');

/**
 * Directories copied from `shared/` into the extension, discovered rather than
 * listed, so a fourth one is covered the day it appears instead of the day
 * someone remembers to add it here.
 */
const synced = readdirSync(sharedRoot)
  .filter((n) => statSync(path.join(sharedRoot, n)).isDirectory());

test('there are shared directories to check', () => {
  // Guards the guard: an empty list would make every test below vacuous.
  assert.ok(synced.length >= 2, `only ${synced.length} shared directories found`);
});

test('shared data is canonical outside the extension', () => {
  // If any of this moves back inside, the desktop half cannot be packaged on
  // its own without copying files out of the extension's directory. That is
  // the whole reason these live in shared/.
  for (const dir of synced) {
    const files = readdirSync(path.join(sharedRoot, dir));
    assert.ok(files.length > 0, `shared/${dir} is empty`);
  }
});

test('the copy the extension loads is present and complete', () => {
  // A Chrome extension can only load files inside its own folder, so the copy
  // is not optional and must be committed, not generated at load time.
  let checked = 0;
  for (const dir of synced) {
    for (const name of readdirSync(path.join(sharedRoot, dir))) {
      const p = path.join(extRoot, 'vendor', dir, name);
      assert.ok(existsSync(p), `vendor/${dir}/${name} is missing from the package`);
      assert.ok(statSync(p).size > 0, `vendor/${dir}/${name} is empty`);
      checked++;
    }
  }
  assert.ok(checked >= 4, `only ${checked} files checked`);
});

test('the copy matches the canonical version byte for byte', () => {
  // The specific failure mode of a copy: a stale duplicate does not break, it
  // answers. Both halves would agree, both harnesses would pass, and words
  // would be pronounced by an older rule with nothing reporting it.
  let compared = 0;
  for (const dir of synced) {
    for (const name of readdirSync(path.join(sharedRoot, dir))) {
      assert.deepEqual(
        readFileSync(path.join(extRoot, 'vendor', dir, name)),
        readFileSync(path.join(sharedRoot, dir, name)),
        `vendor/${dir}/${name} has drifted from shared/${dir}/${name}; re-run tools/sync-shared.mjs`,
      );
      compared++;
    }
  }
  assert.ok(compared >= 4, `only ${compared} files compared; the check is not looking at much`);
});

test('the phoneme alphabet is the size the model expects', () => {
  // Present-and-wrong is as silent as present-and-corrupt: a truncated
  // alphabet drops symbols at tokenization and words quietly lose sounds.
  const vocab = JSON.parse(readFileSync(path.join(extRoot, 'vendor', 'kokoro', 'vocab.json'), 'utf8'));
  assert.equal(Object.keys(vocab).length, 115);
});

test('the shipped dictionary is usable, not merely present', () => {
  // Present-and-corrupt is a real outcome for a copied binary. Decompressing
  // and counting entries is the difference between a file existing and a
  // dictionary working.
  const text = gunzipSync(readFileSync(path.join(extRoot, 'vendor', 'cmudict','cmudict.txt.gz'))).toString('utf8');
  const lines = text.split('\n').filter(Boolean);
  assert.ok(lines.length > 120_000, `only ${lines.length} entries`);
  assert.match(lines[0], /^\S+ [A-Z]/, 'each line is a word followed by ARPAbet phones');
});

test('the homograph table holds real alternates', () => {
  const table = JSON.parse(readFileSync(path.join(extRoot, 'vendor', 'cmudict','homographs.json'), 'utf8'));
  const words = Object.keys(table);
  assert.ok(words.length >= 20, `only ${words.length} homographs`);
  for (const w of words) {
    assert.ok(Array.isArray(table[w].noun) && Array.isArray(table[w].verb), `${w} needs both readings`);
    assert.notDeepEqual(table[w].noun, table[w].verb, `${w} readings must differ`);
  }
});
