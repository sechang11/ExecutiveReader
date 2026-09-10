/**
 * Anchor resolution tests.
 *
 * The cases that matter are the ones where a plausible implementation is
 * silently wrong: repeated sentences, and a rules edit that rewrites the quote.
 * Both come from the desktop half, which hit them first.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { capture, locate, similarity, describe } from '../src/core/anchor.js';

const STAMP = 'sha256:aaaaaaaaaaaaaaaa';
const OTHER = 'sha256:bbbbbbbbbbbbbbbb';

test('resolves an unchanged document exactly and verified', () => {
  const s = ['One.', 'Two.', 'Three.', 'Four.'];
  const a = capture(s, 2, STAMP);
  assert.deepEqual(locate(a, s, STAMP),
    { index: 2, how: 'exact', verified: true, stampChanged: false });
});

test('an exact hit under changed rules resolves but is not verified', () => {
  const s = ['One.', 'Two.', 'Three.', 'Four.'];
  const a = capture(s, 2, STAMP);
  const got = locate(a, s, OTHER);
  assert.equal(got.index, 2, 'still finds the right sentence');
  assert.equal(got.how, 'exact');
  assert.equal(got.verified, false, 'but does not claim confidence');
});

test('repeated sentences resolve by context, not by first match', () => {
  // The case that makes fuzzy-first wrong. Saved on the SECOND copy.
  const s = ['Intro.', 'Same line.', 'Middle.', 'Same line.', 'Tail.'];
  const a = capture(s, 3, STAMP);
  assert.equal(locate(a, s, STAMP).index, 3);
});

test('survives a rules edit that rewrote the saved sentence', () => {
  // Exactly what adding "w/" to expansions did to stored positions.
  const saved = ['Intro.', 'Serve it w/ cream.', 'Tail.'];
  const a = capture(saved, 1, STAMP);
  const now = ['Intro.', 'Serve it with cream.', 'Tail.'];
  const got = locate(a, now, OTHER);
  assert.equal(got.index, 1);
  assert.equal(got.how, 'fuzzy');
  assert.equal(got.verified, false);
});

test('a rewritten sentence appearing twice resolves to the right copy', () => {
  const saved = ['A.', 'Serve it w/ cream.', 'B.', 'Serve it w/ cream.', 'C.'];
  const a = capture(saved, 3, STAMP);
  const now = ['A.', 'Serve it with cream.', 'B.', 'Serve it with cream.', 'C.'];
  assert.equal(locate(a, now, OTHER).index, 3, 'context must break the tie');
});

test('finds the sentence after content is inserted above it', () => {
  const before = ['One.', 'Two.', 'Three.'];
  const a = capture(before, 2, STAMP);
  const after = ['Ad.', 'Promo.', 'One.', 'Two.', 'Three.'];
  assert.equal(locate(a, after, STAMP).index, 4);
});

test('falls back to the recorded index when the article was replaced', () => {
  const a = capture(['One.', 'Two.', 'Three.'], 2, STAMP);
  const now = ['Nothing.', 'In.', 'Common.', 'At.', 'All.'];
  const got = locate(a, now, STAMP);
  assert.equal(got.how, 'hint');
  assert.equal(got.index, 2);
});

test('clamps the fallback to a shorter document', () => {
  const a = capture(['a'.repeat(40), 'b'.repeat(40), 'c'.repeat(40)], 2, STAMP);
  const got = locate(a, ['zzz.', 'yyy.'], STAMP);
  assert.equal(got.how, 'hint');
  assert.ok(got.index <= 1, `index ${got.index} must be in range`);
});

test('handles empty and missing input without throwing', () => {
  assert.equal(capture([], 0, STAMP), null);
  assert.equal(capture(['a'], 5, STAMP), null);
  const blank = { index: 0, how: 'hint', verified: false, stampChanged: false };
  assert.deepEqual(locate(null, ['a'], STAMP), blank);
  assert.deepEqual(locate(capture(['a'], 0, STAMP), [], STAMP), blank);
});

test('similarity is bounded and ordered sensibly', () => {
  assert.equal(similarity('same', 'same'), 1);
  assert.equal(similarity('abc', ''), 0);
  const near = similarity('Serve it w/ cream.', 'Serve it with cream.');
  const far = similarity('Serve it w/ cream.', 'Entirely unrelated text.');
  assert.ok(near > far, 'a rewrite must score above an unrelated sentence');
  assert.ok(near > 0.5 && near <= 1, `near-match scored ${near}`);
});

test('an exact match stays silent even when the stamp changed', () => {
  // The bug this replaces: describe() branched on `verified`, so an exact
  // verbatim hit under a changed fingerprint announced that the position was
  // approximate — doubt about a position that was in fact correct. If the
  // sentence is present word for word, the position is right whatever the
  // stamp says. The old test asserted the wrong behaviour, which is worse than
  // not testing it.
  assert.equal(describe({ index: 1, how: 'exact', verified: true, stampChanged: false }), null);
  assert.equal(describe({ index: 1, how: 'exact', verified: false, stampChanged: true }), null);
});

test('every inexact path explains itself, distinctly', () => {
  const seen = new Set();
  for (const how of ['ambiguous', 'boundary', 'fuzzy', 'hint']) {
    const msg = describe({ index: 1, how, verified: false, stampChanged: false });
    assert.ok(msg && msg.length > 10, `${how} needs an explanation`);
    assert.ok(!seen.has(msg), `${how} must not reuse another path's wording`);
    seen.add(msg);
  }
});

test('ambiguous and boundary describe different situations', () => {
  // These were one label until the two halves discovered they meant different
  // things by it. Collapsing them again would resurrect that bug.
  const ambiguous = describe({ index: 1, how: 'ambiguous', verified: false, stampChanged: false });
  const boundary = describe({ index: 1, how: 'boundary', verified: false, stampChanged: false });
  assert.match(ambiguous, /more than once/i, 'names the duplication');
  assert.match(boundary, /longer or shorter/i, 'names the edge change, not duplication');
  assert.doesNotMatch(boundary, /more than once/i);
});

test('a split sentence resolves as boundary, not fuzzy', () => {
  const saved = ['Intro.', 'The cat sat down and then it slept.', 'Tail.'];
  const a = capture(saved, 1, STAMP);
  const now = ['Intro.', 'The cat sat down.', 'And then it slept.', 'Tail.'];
  const got = locate(a, now, STAMP);
  assert.equal(got.how, 'boundary');
  assert.equal(got.index, 1, 'lands on the first piece');
});

test('a merged sentence resolves as boundary', () => {
  const saved = ['Intro.', 'The cat sat down.', 'And then it slept.', 'Tail.'];
  const a = capture(saved, 1, STAMP);
  const now = ['Intro.', 'The cat sat down. And then it slept.', 'Tail.'];
  const got = locate(a, now, STAMP);
  assert.equal(got.how, 'boundary');
  assert.equal(got.index, 1);
});

test('an extended sentence resolves as boundary', () => {
  const saved = ['Intro.', 'The quick brown fox jumped over it.', 'Tail.'];
  const a = capture(saved, 1, STAMP);
  const now = ['Intro.', 'The quick brown fox jumped over it, twice.', 'Tail.'];
  assert.equal(locate(a, now, STAMP).how, 'boundary');
});

test('a short sentence cannot trigger a spurious boundary match', () => {
  // Without a minimum containment length, "Yes." is inside half the document
  // and every resume would report a confident boundary result.
  const a = capture(['A.', 'Yes.', 'B.'], 1, STAMP);
  const now = ['Yes, I agree completely.', 'Something else entirely here.'];
  assert.notEqual(locate(a, now, STAMP).how, 'boundary');
});

test('containment must land on whole words', () => {
  // "the cat sat down" is a raw substring of "the cat sat downstream ...", but
  // only because a word was extended. That is a different sentence, not a moved
  // boundary. Without padding the comparison, this reports a confident boundary
  // match for an unrelated line.
  const a = capture(['Intro.', 'The cat sat down.', 'Tail.'], 1, STAMP);
  const now = ['Intro.', 'The cat sat downstream today by the water.', 'Tail.'];
  assert.notEqual(locate(a, now, STAMP).how, 'boundary');
});

test('exact still beats boundary when both could match', () => {
  // The quote is present whole AND contained in a longer sentence elsewhere.
  const quote = 'The cat sat down on the mat.';
  const a = capture(['Intro.', quote, 'Tail.'], 1, STAMP);
  const now = ['Intro.', quote, `Later, ${quote} again.`, 'Tail.'];
  const got = locate(a, now, STAMP);
  assert.equal(got.how, 'exact', 'the more certain path must win');
  assert.equal(got.index, 1);
});

test('fuzzy distinguishes a changed page from changed rules', () => {
  const page = describe({ index: 1, how: 'fuzzy', verified: false, stampChanged: false });
  const rules = describe({ index: 1, how: 'fuzzy', verified: false, stampChanged: true });
  assert.notEqual(page, rules, 'the two causes read differently to the user');
  assert.match(rules, /reading rules/i);
  assert.match(page, /page changed/i);
});

test('an absent stamp is unknown provenance, not changed', () => {
  // Positions saved before stamping existed must not be reported as rewritten.
  const s = ['One.', 'Two.', 'Three.'];
  const a = capture(s, 1, null);
  const got = locate(a, s, STAMP);
  assert.equal(got.stampChanged, false);
  assert.equal(got.verified, false, 'unknown provenance cannot be verified');
});

test('an anchor records neighbours on both sides', () => {
  // CONTEXT is a judgment: too few and repeated sentences cannot be told apart,
  // too many and an anchor near an edge carries mostly nothing. Unpinned, the
  // number could drift to 0 and every duplicate would resolve arbitrarily.
  const s = ['a.', 'b.', 'c.', 'd.', 'e.', 'f.', 'g.'];
  const a = capture(s, 3, STAMP);
  assert.equal(a.before.length, 2, 'two neighbours before');
  assert.equal(a.after.length, 2, 'two after');
  assert.deepEqual(a.before, ['b.', 'c.']);
  assert.deepEqual(a.after, ['e.', 'f.']);
});

test('an anchor at the start of a document has no neighbours before it', () => {
  const a = capture(['first.', 'second.', 'third.'], 0, STAMP);
  assert.deepEqual(a.before, []);
  assert.equal(a.after.length, 2);
});

/**
 * The target and two candidates chosen by measurement, not by guess.
 *
 * TIE_MARGIN is the band within which candidates are treated as too close to
 * separate on score, handing the decision to their neighbours. Pinning it needs
 * two candidates whose scores differ by *more* than the band and *less* than a
 * mutated one — the only window where the two values disagree. Everything
 * outside that window resolves identically either way, which is why an earlier
 * test written to pin this did nothing.
 *
 * Measured with the real similarity function: 0.980 against 0.870, a gap of
 * 0.110. Confirmed to resolve differently at 0.05 and at 0.12 before the
 * assertion below was written.
 */
