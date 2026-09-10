/**
 * Cross-language check for the phoneme stage.
 *
 * The two halves must pronounce a word identically. A divergence here is
 * audible and unexplainable to a user: the same reader saying a name two ways
 * depending on which half spoke. Comparing the implementations is the only way
 * to know, since both are ports of one design and neither is the reference.
 *
 * Run: node tools/conformance_g2p.mjs
 */

import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gunzipSync } from 'node:zlib';

import { loadDict, loadHomographs, phonemize } from
  '../extension/src/engines/kokoro/g2p-en.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

const CASES = [
  // Ordinary prose: the common path.
  'The quick brown fox jumps over the lazy dog.',
  'Serve the coffee with cream, not without it.',
  'She sells sea shells by the sea shore.',
  // Stress and length, the conventions borrowed from eSpeak.
  'quick', 'father', 'thought', 'fleece', 'goose', 'about', 'sofa',
  // Schwa: unstressed AH must reduce.
  'banana', 'photograph', 'photography', 'photographic',
  // Homographs, where a dictionary is confidently wrong without context.
  'He read the book.', 'Please read this.', 'I have read it already.',
  'The lead pipe.', 'They lead the way.',
  'a record', 'to record', 'the address', 'to address',
  'a live wire', 'they live here', 'the wind blew', 'wind the clock',
  'a minute detail', 'one minute later',
  'close the door', 'a close call', 'the use of it', 'to use it',
  'a tear rolled down', 'to tear it up', 'take a bow', 'bow the head',
  // Out of dictionary: proper nouns, coinages, product names.
  'Anthropic', 'Kubernetes', 'Xiaomi', 'nginx', 'PostgreSQL',
  'Earmark', 'Kokoro', 'phonemizer', 'blorptastic',
  // Letter-to-sound digraphs, longest-first ordering.
  'nation', 'vision', 'tough', 'laughter', 'watch', 'bridge', 'night',
  'running', 'quick', 'knowledge', 'through',
  // Silent trailing e, and words too short for the rule.
  'name', 'the', 'be', 'ice', 'axe',
  // Folding: marks, strokes and ligatures that split a word if left alone.
  'café', 'naïve', 'Skłodowska', 'Ångström', 'Gödel', 'Erdős', 'Straße',
  'Æsop', 'Œuvre', 'Ørsted',
  // Punctuation is carried through, and never preceded by a space.
  'Wait, what? Yes! Really... fine.',
  'He said "hello" (twice).',
  'One—two—three.',
  // Apostrophes must not split a word.
  "don't", "it's", "o'clock", "they're", "Mary's lamb",
  // Degenerate input.
  '', '   ', '...', '42', 'a',
];

const dictPath = join(root, 'extension', 'vendor', 'cmudict', 'cmudict.txt.gz');
const homPath = join(root, 'extension', 'vendor', 'cmudict', 'homographs.json');
if (!existsSync(dictPath)) {
  console.error(`missing ${dictPath}`);
  process.exit(1);
}
loadDict(gunzipSync(readFileSync(dictPath)).toString('utf8'));
loadHomographs(JSON.parse(readFileSync(homPath, 'utf8')));

const py = process.env.EARMARK_PYTHON
  || join(root, 'desktop', '.venv', 'Scripts', 'python.exe');
const pyOut = JSON.parse(execFileSync(py, [join(root, 'tools', 'conformance_g2p_py.py')], {
  input: JSON.stringify(CASES),
  encoding: 'utf8',
  maxBuffer: 1 << 24,
}));

let identical = 0;
const differing = [];
CASES.forEach((input, i) => {
  const a = pyOut.ipa[i];
  const b = phonemize(input).ipa;
  if (a === b) { identical++; return; }
  differing.push(
    `  ${JSON.stringify(input)}\n    python: ${JSON.stringify(a)}\n    js:     ${JSON.stringify(b)}`,
  );
});

console.log(`${CASES.length} inputs`);
console.log(`  identical:  ${identical}`);
console.log(`  differing:  ${differing.length}`);
if (differing.length) {
  console.log('\n' + differing.join('\n'));
  process.exit(1);
}
// A harness that compares nothing passes trivially; say what it examined.
if (CASES.length < 50) {
  console.error('\nsuspiciously few cases; the list was probably truncated');
  process.exit(1);
}
console.log('\nOK: both halves pronounce every case identically.');
