/**
 * Do both halves split the same text into the same sentences?
 *
 * The README of each half says they do, and until now nothing checked it.
 * tools/conformance.mjs compares the *normalising* stages — symbols, currency,
 * expansions — and stops there. It never calls segment(), even though
 * shared/abbreviations.json exists for no other purpose than segmentation, and
 * both halves describe splitting sentences identically as a shared guarantee.
 *
 * That is the shape CAVEATS entry 16 is about: a claim about behaviour resting
 * on something structural that is genuinely true. `shared/` really is shared,
 * and a harness really does exist, so "they segment identically" reads as
 * already verified.
 *
 * Cases go through the whole pipeline, normalise then segment, because that is
 * what a listener hears. Isolating segment() looked cleaner and was misleading:
 * the desktop half rejoins hard-wrapped prose during normalise, so feeding raw
 * text to segment() alone reports it breaking sentences it never breaks in use.
 *
 * Attribution survives, because conformance.mjs compares the normalising stages
 * on their own. A difference that shows up here and not there is segmentation.
 *
 * Run: node tools/conformance_segment.mjs
 */
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const { segment, loadAbbreviations } = await import(
  pathToFileURL(join(root, 'extension', 'src', 'core', 'segment.js')).href);
const { normalize, loadNormalization } = await import(
  pathToFileURL(join(root, 'extension', 'src', 'core', 'normalize.js')).href);

// Both sets start empty and the extension fills them at runtime from the
// shared file. Skipping this compares a loaded Python segmenter against an
// unloaded JavaScript one, which manufactures disagreement about every
// abbreviation and says nothing about either implementation.
loadAbbreviations(JSON.parse(
  readFileSync(join(root, 'shared', 'abbreviations.json'), 'utf8')));
loadNormalization(JSON.parse(
  readFileSync(join(root, 'shared', 'normalization.json'), 'utf8')));

const LONG = 'This clause carries the sentence forward, and this one extends it '
  + 'further still, and a third keeps going well past the point where any '
  + 'engine would rather start speaking, and a fourth finally ends it so the '
  + 'splitter has somewhere sensible to cut before the limit is reached.';

const CASES = [
  // Plain sentence boundaries.
  ['two sentences', 'One two. Three four.'],
  ['question and exclamation', 'Really?! I had no idea.'],
  ['ellipsis mid sentence', 'Wait... what happened?'],
  ['quote before the break', 'She said "Stop." Then she left.'],
  ['bracket closes the sentence', 'He left (finally). We all cheered.'],

  // Periods that do not end a sentence.
  ['title abbreviation', 'Dr. Smith arrived. He was late.'],
  ['latin abbreviation', 'Use a cache, e.g. Redis. It helps.'],
  ['country abbreviation', 'The U.S. economy grew. Analysts were surprised.'],
  ['initials', 'J. R. R. Tolkien wrote it. Everyone knows.'],
  ['decimal number', 'The cost is 3.5 million. Nobody blinked.'],
  ['year then capital', 'It happened in 1999. The next year was worse.'],
  ['url with dots', 'Visit https://example.com/a.b for more. Then leave.'],
  ['file extension', 'Open report.pdf now. It is ready.'],

  // Degenerate input.
  ['empty', ''],
  ['spaces only', '   '],
  ['no terminator', 'No terminator at all'],
  ['single letter sentence', 'A.'],
  ['terminator only', '...'],
  ['long sentence is split for latency', LONG],

  // Layout. A captured page is never one line.
  ['two lines, no terminator', 'Line one\nLine two'],
  ['two lines, terminated', 'Line one.\nLine two.'],
  ['paragraph break', 'Para one.\n\nPara two.'],
  ['heading then body', 'A Heading\n\nSome body text follows.'],
  ['hard-wrapped prose',
   'The sentence was wrapped by the exporter\nacross two lines even though it\nis one sentence.'],
  ['numbered list', '1. Preheat the oven.\n2. Butter the tin.\n3. Bake it.'],
  ['bulleted list', '- First item.\n- Second item.'],
  ['markdown table',
   '| Path | Size |\n|---|---|\n| vendor/pdfjs | 1.7 MB |\n\nAfter the table.'],
];

/**
 * Divergences that exist today, recorded exactly as they currently behave.
 *
 * Same contract as the anchor harness: an unrecorded disagreement fails, and so
 * does a recorded one that stops happening, because a list describing behaviour
 * the code no longer has is worse than no list.
 */
