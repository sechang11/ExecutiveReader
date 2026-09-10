/**
 * English grapheme-to-phoneme, permissively licensed.
 *
 * CMUdict (BSD-style) for the 126,000 words it knows, letter-to-sound rules for
 * everything else. This is the default path precisely because it ships nothing
 * copyleft; eSpeak covers more languages and more words but is GPL-3.0, so it
 * is an add-on the user installs rather than something we distribute.
 *
 * Output targets Kokoro's 115-symbol alphabet and deliberately imitates
 * eSpeak's conventions rather than correct notation: stress marks the vowel
 * (`kwˈɪk`, not `ˈkwɪk`) and long vowels carry length. Kokoro was trained on
 * eSpeak-shaped input, and an earlier version of this file that notated stress
 * properly sounded worse. See spec section 8.1a.
 */

/** ARPAbet to Kokoro IPA. Verified against the model alphabet by verify(). */
const ARPA = {
  AA: 'ɑ', AE: 'æ', AH: 'ʌ', AO: 'ɔ', AW: 'aʊ', AY: 'aɪ',
  B: 'b', CH: 'ʧ', D: 'd', DH: 'ð',
  EH: 'ɛ', ER: 'ɚ', EY: 'eɪ',
  F: 'f', G: 'ɡ', HH: 'h',
  IH: 'ɪ', IY: 'i', JH: 'ʤ',
  K: 'k', L: 'l', M: 'm', N: 'n', NG: 'ŋ',
  OW: 'oʊ', OY: 'ɔɪ',
  P: 'p', R: 'ɹ', S: 's', SH: 'ʃ',
  T: 't', TH: 'θ',
  UH: 'ʊ', UW: 'u',
  V: 'v', W: 'w', Y: 'j', Z: 'z', ZH: 'ʒ',
};

/** Vowels eSpeak lengthens under stress, matched for the same reason. */
const LENGTHENED = { AA: 'ɑː', AO: 'ɔː', IY: 'iː', UW: 'uː' };

const VOWELS = new Set(['AA', 'AE', 'AH', 'AO', 'AW', 'AY', 'EH', 'ER', 'EY',
  'IH', 'IY', 'OW', 'OY', 'UH', 'UW']);

/** @type {Map<string, string[]>} */
let DICT = new Map();

export const languages = ['en'];

/** Parse the packed dictionary: one "word PH PH PH" line per entry. */
export function loadDict(text) {
  const dict = new Map();
  for (const line of text.split('\n')) {
    const sp = line.indexOf(' ');
    if (sp < 1) continue;
    dict.set(line.slice(0, sp), line.slice(sp + 1).split(' '));
  }
  DICT = dict;
  return dict.size;
}

/**
 * Fetch and decompress the vendored dictionary and homograph table.
 * @param {string} dictUrl gzipped CMUdict
 * @param {string} homographUrl alternate pronunciations
 */
export async function init(dictUrl, homographUrl) {
  const [dictRes, homRes] = await Promise.all([fetch(dictUrl), fetch(homographUrl)]);
  if (!dictRes.ok) throw new Error(`cmudict: ${dictRes.status}`);
  if (!homRes.ok) throw new Error(`homographs: ${homRes.status}`);

  const text = await new Response(
    dictRes.body.pipeThrough(new DecompressionStream('gzip')),
  ).text();
  loadHomographs(await homRes.json());
  return loadDict(text);
}

// ------------------------------------------------------------- homographs

/**
 * Words a dictionary gets confidently wrong.
 *
 * This is the failure that data cannot fix: "he read the book" comes out as
 * "reed" because a dictionary has no context, and adding entries never helps.
 * Real disambiguation wants part-of-speech tagging. These rules are a cheap
 * approximation covering the cases that actually occur in prose.
 *
 * `noun` fires when the preceding word suggests a noun phrase; `verb` when it
 * suggests a verb. Where neither matches, the dictionary's own entry stands.
 */
const DETERMINERS = new Set(['the', 'a', 'an', 'this', 'that', 'these', 'those',
  'his', 'her', 'its', 'their', 'my', 'your', 'our', 'no', 'any', 'some', 'each',
  'every', 'another', 'one', 'two', 'three', 'more', 'most', 'such']);

const VERB_CUES = new Set(['to', 'will', 'would', 'can', 'could', 'shall',
  'should', 'may', 'might', 'must', 'i', 'we', 'you', 'they', 'he', 'she', 'it',
  'who', 'and', 'or', 'not', "don't", 'please', 'help', 'let']);

