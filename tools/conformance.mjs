/**
 * Cross-language conformance check.
 *
 * The premise of `shared/` is that both halves pronounce the same page the same
 * way. That premise is worth exactly nothing unless something tests it, because
 * two people implementing one spec in two languages will diverge on the parts
 * the spec left implicit. This runs the same inputs through the Python and the
 * JavaScript normalizers and reports where they disagree.
 *
 * Two verdicts, because they mean different things:
 *   IDENTICAL   byte-for-byte agreement
 *   EQUIVALENT  agreement after collapsing whitespace — same speech, different
 *               string, so anything doing offset arithmetic must care
 *
 * Run: node tools/conformance.mjs
 */

import { execFileSync } from 'node:child_process';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  loadNormalization, applySymbols, applyCurrency, applyExpansions,
} from '../extension/src/core/normalize.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

loadNormalization(JSON.parse(
  readFileSync(join(root, 'shared', 'normalization.json'), 'utf8'),
));

/** Inputs chosen to hit each condition and each way it is known to fail. */
const CASES = [
  // survival: these must come through untouched
  'R&D', 'C++', 'x && y', 'key=value', 'a==b', 'C#', '#hashtag', '## Heading',
  'me@example.com', '@someone', '~/home/user', '%s and %d', 'x += 1',
  'node->next', 'https://example.com/a?b=c', 'a/b/c',
  // conversion
  'Ben & Jerry', 'a = b', '20%', '#42', '~5', '20°', '§7',
  '©2026 Acme', 'Widget™ and Thing®',
  // currency
  '$50', '$1', '$1.00', '£20', '€1,200', '¥500', 'the $ sign', 'It cost $50.',
  '$50 and $1 and £3',
  // interaction and position
  '%', '&', '100% and 50%', 'A & B & C', '5% + 3%',
  '20 %', '# 42', '$ 50',
  // expansions: the boundary rule, which is where a real bug hid because this
  // stage was not covered here at first
  'node->next is a pointer.', 'Files xw/y and w/o sugar',
  'Compare a<=b and x <= y.', 'A -> B', 'w/ milk', 'w/o milk',
  'i.e. this', 'I.E. THIS', 'cats, dogs, etc.', 'Smith et al. found',
  'n.b. the date', 'N.B. the date', 'b/c reasons', 'n/a here',
  'x >= 5', 'lower->case', 'a=>b', 'p=>q and r => s',
  // expansion meeting symbol: "->" must win over any single-character rule
  '5 -> 10', '50% -> 60%',
  // edges
  '', ' ', '20', 'no symbols here at all',
];

const py = process.platform === 'win32'
  ? join(root, 'desktop', '.venv', 'Scripts', 'python.exe')
  : join(root, 'desktop', '.venv', 'bin', 'python');

if (!existsSync(py)) {
  console.error(`No Python at ${py}. Run the desktop setup first.`);
  process.exit(2);
}

const pyOut = JSON.parse(execFileSync(py, [join(root, 'tools', 'conformance_py.py')], {
  input: JSON.stringify(CASES),
  encoding: 'utf8',
  maxBuffer: 1 << 24,
}));

const jsOut = {
  expansions: CASES.map(applyExpansions),
  currency: CASES.map(applyCurrency),
  symbols: CASES.map(applySymbols),
  both: CASES.map((c) => applySymbols(applyCurrency(c))),
};

/** Stages are compared individually, not just end to end. A disagreement can
 *  cancel out across a full pipeline and hide; comparing per stage is what
 *  surfaced the tidy() placement difference. */
const STAGES = ['expansions', 'currency', 'symbols', 'both'];

const squash = (s) => s.replace(/\s+/g, ' ').trim();

let identical = 0;
let equivalent = 0;
/** @type {string[]} */
const differing = [];

for (const stage of STAGES) {
  CASES.forEach((input, i) => {
    const a = pyOut[stage][i];
    const b = jsOut[stage][i];
    if (a === b) { identical++; return; }
    if (squash(a) === squash(b)) {
      equivalent++;
      return;
    }
    differing.push(
      `  ${stage.padEnd(8)} ${JSON.stringify(input)}\n`
      + `    python: ${JSON.stringify(a)}\n`
      + `    js:     ${JSON.stringify(b)}`,
    );
  });
}

const total = CASES.length * STAGES.length;
console.log(`${total} comparisons across ${CASES.length} inputs`);
console.log(`  identical:  ${identical}`);
console.log(`  equivalent: ${equivalent}  (same speech, different whitespace)`);
console.log(`  differing:  ${differing.length}`);

if (differing.length) {
  console.log('\nReal disagreements:\n');
  console.log(differing.join('\n\n'));
}

/**
 * Self-checks, so this harness cannot pass by examining nothing. That is the
 * failure mode this project has now hit three times: a sync test that compared
 * zero files, a floor assertion that tolerated 163 unchecked results after the
 * suite grew, and a rule no test covered at all. A checker with no floor of its
 * own has no business asserting anyone else's correctness.
 */
const problems = [];
if (CASES.length === 0) problems.push('no inputs; the case list is empty');
if (identical + equivalent + differing.length !== total) {
  problems.push(`accounted for ${identical + equivalent + differing.length} of ${total} comparisons`);
}
// Equivalent is a failure now, not a warning. Both sides honour the output
// contract in `_output_contract`, so identical whitespace is the expectation;
// anything less means one side stopped applying it.
if (equivalent > 0) problems.push(`${equivalent} whitespace-only differences; the output contract is not being honoured on one side`);
if (differing.length) problems.push(`${differing.length} real disagreements`);

if (problems.length) {
  console.error(`\nFAIL: ${problems.join('; ')}`);
  process.exit(1);
}
console.log('\nOK: every comparison identical.');