const TIE_TARGET = 'The quick brown fox jumped over the lazy dog today.';
const TIE_SAVED = [
  'Opening remarks about nothing.',
  'A note on marmalade.',
  TIE_TARGET,
  'A note on chutney.',
  'Closing remarks about nothing.',
];
/** The stronger match sits among strangers; the weaker sits among the saved
 *  neighbours. Score and context disagree, which is the whole point. */
const TIE_NOW = [
  'Wholly unrelated line one.',
  'The quick brown fox jumped over the lazy dog today!',
  'Wholly unrelated line two.',
  'A note on marmalade.',
  'A quick brown fox jumped over the lazy dog.',
  'A note on chutney.',
];

test('the fixture that pins the tie band really does separate the candidates', () => {
  // Guards the guard. If these two ever score within the band, or outside the
  // mutated one, the test below silently stops pinning anything.
  const strong = similarity(TIE_TARGET, TIE_NOW[1]);
  const weak = similarity(TIE_TARGET, TIE_NOW[4]);
  const gap = strong - weak;
  assert.ok(gap > 0.06, `gap ${gap.toFixed(3)} is inside the band; nothing is being distinguished`);
  assert.ok(gap < 0.18, `gap ${gap.toFixed(3)} is beyond any plausible band`);
});

test('a clearly better match wins on score rather than deferring to context', () => {
  // Widen the band and a distinctly better match gets dragged into a tie and
  // decided by neighbours instead, which is how a resume lands on the wrong
  // paragraph while every other test still passes.
  const a = capture(TIE_SAVED, 2, STAMP);
  assert.equal(locate(a, TIE_NOW, STAMP).index, 1,
    'the far better match must win outright');
});

