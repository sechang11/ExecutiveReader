/**
 * Kokoro-82M as a TtsEngine.
 *
 * Natural voices that run on the reader's own machine: free, offline, and with
 * no text leaving the browser. This is the reason to switch from the incumbent,
 * whose free tier is robotic and whose good voices are a subscription.
 *
 * Apache-2.0, model and voice packs both. The phoneme source is a separate
 * licensing question — see spec 8.1b — and is injected rather than imported so
 * that the permissive default and any add-on can be swapped without touching
 * this file.
 *
 * @typedef {import('../types').Voice} Voice
 * @typedef {import('../types').SynthOptions} SynthOptions
 * @typedef {import('../types').SynthChunk} SynthChunk
 */

import { loadVocab, tokenize, styleIndex } from './tokenize.js';
import { availableFor, describeVoice } from './voices.js';
import { loadModel, loadVoice, styleFrame, MODEL_BYTES, status, evict } from './model-store.js';

const SAMPLE_RATE = 24_000;

/** Beyond this, output stops sounding natural whatever the engine allows. */
const MAX_NATURAL_SPEED = 3;

/** Voice packs held here rather than in the worker, since slicing a style frame
 *  is cheap and shipping the whole pack across the boundary each time is not. */
const voicePacks = new Map();

/** @type {Worker|null} */ let worker = null;
let nextId = 1;
/** @type {Map<number, {resolve: Function, reject: Function}>} */
const pending = new Map();

let ready = null;
let backend = 'none';

/** The phoneme source. Injected so the licensing choice stays out of here. */
let g2p = null;

/**
 * @param {{phonemize: (t: string) => {ipa: string, misses: string[]},
 *          languages: string[]}} source
 */
export function useG2P(source) {
  g2p = source;
}

function call(type, payload, transfer = []) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, type, payload }, transfer);
  });
}

async function boot(onProgress) {
  if (!worker) {
    worker = new Worker(chrome.runtime.getURL('src/engines/kokoro/worker.js'), { type: 'module' });
    worker.onmessage = (e) => {
      const { id, ok, result, error } = e.data;
      const slot = pending.get(id);
      if (!slot) return;
      pending.delete(id);
      ok ? slot.resolve(result) : slot.reject(new Error(error));
    };
    // A worker that dies takes every in-flight request with it; rejecting them
    // is what stops the read loop hanging on a promise nobody will settle.
    worker.onerror = (e) => {
      for (const [, slot] of pending) slot.reject(new Error(e.message || 'worker failed'));
      pending.clear();
    };
  }

  const model = await loadModel(onProgress);
  const res = await call('init', {
    ortUrl: chrome.runtime.getURL('vendor/onnxruntime/ort.webgpu.bundle.min.mjs'),
    wasmPath: chrome.runtime.getURL('vendor/onnxruntime/'),
    model,
  }, [model]);
  backend = res.backend;
  return res;
}

/** Which words map to which spans, so estimated timings can drive a highlight. */
function wordSpans(text) {
  const spans = [];
  for (const m of text.matchAll(/[\p{L}\p{M}']+/gu)) {
    spans.push({ word: m[0], charStart: m.index, charEnd: m.index + m[0].length });
  }
  return spans;
}

export const kokoroEngine = {
  id: 'kokoro',
  displayName: 'Natural voices (on this device)',
  appliesSpeedInternally: true,
  maxNaturalSpeed: MAX_NATURAL_SPEED,
  // Timings are estimated from phoneme counts, not measured. Saying otherwise
  // would let the caller trust a highlight position it should not.
  supportsWordBoundaries: false,

  /** True once a phoneme source exists; the model downloads on demand. */
  async available() {
    return Boolean(g2p) && typeof Worker !== 'undefined';
  },

  /**
   * Why neural voices cannot be offered, or null when they can.
   *
   * Two absences with opposite fixes must not share one message. Someone who
   * has just waited for an 88 MB download and is told "not available" will
   * download it again; someone missing the dictionary will wait for a model
   * that is already there. Naming the specific absence is the difference
   * between a message that helps and one that misleads. Found by the desktop
   * half, which had the same collapsed message.
   *
   * @returns {Promise<{reason: string, detail: string}|null>}
   */
  async blockedBy() {
    if (typeof Worker === 'undefined') {
      return { reason: 'no-worker', detail: 'This browser cannot run background workers.' };
    }
    if (!g2p) {
      return {
        reason: 'no-dictionary',
        detail: 'The pronunciation dictionary did not load, so neural voices are unavailable. '
          + 'System voices still work.',
      };
    }
    const state = await status();
    if (!state.model) {
      return {
        reason: 'no-model',
        detail: `Natural voices need a one-time ${Math.round(MODEL_BYTES / 1e6)} MB download.`,
      };
    }
    return null;
  },

  /** @returns {Promise<Voice[]>} */
  async voices() {
    if (!g2p) return [];
    const state = await status();
    // Only voices whose language the phoneme source can actually pronounce:
    // feeding English phonemes to a Japanese voice produces confident nonsense.
    return availableFor(g2p.languages).map((v) => ({
      ...v,
      ready: state.model,
      note: state.model ? undefined : `${Math.round(MODEL_BYTES / 1e6)} MB download`,
    }));
  },

  /** @param {Voice} voice */
  async prepare(voice, onProgress) {
    ready ??= boot(onProgress);
    await ready;
    if (!voicePacks.has(voice.nativeId)) {
      const pack = await loadVoice(voice.nativeId);
      voicePacks.set(voice.nativeId, pack);
    }
  },

  /**
   * @param {string} text
   * @param {SynthOptions} opts
   * @returns {AsyncIterable<SynthChunk>}
   */
  synthesize(text, opts) {
    return {
      async *[Symbol.asyncIterator]() {
        await kokoroEngine.prepare(opts.voice);
        if (opts.signal.aborted) return;

        const { ipa, misses } = g2p.phonemize(text);
        if (misses.length) {
          // Not an error. Worth seeing while tuning the dictionary, since these
          // are exactly the words a listener will notice.
          console.debug('[executive-reader] not in dictionary:', misses.join(', '));
        }

        const { ids, dropped, truncated } = tokenize(ipa);
        if (dropped.length) {
          console.warn('[executive-reader] phonemes outside the model alphabet:', dropped.join(''));
        }
        if (!ids.length) return;
        if (truncated) console.warn('[executive-reader] sentence exceeded the model token limit');

        const pack = voicePacks.get(opts.voice.nativeId);
        const style = styleFrame(pack.floats, styleIndex(ids.length, pack.frames));

        const { pcm, timings } = await call('synth', {
          ids,
          style,
          speed: Math.min(Math.max(opts.speed, 0.5), 4),
          words: wordSpans(text),
          sampleRate: SAMPLE_RATE,
        }, [style.buffer]);

        if (opts.signal.aborted) return;
        if (timings.length) yield { kind: 'timings', words: timings };
        yield { kind: 'audio', pcm, sampleRate: SAMPLE_RATE };
      },
    };
  },

  /** Which backend the session actually got, for an honest settings screen. */
  backend: () => backend,

  /** Free the downloaded weights. Someone who tried neural voices and went back
   *  should not be left carrying 88 MB they cannot see or remove. */
  evict,

  async dispose() {
    if (worker) {
      try { await call('dispose', {}); } catch { /* already gone */ }
      worker.terminate();
      worker = null;
    }
    for (const [, slot] of pending) slot.reject(new Error('disposed'));
    pending.clear();
    voicePacks.clear();
    ready = null;
    backend = 'none';
  },
};

export { loadVocab, describeVoice };
