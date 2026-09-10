/**
 * Offscreen document: owns the audio device, and nothing else.
 *
 * It takes instructions from the service worker and reports back what it heard
 * itself play. It holds no reading state and never decides what comes next,
 * which is what lets the service worker be killed and restarted underneath it
 * without playback noticing.
 *
 * Only buffer-producing engines route through here. The system voice engine
 * speaks through chrome.tts, which is browser-managed and needs no audio graph,
 * so it stays in the service worker. See spec sections 2 and 3.2.
 */

import { Player } from './player.js';
import { kokoroEngine, useG2P, loadVocab } from '../engines/kokoro/index.js';
import * as g2pEn from '../engines/kokoro/g2p-en.js';

/**
 * Neural voices live here rather than in the service worker, because the worker
 * is suspended after about thirty seconds idle and would take an 88 MB session
 * down with it on every pause. The offscreen document survives, so the model
 * loads once per listening session instead of once per sentence.
 *
 * The phoneme source is injected rather than imported by the engine, so the
 * permissive default and any user-installed add-on are interchangeable without
 * touching engine code. See spec 8.1b.
 */
const neuralReady = (async () => {
  const vocab = await fetch(chrome.runtime.getURL('vendor/kokoro/vocab.json')).then((r) => r.json());
  loadVocab(vocab);
  await g2pEn.init(
    chrome.runtime.getURL('vendor/cmudict/cmudict.txt.gz'),
    chrome.runtime.getURL('vendor/cmudict/homographs.json'),
  );
  useG2P(g2pEn);
})().catch((e) => {
  // System voices still work without this; the neural tier simply stays absent.
  console.error('[executive-reader] neural voices unavailable', e);
});

const player = new Player({
  onSentenceStart: (index) => post({ type: 'sentence-start', index }),
  onSentenceEnd: (index) => post({ type: 'sentence-end', index }),
});

/** Messages are addressed, because the service worker broadcasts and this
 *  document must ignore anything meant for the popup or a content script. */
function post(payload) {
  chrome.runtime.sendMessage({ target: 'worker', from: 'offscreen', ...payload })
    .catch(() => { /* worker asleep; it rehydrates from storage on wake */ });
}

/** @param {number[]|ArrayBuffer} raw */
function toFloat32(raw) {
  if (raw instanceof ArrayBuffer) return new Float32Array(raw);
  return Float32Array.from(raw);
}

const handlers = {
  /** Schedule one sentence's audio. Resolves once queued, not once heard. */
  'audio-chunk': ({ index, pcm, sampleRate, playbackRate }) => {
    const { duration } = player.schedule(index, toFloat32(pcm), sampleRate, playbackRate ?? 1);
    return { queued: true, duration, buffered: player.buffered() };
  },

  /** How far ahead we are, so the worker knows whether to synthesize more. */
  'buffered': () => ({ seconds: player.buffered(), playing: player.playing }),

  'set-volume': ({ volume }) => {
    player.setVolume(volume);
    return { ok: true };
  },

  /** Cut playback but keep the device, since another sentence usually follows
   *  immediately — a skip, or a voice change mid-read. */
  'stop': () => {
    player.stop();
    return { ok: true };
  },

  /** Which neural voices this device can offer, and whether weights are here. */
  'neural-voices': async () => {
    await neuralReady;
    // Always report *why* there are none. An empty list with no explanation is
    // the same message for a missing dictionary and a missing model download,
    // and the two have opposite fixes.
    const blocked = await kokoroEngine.blockedBy();
    return {
      voices: blocked?.reason === 'no-model' || !blocked ? await kokoroEngine.voices() : [],
      blocked,
      backend: kokoroEngine.backend(),
    };
  },

  /**
   * Synthesize one sentence with a neural voice and schedule it.
   *
   * The audio never crosses back to the service worker: it is produced and
   * played inside this document, and only timings and progress are reported.
   */
  'neural-speak': async ({ index, text, voiceKey, speed, volume }) => {
    await neuralReady;
    const voice = (await kokoroEngine.voices()).find((v) => v.key === voiceKey);
    if (!voice) throw new Error(`unknown voice ${voiceKey}`);

    player.setVolume(volume ?? 1);
    const controller = new AbortController();
    for await (const chunk of kokoroEngine.synthesize(text, {
      voice, speed: speed ?? 1, volume: volume ?? 1, signal: controller.signal,
    })) {
      if (chunk.kind === 'audio') {
        player.schedule(index, chunk.pcm, chunk.sampleRate);
      } else if (chunk.kind === 'timings') {
        post({ type: 'word-timings', index, words: chunk.words });
      }
    }
    return { ok: true, buffered: player.buffered() };
  },

  /**
   * Download and load the model, reporting progress as it goes.
   *
   * An 88 MB fetch with no feedback is indistinguishable from a hang, and the
   * likely response to a hang is to start it again.
   */
  'neural-prepare': async () => {
    await neuralReady;
    const [voice] = await kokoroEngine.voices();
    if (!voice) throw new Error('no voice to prepare');
    await kokoroEngine.prepare(voice, (loaded, total) => {
      post({ type: 'neural-progress', loaded, total });
    });
    return { ok: true, backend: kokoroEngine.backend() };
  },

  /** Free the downloaded weights. */
  'neural-evict': async () => {
    await kokoroEngine.dispose();
    await kokoroEngine.evict();
    return { ok: true };
  },

  /** Release the device. Ordered teardown lives in Player.dispose(). */
  'shutdown': async () => {
    // Stop the producer before the sink, or a synthesis still in flight writes
    // into a graph that is being torn down. See spec 3.2.
    await kokoroEngine.dispose();
    await player.dispose();
    return { ok: true };
  },
};

chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (msg?.target !== 'offscreen') return false;
  const handler = handlers[msg.type];
  if (!handler) return false;

  Promise.resolve(handler(msg))
    .then(respond, (e) => respond({ error: String(e) }));
  return true; // async
});

// Closing the document without releasing the graph is the ordering bug this
// file exists to avoid, so handle the involuntary close too, not just an
// explicit shutdown.
window.addEventListener('pagehide', () => { player.dispose(); });
