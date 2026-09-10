/**
 * The system voice engine.
 *
 * This is the engine everyone hears first: it needs no download, so it is what
 * speaks the moment the extension is installed. It is also the odd one out —
 * Chrome does the speaking and reports events, so the engine turns a callback
 * stream into an async iterable, and every failure mode is a stall rather than
 * an error.
 *
 * `chrome.tts` is four functions, so it stands in easily. Nothing in the module
 * under test is replaced.
 */

import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

/** @type {{speak: any[], stopped: number, voices: any[], onEvent: Function|null}} */
let tts;

/** Collect an async iterable, with a guard so a stall fails rather than hangs. */
async function collect(iterable, ms = 2000) {
  const out = [];
  const timer = setTimeout(() => { throw new Error('the stream never finished'); }, ms);
  try {
    for await (const chunk of iterable) out.push(chunk);
  } finally {
    clearTimeout(timer);
  }
  return out;
}

beforeEach(() => {
  tts = { speak: [], stopped: 0, voices: [], onEvent: null };
  globalThis.chrome = {
    tts: {
      getVoices: (cb) => cb(tts.voices),
      speak: (text, opts) => {
        tts.speak.push({ text, opts });
        tts.onEvent = opts.onEvent;
      },
      stop: () => { tts.stopped++; },
    },
  };
});

const { systemEngine } = await import('../src/engines/system.js');

const voice = { nativeId: 'Microsoft Aria', lang: 'en-US' };
const options = (extra = {}) => ({
  voice, speed: 1, volume: 1, signal: new AbortController().signal, ...extra,
});

test('the engine is available exactly when chrome.tts is', async () => {
  assert.equal(await systemEngine.available(), true);
  globalThis.chrome = {};
  assert.equal(await systemEngine.available(), false);
});

test('a remote voice is labelled, because it sends text off the device', async () => {
  // The privacy policy names this as one of only two cases where data leaves
  // the machine, and the label in the voice list is how a user can tell.
  tts.voices = [
    { voiceName: 'Microsoft Aria', lang: 'en-US', gender: 'female', remote: false },
    { voiceName: 'Google UK English', lang: 'en-GB', remote: true },
  ];

  const [local, remote] = await systemEngine.voices();

  assert.equal(local.note, undefined);
  assert.equal(remote.note, 'needs a connection');
});

test('a voice with no name is dropped rather than shown as blank', async () => {
  tts.voices = [{ lang: 'en-US' }, { voiceName: 'Aria', lang: 'en-US' }];
  const voices = await systemEngine.voices();
  assert.deepEqual(voices.map((v) => v.name), ['Aria']);
});

test('voice keys carry the engine, so two engines cannot collide', async () => {
  tts.voices = [{ voiceName: 'Aria', lang: 'en-US' }];
  const [v] = await systemEngine.voices();
  assert.equal(v.key, 'system:Aria');
  assert.equal(v.engineId, 'system');
  assert.equal(v.nativeId, 'Aria', 'the key is ours; the native id is what chrome.tts wants');
});

test('an unknown gender is dropped rather than passed through', async () => {
  tts.voices = [{ voiceName: 'Aria', lang: 'en-US', gender: 'neutral' }];
  const [v] = await systemEngine.voices();
  assert.equal(v.gender, undefined);
});

test('getVoices returning nothing is an empty list, not a crash', async () => {
  globalThis.chrome.tts.getVoices = (cb) => cb(undefined);
  assert.deepEqual(await systemEngine.voices(), []);
});

test('speed maps straight across but stays inside the API range', async () => {
  for (const [speed, rate] of [[1, 1], [2.5, 2.5], [0.05, 0.1], [50, 10]]) {
    const it = systemEngine.synthesize('hi', options({ speed }));
    const done = collect(it);
    tts.onEvent({ type: 'end' });
    await done;
    assert.equal(tts.speak.at(-1).opts.rate, rate, `speed ${speed}`);
  }
});

test('utterances are never queued, so a skipped sentence cannot survive', async () => {
  const it = systemEngine.synthesize('hi', options());
  const done = collect(it);
  tts.onEvent({ type: 'end' });
  await done;
  assert.equal(tts.speak[0].opts.enqueue, false);
});

