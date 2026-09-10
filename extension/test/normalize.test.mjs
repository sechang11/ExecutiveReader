/**
 * Normalization tests, mirroring the cases the Python side pins down.
 *
 * The survival cases matter more than the conversion cases. A reader that says
 * "C plus plus" or "me at example dot com" is worse than one that leaves the
 * symbol alone, because the failure is loud and constant rather than rare.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  loadNormalization, normalize, applyExpansions, applyCurrency, applySymbols, tidy, applyCollapse,
} from '../src/core/normalize.js';

const data = JSON.parse(
  readFileSync(new URL('../src/shared/normalization.json', import.meta.url), 'utf8'),
);
loadNormalization(data);

test('leaves code and addresses alone', () => {
  for (const s of [
    'R&D', 'C++', 'x && y', 'key=value', 'a==b', 'C#', '#hashtag',
    'me@example.com', '@someone', '~/home/user', '%s and %d', 'x += 1',
  ]) {
    assert.equal(applySymbols(s), s, `should be untouched: ${s}`);
  }
});

test('expands isolated symbols', () => {
  assert.equal(applySymbols('Ben & Jerry'), 'Ben and Jerry');
  assert.equal(applySymbols('a = b'), 'a equals b');
});

test('expands symbols only next to digits', () => {
  assert.equal(applySymbols('20%'), '20 percent');
  assert.equal(applySymbols('#42'), 'number 42');
  assert.equal(applySymbols('~5'), 'about 5');
  assert.equal(applySymbols('20°'), '20 degrees');
});

test('always-symbols expand anywhere', () => {
  assert.equal(applySymbols('©2026'), 'copyright 2026');
});

test('currency moves the unit after the amount', () => {
  assert.equal(applyCurrency('$50'), '50 dollars');
  assert.equal(applyCurrency('$1'), '1 dollar');
  assert.equal(applyCurrency('£20'), '20 pounds');
  assert.equal(applyCurrency('€1,200'), '1,200 euros');
});

test('currency leaves a bare symbol alone', () => {
  assert.equal(applyCurrency('the $ sign'), 'the $ sign');
});

test('currency does not swallow the sentence full stop', () => {
  assert.equal(applyCurrency('It cost $50.'), 'It cost 50 dollars.');
});

test('expands say-as abbreviations', () => {
  assert.equal(applyExpansions('i.e. this'), 'that is this');
  assert.equal(applyExpansions('cats, dogs, etc.'), 'cats, dogs, et cetera');
  assert.equal(applyExpansions('Smith et al. found'), 'Smith and others found');
});

test('expansion matching is case-insensitive by default', () => {
  assert.equal(applyExpansions('n.b. the date'), 'note well the date');
  assert.equal(applyExpansions('N.B. the date'), 'note well the date');
});

test('longest match wins', () => {
  assert.equal(applyExpansions('w/o sugar'), 'without sugar');
  assert.equal(applyExpansions('w/ sugar'), 'with sugar');
});

test('boundary rule protects embedded matches', () => {
  // '->' must not fire inside C pointer syntax, but must fire in prose.
  assert.equal(applyExpansions('node->next'), 'node->next');
  assert.equal(applyExpansions('A -> B'), 'A to B');
  // A letter immediately before or after rejects the match.
  assert.equal(applyExpansions('xw/y'), 'xw/y');
});

test('comparison operators expand in prose', () => {
  assert.equal(applyExpansions('x >= 5'), 'x greater than or equal to 5');
  assert.equal(applyExpansions('n <= 10'), 'n less than or equal to 10');
});

test('unknown conditions are ignored rather than guessed', () => {
  loadNormalization({ symbols: [{ symbol: '!', say: 'bang', when: 'from-the-future' }] });
  assert.equal(applySymbols('hi!'), 'hi!');
  loadNormalization(data); // restore for any later test
});

test('tidy collapses spaces and tabs but never newlines', () => {
  // The output contract both halves implement. It was only ever exercised
  // through applySymbols, so nothing checked its own terms — and the newline
  // clause is the one that would break silently, since a paragraph break
  // collapsing into a space changes where sentences end.
  assert.equal(tidy('a   b'), 'a b');
  assert.equal(tidy('a\t\tb'), 'a b');
  assert.equal(tidy('  a b  '), 'a b');
  assert.equal(tidy('a\n\nb'), 'a\n\nb', 'newlines must survive');
  // Spaces beside a newline are runs of one and stay. The contract collapses
  // runs and trims the ends of the string; it does not tidy around newlines,
  // and both halves implement exactly that. Asserting otherwise would pin a
  // behaviour neither side has and break parity to satisfy the test.
  assert.equal(tidy('a \n b'), 'a \n b');
  assert.equal(tidy('a  \n  b'), 'a \n b', 'but runs beside a newline do collapse');
  assert.equal(tidy(''), '');
});

test('full pipeline runs in schema order', () => {
  assert.equal(normalize('It cost $50, i.e. 20% over budget.'),
    'It cost 50 dollars, that is 20 percent over budget.');
});

/**
 * Typographic collapse.
 *
 * `collapse` sat in shared/normalization.json, every flag set, read by neither
 * half. The cost was not cosmetic: the phonemizer looks words up in CMUdict by
 * literal text, so a curly apostrophe misses the entry and the fallback letter
 * rules take over. On the shipped dictionary "don’t" phonemised as "dawn tee".
 * Nearly every professionally typeset page uses curly apostrophes.
 *
 * The pronunciation numbers are asserted in g2p.test.mjs, where the dictionary
 * lives. These pin the text transformation.
 */