test('candidates too close to separate defer to their neighbours', () => {
  // The lower bound of the same constant, and the harder half to pin. Exactly
  // equal scores tie at *any* band width, including zero, so a fixture built
  // from identical sentences pins nothing. It needs two candidates scoring
  // near but not equal: 0.940 against 0.920, inside the band and outside zero.
  //
  // Both differ from the target by a word substitution rather than a trim, so
  // neither is contained in it. A contained candidate resolves as `boundary`,
  // which runs before fuzzy and never reaches the band at all — the first
  // attempt at this test failed for exactly that reason.
  const target = 'The quick brown fox jumped over the lazy dog today.';
  const saved = ['Alpha note.', 'A note on marmalade.', target, 'A note on chutney.', 'Omega note.'];
  const now = [
    'Unrelated one.',
    'The quick brown fox jumped over the lazy pig today.',
    'Unrelated two.',
    'A note on marmalade.',
    'The quick brown fox jumped over the lazy cat today.',
    'A note on chutney.',
  ];

  const gap = similarity(target, now[1]) - similarity(target, now[4]);
  assert.ok(gap > 0 && gap < 0.05, `gap ${gap.toFixed(4)} must be inside the band but not zero`);

  const got = locate(capture(saved, 2, STAMP), now, STAMP);
  assert.equal(got.how, 'fuzzy', 'containment would route this past the band entirely');
  assert.equal(got.index, 4, 'too close to call on score, so the neighbours decide');
});