const PAST_CUES = new Set(['have', 'has', 'had', 'having', 'already',
  'yesterday', 'once', 'never', 'just']);

/**
 * Noun/verb pairs, as two real pronunciations from the dictionary.
 *
 * An earlier version derived the noun from the verb by moving the stress mark.
 * That is wrong, and audibly so: English reduces unstressed vowels to schwa, so
 * "record" the verb is `R IH0 K AO1 R D` and re-stressing its first vowel gives
 * "ruh-CORD", not "REC-ord". The vowel *quality* changes with stress, not just
 * the mark. CMUdict already carries both readings; the packer was discarding
 * every variant after the first.
 *
 * @type {Record<string, {noun: string[], verb: string[]}>}
 */
let HOMOGRAPHS = {};

/** @param {Record<string, {noun: string[], verb: string[]}>} table */
export function loadHomographs(table) {
  HOMOGRAPHS = table;
}

/** Special cases whose two readings are not a stress shift at all. */
const SPECIAL = {
  read: { past: ['R', 'EH1', 'D'], present: ['R', 'IY1', 'D'] },
  lead: { noun: ['L', 'EH1', 'D'], verb: ['L', 'IY1', 'D'] },
  live: { adj: ['L', 'AY1', 'V'], verb: ['L', 'IH1', 'V'] },
  wind: { noun: ['W', 'IH1', 'N', 'D'], verb: ['W', 'AY1', 'N', 'D'] },
  close: { noun: ['K', 'L', 'OW1', 'S'], verb: ['K', 'L', 'OW1', 'Z'] },
  use: { noun: ['Y', 'UW1', 'S'], verb: ['Y', 'UW1', 'Z'] },
  minute: { noun: ['M', 'IH1', 'N', 'AH0', 'T'], adj: ['M', 'AY0', 'N', 'UW1', 'T'] },
  tear: { noun: ['T', 'IH1', 'R'], verb: ['T', 'EH1', 'R'] },
  bow: { noun: ['B', 'OW1'], verb: ['B', 'AW1'] },
};

/**
 * Choose between readings using the preceding word.
 * @param {string} word lowercased
 * @param {string|null} prev lowercased previous word, or null
 * @returns {string[]|null} ARPAbet phones, or null to use the dictionary
 */
export function disambiguate(word, prev) {
  const s = SPECIAL[word];
  if (s) {
    // "read" is the awkward one: the dictionary's own first entry is the past
    // tense, so leaving it alone gets "please read" wrong. Both readings need
    // an explicit cue, and only a genuinely ambiguous context defers.
    if (word === 'read') {
      if (PAST_CUES.has(prev)) return s.past;
      if (VERB_CUES.has(prev)) return s.present;
      return null;
    }
    if (word === 'live') return DETERMINERS.has(prev) ? s.adj : s.verb;
    if (prev && DETERMINERS.has(prev)) return s.noun ?? null;
    if (prev && VERB_CUES.has(prev)) return s.verb ?? null;
    return null;
  }

  const pair = HOMOGRAPHS[word];
  if (!pair || !prev) return null;
  if (DETERMINERS.has(prev)) return pair.noun;
  if (VERB_CUES.has(prev)) return pair.verb;
  // No cue either way: the dictionary's own entry is better than a guess.
  return null;
}

// ------------------------------------------------------------- conversion

/** @param {string[]} phones */
function toIpa(phones) {
  let out = '';
  for (const p of phones) {
    const bare = p.replace(/\d$/, '');
    const stress = p.match(/(\d)$/)?.[1] ?? null;
    if (stress === '1') out += 'ˈ';
    else if (stress === '2') out += 'ˌ';

    if (bare === 'AH' && stress === '0') out += 'ə'; // unstressed AH is a schwa
    else if (stress !== '0' && LENGTHENED[bare]) out += LENGTHENED[bare];
    else out += ARPA[bare] ?? '';
  }
  return out;
}

/**
 * Letter-to-sound fallback, for words the dictionary lacks.
 *
 * The honest weak point: proper nouns, product names and coinages all land
 * here and get an approximation. Ordered longest-first so digraphs win.
 */
