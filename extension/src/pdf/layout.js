/**
 * Turning PDF text items into reading order.
 *
 * A PDF has no paragraphs, no columns and no reading order — only glyphs at
 * coordinates. Everything here is reconstruction, and the failure modes are
 * loud: a two-column paper read in visual top-to-bottom order interleaves the
 * columns sentence by sentence and is completely incomprehensible.
 *
 * All of it is pure geometry, so it can be tested without a PDF.
 *
 * @typedef {{str: string, x: number, y: number, width: number, height: number}} Item
 * @typedef {{y: number, x: number, right: number, text: string}} Line
 */

/** Vertical distance, as a fraction of line height, still counted as one line.
 *  Superscripts and slightly-off baselines sit within this. */
const LINE_TOLERANCE = 0.5;

/** A horizontal gap wider than this fraction of the page splits columns. */
const COLUMN_GAP = 0.06;

/** A column must hold at least this share of the page's text to be real,
 *  which keeps a marginal note from being promoted to a column. */
const COLUMN_MIN_SHARE = 0.15;

/**
 * Group items onto lines by their baseline.
 * @param {Item[]} items
 * @returns {Line[]}
 */
export function groupLines(items) {
  const usable = items.filter((i) => i.str && i.str.trim());
  if (!usable.length) return [];

  const medianHeight = median(usable.map((i) => i.height).filter(Boolean)) || 10;
  const tolerance = medianHeight * LINE_TOLERANCE;

  // PDF origin is bottom-left, so descending y is top-to-bottom on the page.
  const sorted = [...usable].sort((a, b) => b.y - a.y || a.x - b.x);
  /** @type {Item[][]} */
  const rows = [];
  for (const item of sorted) {
    const row = rows[rows.length - 1];
    if (row && Math.abs(row[0].y - item.y) <= tolerance) row.push(item);
    else rows.push([item]);
  }

  return rows.map((row) => {
    const ordered = [...row].sort((a, b) => a.x - b.x);
    return {
      y: ordered[0].y,
      x: Math.min(...ordered.map((i) => i.x)),
      right: Math.max(...ordered.map((i) => i.x + i.width)),
      text: joinRow(ordered),
    };
  });
}

/**
 * Join items on one line, inserting a space only where the glyphs are actually
 * apart. PDFs frequently emit each word, or each letter, as its own item with
 * no spaces of their own.
 * @param {Item[]} row
 */
function joinRow(row) {
  let out = '';
  let prevRight = null;
  const gap = (median(row.map((i) => i.height).filter(Boolean)) || 10) * 0.2;

  for (const item of row) {
    if (prevRight !== null && item.x - prevRight > gap && !/\s$/.test(out)) out += ' ';
    out += item.str;
    prevRight = item.x + item.width;
  }
  return out.replace(/\s+/g, ' ').trim();
}

/**
 * Split *items* into columns, before they are grouped into lines.
 *
 * Order matters and getting it wrong is silent. In a two-column layout the left
 * and right columns share baselines, so grouping into lines first merges a line
 * of one column with the line beside it in the other, and no later step can
 * separate them again. Columns are a property of x, lines a property of y, and
 * x has to be resolved first.
 *
 * @param {Item[]} items
 * @param {number} pageWidth
 * @returns {Item[][]} columns, left to right
 */
export function findColumns(items, pageWidth) {
  const usable = items.filter((i) => i.str && i.str.trim());
  if (usable.length < 8) return [usable];

  const minGap = pageWidth * COLUMN_GAP;
  // Sweep for an x that no item spans: a vertical corridor of white space.
  const candidates = [];
  for (let x = pageWidth * 0.25; x <= pageWidth * 0.75; x += pageWidth * 0.01) {
    const crossing = usable.some((i) => i.x < x && i.x + i.width > x);
    if (!crossing) candidates.push(x);
  }
  if (!candidates.length) return [usable];

  const band = candidates[candidates.length - 1] - candidates[0];
  if (band < minGap) return [usable];
  const split = candidates[Math.floor(candidates.length / 2)];

  const left = usable.filter((i) => i.x + i.width <= split);
  const right = usable.filter((i) => i.x >= split);
  if (!left.length || !right.length) return [usable];

  const total = usable.reduce((n, i) => n + i.str.length, 0);
  const share = (g) => g.reduce((n, i) => n + i.str.length, 0) / total;
  // A marginal note is not a column; a real one carries a real share of text.
  if (share(left) < COLUMN_MIN_SHARE || share(right) < COLUMN_MIN_SHARE) return [usable];

  return [left, right];
}