test('genuinely equal candidates still defer to their neighbours', () => {
  const s = ['Intro.', 'Same line.', 'Middle.', 'Same line.', 'Tail.'];
  assert.equal(locate(capture(s, 3, STAMP), s, STAMP).index, 3);
});

/**
 * Whitespace the normalization contract already forbids.
 *
 * `_output_contract` in shared/normalization.json requires both halves to
 * collapse runs of spaces and tabs and trim the ends. Two strings differing only
 * in that respect cannot both be legal output of the pipeline, so treating them
 * as different sentences was not strictness — it reported a violated invariant
 * as evidence that the page had changed, and told the reader their position was
 * only "probably" right when it was certainly right.
 *
 * Found by the desktop half's anchor conformance harness, which resolves the
 * same bookmark through both implementations. No test on either side covered it.
 */
test('a collapsed run of spaces is still an exact match', () => {
  const saved = ['Intro.', 'The  cat  sat  down  quietly.', 'Tail.'];
  const now = ['Intro.', 'The cat sat down quietly.', 'Tail.'];

  const got = locate(capture(saved, 1, STAMP), now, STAMP);

  assert.equal(got.how, 'exact');
  assert.equal(got.index, 1);
  assert.equal(got.verified, true, 'an exact match with a matching stamp is verified');
});

test('a tab where a space was is still an exact match', () => {
  const saved = ['Intro.', 'The cat\tsat down quietly.', 'Tail.'];
  const now = ['Intro.', 'The cat sat down quietly.', 'Tail.'];
  assert.equal(locate(capture(saved, 1, STAMP), now, STAMP).how, 'exact');
});

test('leading and trailing whitespace is still an exact match', () => {
  const saved = ['Intro.', '   The cat sat down quietly.  ', 'Tail.'];
  const now = ['Intro.', 'The cat sat down quietly.', 'Tail.'];
  assert.equal(locate(capture(saved, 1, STAMP), now, STAMP).how, 'exact');
});

/**
 * The line the contract draws, and this file will not cross alone.
 *
 * `docs/anchor-vocabulary.md` defines `exact` as character for character, and
 * the normalization contract says in as many words that newlines are left
 * alone. A recapitalised sentence or a restyled apostrophe genuinely is not the
 * same characters, so it belongs to `fuzzy`. The desktop half currently reports
 * these as `exact`; that disagreement is recorded in tools/conformance_anchor.mjs
 * rather than settled by whichever side edited first.
 */
test('a recapitalised sentence is not an exact match', () => {
  const saved = ['Intro.', 'The cat sat down quietly.', 'Tail.'];
  const now = ['Intro.', 'THE CAT SAT DOWN QUIETLY.', 'Tail.'];

  const got = locate(capture(saved, 1, STAMP), now, STAMP);

  assert.notEqual(got.how, 'exact', 'case folding is not in the vocabulary');
  assert.equal(got.index, 1, 'and it still finds the right sentence');
});

test('a newline where a space was is not an exact match', () => {
  // The contract preserves newlines deliberately: on the PDF path a line break
  // carries layout meaning that a space does not.
  const saved = ['Intro.', 'The cat\nsat down quietly.', 'Tail.'];
  const now = ['Intro.', 'The cat sat down quietly.', 'Tail.'];

  const got = locate(capture(saved, 1, STAMP), now, STAMP);

  assert.notEqual(got.how, 'exact');
  assert.equal(got.index, 1);
});