test('the stream opens with an external chunk, because we produce no audio', async () => {
  const it = systemEngine.synthesize('Hello there', options());
  const done = collect(it);

  tts.onEvent({ type: 'start' });
  tts.onEvent({ type: 'end' });

  assert.deepEqual(await done, [{ kind: 'external' }]);
});

test('word events become timings a highlight can use', async () => {
  const it = systemEngine.synthesize('Hello there', options());
  const done = collect(it);

  tts.onEvent({ type: 'start' });
  tts.onEvent({ type: 'word', charIndex: 0, length: 5 });
  tts.onEvent({ type: 'word', charIndex: 6, length: 5 });
  tts.onEvent({ type: 'end' });

  const chunks = await done;
  assert.deepEqual(chunks.slice(1), [
    { kind: 'timings', words: [{ charStart: 0, charEnd: 5, timeStart: 0 }] },
    { kind: 'timings', words: [{ charStart: 6, charEnd: 11, timeStart: 0 }] },
  ]);
});

test('a word event with no length still covers a whole word', async () => {
  // Older voices omit `length`. Highlighting a single character would look like
  // a bug in the highlight rather than a gap in the voice's reporting.
  const it = systemEngine.synthesize('Hello there, friend', options());
  const done = collect(it);

  tts.onEvent({ type: 'word', charIndex: 6 });
  tts.onEvent({ type: 'word', charIndex: 13 });
  tts.onEvent({ type: 'end' });

  const chunks = await done;
  assert.deepEqual(chunks[0].words, [{ charStart: 6, charEnd: 11, timeStart: 0 }]);
  assert.deepEqual(chunks[1].words, [{ charStart: 13, charEnd: 19, timeStart: 0 }],
    'the last word runs to the end of the text');
});

test('an error event ends the stream by throwing, not by stopping quietly', async () => {
  // A silent end here is a reader that stops mid-page with no explanation.
  const it = systemEngine.synthesize('hi', options());
  const done = collect(it);

  tts.onEvent({ type: 'error', errorMessage: 'voice unavailable' });

  await assert.rejects(() => done, /voice unavailable/);
});

test('an error with no message still throws something readable', async () => {
  const it = systemEngine.synthesize('hi', options());
  const done = collect(it);
  tts.onEvent({ type: 'error' });
  await assert.rejects(() => done, /speech failed/);
});

test('an interruption ends the stream cleanly, because the user caused it', async () => {
  for (const type of ['interrupted', 'cancelled']) {
    const it = systemEngine.synthesize('hi', options());
    const done = collect(it);
    tts.onEvent({ type: 'start' });
    tts.onEvent({ type });
    assert.deepEqual(await done, [{ kind: 'external' }], type);
  }
});

test('aborting stops chrome speaking and ends the stream', async () => {
  const controller = new AbortController();
  const it = systemEngine.synthesize('hi', options({ signal: controller.signal }));
  const done = collect(it);

  tts.onEvent({ type: 'start' });
  controller.abort();

  assert.deepEqual(await done, [{ kind: 'external' }]);
  assert.equal(tts.stopped, 1, 'chrome keeps speaking unless it is told not to');
});

test('chunks queued before anything reads them are not lost', async () => {
  // Events arrive from a callback, so they can all fire before the consumer
  // starts iterating. Dropping those would lose the start of every sentence.
  const it = systemEngine.synthesize('Hello there', options());
  tts.onEvent({ type: 'start' });
  tts.onEvent({ type: 'word', charIndex: 0, length: 5 });
  tts.onEvent({ type: 'end' });

  const chunks = await collect(it);
  assert.equal(chunks.length, 2);
  assert.equal(chunks[0].kind, 'external');
});

test('an unrecognised event is ignored rather than ending the stream', async () => {
  const it = systemEngine.synthesize('hi', options());
  const done = collect(it);

  tts.onEvent({ type: 'sentence' });
  tts.onEvent({ type: 'resume' });
  tts.onEvent({ type: 'end' });

  assert.deepEqual(await done, []);
});

test('disposing stops speech', async () => {
  await systemEngine.dispose();
  assert.equal(tts.stopped, 1);
});