/**
 * A whole page, in reading order: columns resolved first, then lines.
 * @param {Item[]} items
 * @param {number} pageWidth
 * @returns {Line[]}
 */
export function pageLines(items, pageWidth) {
  return findColumns(items, pageWidth).flatMap((col) => groupLines(col));
}

/**
 * Join lines into paragraphs, repairing words split across a line break.
 *
 * De-hyphenation is deliberately conservative: only a lowercase letter, then a
 * hyphen at the very end of a line, then a lowercase letter, gets joined.
 * Rejoining "well-known" across a break would invent a word, and a compound
 * that happened to wrap is far rarer than a genuine hyphenation.
 *
 * @param {Line[]} lines
 */
export function linesToText(lines) {
  if (!lines.length) return '';

  const gaps = [];
  for (let i = 1; i < lines.length; i++) gaps.push(lines[i - 1].y - lines[i].y);
  // The *typical* line gap, not the average one. A median over a page with a
  // few paragraph breaks sits between the two spacings and then matches
  // neither, so nothing is ever detected as a break.
  const normalGap = percentile(gaps.filter((g) => g > 0), 0.25);

  let out = '';
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const prev = lines[i - 1];

    if (i > 0) {
      const gap = prev.y - line.y;
      // A gap markedly larger than the usual line spacing is a paragraph break.
      if (normalGap > 0 && gap > normalGap * 1.6) out += '\n\n';
      // De-hyphenate conservatively: lowercase, hyphen at the line end, then a
      // lowercase continuation. "well-" followed by "Known" is a compound that
      // happened to wrap, and joining it would invent a word.
      else if (/[a-z]-$/.test(out) && /^[a-z]/.test(line.text)) out = out.slice(0, -1);
      else if (!/\s$/.test(out)) out += ' ';
    }
    out += line.text;
  }
  return out.replace(/[ \t]+/g, ' ').trim();
}

/**
 * Lines that repeat at the same height across pages are running heads and page
 * furniture. Reading "Chapter Four   17" between every paragraph is the single
 * most irritating thing a PDF reader can do.
 *
 * @param {Line[][]} pages
 * @returns {(line: Line, pageIndex: number) => boolean} true to keep
 */
export function furnitureFilter(pages) {
  if (pages.length < 3) return () => true;

  const counts = new Map();
  for (const lines of pages) {
    // Only the very top and bottom can be a running head or footer. The window
    // widens to two lines only when the page is long enough for that to still
    // exclude the body — on a short page it would swallow the content, and
    // since digits are collapsed to match varying page numbers, body text
    // differing only by a number would look identical across pages.
    const wide = lines.length > 5;
    const edge = lines.filter((_, i) => (
      wide ? (i < 2 || i >= lines.length - 2) : (i === 0 || i === lines.length - 1)
    ));
    for (const l of edge) {
      if (l.text.length > 80) continue; // running heads are short
      const key = l.text.replace(/\d+/g, '#').trim();
      if (key.length < 2) continue;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
  }

  // Repeating on more than half the pages makes it furniture, not content.
  const threshold = Math.max(3, Math.ceil(pages.length * 0.5));
  const furniture = new Set([...counts].filter(([, n]) => n >= threshold).map(([k]) => k));

  return (line) => {
    const key = line.text.replace(/\d+/g, '#').trim();
    if (furniture.has(key)) return false;
    // A line that is only a number is a page number wherever it appears.
    return !/^\s*\d{1,4}\s*$/.test(line.text);
  };
}

/** @param {number[]} xs */
function median(xs) {
  return percentile(xs, 0.5);
}

/** @param {number[]} xs @param {number} p 0..1 */
function percentile(xs, p) {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const i = (s.length - 1) * p;
  const lo = Math.floor(i);
  const hi = Math.ceil(i);
  return lo === hi ? s[lo] : s[lo] + (s[hi] - s[lo]) * (i - lo);
}