test('a curly apostrophe becomes the one the dictionary has', () => {
  assert.equal(normalize('don’t stop'), "don't stop");
  assert.equal(normalize('it’s here'), "it's here");
});

test('curly double quotes become straight ones', () => {
  assert.equal(normalize('“fine”'), '"fine"');
});

test('guillemets are left alone rather than guessed at', () => {
  // Not English quoting. Mapping them would be a guess, and the rules file
  // says so, so this pins the absence of a rule as much as its presence.
  assert.equal(normalize('«bonjour»'), '«bonjour»');
});

test('a soft hyphen inside a word disappears', () => {
  assert.equal(normalize('won­derful'), 'wonderful');
});

test('zero-width characters disappear', () => {
  assert.equal(normalize('won​der‍ful'), 'wonderful');
});

test('a non-breaking space becomes an ordinary one', () => {
  // The output contract collapses spaces and tabs. A non-breaking space is
  // neither, so nothing downstream reaches it.
  assert.equal(normalize('the cat'), 'the cat');
  assert.equal(normalize('the cat'), 'the cat');
});

test('an en dash becomes a hyphen', () => {
  assert.equal(normalize('1914–1918'), '1914-1918');
});

test('an em dash becomes a comma, attached to the word before it', () => {
  // Replacing the character alone leaves "left , quickly", with the comma
  // floating. The engines have no phoneme for a dash and drop it silently, so
  // without this the pause the author wrote simply disappears.
  assert.equal(normalize('He left — quickly.'), 'He left, quickly.');
  assert.equal(normalize('a—b'), 'a, b');
});

test('a run of terminators becomes one', () => {
  assert.equal(normalize('Wait... what?'), 'Wait. what?');
  assert.equal(normalize('Stop!!!'), 'Stop!');
});

test('a table-of-contents leader is removed, not turned into a full stop', () => {
  // dot_leaders has to run before repeated_punctuation, or a leader collapses
  // to a single period and reads as the end of a sentence.
  assert.equal(normalize('Chapter 1......5'), 'Chapter 1 5');
  assert.equal(normalize('Intro . . . . 7'), 'Intro 7');
});

test('superscript footnote markers go, bracketed ones stay', () => {
  // A bracketed marker is indistinguishable from a citation the reader wants.
  assert.equal(normalize('claimed¹ that'), 'claimed that');
  assert.equal(normalize('claimed[1] that'), 'claimed[1] that');
});

test('emoji are dropped when the rules say skip', () => {
  assert.equal(normalize('nice \u{1f389} day'), 'nice day');
});

test('collapse runs before expansions, because expansions match literal text', () => {
  // "w/o" written with a curly apostrophe nearby must still expand; more
  // importantly, the order is what the contract states, so pin it.
  assert.equal(normalize('“w/o” it'), '"without" it');
});

test('the collapse stage is ordered, and the order is part of the contract', () => {
  // Exercised directly rather than only through normalize(), because the two
  // rules that interact are inside this stage. dot_leaders must run first: a
  // leader run reaching repeated_punctuation collapses to a single period and
  // then reads as the end of a sentence.
  assert.equal(applyCollapse('Intro......7'), 'Intro 7');
  assert.equal(applyCollapse('Wait...'), 'Wait.');

  // And it is the stage, not the pipeline: nothing here expands or pads.
  assert.equal(applyCollapse('w/o'), 'w/o');
  assert.equal(applyCollapse('R&D'), 'R&D');
});
