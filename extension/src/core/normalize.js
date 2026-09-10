/**
 * Turn extracted text into speakable text, driven entirely by
 * shared/normalization.json.
 *
 * The Python side implements the same four conditions in
 * desktop/src/executive_reader/textproc/symbols.py. Neither implementation may invent
 * behaviour the schema does not describe, because the whole point of the shared
 * file is that both halves pronounce the same page the same way.
 *
 * Pipeline order is fixed and matters: expansions, then currency, then symbols,
 * then (elsewhere) the pronunciation dictionary. Expansions run first because
 * they decide meaning; pronunciation runs last because it decides sound.
 */

/** @typedef {{match: string, say: string, matchCase?: boolean}} Expansion */
/** @typedef {{symbol: string, say: string, when: string}} SymbolRule */
/** @typedef {{symbol: string, singular: string, plural: string}} CurrencyRule */

/** @type {Expansion[]} */ let EXPANSIONS = [];
/** @type {Map<string, SymbolRule>} */ let SYMBOLS = new Map();
/** @type {Map<string, CurrencyRule>} */ let CURRENCY = new Map();

const isWordChar = (c) => c !== undefined && /[A-Za-z0-9]/.test(c);
const isSpace = (c) => c !== undefined && /\s/.test(c);
const isDigit = (c) => c !== undefined && /[0-9]/.test(c);

/**
 * Typographic cleanup, defined by `collapse` and `_collapse_rules`.
 *
 * This section sat in the shared file, set to true, read by neither half, for
 * the whole of the project so far. What it cost is not subtle: the phonemizer
 * looks words up in CMUdict by literal text, so a curly apostrophe or a soft
 * hyphen inside a word misses the entry and the fallback letter rules take
 * over. Measured on the shipped dictionary:
 *
 *     don’t         -> dˈɑːn tˈiː     ("dawn tee")
 *     it’s          -> ˈɪt ˈɛs        ("it ess")
 *     won{shy}derful-> wˈʌn ˈdɛɹfʌl   ("wun DERful")
 *
 * Nearly every professionally typeset page uses curly apostrophes, so this was
 * most contractions on most articles, in the neural voices that are the whole
 * pitch. It was found by the desktop half's conformance harness noticing that
 * the two normalizers disagreed — which is exactly the value of the harness,
 * since neither side's own tests could see it.
 *
 * Order is part of the contract; see `_collapse_rules._order`.
 * @type {Record<string, any>}
 */
let COLLAPSE = {};

/** Characters that are invisible and split a word for anything matching text. */
const ZERO_WIDTH = /[​‌‍⁠﻿]/g;
const SOFT_HYPHEN = /­/g;
const DOUBLE_QUOTES = /[“”„‟″]/g;
const SINGLE_QUOTES = /[‘’‚‛′]/g;
const HARD_SPACES = /[   ]/g;
const PLAIN_DASHES = /[‒–―]/g;
/** Spaces either side are absorbed, or "left — quickly" becomes "left , quickly"
 *  with the comma floating away from the word it belongs to. */
const EM_DASH = /[ \t]*—[ \t]*/g;
const SUPERSCRIPT_DIGITS = /[¹²³⁰-⁹]/g;
/** A bracketed footnote marker. Three digits at most, so an array index or a
 *  bracketed year is less likely to be caught. */
const BRACKETED_MARKER = /\[\s*\d{1,3}\s*\]/g;
/**
 * Four or more periods, optionally spaced: a table-of-contents leader.
 *
 * The spaces are the ones BETWEEN the dots. An earlier version also swallowed
 * the whitespace after the final dot, which is one more character than
 * "separated by" describes, and it left this half a space short of the other at
 * this stage. The end-of-pipeline tidy hid the difference in the finished text,
 * so only a stage-by-stage comparison could see it.
 */
const DOT_LEADER = /\.(?:[ \t]*\.){3,}/g;

/**
 * Exactly three periods is an ellipsis; two is a typo.
 *
 * Four or more never reach here — `dot_leaders` has already turned them into a
 * space, which is the whole reason it runs first. Bang and question runs still
 * collapse to one of themselves.
 */
const THREE_PERIODS = /\.{3}/g;
const TWO_PERIODS = /\.{2}/g;
const REPEATED_BANG = /([!?])\1+/g;
const EMOJI = /\p{Extended_Pictographic}/gu;

/** @param {string} text */
export function applyCollapse(text) {
  let out = text;
  if (COLLAPSE.zero_width) out = out.replace(ZERO_WIDTH, '');
  if (COLLAPSE.soft_hyphens) out = out.replace(SOFT_HYPHEN, '');
  if (COLLAPSE.smart_quotes_to_plain) {
    out = out.replace(DOUBLE_QUOTES, '"').replace(SINGLE_QUOTES, "'");
  }
  if (COLLAPSE.nbsp_to_space) out = out.replace(HARD_SPACES, ' ');
  if (COLLAPSE.dashes_to_plain) out = out.replace(PLAIN_DASHES, '-');
  // Not cleanup. The engines have no phoneme for a dash and drop it in
  // silence, so the pause the author wrote disappears; a comma restores it.
  if (COLLAPSE.em_dash_to_comma) out = out.replace(EM_DASH, ', ');
  if (COLLAPSE.footnote_markers) {
    out = out.replace(SUPERSCRIPT_DIGITS, '').replace(BRACKETED_MARKER, '');
  }
  // Before repeated_punctuation, which would otherwise turn a leader into an
  // ellipsis and read it as a trailing-off in the middle of a contents page.
  if (COLLAPSE.dot_leaders) out = out.replace(DOT_LEADER, ' ');
  if (COLLAPSE.repeated_punctuation) {
    // Not tidying. Measured on five sentences on Microsoft David, the default
    // Windows voice, collapsing an authored "..." to a period costs about half
    // a second of extra pause every time, turning a trailing-off into a firmer
    // stop than was written. That voice cannot tell "..." from U+2026 at all,
    // so the gain is from no longer collapsing to a period rather than from the
    // character; U+2026 is the target because Kokoro has a real token for it
    // and the system voices are indifferent.
    out = out.replace(THREE_PERIODS, '…').replace(TWO_PERIODS, '.').replace(REPEATED_BANG, '$1');
  }
  // Anything the symbols or currency lists speak is not an emoji, whatever
  // Unicode says. ©, ® and ™ are all Extended_Pictographic, so the obvious
  // implementation deleted them — and with them the words "copyright",
  // "registered" and "trademark" that the symbols list exists to produce.
  // Caught by the cross-language harness the moment this stage was compared,
  // having passed every test on this side.
  if (COLLAPSE.emoji === 'skip') {
    out = out.replace(EMOJI, (ch) => (SYMBOLS.has(ch) || CURRENCY.has(ch) ? ch : ''));
  }
  return out;
}

