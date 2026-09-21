/**
 * Do both halves resolve the same bookmark to the same place, and call it the
 * same thing?
 *
 * The text and phoneme harnesses already guard the two pipelines that must not
 * drift. Anchors are the third, and had no guard: docs/anchor-vocabulary.md
 * calls the five labels a contract neither half may change alone, and nothing
 * checked it. The gap was not theoretical. The first run of this file found
 * eight disagreements.
 *
 * The two implementations store an anchor differently on purpose — this side
 * keeps whole neighbouring sentences, the desktop keeps a character window —
 * so the harness compares the answer rather than the representation. Each side
 * builds its own anchor from the same document, the document is then edited,
 * and both are asked where the bookmark went.
 *
 * Run: node tools/conformance_anchor.mjs
 */
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const { capture, locate } = await import(
  pathToFileURL(join(root, 'extension', 'src', 'core', 'anchor.js')).href);
const { normalize, loadNormalization } = await import(
  pathToFileURL(join(root, 'extension', 'src', 'core', 'normalize.js')).href);

loadNormalization(JSON.parse(
  readFileSync(join(root, 'shared', 'normalization.json'), 'utf8')));

/**
 * Model what an anchor is actually built from.
 *
 * A segment reaching the anchor layer is always normaliser output, never raw
 * page text. Anchoring raw text compares the two halves at a point production
 * never reaches, and three of the disagreements this harness first reported
 * were exactly that: a newline, a non-breaking space and a curly apostrophe
 * that the normaliser had already resolved on at least one side.
 */
const asSegments = (lines) => lines.map(normalize);

const BASE = [
  'Introduction to the topic.',
  'The first point is about clarity.',
  'The second point is about contrast.',
  'The third point is about spacing.',
  'A closing thought to finish on.',
];
const swap = (arr, i, v) => arr.map((s, j) => (j === i ? v : s));

/**
 * `before` is the document when the bookmark was taken, `index` the sentence,
 * `after` the document when it is resolved again.
 */
const CASES = [
  { name: 'unchanged', before: BASE, index: 2, after: BASE },
  { name: 'first sentence', before: BASE, index: 0, after: BASE },
  { name: 'last sentence', before: BASE, index: 4, after: BASE },
  { name: 'paragraph inserted above', before: BASE, index: 2,
    after: ['New opener.', 'Another new line.', ...BASE] },
  { name: 'paragraph removed above', before: BASE, index: 3, after: BASE.slice(1) },
  { name: 'repeated sentence, context decides',
    before: ['Intro.', 'Same line.', 'Middle.', 'Same line.', 'Tail.'], index: 3,
    after: ['Intro.', 'Same line.', 'Middle.', 'Same line.', 'Tail.'] },
  { name: 'sentence split in two',
    before: ['Alpha.', 'The cat sat down and then it slept.', 'Beta.'], index: 1,
    after: ['Alpha.', 'The cat sat down.', 'And then it slept.', 'Beta.'] },
  { name: 'two sentences merged',
    before: ['Alpha.', 'The cat sat down.', 'And then it slept.', 'Beta.'], index: 1,
    after: ['Alpha.', 'The cat sat down and then it slept.', 'Beta.'] },
  { name: 'sentence lightly reworded',
    before: ['Alpha.', 'The second point is about contrast.', 'Beta.'], index: 1,
    after: ['Alpha.', 'The second point concerns contrast.', 'Beta.'] },
  { name: 'sentence gone entirely', before: BASE, index: 2,
    after: ['Wholly different.', 'Nothing in common here.', 'Third thing.'] },
  { name: 'document now empty', before: BASE, index: 2, after: [] },
  { name: 'document shorter than the hint', before: BASE, index: 4,
    after: ['Only one line left, unrelated.'] },

  // Normalisation. Every one of these is the same sentence to a listener, and
  // every one arrives from ordinary causes: re-extracting a page, a PDF
  // re-export, a heading recased, a typographic quote substituted.
  { name: 'ws: internal run collapsed', before: BASE, index: 1,
    after: swap(BASE, 1, 'The first   point is about clarity.') },
  { name: 'ws: leading and trailing', before: BASE, index: 1,
    after: swap(BASE, 1, '   The first point is about clarity.  ') },
  { name: 'ws: tab instead of space', before: BASE, index: 1,
    after: swap(BASE, 1, 'The first\tpoint is about clarity.') },
  { name: 'ws: newline instead of space', before: BASE, index: 1,
    after: swap(BASE, 1, 'The first\npoint is about clarity.') },
  { name: 'ws: non-breaking space', before: BASE, index: 1,
    after: swap(BASE, 1, 'The first point is about clarity.') },
  { name: 'case: sentence recapitalised', before: BASE, index: 1,
    after: swap(BASE, 1, 'THE FIRST POINT IS ABOUT CLARITY.') },
  { name: 'case: one word recapitalised', before: BASE, index: 1,
    after: swap(BASE, 1, 'The First point is about clarity.') },
  { name: 'punctuation: curly apostrophe',
    before: ['Alpha.', "It's a fine day to read.", 'Beta.'], index: 1,
    after: ['Alpha.', 'It’s a fine day to read.', 'Beta.'] },

  { name: 'rules changed, quote intact',
    before: ['Alpha.', 'Serve it w/ cream.', 'Beta.'], index: 1,
    after: ['Alpha.', 'Serve it w/ cream.', 'Beta.'], rulesChanged: true },
  { name: 'rules changed, quote rewritten',
    before: ['Alpha.', 'Serve it w/ cream.', 'Beta.'], index: 1,
    after: ['Alpha.', 'Serve it with cream.', 'Beta.'], rulesChanged: true },
  { name: 'rules changed, repeated and rewritten',
    before: ['Alpha.', 'Serve it w/ cream.', 'Beta.', 'Serve it w/ cream.', 'Gamma.'],
    index: 3,
    after: ['Alpha.', 'Serve it with cream.', 'Beta.', 'Serve it with cream.', 'Gamma.'],
    rulesChanged: true },
];

