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
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const { capture, locate } = await import(
  pathToFileURL(join(root, 'extension', 'src', 'core', 'anchor.js')).href);

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
const KNOWN = new Map([
  ['ws: newline instead of space', { python: 'exact', js: 'fuzzy' }],
  ['ws: non-breaking space', { python: 'exact', js: 'fuzzy' }],
  ['case: sentence recapitalised', { python: 'exact', js: 'fuzzy' }],
  ['case: one word recapitalised', { python: 'exact', js: 'fuzzy' }],
  ['punctuation: curly apostrophe', { python: 'boundary', js: 'fuzzy' }],
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
  const anchor = capture(c.before, c.index, 'stamp-1');
  const js = locate(anchor, c.after, c.rulesChanged ? 'stamp-2' : 'stamp-1');
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

if (unexpected.length || stale.length) process.exit(1);

if (matchedKnown.size) {
  console.log('\nOK, with ' + matchedKnown.size + ' recorded divergences.'
    + ' All of them agree on where the bookmark lands and differ only in what'
    + ' the position is called. See KNOWN in this file.');
} else {
  console.log('\nOK: both halves resolve every case identically.');
}
