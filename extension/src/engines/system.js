/**
 * System voices, via chrome.tts.
 *
 * This engine is the odd one out: it never hands us audio. Chrome speaks on its
 * own and reports progress, so we emit an `external` chunk and then a stream of
 * `timings` as word events arrive. Everything downstream is written against
 * that shape, which is exactly why the interface models synthesis as a stream
 * rather than a promise of a buffer.
 *
 * On Windows the Microsoft Natural voices installed through Settings appear
 * here automatically and are markedly better than the legacy SAPI ones, so the
 * onboarding points users at them. Costs us nothing and lifts the free tier.
 *
 * @typedef {import('./types').Voice} Voice
 * @typedef {import('./types').SynthOptions} SynthOptions
 * @typedef {import('./types').SynthChunk} SynthChunk
 */

const ID = 'system';

/** chrome.tts rate is 0.1–10.0 with 1.0 normal, so speed maps straight across. */
function clampRate(speed) {
  return Math.min(Math.max(speed, 0.1), 10);
}

/** @returns {Promise<chrome.tts.TtsVoice[]>} */
function nativeVoices() {
  return new Promise((resolve) => chrome.tts.getVoices((v) => resolve(v ?? [])));
}

export const systemEngine = {
  id: ID,
  displayName: 'System voices',
  appliesSpeedInternally: true,
  maxNaturalSpeed: 4,
  supportsWordBoundaries: true,

  async available() {
    return typeof chrome !== 'undefined' && !!chrome.tts;
  },

  /** @returns {Promise<Voice[]>} */
  async voices() {
    const raw = await nativeVoices();
    return raw
      .filter((v) => v.voiceName)
      .map((v) => ({
        key: `${ID}:${v.voiceName}`,
        nativeId: v.voiceName,
        engineId: ID,
        name: v.voiceName,
        lang: v.lang ?? 'en-US',
        gender: v.gender === 'male' || v.gender === 'female' ? v.gender : undefined,
        ready: true,
        note: v.remote ? 'needs a connection' : undefined,
      }));
  },

  async prepare() {
    // Nothing to download. Present so callers need not special-case engines.
  },

  /**
   * @param {string} text
   * @param {SynthOptions} opts
   * @returns {AsyncIterable<SynthChunk>}
   */
  synthesize(text, opts) {
    /** @type {SynthChunk[]} */
    const pending = [];
    let notify = null;
    let finished = false;
    /** @type {Error|null} */
    let failure = null;

    const wake = () => { const n = notify; notify = null; n?.(); };

    const push = (chunk) => { pending.push(chunk); wake(); };
    const finish = (err) => { failure = err ?? null; finished = true; wake(); };

    chrome.tts.speak(text, {
      voiceName: opts.voice.nativeId,
      lang: opts.voice.lang,
      rate: clampRate(opts.speed),
      volume: Math.min(Math.max(opts.volume, 0), 1),
      // Every sentence is queued as its own utterance and we sequence them
      // ourselves, so a stale one must never survive a skip.
      enqueue: false,
      onEvent: (e) => {
        switch (e.type) {
          case 'start':
            push({ kind: 'external' });
            break;
          case 'word': {
            const charStart = e.charIndex ?? 0;
            // `length` is absent on older voices; fall back to the next word
            // break so the highlight still covers a whole word.
            const charEnd = e.length != null
              ? charStart + e.length
              : nextBreak(text, charStart);
            push({ kind: 'timings', words: [{ charStart, charEnd, timeStart: 0 }] });
            break;
          }
          case 'end':
            finish(null);
            break;
          case 'interrupted':
          case 'cancelled':
            finish(null);
            break;
          case 'error':
            finish(new Error(e.errorMessage || 'speech failed'));
            break;
          default:
            break;
        }
      },
    });

    const onAbort = () => { chrome.tts.stop(); finish(null); };
    opts.signal.addEventListener('abort', onAbort, { once: true });

    return {
      async *[Symbol.asyncIterator]() {
        try {
          while (true) {
            while (pending.length) yield pending.shift();
            if (finished) {
              if (failure) throw failure;
              return;
            }
            await new Promise((r) => { notify = r; });
          }
        } finally {
          opts.signal.removeEventListener('abort', onAbort);
        }
      },
    };
  },

  async dispose() {
    chrome.tts.stop();
  },
};

/** @param {string} text @param {number} from */
function nextBreak(text, from) {
  const m = /[\s.,;:!?]/.exec(text.slice(from));
  return m ? from + m.index : text.length;
}
