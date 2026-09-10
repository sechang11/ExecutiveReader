/**
 * Sentence segmentation.
 *
 * This is deliberately not a regex split on [.!?]. Getting boundaries wrong is
 * the most audible bug a reader can have: a false break mid-sentence produces a
 * jarring stop, and a missed break produces a run-on that never lets the
 * listener breathe. The rules below come from `shared/abbreviations.json`,
 * which the Python side reads too, so the two halves agree.
 *
 * Contract: the concatenation of every returned `text`, interleaved with the
 * gaps the offsets imply, reconstructs the input exactly. Callers rely on the
 * offsets to map sentences back to DOM Ranges, so they must be exact.
 */

/** @typedef {{ text: string, start: number, end: number }} Sentence */

const TERMINATORS = new Set(['.', '!', '?', '…', '。', '！', '？']);
const CLOSERS = new Set(['"', "'", ')', ']', '}', '”', '’', '»', '›']);

/**
 * Abbreviations split into two classes, because they behave differently and
 * conflating them is what makes naive segmenters break on "Dr. Chen".
 *
 * `NEVER` are abbreviations that are essentially always followed by more of the
 * same sentence: titles and ranks precede a name, so a capital after them is
 * expected rather than evidence of a new sentence.
 *
 * `MAYBE` are abbreviations that can legitimately end a sentence — "at 5 p.m."
 * or "the deal closed with Acme Inc." — so for these a following capital does
 * mean a boundary.
 *
 * Both are lower-cased with any trailing dot stripped.
 */
let NEVER = new Set();
let MAYBE = new Set();

/** Groups in shared/abbreviations.json whose members never end a sentence. */
const NEVER_GROUPS = new Set(['titles', 'military']);

/**
 * Whether a leading "1." is a list marker rather than a sentence.
 *
 * See `_list_markers` in shared/abbreviations.json. Both halves segment one
 * block or line at a time, so position zero is exactly where a list marker
 * sits and nowhere else, which is what makes the rule safe.
 */
let LIST_DIGITS_AT_START = false;
let LIST_LETTER_AT_START = false;

/** @param {Record<string, unknown>} data parsed shared/abbreviations.json */
export function loadAbbreviations(data) {
  const markers = /** @type {any} */ (data.list_markers) ?? {};
  LIST_DIGITS_AT_START = Boolean(markers.digits_at_start);
  LIST_LETTER_AT_START = Boolean(markers.single_letter_at_start);
  const never = new Set();
  const maybe = new Set();
  for (const [key, list] of Object.entries(data)) {
    if (key.startsWith('_') || key === 'notes' || !Array.isArray(list)) continue;
    const target = NEVER_GROUPS.has(key) ? never : maybe;
    for (const item of list) target.add(String(item).toLowerCase().replace(/\.+$/, ''));
  }
  NEVER = never;
  MAYBE = maybe;
}

/**
 * Classify the period at `i`.
 *
 * - `never`    it is inside a token; do not break, whatever follows
 * - `maybe`    it is an abbreviation that could also end a sentence
 * - `boundary` it is an ordinary full stop
 *
 * Ordered cheapest test first; this runs at every candidate boundary.
 * @param {string} text
 * @param {number} i index of the '.' in text
 * @returns {'never'|'maybe'|'boundary'}
 */
function classifyPeriod(text, i) {
  // 3.14, or a version like 2.1.3 — a digit either side is never a boundary.
  if (/\d/.test(text[i - 1] ?? '') && /\d/.test(text[i + 1] ?? '')) return 'never';

  // Walk back over the word attached to this period.
  let s = i;
  while (s > 0 && /[^\s]/.test(text[s - 1])) s--;
  const word = text.slice(s, i);
  const lower = word.toLowerCase();

  // A list marker: digits only, and sitting at the very start of the text
  // being segmented. Without this every numbered list is spoken as "one."
  // pause "First item" — the pause landing between the number and the thing
  // it numbers. Anchored at the start so it can never fire mid-sentence;
  // "Step 1. Preheat" therefore still splits, which is the price and is
  // recorded as a known limit in the rules file.
  // The letter half is not covered by the initials rule below: that only ever
  // matched a CAPITAL, because it was written for "J. R. R. Tolkien". Widening
  // it would also stop splitting a sentence that genuinely ends in a lone
  // letter, so this stays anchored at the start instead.
  if (s === 0 && LIST_DIGITS_AT_START && /^\d+$/.test(word)) return 'never';
  if (s === 0 && LIST_LETTER_AT_START && /^[A-Za-z]$/.test(word)) return 'never';

  // A single capital is an initial: "J. R. R. Tolkien". The next initial is
  // also capitalised, so this must outrank the new-sentence check.
  if (/^[A-Z]$/.test(word)) return 'never';

  if (NEVER.has(lower)) return 'never';

  // Dotted acronyms — U.S., e.g., p.m., Ph.D — still hold interior dots here.
  if (/^(?:[A-Za-z]\.)+[A-Za-z]?$/.test(word)) return 'maybe';

  if (MAYBE.has(lower)) return 'maybe';

  return 'boundary';
}

