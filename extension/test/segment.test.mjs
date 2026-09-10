/**
 * Segmentation tests. Run: node --test extension/test/
 *
 * These are the cases that actually bite. A false break mid-sentence is the
 * most audible bug the reader can have, so abbreviations, initials, decimals,
 * and quoted speech all get pinned down here.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { segment, loadAbbreviations } from '../src/core/segment.js';

loadAbbreviations(JSON.parse(
  readFileSync(new URL('../src/shared/abbreviations.json', import.meta.url), 'utf8'),
));

/** @param {string} text */
const texts = (text) => segment(text).map((s) => s.text);

test('splits plain sentences', () => {
  assert.deepEqual(
    texts('One thing happened. Then another. And a third!'),
    ['One thing happened.', 'Then another.', 'And a third!'],
  );
});

test('does not break on titles', () => {
  assert.deepEqual(
    texts('Dr. Chen met Mrs. Alvarez on Tuesday.'),
    ['Dr. Chen met Mrs. Alvarez on Tuesday.'],
  );
});

test('does not break on initials', () => {
  assert.deepEqual(
    texts('J. R. R. Tolkien wrote it.'),
    ['J. R. R. Tolkien wrote it.'],
  );
});

test('does not break on decimals or versions', () => {
  assert.deepEqual(texts('It cost 3.50 and weighed 1.2 kg.'), ['It cost 3.50 and weighed 1.2 kg.']);
  assert.deepEqual(texts('Upgrade to 2.1.3 today.'), ['Upgrade to 2.1.3 today.']);
});

test('does not break on dotted acronyms', () => {
  assert.deepEqual(
    texts('The U.S. economy grew, i.e. it expanded.'),
    ['The U.S. economy grew, i.e. it expanded.'],
  );
});

test('still breaks after an abbreviation that ends a sentence', () => {
  assert.deepEqual(
    texts('The meeting is at 5 p.m. The train leaves later.'),
    ['The meeting is at 5 p.m.', 'The train leaves later.'],
  );
});

test('keeps closing punctuation with its sentence', () => {
  assert.deepEqual(
    texts('She said "go now." He left.'),
    ['She said "go now."', 'He left.'],
  );
});

test('treats ?! as one boundary', () => {
  assert.deepEqual(texts('Really?! I had no idea.'), ['Really?!', 'I had no idea.']);
});

test('does not break inside a URL or filename', () => {
  assert.deepEqual(
    texts('Open notes.txt from example.com/a.b now.'),
    ['Open notes.txt from example.com/a.b now.'],
  );
});

test('offsets map back to the source exactly', () => {
  const src = '  First one. Then the second!  ';
  for (const s of segment(src)) {
    assert.equal(src.slice(s.start, s.end), s.text);
  }
});

test('splits over-long sentences at clause boundaries', () => {
  const long = `${'word '.repeat(60)}, and then ${'more '.repeat(60)}.`;
  const parts = segment(long, { maxChars: 120 });
  assert.ok(parts.length > 1, 'should split');
  for (const p of parts) assert.ok(p.text.length <= 130, `too long: ${p.text.length}`);
  // Splitting must not lose or invent characters.
  assert.equal(parts.map((p) => p.text).join(' ').replace(/\s+/g, ' ').trim(),
    long.replace(/\s+/g, ' ').trim());
});

test('handles empty and whitespace input', () => {
  assert.deepEqual(texts(''), []);
  assert.deepEqual(texts('   \n  '), []);
});
