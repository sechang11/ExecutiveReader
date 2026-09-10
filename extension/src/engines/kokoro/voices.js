/**
 * The Kokoro voice catalogue.
 *
 * Voice ids encode language and gender in their first two letters: "af_heart"
 * is American English, female. Each pack is about 510 KB and is fetched on
 * demand, so a user who only ever picks one voice downloads one voice.
 *
 * Which of these we can actually offer depends on the phoneme source. A
 * dictionary-based English grapheme-to-phoneme cannot pronounce Japanese, so
 * the non-English voices are only reachable with a phonemizer that covers their
 * language. `availableFor()` is where that gate lives, rather than in the
 * picker, so the two cannot drift apart.
 */

/** First letter of a voice id to BCP-47. */
const LANGS = {
  a: { code: 'en-US', name: 'American English' },
  b: { code: 'en-GB', name: 'British English' },
  e: { code: 'es', name: 'Spanish' },
  f: { code: 'fr-FR', name: 'French' },
  h: { code: 'hi', name: 'Hindi' },
  i: { code: 'it', name: 'Italian' },
  j: { code: 'ja', name: 'Japanese' },
  p: { code: 'pt-BR', name: 'Portuguese' },
  z: { code: 'zh', name: 'Chinese' },
};

/** Every voice pack published for Kokoro-82M v1.0. */
export const VOICE_IDS = [
  'af', 'af_alloy', 'af_aoede', 'af_bella', 'af_heart', 'af_jessica',
  'af_kore', 'af_nicole', 'af_nova', 'af_river', 'af_sarah', 'af_sky',
  'am_adam', 'am_echo', 'am_eric', 'am_fenrir', 'am_liam', 'am_michael',
  'am_onyx', 'am_puck', 'am_santa',
  'bf_alice', 'bf_emma', 'bf_isabella', 'bf_lily',
  'bm_daniel', 'bm_fable', 'bm_george', 'bm_lewis',
  'ef_dora', 'em_alex', 'em_santa',
  'ff_siwis',
  'hf_alpha', 'hf_beta', 'hm_omega', 'hm_psi',
  'if_sara', 'im_nicola',
  'jf_alpha', 'jf_gongitsune', 'jf_nezumi', 'jf_tebukuro', 'jm_kumo',
  'pf_dora', 'pm_alex', 'pm_santa',
  'zf_xiaobei', 'zf_xiaoni', 'zf_xiaoxiao', 'zf_xiaoyi',
  'zm_yunjian', 'zm_yunxi', 'zm_yunxia', 'zm_yunyang',
];

/** Kokoro's own quality grades, where published. Shown so a user picking a
 *  voice knows which ones were trained on the most data. */
const GRADE = {
  af_heart: 'A', af_bella: 'A-', af_nicole: 'B-', am_fenrir: 'C+',
  am_michael: 'C+', am_puck: 'C+', bf_emma: 'B-', bm_george: 'C',
};

/** @param {string} id */
export function describeVoice(id) {
  const lang = LANGS[id[0]] ?? { code: 'en-US', name: 'Unknown' };
  const gender = id[1] === 'f' ? 'female' : id[1] === 'm' ? 'male' : undefined;
  const bare = id.includes('_') ? id.slice(id.indexOf('_') + 1) : id;
  const name = bare.charAt(0).toUpperCase() + bare.slice(1);
  return {
    key: `kokoro:${id}`,
    nativeId: id,
    engineId: 'kokoro',
    name: `${name} (${lang.name})`,
    lang: lang.code,
    gender,
    grade: GRADE[id],
  };
}

/**
 * Voices we can actually speak, given which languages the phoneme source
 * covers.
 *
 * Offering a Japanese voice that an English-only dictionary will feed English
 * phonemes to produces confident nonsense, which is worse than not offering it.
 *
 * @param {string[]} languages BCP-47 prefixes the phonemizer supports
 */
export function availableFor(languages) {
  const prefixes = languages.map((l) => l.split('-')[0]);
  return VOICE_IDS
    .map(describeVoice)
    .filter((v) => prefixes.includes(v.lang.split('-')[0]));
}
