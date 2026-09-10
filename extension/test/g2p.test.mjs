/**
 * English grapheme-to-phoneme.
 *
 * Two things are worth pinning here. That output stays inside the model's
 * alphabet, because a symbol outside it is dropped silently and the word just
 * loses a sound. And the homograph rules, because that is the failure a
 * dictionary cannot fix with more data and the reason these rules exist at all.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import * as G from '../src/engines/kokoro/g2p-en.js';

const vocab = JSON.parse(
  readFileSync(new URL('../vendor/kokoro/vocab.json', import.meta.url), 'utf8'),
);
const entries = G.loadDict(
  gunzipSync(readFileSync(new URL('../vendor/cmudict/cmudict.txt.gz', import.meta.url)))
    .toString('utf8'),
);
G.loadHomographs(JSON.parse(
  readFileSync(new URL('../vendor/cmudict/homographs.json', import.meta.url), 'utf8'),
));

test('the vendored dictionary loaded', () => {
  assert.ok(entries > 120_000, `only ${entries} entries`);
});

test('every symbol it can emit is in the model alphabet', () => {
  assert.deepEqual(G.verify(vocab), [],
    'a symbol outside the alphabet is dropped silently, not rejected');
});

test('phonemizes ordinary prose with nothing missing', () => {
  const { ipa, misses } = G.phonemize('The quick brown fox jumps over the lazy dog.');
  assert.deepEqual(misses, []);
  assert.match(ipa, /^ðə kwˈɪk bɹˈaʊn/);
  // eSpeak's convention: the mark sits on the vowel, not the syllable onset.
  assert.ok(!ipa.includes('ˈkwɪk'), 'stress must mark the vowel, as the model was trained');
});

test('output never leaves the alphabet, even for invented words', () => {
  const { ipa } = G.phonemize('Zorblax quixotry Nnnghh Xiaomi');
  for (const ch of ipa) {
    if (/\s/.test(ch)) continue;
    assert.ok(ch in vocab, `emitted ${JSON.stringify(ch)}, which is not in the alphabet`);
  }
});

test('unknown words are reported, not silently guessed at', () => {
  const { misses } = G.phonemize('Anthropic released Kubernetes today.');
  assert.ok(misses.includes('Anthropic'));
  assert.ok(misses.includes('Kubernetes'));
  assert.ok(!misses.includes('today'));
});

test('read: past tense after a perfect auxiliary', () => {
  // The case that motivated the homograph table. A dictionary alone says
  // "reed" here, confidently and wrongly, and no extra entries fix it.
  const past = G.phonemize('He has read the book.').ipa;
  const present = G.phonemize('Please read the book.').ipa;
  assert.match(past, /ɹˈɛd/, 'after "has" it is the past tense');
  assert.match(present, /ɹˈiːd/, 'after "please" it is the present');
  assert.notEqual(past, present);
});

test('lead: metal after a determiner, verb after a pronoun', () => {
  assert.match(G.phonemize('the lead pipe').ipa, /lˈɛd/);
  assert.match(G.phonemize('they lead us').ipa, /lˈiːd/);
});

test('live: adjective after a determiner, verb otherwise', () => {
  assert.match(G.phonemize('a live show').ipa, /lˈaɪv/);
  assert.match(G.phonemize('they live here').ipa, /lˈɪv/);
});

test('noun and verb readings differ in vowel, not just stress', () => {
  // "REC-ord" against "ri-CORD". The vowels genuinely differ, because English
  // reduces unstressed ones: deriving one from the other by moving the stress
  // mark produced "ruh-CORD", which is why both readings come from the
  // dictionary rather than from a transformation.
  const noun = G.disambiguate('record', 'the');
  const verb = G.disambiguate('record', 'to');
  assert.ok(noun && verb, 'both readings must exist');
  assert.notDeepEqual(noun, verb);
  assert.equal(noun[1], 'EH1', 'the noun stresses the first vowel, unreduced');
  assert.equal(verb[1], 'IH0', 'the verb reduces the first vowel');

  const nounIpa = G.phonemize('the record shows').ipa;
  assert.ok(nounIpa.includes('ɹˈɛk'), `noun reading was ${nounIpa}`);
  assert.ok(G.phonemize('to record it').ipa.includes('kˈɔːɹd'));
});

test('the homograph table holds real pairs, not derived ones', () => {
  for (const word of ['record', 'present', 'object', 'contract']) {
    const noun = G.disambiguate(word, 'the');
    const verb = G.disambiguate(word, 'to');
    assert.ok(noun && verb, `${word} needs both readings`);
    assert.notDeepEqual(noun, verb, `${word} readings must actually differ`);
  }
});

test('an ambiguous context leaves the dictionary entry alone', () => {
  // With no cue either way, guessing is worse than the dictionary's own answer.
  assert.equal(G.disambiguate('record', null), null);
  assert.equal(G.disambiguate('record', 'quickly'), null);
});

test('a word outside the homograph table is never rewritten', () => {
  assert.equal(G.disambiguate('table', 'the'), null);
});

test('accented and stroked letters stay part of their word', () => {
  // Found by measuring miss rate on real articles: "Skłodowska" was arriving
  // as "Sk" and "odowska", two fragments each pronounced confidently. Splitting
  // a name is worse than mispronouncing it.
  assert.equal(G.fold('Skłodowska'), 'sklodowska');
  assert.equal(G.fold('café'), 'cafe');
  assert.equal(G.fold('naïve'), 'naive');
  assert.equal(G.fold('Ørsted'), 'orsted');

  const { misses } = G.phonemize('Marie Skłodowska visited a café.');
  assert.ok(!misses.includes('Sk'), 'must not split the name into fragments');
  assert.ok(misses.every((m) => m.length > 2), `fragments in ${JSON.stringify(misses)}`);
  // "café" folds to "cafe", which the dictionary does have.
  assert.ok(!misses.some((m) => m.toLowerCase().startsWith('caf')));
});

test('a dictionary word is found regardless of its accents', () => {
  assert.deepEqual(G.phonemize('resume').ipa, G.phonemize('résumé').ipa);
});

test('letter-to-sound handles the empty and trivial cases', () => {
  assert.equal(G.sound(''), '');
  assert.ok(G.sound('cat').startsWith('ˈ'));
  assert.equal(G.phonemize('').ipa, '');
  assert.equal(G.phonemize('...').ipa, '...');
});

test('punctuation survives and is not spaced away from its word', () => {
  const { ipa } = G.phonemize('Yes, really.');
  assert.ok(ipa.includes(','), 'the model alphabet includes punctuation');
  assert.ok(!/\s,/.test(ipa), 'a comma must not float away from its word');
});
