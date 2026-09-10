/**
 * PDF reading-order reconstruction.
 *
 * A PDF is glyphs at coordinates and nothing else, so all of this is inference,
 * and the failures are severe rather than subtle: a two-column paper read in
 * visual order interleaves the columns sentence by sentence and is unreadable.
 * These use synthetic items so the geometry is exercised without a PDF.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  groupLines, findColumns, pageLines, linesToText, furnitureFilter,
} from '../src/pdf/layout.js';

/** Build a text item the way PDF.js reports one. */
const item = (str, x, y, width = str.length * 5, height = 10) => ({ str, x, y, width, height });

/** One line of text at a given height. */
const line = (text, y, x = 50, width = text.length * 5) => item(text, x, y, width);

test('orders lines top to bottom, despite PDF y running upward', () => {
  const lines = groupLines([line('third', 100), line('first', 300), line('second', 200)]);
  assert.deepEqual(lines.map((l) => l.text), ['first', 'second', 'third']);
});

test('groups items on the same baseline into one line', () => {
  // PDFs routinely emit each word, sometimes each letter, as its own item.
  const lines = groupLines([item('Hello', 50, 200, 30), item('world', 85, 200, 30)]);
  assert.equal(lines.length, 1);
  assert.equal(lines[0].text, 'Hello world');
});

test('tolerates a slightly off baseline, as superscripts have', () => {
  const lines = groupLines([item('text', 50, 200, 20), item('1', 71, 203, 4, 6)]);
  assert.equal(lines.length, 1, 'a superscript belongs to its line');
});

test('inserts a space only where glyphs are actually apart', () => {
  // Adjacent items with no gap are one word: "to" + "day" is "today".
  const tight = groupLines([item('to', 50, 200, 10), item('day', 60, 200, 15)]);
  assert.equal(tight[0].text, 'today');
  const apart = groupLines([item('to', 50, 200, 10), item('day', 75, 200, 15)]);
  assert.equal(apart[0].text, 'to day');
});

test('reads a two-column page one column at a time', () => {
  // The failure this exists to prevent: read in visual order this comes out as
  // "Left one / Right one / Left two / Right two", which is unreadable.
  //
  // Note the columns share baselines, which is why columns must be resolved on
  // items before lines are grouped. Grouping first merges each left line with
  // the right line beside it, and nothing downstream can undo that.
  const items = [
    line('Left one', 300, 50, 150), line('Right one', 300, 320, 150),
    line('Left two', 280, 50, 150), line('Right two', 280, 320, 150),
    line('Left three', 260, 50, 150), line('Right three', 260, 320, 150),
    line('Left four', 240, 50, 150), line('Right four', 240, 320, 150),
  ];
  const cols = findColumns(items, 600);
  assert.equal(cols.length, 2, 'two columns must be detected');

  const order = pageLines(items, 600).map((l) => l.text);
  assert.deepEqual(order, [
    'Left one', 'Left two', 'Left three', 'Left four',
    'Right one', 'Right two', 'Right three', 'Right four',
  ], 'the whole left column must be read before the right');
});

test('a single-column page stays one column', () => {
  const items = ['one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight']
    .map((t, i) => line(t, 300 - i * 20, 50, 500));
  assert.equal(findColumns(items, 600).length, 1);
});

test('a narrow margin note does not become a column', () => {
  const items = [
    ...['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta', 'eta', 'theta']
      .map((t, i) => line(t.repeat(8), 300 - i * 20, 50, 400)),
    line('note', 300, 520, 30),
  ];
  assert.equal(findColumns(items, 600).length, 1,
    'a column must hold a real share of the text');
});

test('repairs a word hyphenated across a line break', () => {
  const lines = groupLines([line('some inter-', 300), line('national text', 285)]);
  assert.equal(linesToText(lines), 'some international text');
});

test('does not rejoin a genuine compound that happened to wrap', () => {
  // "well-known" must survive; inventing "wellknown" is worse than a pause.
  const lines = groupLines([line('a well-', 300), line('Known name', 285)]);
  assert.ok(linesToText(lines).includes('well-'), 'uppercase after the hyphen is not a split word');
});