/**
 * Divergences that exist today, each recorded exactly as it currently behaves.
 *
 * A harness that simply passed while these existed would be the stale-copy
 * problem again: green, consistent, and wrong. A harness that simply failed
 * would be red every day until someone changed a half they may not own, and a
 * permanently red check is one nobody reads.
 *
 * So they are enumerated. An unlisted disagreement fails. A listed one that
 * stops happening also fails, because a fixed divergence must be deleted from
 * this list rather than left to describe something that is no longer true.
 *
 * These began as eight, all attributed to the extension comparing raw strings
 * where the desktop compares with whitespace collapsed, case folded and
 * punctuation stripped. Three of the eight were the extension's to fix and are
 * gone: `_output_contract` in shared/normalization.json already requires both
 * halves to collapse runs of spaces and tabs and trim the ends, so two strings
 * differing only in that respect cannot both be legal pipeline output. The
 * extension now compares on that form, using the contract's own `tidy`.
 *
 * The five below are not the same kind of thing, and the difference is worth
 * stating rather than folding into one cause. `docs/anchor-vocabulary.md`
 * defines `exact` as "character for character", and the same contract says in
 * as many words that **newlines are left alone**. A non-breaking space is not a
 * space or a tab and is likewise untouched. So a quote that differs from the
 * live sentence by a newline, a non-breaking space, a capital letter or an
 * apostrophe style differs in characters, and `exact` is not available for it
 * under the vocabulary as written.
 *
 * That makes these five the desktop half's deviation from the shared contract
 * rather than the extension's oversight — or, if the contract is wrong, a
 * change to `docs/anchor-vocabulary.md` that neither half makes alone. The
 * document says so itself. Left listed, unresolved, and deliberately not
 * silently conformed to in either direction.
 *
 * They agree on *where* the bookmark lands in every case; they disagree on what
 * to call it, which decides what the user is told about how much to trust it.
 */
/**
 * Read a repository file, for premises that rest on something written down
 * rather than on the corpus.
 *
 * A wider signature than the segment harness's `holds(CASES)`, deliberately.
 * The reasoning these entries rest on lives in the shared data and in the
 * vocabulary document, not in the case list, and a predicate that cannot reach
 * it would have to be written as prose again — which is the thing that expired.
 */
const read = (rel) => {
  const text = readFileSync(join(root, rel), 'utf8');
  return rel.endsWith('.json') ? JSON.parse(text) : text;
};

