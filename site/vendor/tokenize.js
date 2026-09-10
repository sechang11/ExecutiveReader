/**
 * Kokoro's phoneme tokenizer.
 *
 * The model's alphabet is 115 symbols and tokenization is character-level over
 * it, with two properties that are easy to get wrong and silent when you do:
 *
 * 1. Symbols outside the alphabet are dropped, not rejected. A bad
 *    grapheme-to-phoneme mapping does not fail — the word just loses a sound
 *    and comes out subtly mangled. So dropping is counted and reported.
 * 2. Affricates are ligatures. The alphabet has ʧ and ʤ as single symbols but
 *    no "tʃ" or "dʒ", and no diphthong tokens at all: "oʊ" is o followed by ʊ,
 *    both of which are in the alphabet, so it tokenizes correctly by accident.
 *    "tʃ" also tokenizes, as t then ʃ, which is a different sound. Normalizing
 *    to the ligature is therefore not cosmetic.
 */

/** Longest sequence the model accepts, before the two boundary tokens. */
export const MAX_TOKENS = 508;

/** Two-character forms that must become their single-symbol equivalents. */
const LIGATURES = [
  ['tʃ', 'ʧ'], ['dʒ', 'ʤ'],
  ['ts', 'ʦ'], ['dz', 'ʣ'],
  ['tɕ', 'ʨ'], ['dʑ', 'ʥ'],
];

/** @type {Record<string, number>|null} */
let VOCAB = null;

/** @param {Record<string, number>} vocab from vendor/kokoro/vocab.json */
export function loadVocab(vocab) {
  VOCAB = vocab;
}

/** @param {string} ipa */
export function normalizePhonemes(ipa) {
  let out = ipa;
  for (const [from, to] of LIGATURES) out = out.split(from).join(to);
  return out;
}

/**
 * @param {string} ipa
 * @returns {{ids: number[], dropped: string[], truncated: boolean}}
 */
export function tokenize(ipa) {
  if (!VOCAB) throw new Error('tokenize() before loadVocab()');

  const ids = [];
  const dropped = [];
  for (const ch of normalizePhonemes(ipa)) {
    const id = VOCAB[ch];
    if (id !== undefined) ids.push(id);
    else if (!/\s/.test(ch)) dropped.push(ch);
    // Whitespace outside the alphabet is a word gap we can safely lose; a
    // missing consonant is not, which is why the two are treated differently.
  }

  const truncated = ids.length > MAX_TOKENS;
  return { ids: truncated ? ids.slice(0, MAX_TOKENS) : ids, dropped, truncated };
}

/**
 * Wrap with the boundary tokens the model expects.
 * @param {number[]} ids
 */
export function withBoundaries(ids) {
  return [0, ...ids, 0];
}

/**
 * Which prosody frame to take from a voice pack.
 *
 * A voice file is 510 frames of 256 floats, indexed by how many phoneme tokens
 * are being spoken, so a long sentence is delivered with different pacing from
 * a short one. The index is the token count *before* the boundary tokens are
 * added; using the wrapped length shifts every utterance by one frame, which
 * is not obviously wrong when you hear it and is wrong.
 *
 * @param {number} tokenCount
 * @param {number} frames
 */
export function styleIndex(tokenCount, frames) {
  return Math.max(0, Math.min(tokenCount, frames - 1));
}