const KNOWN = new Map([
  // --- layout ---------------------------------------------------------------
  // The desktop half treats a line break as structure: it breaks on blank
  // lines, rejoins prose the exporter hard-wrapped, and strips list bullets.
  // The extension treats a newline as an ordinary character, so a heading and
  // the paragraph under it arrive as one segment with the break inside it.
  //
  // The desktop behaviour is the one that reads well aloud, and the extension
  // gets away without it because the DOM already separates the elements it
  // reads. It does not get away with it on a PDF, which is why it vendors
  // pdf.js. This is the largest group here and the one worth resolving first.
  ['two lines, no terminator', {
    python: ['Line one.', 'Line two'],
    js: ['Line one\nLine two'],
  }],
  ['heading then body', {
    python: ['A Heading', 'Some body text follows.'],
    js: ['A Heading\n\nSome body text follows.'],
  }],
  ['hard-wrapped prose', {
    python: ['The sentence was wrapped by the exporter across two lines even though it is one sentence.'],
    js: ['The sentence was wrapped by the exporter\nacross two lines even though it\nis one sentence.'],
  }],
  ['bulleted list', {
    python: ['First item.', 'Second item.'],
    js: ['- First item.', '- Second item.'],
  }],
  ['markdown table', {
    python: ['| Path | Size |', '| vendor/pdfjs | 1.7 MB |', 'After the table.'],
    js: ['| Path | Size |\n|---|---|\n| vendor/pdfjs | 1.7 MB |\n\nAfter the table.'],
  }],

  // --- normalising, not splitting -------------------------------------------
  // These two are decided before the segmenter sees the text, and neither
  // stage is covered by conformance.mjs, which stops at symbols, currency and
  // expansions. The desktop half collapses a run of periods and reads a link
  // as its domain; the extension does neither.
  ['ellipsis mid sentence', {
    python: ['Wait. what happened?'],
    js: ['Wait...', 'what happened?'],
  }],
  ['url with dots', {
    python: ['Visit link to example.com for more.', 'Then leave.'],
    js: ['Visit https://example.com/a.b for more.', 'Then leave.'],
  }],

  // --- degenerate -----------------------------------------------------------
  // Punctuation with no words in it. The desktop drops it, the extension keeps
  // it and would speak it. Harmless either way, listed so it is not mistaken
  // for a new problem later.
  ['terminator only', { python: [], js: ['...'] }],
]);

const py = process.env.EXECUTIVE_READER_PYTHON
  || join(root, 'desktop', '.venv', 'Scripts', 'python.exe');
const pyOut = JSON.parse(
  execFileSync(py, [join(root, 'tools', 'conformance_segment_py.py')], {
    input: JSON.stringify(CASES.map(([, text]) => text)),
    encoding: 'utf8',
    maxBuffer: 1 << 24,
  }));

let identical = 0;
const unexpected = [];
const matchedKnown = new Set();

CASES.forEach(([name, text], i) => {
  const js = segment(normalize(text)).map((s) => s.text);
  const p = pyOut[i];
  const same = js.length === p.length && js.every((s, k) => s === p[k]);
  if (same) { identical++; return; }

  const known = KNOWN.get(name);
  if (known
      && JSON.stringify(p) === JSON.stringify(known.python)
      && JSON.stringify(js) === JSON.stringify(known.js)) {
    matchedKnown.add(name);
    return;
  }
  unexpected.push(`  ${name}\n    input:  ${JSON.stringify(text)}\n`
    + `    python: ${JSON.stringify(p)}\n    js:     ${JSON.stringify(js)}`);
});

const stale = [...KNOWN.keys()].filter((name) => !matchedKnown.has(name));

console.log(`${CASES.length} cases`);
console.log(`  identical:        ${identical}`);
console.log(`  known divergence: ${matchedKnown.size}`);
console.log(`  unexpected:       ${unexpected.length}`);

if (unexpected.length) {
  console.error('\nThe two halves split text differently, and nobody recorded it:\n');
  console.error(unexpected.join('\n'));
  console.error('\nEither fix the half that is wrong, or add the case to KNOWN'
    + ' in this file with a reason.');
}
if (stale.length) {
  console.error('\nListed as known divergences but they now agree:\n');
  for (const name of stale) console.error(`  ${name}`);
  console.error('\nDelete them from KNOWN.');
}
if (unexpected.length || stale.length) process.exit(1);

if (matchedKnown.size) {
  console.log('\nOK, with ' + matchedKnown.size
    + ' recorded divergences. See KNOWN in this file.');
} else {
  console.log('\nOK: both halves split every case identically.');
}
