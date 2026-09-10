/**
 * Offset mapping tests.
 *
 * Every highlight, every click-to-read-from-here, and every history resume goes
 * through this conversion. If it is off by a character the highlighter appears
 * to lag the voice, which reads as a timing bug and sends you looking in
 * entirely the wrong place. Hence the pinning.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { clampToNode, trustsExplicitRoot } from '../src/content/extract.js';

/**
 * Mirror what extractBlocks does to a raw text node, then assert that every
 * collapsed offset maps back to the right raw character.
 * @param {string} raw
 */
function checkRoundTrip(raw) {
  const cleaned = raw.replace(/\s+/g, ' ').trim();
  const piece = { node: { nodeValue: raw }, start: 0, end: cleaned.length };

  for (let i = 0; i < cleaned.length; i++) {
    const at = clampToNode(piece, i);
    const expected = cleaned[i];
    const actual = raw[at];
    // A collapsed space may land on any character of the original run.
    if (expected === ' ') {
      assert.match(actual ?? '', /\s/, `offset ${i} of ${JSON.stringify(raw)}`);
    } else {
      assert.equal(actual, expected, `offset ${i} of ${JSON.stringify(raw)}`);
    }
  }
}

test('maps offsets in clean text', () => {
  checkRoundTrip('Hello world');
});

test('skips leading whitespace that trim removed', () => {
  const piece = { node: { nodeValue: '   Hello' }, start: 0, end: 5 };
  assert.equal(clampToNode(piece, 0), 3);
  assert.equal(clampToNode(piece, 1), 4);
});

test('collapses internal whitespace runs', () => {
  checkRoundTrip('Hello    world');
  checkRoundTrip('Hello\n\n\tworld');
  checkRoundTrip('  a  b  c  ');
});

test('handles newline-indented markup, the common real case', () => {
  checkRoundTrip('\n      The quick brown fox\n      jumps over it.\n    ');
});

test('respects a non-zero piece start', () => {
  const piece = { node: { nodeValue: 'world' }, start: 6, end: 11 };
  assert.equal(clampToNode(piece, 6), 0);
  assert.equal(clampToNode(piece, 8), 2);
});

test('clamps past the end rather than throwing', () => {
  const piece = { node: { nodeValue: 'abc' }, start: 0, end: 3 };
  assert.equal(clampToNode(piece, 99), 3);
});

test('handles whitespace-only and empty nodes', () => {
  assert.equal(clampToNode({ node: { nodeValue: '' }, start: 0, end: 0 }, 0), 0);
  assert.equal(clampToNode({ node: { nodeValue: '   ' }, start: 0, end: 0 }, 0), 3);
});

test('a short article is still recognised as the article', () => {
  // The bug this replaces: a 400-character floor meant any genuinely short
  // page — a news brief, a definition, a poem — was rejected as "not enough
  // text" and sent to the scoring walk, which on a short page can find nothing
  // and fall back to the whole body, navigation included. The question is
  // whether the element holds the content, not whether there is much of it.
  assert.equal(trustsExplicitRoot(58, 70), true, 'a 58-character page is a page');
  assert.equal(trustsExplicitRoot(150, 200), true);
  assert.equal(trustsExplicitRoot(2000, 9000), true, 'substantial on its own');
});

test('a tiny element on a large page is not the article', () => {
  // The case the floor was really guarding: a stub <main> wrapping a heading
  // while the actual text sits elsewhere.
  assert.equal(trustsExplicitRoot(40, 20_000), false);
  assert.equal(trustsExplicitRoot(199, 5000), false, 'just under, and only 4% of the page');
});

test('a paragraph is substantial even on a page dominated by other things', () => {
  // Pins the floor itself. Without this, raising it back to 400 broke nothing,
  // because every other case passes on the share test instead. A 300-character
  // article inside a 5,000-character page of navigation and comments is 6% of
  // the page and still the article.
  assert.equal(trustsExplicitRoot(300, 5000), true);
  assert.equal(trustsExplicitRoot(199, 5000), false, 'just below the floor, and too small a share');
});

test('an empty explicit element is never the article', () => {
  assert.equal(trustsExplicitRoot(0, 5000), false);
  assert.equal(trustsExplicitRoot(0, 0), false);
});

test('the share test survives a body smaller than the candidate', () => {
  // Shadow DOM and iframes can make a candidate report more text than body.
  assert.equal(trustsExplicitRoot(100, 0), true);
});
