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
  // The other side of the ellipsis rule. Without this the corpus cannot tell
  // an implementation that checks what follows from one that suppresses the
  // boundary unconditionally — a mutation to that effect passed the whole
  // harness while the unit tests caught it.
  ['ellipsis before a capital', 'That was the end... The next began.'],
  ['ellipsis at the very end', 'It ended...'],
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
  // Checked on both sides since this was written, and it is structural rather
  // than a defect in either half. Newlines survive the desktop normaliser and
  // reach its segmenter from OCR, the clipboard, window text and PDF, so its
  // line handling is load-bearing. Nothing reaches the extension's segmenter
  // with a newline in it: extractBlocks collapses every whitespace run inside a
  // block, and pdf/layout.js rejoins hard-wrapped lines using the gap between
  // baselines — the only place that decision can be made correctly — after
  // which pdf/viewer.js renders one paragraph element per break.
  //
  // So this group is unreachable on the extension side rather than latent, and
  // asserted to be: tools/domtest/ checks no block text carries a line break,
  // and pdf-layout.test.mjs checks no rejoined paragraph does.
  ['two lines, no terminator', {
    python: ['Line one.', 'Line two'],
    js: ['Line one\nLine two'],
    // The premise for this whole group, attached to its first member. The
    // divergence is tolerable only because nothing reaches the extension's
    // segmenter with a newline in it, and the load-bearing half of that is the
    // PDF viewer: it splits linesToText output on blank lines and renders one
    // paragraph element per break. Change it to hand a whole page to the
    // segmenter and this group stops being structural and becomes a defect —
    // decided in a file with nothing to do with this one, which is exactly the
    // distance that let the last expired reason survive.
    premise: {
      describe: 'extension/src/pdf/viewer.js still splits paragraphs on blank '
        + 'lines, so the segmenter never receives a newline from the PDF path',
      holds: () => readFileSync(
        join(root, 'extension', 'src', 'pdf', 'viewer.js'), 'utf8',
      ).includes('split(/\\n{2,}/)'),
    },
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
  // Two things at once, recorded exactly as it behaves today rather than as it
  // will behave. `list_markers` in shared/abbreviations.json is implemented on
  // the extension side only so far, which is why the extension keeps its first
  // item whole and the desktop keeps none. When the Python side lands, this
  // entry goes stale and the harness will say so.
  //
  // What will remain after that is the same structural difference as the group
  // above. The rule is anchored at position zero because both halves segment
  // one block or line at a time in production, which is exactly where a marker
  // sits. This harness hands the extension all three lines as ONE string, a
  // shape it never receives, so only the first marker is at position zero.
  // `list_markers` has now landed on both sides, and what is left is the
  // structural difference above wearing a different hat. The rule is anchored
  // at position zero because both halves segment one block or line at a time in
  // production, which is exactly where a marker sits. This harness hands the
  // text to each half as ONE string, so only the first marker is at position
  // zero for the extension. The desktop splits on newlines first, so all three
  // markers are at the start of a line and all three are recognised.
  //
  // Neither is wrong. The harness is feeding a shape neither half receives.
  ['numbered list', {
    python: ['1. Preheat the oven.', '2. Butter the tin.', '3. Bake it.'],
    js: ['1. Preheat the oven.', '2.', 'Butter the tin.', '3.', 'Bake it.'],
    // The whole tolerance rests on this input being a shape neither half is
    // handed in production. Written as a predicate rather than as prose,
    // because prose cannot be checked: rewrite the case one line at a time,
    // the way both halves really receive it, and the divergence should vanish
    // rather than be excused by a comment that no longer applies.
    premise: {
      describe: 'the case feeds all three items as one multi-line string, '
        + 'which neither half receives in production',
      holds: (cases) => {
        const found = cases.find(([name]) => name === 'numbered list');
        return Boolean(found) && found[1].includes('\n');
      },
    },
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
  // Resolved rather than recorded. `terminators` in shared/abbreviations.json
  // now says an ellipsis followed by a lowercase word is not a boundary, and
  // both halves implement it, so "Wait… what happened?" is one segment on each.
  //
  // Deliberately NOT generalised to the plain period, which is the wider rule
  // the desktop half's segmenter applies. The argument looks identical, but
  // informal all-lowercase writing — chat logs, forum posts, notes — is real
  // content for a reader, and the general rule swallows every boundary in the
  // piece and reads it as one utterance with no pauses. The ellipsis has no
  // such counterexample.
  //
  // The plain-period difference therefore remains, and remains unrecorded here
  // because no case in this corpus reaches it any more.
  ['url with dots', {
    python: ['Visit link to example.com for more.', 'Then leave.'],
    js: ['Visit https://example.com/a.b for more.', 'Then leave.'],
  }],

  // --- degenerate -----------------------------------------------------------
  // Punctuation with no words in it. The desktop drops it, the extension keeps
  // it and would speak it. Harmless either way, listed so it is not mistaken
  // for a new problem later.
  ['terminator only', { python: [], js: ['…'] }],
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

/**
 * Entries whose stated reason no longer holds, even though the behaviour does.
 *
 * The staleness check above catches an entry whose *behaviour* changed. Nothing
 * caught one whose *reasoning* changed, and that happened here: an entry said
 * "outside a collapsed ellipsis the case is rare, which is why it is recorded
 * rather than chased". True the day it was written. A rule in a different file
 * then turned every authored "..." into an ellipsis, which moved the case from
 * rare to every document, and the entry went on being correct about the
 * behaviour while being wrong about why that was acceptable. It was caught by
 * someone re-reading the sentence while landing something else, which is not a
 * procedure.
 *
 * So an entry may carry a `premise`: the claim its acceptability rests on,
 * written as a predicate over the corpus rather than as prose. Prose cannot be
 * checked; a predicate can. An entry without one is not wrong, it just has
 * nothing here to check.
 */
const brokenPremise = [];
for (const [name, entry] of KNOWN) {
  if (!entry.premise) continue;
  let holds = false;
  try {
    holds = entry.premise.holds(CASES);
  } catch (err) {
    holds = false;
  }
  if (!holds) {
    brokenPremise.push(`  ${name}\n    stated reason: ${entry.premise.describe}`);
  }
}

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
if (brokenPremise.length) {
  console.error('\nStill diverging, but no longer for the reason recorded:\n');
  console.error(brokenPremise.join('\n'));
  console.error('\nThe behaviour is unchanged; the argument for tolerating it is'
    + ' not. Decide again on the facts as they are now, then rewrite or remove'
    + ' the entry. Do not just update the predicate to whatever passes.');
}
if (unexpected.length || stale.length || brokenPremise.length) process.exit(1);

if (matchedKnown.size) {
  console.log('\nOK, with ' + matchedKnown.size
    + ' recorded divergences. See KNOWN in this file.');
} else {
  console.log('\nOK: both halves split every case identically.');
}
