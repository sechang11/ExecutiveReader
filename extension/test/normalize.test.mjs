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

test('exactly three periods is an ellipsis, and two is a typo', () => {
  // Measured on five sentences on Microsoft David, the default Windows voice,
  // timing the authored form against a collapse to a period: +0.485, +0.445,
  // +0.495, +0.480, +0.490. Collapsing costs about half a second of extra
  // pause every time, turning an authored trailing-off into a firmer stop.
  //
  // Note what the gain is not: that voice cannot tell "..." from U+2026 at
  // all. The gain is from no longer collapsing to a period. U+2026 is the
  // target because Kokoro has a real token for it and the system voices are
  // indifferent.
  assert.equal(normalize('Wait... what?'), 'Wait… what?');

  // Two is a typo. Turning it into an ellipsis would be worse than leaving it.
  assert.equal(normalize('Wait.. what?'), 'Wait. what?');
});

test('bang and question runs still collapse to one', () => {
  assert.equal(normalize('Stop!!!'), 'Stop!');
  assert.equal(normalize('Really??'), 'Really?');
});

test('four or more periods never reach the ellipsis rule', () => {
  // dot_leaders runs first and turns them into a space. If the order were
  // reversed a table-of-contents leader would become an ellipsis.
  assert.equal(normalize('Chapter 1......5'), 'Chapter 1 5');
});

test('a table-of-contents leader is removed, not turned into punctuation', () => {
  // dot_leaders has to run before repeated_punctuation, or a leader becomes an
  // ellipsis and reads as a trailing-off in the middle of a contents page.
  assert.equal(normalize('Chapter 1......5'), 'Chapter 1 5');
  assert.equal(normalize('Intro . . . . 7'), 'Intro 7');
});

test('footnote markers go, superscript and bracketed alike', () => {
  // Both halves argued the bracketed case, in opposite directions, and it was
  // settled by measurement rather than taste. On Microsoft David, the default
  // Windows voice, "As shown by Smith [1] the result holds." runs 3.129s
  // against 2.924s without it — an audible interruption mid-sentence. On
  // Kokoro it is invisible: identical token count and identical duration,
  // because the model vocabulary has no token for a bracket or a digit.
  // Removing it is better on one engine and free on the other.
  assert.equal(normalize('claimed¹ that'), 'claimed that');
  assert.equal(normalize('claimed [1] that'), 'claimed that');
  assert.equal(normalize('see [ 12 ] below'), 'see below');
});

test('a bracketed number that is not a marker survives', () => {
  // Capped at three digits so an array index is less likely to be caught. This
  // is a limit, not a solution: "a[12]" still goes.
  assert.equal(normalize('the array a[1234] holds it'), 'the array a[1234] holds it');
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
  assert.equal(applyCollapse('Wait...'), 'Wait…');

  // And it is the stage, not the pipeline: nothing here expands or pads.
  assert.equal(applyCollapse('w/o'), 'w/o');
  assert.equal(applyCollapse('R&D'), 'R&D');
});

test('a symbol the rules speak is not an emoji, whatever Unicode says', () => {
  // ©, ® and ™ are all Extended_Pictographic, so a plain pictographic test
  // deletes them — and with them the words the symbols list exists to produce.
  // This passed every test on this side and was caught by the cross-language
  // harness the first time the collapse stage was actually compared.
  assert.equal(normalize('©2026 Acme'), 'copyright 2026 Acme');
  assert.equal(normalize('Widget™ and Thing®'), 'Widget trademark and Thing registered');

  // The stage itself leaves them for the symbols stage rather than speaking them.
  assert.equal(applyCollapse('©2026'), '©2026');

  // And a real emoji still goes.
  assert.equal(applyCollapse('nice \u{1f389}'), 'nice ');
});

test('an ellipsis is left alone, on the numbers rather than the vocabulary', () => {
  // First argued from the vocabulary: U+2026 has a token, so the model can
  // voice it. That was weak evidence and the measurements narrow it.
  //
  //   Kokoro, tools/voicelab/:  "Wait." 1.800s   "Wait…" 1.850s   "Wait..." 1.800s
  //   Microsoft David:          "Wait." 2.749s   "Wait…" 2.264s   "Wait..." 2.264s
  //
  // So collapsing an ellipsis to a period is worth 0.05s on Kokoro — nothing —
  // and on the default Windows voice it makes the pause 0.485s LONGER, turning
  // a trailing-off into a firmer stop than the author wrote. Leaving it alone
  // is right, but not for the reason first given.
  assert.equal(normalize('Wait… what?'), 'Wait… what?');
});
