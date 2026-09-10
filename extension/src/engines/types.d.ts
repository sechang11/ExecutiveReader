/**
 * Types only. No build step: this file is never loaded at runtime, it just
 * gives the editor something to check the JSDoc annotations in the .js files
 * against. See docs/extension-spec.md section 8.
 *
 * Shape note, and it is the reason this file matters: synthesis is modelled as
 * an async *stream* of chunks, with word timings arriving separately, even
 * though neither launch engine needs that. The system-voice engine speaks
 * without ever handing us audio, and Kokoro will return one finished buffer.
 * The streaming shape exists so the desktop companion bridge, which returns PCM
 * in pieces over stdio with timings that trail the audio, can be added later as
 * one more adapter rather than a rewrite of the player.
 */

export interface Voice {
  /** Stable across sessions: `${engineId}:${nativeId}`. Recorded in history. */
  key: string;
  nativeId: string;
  engineId: string;
  name: string;
  /** BCP-47, e.g. "en-US". Drives per-block voice switching. */
  lang: string;
  gender?: 'male' | 'female' | 'neutral';
  /** False for a neural voice whose weights are not downloaded yet. */
  ready: boolean;
  /** Shown in the picker, e.g. "80 MB download" or "needs the desktop app". */
  note?: string;
}

export interface SynthOptions {
  voice: Voice;
  /** 0.5 to 4.0. Engines apply this at synthesis time where they can, so pitch
   *  survives. The player falls back to playbackRate only when
   *  `appliesSpeedInternally` is false. */
  speed: number;
  /** 0 to 1. */
  volume: number;
  signal: AbortSignal;
}

/** Character offsets are into the *original* sentence text passed to
 *  synthesize(), so the content script maps them straight back to its DOM
 *  Ranges without re-tokenizing. */
export interface WordTiming {
  charStart: number;
  charEnd: number;
  /** Seconds from the start of this sentence's audio. */
  timeStart: number;
  timeEnd?: number;
}

export type SynthChunk =
  /** Raw audio. Emit as many as you like; the player concatenates in order. */
  | { kind: 'audio'; pcm: Float32Array; sampleRate: number }
  /** Timings may arrive before, during, or after the audio they describe. */
  | { kind: 'timings'; words: WordTiming[] }
  /** The engine speaks on its own and hands us no buffer, as chrome.tts does.
   *  The player must not schedule audio for this sentence, only track progress
   *  from `timings` and the end of the stream. */
  | { kind: 'external'; durationHint?: number };

export interface TtsEngine {
  readonly id: string;
  readonly displayName: string;

  /** False when the backing model or host app is missing. Never throws. */
  available(): Promise<boolean>;

  voices(): Promise<Voice[]>;

  /** Download weights, spin up the worker, connect to the host. Safe to call
   *  more than once. Progress drives the voice-picker download bar. */
  prepare(voice: Voice, onProgress?: (fraction: number) => void): Promise<void>;

  synthesize(text: string, opts: SynthOptions): AsyncIterable<SynthChunk>;

  /** True when `speed` is baked into synthesis and pitch is preserved. False
   *  means the player resamples, which sounds poor above about 1.5x. */
  readonly appliesSpeedInternally: boolean;

  /** Past this, output stops sounding natural and the UI warns. */
  readonly maxNaturalSpeed: number;

  /** False means we estimate word timings from token duration instead. */
  readonly supportsWordBoundaries: boolean;

  dispose(): Promise<void>;
}