test('a wider than usual gap becomes a paragraph break', () => {
  const lines = groupLines([
    line('first para', 300), line('continues here', 285),
    line('second para', 230),
  ]);
  const text = linesToText(lines);
  assert.ok(text.includes('\n\n'), 'a large vertical gap is a paragraph break');
  assert.ok(text.startsWith('first para continues here'), 'normal gaps join');
});

test('drops running heads that repeat across pages', () => {
  // "Chapter Four  17" between every paragraph is the most irritating thing a
  // PDF reader can do.
  const pages = [1, 2, 3, 4].map((n) => groupLines([
    line('Chapter Four', 400),
    line(`body text of page ${n}`, 300),
    line(String(n), 50),
  ]));
  const keep = furnitureFilter(pages);
  const kept = pages[0].filter((l) => keep(l, 0)).map((l) => l.text);
  assert.deepEqual(kept, ['body text of page 1']);
});

test('keeps everything when there are too few pages to judge', () => {
  const pages = [groupLines([line('Title', 400), line('body', 300)])];
  const keep = furnitureFilter(pages);
  assert.equal(pages[0].filter((l) => keep(l, 0)).length, 2);
});

test('handles empty and whitespace-only input', () => {
  assert.deepEqual(groupLines([]), []);
  assert.deepEqual(groupLines([item('   ', 0, 0)]), []);
  assert.equal(linesToText([]), '');
  assert.deepEqual(findColumns([], 600), [[]]);
});

/**
 * The invariant the segmenter relies on, checked at the boundary that produces
 * it.
 *
 * The desktop half segments on line breaks and this half does not, which its
 * conformance harness records as its largest group of divergences, with the
 * reasoning that a PDF is where hard wrapping is universal. It is not reachable
 * here, and the reason is two steps upstream rather than in the segmenter.
 *
 * `linesToText` rejoins wrapped lines with spaces and emits a blank line only
 * at a paragraph break, using the *geometry* — the gap between baselines — which
 * is the only place that decision can be made correctly. The viewer then splits
 * on those blank lines and renders one `<p>` per paragraph, so what reaches the
 * segmenter is DOM blocks with no line breaks in them at all.
 *
 * This pins the first step. The second is pinned in tools/domtest/.
 */
test('rejoined paragraphs carry no line breaks into the page', () => {
  // Two paragraphs of hard-wrapped prose: three tight lines, a wide gap, two
  // more tight lines.
  const lines = [
    { y: 300, x: 0, right: 100, text: 'The sentence was wrapped by the' },
    { y: 288, x: 0, right: 100, text: 'exporter across three lines even' },
    { y: 276, x: 0, right: 100, text: 'though it is one sentence.' },
    { y: 240, x: 0, right: 100, text: 'A second paragraph begins here' },
    { y: 228, x: 0, right: 100, text: 'and also wraps.' },
  ];

  const text = linesToText(lines);

  // The viewer's own rule, mirrored: src/pdf/viewer.js splits on /\n{2,}/.
  const paragraphs = text.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);

  assert.equal(paragraphs.length, 2, 'the paragraph break was not detected');
  assert.equal(paragraphs[0],
    'The sentence was wrapped by the exporter across three lines even though it is one sentence.');
  for (const p of paragraphs) {
    assert.ok(!/[\r\n]/.test(p), `a paragraph kept a line break: ${JSON.stringify(p)}`);
  }
});

test('a paragraph break is the only thing that survives as a blank line', () => {
  // Guards the guard above: if linesToText emitted no break at all, the split
  // would trivially produce newline-free paragraphs and prove nothing.
  const tight = [
    { y: 300, x: 0, right: 100, text: 'One line.' },
    { y: 288, x: 0, right: 100, text: 'Another line.' },
  ];
  assert.ok(!/\n/.test(linesToText(tight)), 'evenly spaced lines are one paragraph');
});