const LTS = [
  ['tion', 'ʃən'], ['sion', 'ʒən'], ['ough', 'ʌf'], ['augh', 'æf'],
  ['tch', 'ʧ'], ['dge', 'ʤ'], ['igh', 'aɪ'], ['ing', 'ɪŋ'],
  ['ch', 'ʧ'], ['sh', 'ʃ'], ['th', 'θ'], ['ph', 'f'], ['wh', 'w'],
  ['ck', 'k'], ['qu', 'kw'], ['ng', 'ŋ'], ['gh', 'ɡ'],
  ['ee', 'iː'], ['ea', 'iː'], ['oo', 'uː'], ['ou', 'aʊ'], ['ow', 'aʊ'],
  ['ai', 'eɪ'], ['ay', 'eɪ'], ['oi', 'ɔɪ'], ['oy', 'ɔɪ'], ['au', 'ɔː'],
  ['a', 'æ'], ['e', 'ɛ'], ['i', 'ɪ'], ['o', 'ɑ'], ['u', 'ʌ'], ['y', 'i'],
  ['b', 'b'], ['c', 'k'], ['d', 'd'], ['f', 'f'], ['g', 'ɡ'], ['h', 'h'],
  ['j', 'ʤ'], ['k', 'k'], ['l', 'l'], ['m', 'm'], ['n', 'n'], ['p', 'p'],
  ['r', 'ɹ'], ['s', 's'], ['t', 't'], ['v', 'v'], ['w', 'w'], ['x', 'ks'],
  ['z', 'z'],
];

/**
 * Letters that survive neither Unicode decomposition nor an ASCII lookup.
 *
 * Stripping combining marks turns "é" into "e", but "ł" and "ø" carry their
 * stroke inside the codepoint and decompose to themselves. Left alone they fall
 * out of a word-character match and split the word: "Skłodowska" arrived as
 * "Sk" and "odowska", two fragments each given a confident wrong pronunciation.
 */
const FOLD = {
  ł: 'l', Ł: 'l', ø: 'o', Ø: 'o', đ: 'd', Đ: 'd', ð: 'th', Ð: 'th',
  þ: 'th', Þ: 'th', ß: 'ss', æ: 'ae', Æ: 'ae', œ: 'oe', Œ: 'oe', ı: 'i',
};

/**
 * Reduce a word to the ASCII letters the dictionary and the rules understand.
 * @param {string} word
 */
export function fold(word) {
  return word
    .normalize('NFD')
    .replace(/\p{M}+/gu, '') // é -> e, ñ -> n
    .split('')
    .map((ch) => FOLD[ch] ?? ch)
    .join('')
    .toLowerCase();
}

/** @param {string} word lowercased */
export function sound(word) {
  let out = '';
  let i = 0;
  while (i < word.length) {
    // Trailing silent e: "name" is not "nameh".
    if (i === word.length - 1 && word[i] === 'e' && word.length > 3) break;
    const hit = LTS.find(([g]) => word.startsWith(g, i));
    if (hit) { out += hit[1]; i += hit[0].length; } else i++;
  }
  return out ? `ˈ${out}` : '';
}

/**
 * @param {string} text already normalized for speech
 * @returns {{ipa: string, misses: string[]}}
 */
export function phonemize(text) {
  const misses = [];
  const out = [];
  // Unicode-aware: an ASCII-only match splits any word carrying a diacritic
  // into fragments and pronounces each one, which is worse than mispronouncing
  // the whole word.
  const tokens = text.match(/[\p{L}\p{M}']+|[.,;:!?—…"()]/gu) ?? [];
  let prev = null;

  for (const tok of tokens) {
    if (/^[.,;:!?—…"()]$/.test(tok)) { out.push(tok); continue; }
    const key = fold(tok);
    if (!key) continue; // a script we cannot fold to letters at all

    const forced = disambiguate(key, prev);
    const phones = forced ?? DICT.get(key);
    if (phones) out.push(toIpa(phones));
    else { misses.push(tok); out.push(sound(key)); }
    prev = key;
  }

  return { ipa: out.join(' ').replace(/\s+([.,;:!?…])/g, '$1'), misses };
}

/** Every symbol we can emit must exist in the model's alphabet, or it is
 *  dropped without complaint and the word quietly loses a sound. */
export function verify(vocab) {
  const bad = new Set();
  const all = [...Object.values(ARPA), ...Object.values(LENGTHENED),
    ...LTS.map((r) => r[1]), 'ə', 'ˈ', 'ˌ'];
  for (const v of all) for (const ch of v) if (!(ch in vocab)) bad.add(ch);
  return [...bad];
}
