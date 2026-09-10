/**
 * Turn extracted text into speakable text, driven entirely by
 * shared/normalization.json.
 *
 * The Python side implements the same four conditions in
 * desktop/src/earmark/textproc/symbols.py. Neither implementation may invent
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

/** @param {any} data parsed shared/normalization.json */
export function loadNormalization(data) {
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
  return applySymbols(applyCurrency(applyExpansions(text)));
}