/**
 * True when what follows `i` looks like the start of a new sentence. Used to
 * rescue boundaries that the abbreviation list would otherwise suppress, so
 * "...at 5 p.m. The train left." still breaks correctly.
 * @param {string} text
 * @param {number} i index just past the terminator and its closers
 */
function startsNewSentence(text, i) {
  let j = i;
  while (j < text.length && /\s/.test(text[j])) j++;
  if (j >= text.length) return true;
  const ch = text[j];
  // An opening quote or bracket then a capital also begins a sentence.
  if (/["'“‘(\[]/.test(ch)) return /[A-ZÀ-Þ]/.test(text[j + 1] ?? '');
  return /[A-ZÀ-Þ\d]/.test(ch);
}

/**
 * Split `text` into sentences.
 *
 * @param {string} text
 * @param {{maxChars?: number}} [opts] Long sentences are split at clause
 *   boundaries so playback can start before the whole thing is synthesized.
 *   This matters most for neural engines, where latency scales with length.
 * @returns {Sentence[]}
 */
export function segment(text, opts = {}) {
  const maxChars = opts.maxChars ?? 320;
  /** @type {Sentence[]} */
  const out = [];
  let start = 0;

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (!TERMINATORS.has(ch)) continue;

    if (ch === '.') {
      const kind = classifyPeriod(text, i);
      if (kind === 'never') continue;
      // An abbreviation that *could* end a sentence only does so when what
      // follows actually looks like a new one.
      if (kind === 'maybe' && !startsNewSentence(text, i + 1)) continue;
    }

    // Absorb runs of terminators ("?!") and any closing quotes or brackets.
    let end = i + 1;
    while (end < text.length && TERMINATORS.has(text[end])) end++;
    while (end < text.length && CLOSERS.has(text[end])) end++;

    // A terminator with no whitespace after it is usually inside a token,
    // like a filename or a URL path.
    if (end < text.length && !/\s/.test(text[end])) continue;

    push(out, text, start, end, maxChars);
    start = end;
    i = end - 1;
  }

  if (start < text.length) push(out, text, start, text.length, maxChars);
  return out;
}

/**
 * Trim to the non-space span, then split if it is too long to start quickly.
 * @param {Sentence[]} out
 * @param {string} text
 * @param {number} rawStart
 * @param {number} rawEnd
 * @param {number} maxChars
 */
function push(out, text, rawStart, rawEnd, maxChars) {
  let s = rawStart;
  let e = rawEnd;
  while (s < e && /\s/.test(text[s])) s++;
  while (e > s && /\s/.test(text[e - 1])) e--;
  if (s >= e) return;

  if (e - s <= maxChars) {
    out.push({ text: text.slice(s, e), start: s, end: e });
    return;
  }

  for (const [cs, ce] of splitLong(text, s, e, maxChars)) {
    out.push({ text: text.slice(cs, ce), start: cs, end: ce });
  }
}

/**
 * Break an over-long sentence at the most natural pause available, preferring
 * semicolons and colons, then commas, then any space. Never mid-word.
 * @returns {Array<[number, number]>}
 */
function splitLong(text, start, end, maxChars) {
  /** @type {Array<[number, number]>} */
  const spans = [];
  let s = start;

  while (end - s > maxChars) {
    const limit = s + maxChars;
    let cut = -1;
    for (const re of [/[;:]\s/g, /,\s/g, /\s/g]) {
      re.lastIndex = s;
      let m;
      while ((m = re.exec(text)) && m.index < limit) cut = m.index + m[0].length;
      if (cut > s) break;
      cut = -1;
    }
    if (cut <= s) cut = limit; // pathological input, e.g. no spaces at all
    spans.push([s, cut]);
    s = cut;
    while (s < end && /\s/.test(text[s])) s++;
  }

  if (s < end) spans.push([s, end]);
  return spans;
}
