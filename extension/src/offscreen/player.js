/**
 * Gapless sentence player over Web Audio.
 *
 * Engines that hand us buffers (Kokoro now, the desktop bridge later) need
 * something that schedules them back to back with no seam, reports progress, and
 * tears down in the right order. `chrome.tts` bypasses this entirely because it
 * speaks on its own; that path only reports progress.
 *
 * Teardown ordering is the hazard here and it is not a tidiness concern. Stop
 * the producer, await it, and only then release the sink. The desktop half hit
 * the mirror image of this bug: it closed its audio device while a write was
 * still in flight and segfaulted at exit, having already logged that shutdown
 * was clean. A browser will not segfault, but the same order gives
 * InvalidStateError on a closed context and audio that outlives stop. Nothing
 * that mocks the sink will ever exercise it. See spec 3.2.
 */

/** Scheduling lead. Long enough to survive a slow synthesis, short enough that
 *  stopping feels immediate, since queued audio must drain or be cut. */
const LOOKAHEAD_S = 0.25;

export class Player {
  constructor({ onSentenceStart, onSentenceEnd, onProgress } = {}) {
    /** @type {AudioContext|null} */
    this.ctx = null;
    /** @type {GainNode|null} */
    this.gain = null;
    /** Sources still scheduled or playing, so stop() can cut them. */
    this.live = new Set();
    /** When the next buffer should start, in AudioContext time. */
    this.cursor = 0;
    this.playing = false;
    this.volume = 1;
    /** Resolves when the last scheduled buffer has finished. */
    this.drained = Promise.resolve();

    this.onSentenceStart = onSentenceStart ?? (() => {});
    this.onSentenceEnd = onSentenceEnd ?? (() => {});
    this.onProgress = onProgress ?? (() => {});
  }

  /** Created lazily: an AudioContext opened before the user acts may start
   *  suspended, and holding one open keeps the offscreen document alive. */
  ensureContext() {
    if (this.ctx && this.ctx.state !== 'closed') return this.ctx;
    this.ctx = new AudioContext();
    this.gain = this.ctx.createGain();
    this.gain.gain.value = this.volume;
    this.gain.connect(this.ctx.destination);
    this.cursor = this.ctx.currentTime;
    return this.ctx;
  }

  setVolume(v) {
    this.volume = Math.min(Math.max(v, 0), 1);
    if (this.gain) this.gain.gain.value = this.volume;
  }

  /**
   * Schedule one sentence's audio. Returns when it has been *scheduled*, not
   * when it has been heard, so the caller can keep synthesizing ahead.
   *
   * @param {number} index sentence index, echoed back in the callbacks
   * @param {Float32Array} pcm mono samples in [-1, 1]
   * @param {number} sampleRate
   * @param {number} playbackRate 1 unless the engine could not apply speed
   *   itself, in which case this resamples and the pitch rises with it
   */
  schedule(index, pcm, sampleRate, playbackRate = 1) {
    const ctx = this.ensureContext();

    const buffer = ctx.createBuffer(1, pcm.length, sampleRate);
    buffer.copyToChannel(pcm, 0);

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.playbackRate.value = playbackRate;
    source.connect(this.gain);

    // Never schedule in the past: a slow synthesis can leave the cursor behind
    // the clock, and a past start time plays immediately and overlaps whatever
    // is already sounding.
    const startAt = Math.max(this.cursor, ctx.currentTime + LOOKAHEAD_S);
    source.start(startAt);

    const duration = buffer.duration / playbackRate;
    this.cursor = startAt + duration;
    this.playing = true;
    this.live.add(source);

    const finished = new Promise((resolve) => {
      source.onended = () => {
        this.live.delete(source);
        source.disconnect();
        resolve();
      };
    });
    this.drained = finished;

    // Fire the start callback when this sentence actually becomes audible,
    // not when it was queued, so highlighting tracks the voice.
    const delayMs = Math.max(0, (startAt - ctx.currentTime) * 1000);
    const startTimer = setTimeout(() => this.onSentenceStart(index), delayMs);

    finished.then(() => {
      clearTimeout(startTimer);
      if (this.live.size === 0) this.playing = false;
      this.onSentenceEnd(index);
    });

    return { startAt, duration };
  }

  /** Seconds of audio queued beyond the present moment. Drives prefetch depth. */
  buffered() {
    if (!this.ctx) return 0;
    return Math.max(0, this.cursor - this.ctx.currentTime);
  }

  /**
   * Cut playback immediately. Safe to call when nothing is playing, and safe to
   * call twice, because stop paths get hit from several directions at once:
   * a keypress, a navigation, and a service worker restart can all arrive
   * together.
   */
  stop() {
    for (const source of this.live) {
      source.onended = null; // do not fire sentence-end for audio we cut
      try {
        source.stop();
      } catch {
        // Already stopped or never started. Not an error worth surfacing.
      }
      source.disconnect();
    }
    this.live.clear();
    this.playing = false;
    this.cursor = this.ctx ? this.ctx.currentTime : 0;
    this.drained = Promise.resolve();
  }

  /**
   * Release the audio device.
   *
   * Order is the whole point of this existing separately from stop(): every
   * source stopped and disconnected, then the gain node, then the context.
   * Closing a context with sources still attached is how you get audio that
   * outlives teardown, and on the desktop half the same mistake in C freed a
   * stream mid-write and crashed at exit after logging a clean shutdown.
   *
   * This **cuts** rather than drains, deliberately. An earlier version awaited
   * `this.drained` here, which read as waiting for in-flight audio to finish
   * and did nothing at all: stop() has already killed every source and reset
   * that promise to a resolved one. A line that implies a guarantee it does not
   * provide is worse than no line, because the next person reads it as a
   * reason not to look. Draining would also be wrong — a stop should be
   * immediate, not play to the end of the sentence.
   */
  async dispose() {
    this.stop();
    this.gain?.disconnect();
    this.gain = null;
    if (this.ctx && this.ctx.state !== 'closed') {
      try {
        await this.ctx.close();
      } catch {
        // Racing another dispose. The device is going away either way.
      }
    }
    this.ctx = null;
  }
}