/** Whether the vocabulary still contains both of its incompatible definitions. */
/**
 * Does the vocabulary's summary table still define `exact` as word for word?
 *
 * Reads the table row rather than searching the whole document. Its predecessor
 * asked whether both phrasings appeared anywhere, and the edit that resolved
 * the contradiction also used the rejected phrase while explaining what had
 * been rejected: both were present, the premise held, and nothing fired on the
 * one day it existed for. A phrase match across prose cannot tell a definition
 * from a mention of one.
 */
function tableDefinesExactAsWordForWord() {
  const row = read('docs/anchor-vocabulary.md')
    .split('\n')
    .find((line) => line.startsWith('| `exact` |'));
  return Boolean(row) && row.includes('word for word')
    && !row.includes('character for character');
}

const KNOWN = new Map([
  // Not an anchor disagreement, and the diagnosis above it was right: these
  // were the two halves holding different TEXT for the same page, with the
  // anchor layer merely where it became visible.
  //
  // Two of the three are now gone. `collapse` in shared/normalization.json —
  // smart quotes, soft hyphens, zero-width characters, dashes, footnote
  // markers — had every flag set and was read by neither half for the whole of
  // the project. The extension now implements it, against definitions written
  // into `_collapse_rules` so both halves can converge on the same ones, and
  // the non-breaking space and curly apostrophe cases went identical.
  //
  // It was not only an anchor question. The phonemizer looks words up in
  // CMUdict by literal text, so on the extension "don't" with a curly
  // apostrophe was pronounced "dawn tee" — most contractions on most
  // professionally typeset pages, in the neural voices that are the pitch.
  //
  // The newline survives because the contract says in as many words that
  // newlines are left alone, and because they are load-bearing for the desktop
  // half, whose segmenter splits on them. That makes it the same structural
  // difference as the layout group in conformance_segment.mjs rather than a
  // bug in either side.
  ['ws: newline instead of space', {
    python: 'exact',
    js: 'fuzzy',
    premise: {
      describe: 'the output contract says newlines are left alone, so a quote '
        + 'differing by one differs in characters and cannot be an exact match',
      holds: () => read('shared/normalization.json')._output_contract
        .includes('Newlines are left alone'),
    },
  }],

  // RESOLVED by the user on 2026-09-21: `exact` means word for word.
  //
  // These two stopped being a shared open question that day and became the
  // extension's deviation. It compares raw strings; the decision is that
  // comparison is on words, with whitespace collapsed, case folded and
  // punctuation stripped, which is the desktop half's existing behaviour. So
  // the extension is the side that changes and these entries are its migration,
  // in the same shape as the bracketed-footnote one the desktop half carried.
  //
  // The reason, kept here because an entry that outlives the argument is how
  // the last one went stale: `exact` means say nothing to the user. Character
  // equality would announce a guess about a position that is certainly right,
  // every time a page is re-extracted with a heading recased.
  //
  // The premise is now the decision rather than the contradiction, and it goes
  // the other way: these are tolerable only while the document still says word
  // for word. Reopen the question and this fails, which is right, because then
  // they are not a migration any more.
  //
  // A caution earned the hard way. The previous premise asked whether both
  // phrasings still appeared anywhere in the document, and the edit that
  // resolved the contradiction ALSO used the words "character for character"
  // while explaining what had been rejected. Both phrases were present, the
  // premise held, and nothing fired on the day it was supposed to. Matching a
  // phrase across a whole prose file cannot tell a definition from a mention of
  // one, which is the same failure as matching source text instead of calling
  // the behaviour. This one reads the table row.
  ['case: sentence recapitalised', {
    python: 'exact',
    js: 'fuzzy',
    premise: {
      describe: 'the vocabulary table still defines exact as word for word, so '
        + 'these remain the extension\'s migration rather than an open question',
      holds: tableDefinesExactAsWordForWord,
    },
  }],
  ['case: one word recapitalised', {
    python: 'exact',
    js: 'fuzzy',
    premise: {
      describe: 'as above: the decision still stands in the table',
      holds: tableDefinesExactAsWordForWord,
    },
  }],
]);

const py = process.env.EXECUTIVE_READER_PYTHON
  || join(root, 'desktop', '.venv', 'Scripts', 'python.exe');
