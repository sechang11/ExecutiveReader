/**
 * Audio player, against a fake Web Audio graph.
 *
 * CAVEATS.md item 3 says the teardown path has zero coverage by construction,
 * because the tests use a stub sink and only a real device exercises the real
 * code. That is true of *behaviour* but not of *ordering*, and ordering is the
 * part that bit the desktop half: it closed its device while a write was in
 * flight and segfaulted at exit after logging a clean shutdown.
 *
 * A fake cannot prove the audio sounds right. It can prove that stop clears the
 * handlers before cutting the sources, that dispose waits for the drain before
 * closing, and that neither throws when called twice — which is exactly the
 * class of bug that produced a crash on the other side.
 */

import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

/** Records what happened, in order, so sequencing can be asserted. */
let log = [];
let now = 0;

class FakeSource {
  constructor() {
    this.onended = null;
    this.playbackRate = { value: 1 };
    this.started = null;
    this.stopped = false;
    this.disconnected = false;
  }

  connect() { log.push('source.connect'); }

  start(at) {
    this.started = at;
    log.push(`source.start@${at.toFixed(2)}`);
  }

  stop() {
    if (this.stopped) throw new Error('InvalidStateError: already stopped');
    this.stopped = true;
    log.push('source.stop');
  }

  disconnect() { this.disconnected = true; log.push('source.disconnect'); }
}

class FakeContext {
  constructor() {
    this.state = 'running';
    this.destination = { kind: 'destination' };
    this.sources = [];
  }

  get currentTime() { return now; }

  createBuffer(channels, length, rate) {
    return {
      duration: length / rate,
      copyToChannel() {},
    };
  }

  createBufferSource() {
    const s = new FakeSource();
    this.sources.push(s);
    return s;
  }

  createGain() {
    return { gain: { value: 1 }, connect() { log.push('gain.connect'); }, disconnect() { log.push('gain.disconnect'); } };
  }

  async close() {
    if (this.state === 'closed') throw new Error('InvalidStateError: already closed');
    this.state = 'closed';
    log.push('context.close');
  }
}

globalThis.AudioContext = FakeContext;

const { Player } = await import('../src/offscreen/player.js');

const pcm = (seconds = 1) => new Float32Array(24_000 * seconds);

beforeEach(() => { log = []; now = 0; });

test('schedules a sentence and connects it to the graph', () => {
  const p = new Player();
  const { duration } = p.schedule(0, pcm(2), 24_000);
  assert.equal(duration, 2);
  assert.ok(log.includes('source.connect'));
  assert.ok(log.some((l) => l.startsWith('source.start')));
  assert.equal(p.playing, true);
});

test('never schedules in the past', () => {
  // A slow synthesis can leave the cursor behind the clock. A start time in the
  // past plays immediately and overlaps whatever is already sounding.
  const p = new Player();
  p.schedule(0, pcm(1), 24_000);
  now = 100; // the clock ran on while we were synthesizing
  p.schedule(1, pcm(1), 24_000);
  const starts = log.filter((l) => l.startsWith('source.start'))
    .map((l) => Number(l.split('@')[1]));
  assert.ok(starts[1] >= now, `scheduled at ${starts[1]} but the clock is ${now}`);
});

test('queues the next sentence after the previous one, gaplessly', () => {
  const p = new Player();
  p.schedule(0, pcm(2), 24_000);
  const before = p.buffered();
  p.schedule(1, pcm(3), 24_000);
  assert.ok(p.buffered() > before + 2.9, 'the second must queue after the first, not over it');
});

test('stop clears the ended handler before cutting the source', () => {
  // The ordering that matters. Cutting first fires onended for audio we
  // deliberately killed, which advances the reader a sentence nobody heard.
  const p = new Player();
  let endedFired = false;
  p.schedule(0, pcm(1), 24_000);
  const source = p.live.values().next().value;
  source.onended = () => { endedFired = true; };

  p.stop();
  assert.equal(source.onended, null, 'the handler must be detached, not merely ignored');
  assert.equal(endedFired, false);
  assert.ok(source.stopped && source.disconnected);
  assert.equal(p.playing, false);
});

test('stop is safe when nothing is playing, and safe twice', () => {
  // Stop arrives from several directions at once: a keypress, a navigation, and
  // a worker restart can all land together.
  const p = new Player();
  assert.doesNotThrow(() => p.stop());
  p.schedule(0, pcm(1), 24_000);
  assert.doesNotThrow(() => { p.stop(); p.stop(); });
});

test('dispose releases the graph in order: sources, gain, then context', async () => {
  const p = new Player();
  p.schedule(0, pcm(1), 24_000);
  await p.dispose();

  const order = log.filter((l) => ['source.stop', 'gain.disconnect', 'context.close'].includes(l));
  assert.deepEqual(order, ['source.stop', 'gain.disconnect', 'context.close'],
    'closing the context before releasing the sources is the crash the desktop half hit');
  assert.equal(p.ctx, null);
});

test('dispose cuts rather than drains', async () => {
  // Found by mutation: removing an `await this.drained` from dispose changed
  // nothing, because stop() has already killed every source and reset that
  // promise. The await implied a wait that did not happen. Cutting is the
  // correct behaviour — a stop should be immediate, not play out the sentence —
  // so this pins that rather than the removed line.
  const p = new Player();
  p.schedule(0, pcm(30), 24_000); // half a minute of audio queued
  const source = p.live.values().next().value;

  const started = Date.now();
  await p.dispose();
  assert.ok(Date.now() - started < 500, 'dispose must not wait for queued audio to play out');
  assert.ok(source.stopped, 'the queued source is cut, not left to finish');
  assert.equal(p.ctx, null);
});

test('dispose is safe twice, and after stop', async () => {
  const p = new Player();
  p.schedule(0, pcm(1), 24_000);
  p.stop();
  await p.dispose();
  await assert.doesNotReject(() => p.dispose(), 'a second dispose must not throw');
});

test('a fresh context is created after dispose', () => {
  // Reading again after stopping must work; a disposed context cannot be reused.
  const p = new Player();
  p.schedule(0, pcm(1), 24_000);
  const first = p.ctx;
  p.stop();
  p.ctx.state = 'closed';
  p.schedule(1, pcm(1), 24_000);
  assert.notEqual(p.ctx, first, 'a closed context must be replaced, not reused');
});

test('volume is clamped and applied to the gain node', () => {
  const p = new Player();
  p.schedule(0, pcm(1), 24_000);
  p.setVolume(2);
  assert.equal(p.gain.gain.value, 1, 'above one is clamped');
  p.setVolume(-1);
  assert.equal(p.gain.gain.value, 0, 'below zero is clamped');
});

test('buffered reports nothing before anything is scheduled', () => {
  assert.equal(new Player().buffered(), 0);
});

test('scheduling keeps a lead over the clock', () => {
  // LOOKAHEAD_S is a judgment: too small and a slow synthesis starts audio in
  // the past, too large and stopping feels laggy because queued audio has to be
  // cut. Unpinned it could drift to zero and gaps would appear only under load.
  const p = new Player();
  now = 10;
  const { startAt } = p.schedule(0, pcm(1), 24_000);
  assert.ok(startAt > now, 'the first sentence must start after the clock, not on it');
  assert.ok(startAt - now <= 0.5, `a lead of ${startAt - now}s would make stopping feel laggy`);
});