/** @param {any} data parsed shared/normalization.json */
export function loadNormalization(data) {
  COLLAPSE = data.collapse ?? {};
  // Longest first, so "w/o" wins over "w/" and "et al." over any prefix.
  EXPANSIONS = [...(data.expansions ?? [])]
    .sort((a, b) => b.match.length - a.match.length);

  SYMBOLS = new Map((data.symbols ?? []).map((s) => [s.symbol, s]));
  CURRENCY = new Map((data.currency ?? []).map((c) => [c.symbol, c]));
}

/**
 * A match is rejected when a letter or digit sits immediately either side of
 * it. This replaces the word-boundary anchors the old regex entries carried:
 * it stops "w/" firing inside "w/o", and "->" firing inside "node->next".
 * @param {string} text @param {number} start @param {number} end
 */
function boundariesOk(text, start, end) {
  return !isWordChar(text[start - 1]) && !isWordChar(text[end]);
}

/** @param {string} text */
export function applyExpansions(text) {
  let out = '';
  let i = 0;

  outer: while (i < text.length) {
    for (const rule of EXPANSIONS) {
      const end = i + rule.match.length;
      const slice = text.slice(i, end);
      const hit = rule.matchCase
        ? slice === rule.match
        : slice.toLowerCase() === rule.match.toLowerCase();
      if (!hit || !boundariesOk(text, i, end)) continue;
      out += rule.say;
      i = end;
      continue outer;
    }
    out += text[i];
    i++;
  }
  return out;
}

/**
 * "$50" speaks as "50 dollars": the symbol is a prefix in text but a suffix in
 * speech, which is why currency cannot ride the plain substitution path.
 * @param {string} text
 */
export function applyCurrency(text) {
  let out = '';
  let i = 0;

  while (i < text.length) {
    const rule = CURRENCY.get(text[i]);
    if (!rule) { out += text[i]; i++; continue; }

    // Read the amount that follows, allowing thousands separators and decimals.
    let j = i + 1;
    while (isSpace(text[j])) j++;
    const numStart = j;
    while (j < text.length && /[0-9.,]/.test(text[j])) j++;
    // A trailing separator belongs to the sentence, not the number.
    while (j > numStart && /[.,]/.test(text[j - 1])) j--;

    if (j === numStart) { out += text[i]; i++; continue; } // lone symbol

    const amount = text.slice(numStart, j);
    const value = Number(amount.replace(/,/g, ''));
    const unit = value === 1 ? rule.singular : rule.plural;
    out += `${amount} ${unit}`;
    i = j;
  }
  return out;
}

/**
 * @param {string} text @param {number} i index of the symbol
 * @param {string} when condition from _symbol_conditions
 */
function conditionMet(text, i, when) {
  switch (when) {
    case 'always':
      return true;
    case 'isolated':
      return (i === 0 || isSpace(text[i - 1]))
        && (i === text.length - 1 || isSpace(text[i + 1]));
    case 'digit-before': {
      let j = i - 1;
      while (j >= 0 && isSpace(text[j])) j--;
      return isDigit(text[j]);
    }
    case 'digit-after': {
      let j = i + 1;
      while (j < text.length && isSpace(text[j])) j++;
      return isDigit(text[j]);
    }
    default:
      // An unknown condition means this file is newer than this code. Doing
      // nothing is the safe reading; expanding blindly is not.
      return false;
  }
}

/**
 * Per `_output_contract`: collapse runs of spaces and tabs, leave newlines,
 * trim the ends. This is what lets the Python and JavaScript implementations be
 * compared for equality rather than merely for equivalence.
 * @param {string} text
 */
export function tidy(text) {
  return text.replace(/[ \t]+/g, ' ').replace(/^[ \t]+|[ \t]+$/g, '');
}

/** @param {string} text */
export function applySymbols(text) {
  let out = '';
  for (let i = 0; i < text.length; i++) {
    const rule = SYMBOLS.get(text[i]);
    if (!rule || !conditionMet(text, i, rule.when)) { out += text[i]; continue; }

    // Pad both sides, then let tidy() sort out the doubles. Padding
    // unconditionally is what the Python side does, and matching it here is
    // what makes the two byte-identical.
    out += ` ${rule.say} `;
  }
  return tidy(out);
}

/** The full pipeline, in the order the schema mandates. @param {string} text */
export function normalize(text) {
  // Collapse first, and it has to be first: everything after it matches literal
  // text, and so does the pronunciation dictionary after that. A curly
  // apostrophe inside a word defeats all of them.
  return applySymbols(applyCurrency(applyExpansions(applyCollapse(text))));
}