const pyOut = JSON.parse(execFileSync(py, [join(root, 'tools', 'conformance_anchor_py.py')], {
  input: JSON.stringify(CASES),
  encoding: 'utf8',
  maxBuffer: 1 << 24,
}));

let identical = 0;
const unexpected = [];
const matchedKnown = new Set();

CASES.forEach((c, i) => {
  // Real use always stamps: capture writes the current rule fingerprint and
  // locate is handed the current one. Passing null instead would compare the
  // two halves' opinions about unstamped legacy anchors, which is a different
  // question from whether the ladder agrees.
  const anchor = capture(asSegments(c.before), c.index, 'stamp-1');
  const js = locate(anchor, asSegments(c.after),
                    c.rulesChanged ? 'stamp-2' : 'stamp-1');
  const p = pyOut[i];

  if (js.index === p.index && js.how === p.how && js.verified === p.verified) {
    identical++;
    return;
  }

  const known = KNOWN.get(c.name);
  // Position must match even for a known divergence. These are disagreements
  // about the label; one that started moving the bookmark would be a different
  // and much worse bug wearing the same name.
  if (known && js.index === p.index && p.how === known.python && js.how === known.js) {
    matchedKnown.add(c.name);
    return;
  }

  unexpected.push(
    `  ${c.name}\n`
    + `    python: index=${p.index} how=${p.how} verified=${p.verified}\n`
    + `    js:     index=${js.index} how=${js.how} verified=${js.verified}`
    + (known ? `\n    (listed as a known divergence, but not this one:`
             + ` expected python=${known.python} js=${known.js})` : ''));
});

const stale = [...KNOWN.keys()].filter((name) => !matchedKnown.has(name));

/**
 * Entries still diverging, but no longer for the reason recorded.
 *
 * The mechanism is the desktop half's, from conformance_segment.mjs, and it
 * exists because a recorded divergence carries two claims and only one of them
 * was ever checked: what the two halves do, and why that is acceptable. The
 * staleness check above covers the first. An entry whose behaviour is unchanged
 * while its argument has quietly expired passes everything.
 *
 * All three entries here rest on something written down elsewhere — the output
 * contract, and the vocabulary document's two incompatible definitions of
 * `exact`. Those are exactly the sentences that get edited by someone resolving
 * the question, in a file that has nothing to do with this one.
 */
const brokenPremise = [];
for (const [name, entry] of KNOWN) {
  if (!entry.premise) continue;
  let holds = false;
  try {
    holds = entry.premise.holds(CASES);
  } catch {
    holds = false; // a premise that cannot even be evaluated has not survived
  }
  if (!holds) brokenPremise.push(`  ${name}\n    stated reason: ${entry.premise.describe}`);
}

console.log(`${CASES.length} cases`);
console.log(`  identical:        ${identical}`);
console.log(`  known divergence: ${matchedKnown.size}`);
console.log(`  unexpected:       ${unexpected.length}`);

if (unexpected.length) {
  console.error('\nThe two halves disagree in a way nobody recorded:\n');
  console.error(unexpected.join('\n'));
  console.error('\nEither fix the half that is wrong, or add the case to KNOWN'
    + ' in this file with a reason.');
}

if (stale.length) {
  console.error('\nListed as known divergences but they now agree:\n');
  for (const name of stale) console.error(`  ${name}`);
  console.error('\nDelete them from KNOWN. A list that describes behaviour the'
    + ' code no longer has is worse than no list.');
}

if (brokenPremise.length) {
  console.error('\nStill diverging, but no longer for the reason recorded:\n');
  console.error(brokenPremise.join('\n'));
  console.error('\nThe behaviour is unchanged; the argument for tolerating it is'
    + ' not. Decide again on the facts as they are now, then rewrite or remove'
    + ' the entry. Do not just update the predicate to whatever passes.');
}

if (unexpected.length || stale.length || brokenPremise.length) process.exit(1);

if (matchedKnown.size) {
  console.log('\nOK, with ' + matchedKnown.size + ' recorded divergences.'
    + ' All of them agree on where the bookmark lands and differ only in what'
    + ' the position is called. See KNOWN in this file.');
} else {
  console.log('\nOK: both halves resolve every case identically.');
}
