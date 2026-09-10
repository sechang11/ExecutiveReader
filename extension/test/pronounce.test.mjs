/**
 * Pronunciation overrides.
 *
 * This module exists because the permissive dictionary mispronounces proper
 * nouns, which is the failure a text-to-speech reader gets judged on. The
 * shipped list had been present in the package and read by nothing, so every
 * one of these assertions is against behaviour that did not previously happen.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { loadBuiltin, loadUser, pronounce, rules } from '../src/core/pronounce.js';

const data = JSON.parse(
  readFileSync(new URL('../src/shared/pronunciation.json', import.meta.url), 'utf8'),
);
const builtinCount = loadBuiltin(data);
loadUser([]);

test('the shipped list is loaded and non-trivial', () => {
  assert.ok(builtinCount >= 40, `only ${builtinCount} shipped rules`);
});

test('applies a shipped override', () => {
  assert.equal(pronounce('The GIF loaded.').text, 'The jiff loaded.');
  assert.equal(pronounce('Run nginx now.').text, 'Run engine ex now.');
});

test('reports whether it changed anything', () => {
  assert.equal(pronounce('The GIF loaded.').changed, true);
  assert.equal(pronounce('Nothing here matches.').changed, false);
});

test('matches whole words only', () => {
  // "UI" must not fire inside "GUI", and "env" not inside "environment".
  // Without boundaries the overrides corrupt more text than they fix.
  const gui = pronounce('The GUI appeared.').text;
  assert.ok(gui.includes('gooey'), 'GUI has its own rule');
  assert.ok(!gui.includes('U I appeared'), `UI fired inside GUI: ${gui}`);

  assert.equal(pronounce('environmental').text, 'environmental',
    '"env" must not fire inside a longer word');
});

test('is case-insensitive by default', () => {
  assert.equal(pronounce('gif').text, 'jiff');
  assert.equal(pronounce('Gif').text, 'jiff');
});

test('a user rule overrides a shipped one', () => {
  // The whole point: someone who disagrees with a shipped pronunciation, or
  // whose name we get wrong, can fix it and never hear it again.
  loadUser([{ match: 'GIF', say: 'gif' }]);
  assert.equal(pronounce('The GIF loaded.').text, 'The gif loaded.');
  loadUser([]);
  assert.equal(pronounce('The GIF loaded.').text, 'The jiff loaded.');
});

test('a longer rule is not shadowed by a shorter one inside it', () => {
  loadUser([
    { match: 'Acme', say: 'ack me' },
    { match: 'Acme Corporation', say: 'ack me corp' },
  ]);
  assert.equal(pronounce('Acme Corporation ships.').text, 'ack me corp ships.');
  loadUser([]);
});

test('a replacement is never re-matched by its own rule', () => {
  // "cat" -> "cat cat" must terminate rather than looping.
  loadUser([{ match: 'cat', say: 'cat cat' }]);
  assert.equal(pronounce('one cat').text, 'one cat cat');
  loadUser([]);
});

test('supports a regular expression when the user asks for one', () => {
  loadUser([{ match: '\\bDr\\.', say: 'Doctor', regex: true }]);
  assert.equal(pronounce('Dr. Chen').text, 'Doctor Chen');
  loadUser([]);
});

test('a malformed user pattern is dropped, not thrown', () => {
  // A bad pattern must not break every sentence from then on.
  loadUser([{ match: '([unclosed', say: 'x', regex: true }, { match: 'ok', say: 'fine' }]);
  assert.doesNotThrow(() => pronounce('this is ok'));
  assert.equal(pronounce('this is ok').text, 'this is fine', 'the good rule still applies');
  loadUser([]);
});

test('handles empty input and an empty rule set', () => {
  assert.deepEqual(pronounce(''), { text: '', changed: false });
  loadBuiltin({});
  loadUser([]);
  assert.deepEqual(pronounce('anything'), { text: 'anything', changed: false });
  loadBuiltin(data); // restore for anything after this
});

test('exposes both rule sets for the editor', () => {
  loadUser([{ match: 'x', say: 'y' }]);
  const r = rules();
  assert.ok(r.builtin.length >= 40);
  assert.equal(r.user.length, 1);
  loadUser([]);
});

test('every shipped rule is well formed', () => {
  // A rule with no `say` would silently delete the word it matches.
  for (const r of data.builtin) {
    assert.ok(r.match && typeof r.match === 'string', `bad match: ${JSON.stringify(r)}`);
    assert.ok(r.say && typeof r.say === 'string', `${r.match} has no replacement`);
    assert.notEqual(r.match, r.say, `${r.match} replaces itself`);
  }
});

test('no shipped rule rewrites another shipped rule output', () => {
  // The chaining hazard, checked across the whole list rather than assumed.
  // If one rule's replacement contains a word another rule matches, the second
  // fires on the first's output and the result is neither rule's intent.
  loadUser([]);
  const offenders = [];
  for (const rule of data.builtin) {
    const { text, changed } = pronounce(rule.say);
    if (changed) offenders.push(`${rule.match} -> "${rule.say}" -> "${text}"`);
  }
  assert.deepEqual(offenders, [],
    'a rule whose output another rule rewrites produces neither rule\'s intent');
});

test('applying the rules twice changes nothing the second time', () => {
  // Idempotence, stated directly because it is the property a reader cares
  // about — but it is the **weaker** of the two chaining checks, not the
  // stronger one, which is the opposite of what I first claimed.
  //
  // Rules apply sequentially within one pass, so a rule that rewrites an
  // earlier rule's output usually fires in that same pass and leaves the result
  // already stable. Measured across three planted chains: pairwise caught all
  // three, this caught one — only the arrangement where the rewriting rule runs
  // *before* the rule that feeds it, which given the length-descending order
  // means the rewriter's match is the longer string.
  //
  // The pairwise test above carries this. Kept anyway, honestly labelled.
  loadUser([]);
  // Built from the rule set rather than hand-written, so every rule has
  // something to fire on. A hand-written sentence can miss a planted chain for
  // the unrelated reason that it never mentions the affected word.
  const sample = data.builtin.map((r) => r.match).join(' ');
  const once = pronounce(sample).text;
  assert.equal(pronounce(once).text, once, 'the rule set must reach a fixed point in one pass');
});
