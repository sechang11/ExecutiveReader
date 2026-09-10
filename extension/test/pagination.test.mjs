/**
 * Next-page detection.
 *
 * The DOM strategies need a document, so these cover the pure parts: URL
 * incrementing, which is the strategy most likely to be wrong, and the
 * confidence gate that decides whether a guess is followed or offered.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { incrementUrl, needsConfirmation, loadRules } from '../src/content/pagination.js';

const rules = JSON.parse(
  readFileSync(new URL('../src/shared/pagination.json', import.meta.url), 'utf8'),
);
loadRules(rules);

/** The url-increment strategy's pattern, taken from the shared rules rather
 *  than duplicated, so a change there is caught here. */
const PATTERN = rules.strategies.find((s) => s.id === 'url-increment').pattern;

test('increments a page query parameter', () => {
  assert.equal(incrementUrl('https://x.test/a?page=2', PATTERN), 'https://x.test/a?page=3');
  assert.equal(incrementUrl('https://x.test/a?p=9', PATTERN), 'https://x.test/a?p=10');
});

test('preserves zero padding', () => {
  // page=007 becoming page=8 breaks sites that pad, and the failure is a 404
  // rather than anything visible here.
  assert.equal(incrementUrl('https://x.test/a?page=007', PATTERN), 'https://x.test/a?page=008');
  assert.equal(incrementUrl('https://x.test/a?page=09', PATTERN), 'https://x.test/a?page=10');
});

test('leaves other numbers in the URL alone', () => {
  assert.equal(incrementUrl('https://x.test/2024/03/article', PATTERN), null);
  assert.equal(incrementUrl('https://x.test/a?id=5', PATTERN), null);
});

test('returns null rather than throwing on an unusable pattern', () => {
  // A rules file newer than this code must degrade, not crash the reader.
  assert.equal(incrementUrl('https://x.test/a?page=2', '('), null);
});

test('only high and medium confidence are followed without asking', () => {
  // Following a low-confidence guess navigates away from what someone is
  // reading, which is the expensive failure here.
  assert.equal(needsConfirmation({ confidence: 'high' }), false);
  assert.equal(needsConfirmation({ confidence: 'medium' }), false);
  assert.equal(needsConfirmation({ confidence: 'low' }), true);
  assert.equal(needsConfirmation({ confidence: undefined }), true);
});

test('the shared rules are ordered most reliable first', () => {
  const rank = { high: 3, medium: 2, low: 1 };
  const order = rules.strategies.map((s) => rank[s.confidence] ?? 0);
  assert.deepEqual([...order].sort((a, b) => b - a), order,
    'a low-confidence strategy must never be tried before a high-confidence one');
});

test('every shared strategy is a shape the code understands', () => {
  for (const s of rules.strategies) {
    assert.ok(s.id, 'each strategy needs an id');
    assert.ok(s.selector || s.textMatch || s.pattern,
      `${s.id} has no selector, textMatch or pattern, so it can never match`);
  }
});
