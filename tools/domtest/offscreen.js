/**
 * The offscreen document, against a real audio graph.
 *
 * This is the only part of the extension that owns the audio device. It is
 * also the part that exists to survive the service worker being killed
 * mid-sentence, which is exactly the situation nobody reproduces by hand.
 *
 * `player.test.mjs` already covers the scheduler against a fake Web Audio, and
 * that fake is where its edges were found. What it cannot check is whether the
 * numbers mean anything to a real `AudioContext`: a duration computed from a
 * sample rate the device does not use, or a buffer built with the wrong
 * channel count, is arithmetic that passes a fake and silence that does not.
 *
 * @param {{test: Function, assert: Function, equal: Function, deepEqual: Function, stage: Element}} api
 */
import { pageChrome } from './chrome-stub.js';

const OFFSCREEN_JS = '/extension/src/offscreen/offscreen.js';
const SAMPLE_RATE = 24000;

const tick = () => new Promise((r) => setTimeout(r, 0));

/** One second of quiet, in the shape the worker sends it. */
function silence(seconds = 0.5) {
  return new Float32Array(Math.round(SAMPLE_RATE * seconds)).buffer;
}

export async function offscreenTests({ test, assert, equal, stage }) {
  /** Load the document's script and give back a way to send it worker messages. */
  async function offscreen(name, fn) {
    await test(name, '', async () => {
      const h = pageChrome({});
      const listeners = [];
      h.chrome.runtime.onMessage.addListener = (fn2) => listeners.push(fn2);
      window.chrome = h.chrome;

      try {
        await import(`${OFFSCREEN_JS}?t=${Math.random()}`);
        /**
         * Send as the worker does. Returns `undefined` when nothing claimed
         * the message, which is a distinct outcome from an error and is what
         * two of these tests are about.
         */
        const ask = (msg) => new Promise((resolve) => {
          let answered = false;
          const kept = listeners.map((l) => l(msg, {}, (r) => { answered = true; resolve(r); }));
          if (!kept.some((k) => k === true) && !answered) resolve(undefined);
        });
        await fn({ ...h, ask });
        await ask({ target: 'offscreen', type: 'shutdown' });
      } finally {
        delete window.chrome;
      }
    });
  }

  await offscreen('a message not addressed here is left alone', async ({ ask }) => {
    // The worker broadcasts, and this document shares type names with it —
    // both understand 'stop'. Answering one meant for the popup would stop
    // playback on someone else's message.
    equal(await ask({ type: 'stop' }), undefined);
    equal(await ask({ target: 'panel', type: 'stop' }), undefined);
  });

  await offscreen('an unknown message is declined rather than swallowed',
    async ({ ask }) => {
      equal(await ask({ target: 'offscreen', type: 'no-such-thing' }), undefined);
    });

  await offscreen('scheduled audio reports a duration the sample rate implies',
    async ({ ask }) => {
      // Half a second at 24 kHz. A device running at 48 kHz must not turn this
      // into a quarter of a second of double-speed speech.
      const reply = await ask({
        target: 'offscreen', type: 'audio-chunk',
        index: 0, pcm: silence(0.5), sampleRate: SAMPLE_RATE, playbackRate: 1,
      });

      assert(reply, 'nothing answered');
      equal(reply.queued, true);
      assert(Math.abs(reply.duration - 0.5) < 0.01, `duration ${reply.duration}`);
    });

  await offscreen('queued audio accumulates, so the worker knows to wait',
    async ({ ask }) => {
      // The worker uses this to decide whether to synthesize further ahead.
      // A buffered() that always answers zero makes it run flat out.
      const first = await ask({
        target: 'offscreen', type: 'audio-chunk',
        index: 0, pcm: silence(0.5), sampleRate: SAMPLE_RATE,
      });
      const second = await ask({
        target: 'offscreen', type: 'audio-chunk',
        index: 1, pcm: silence(0.5), sampleRate: SAMPLE_RATE,
      });

      assert(second.buffered > first.buffered,
        `buffered went ${first.buffered} then ${second.buffered}`);
    });

  await offscreen('speed is applied without resampling the audio', async ({ ask }) => {
    // Playing samples faster is what makes fast speech sound like a chipmunk.
    // The engines that can do it change the synthesis instead, and this path
    // is for the ones that cannot — so the duration must actually shorten.
    const normal = await ask({
      target: 'offscreen', type: 'audio-chunk',
      index: 0, pcm: silence(1), sampleRate: SAMPLE_RATE, playbackRate: 1,
    });
    await ask({ target: 'offscreen', type: 'stop' });
    const fast = await ask({
      target: 'offscreen', type: 'audio-chunk',
      index: 1, pcm: silence(1), sampleRate: SAMPLE_RATE, playbackRate: 2,
    });

    assert(fast.duration < normal.duration,
      `${fast.duration} should be shorter than ${normal.duration}`);
  });

  await offscreen('stopping empties the queue but keeps the device',
    async ({ ask }) => {
      // A skip and a voice change both stop and immediately restart. Tearing
      // the graph down between them adds an audible gap to every skip.
      await ask({
        target: 'offscreen', type: 'audio-chunk',
        index: 0, pcm: silence(1), sampleRate: SAMPLE_RATE,
      });

      await ask({ target: 'offscreen', type: 'stop' });
      const after = await ask({ target: 'offscreen', type: 'buffered' });
      equal(after.seconds, 0, 'the queue survived a stop');

      const again = await ask({
        target: 'offscreen', type: 'audio-chunk',
        index: 1, pcm: silence(0.5), sampleRate: SAMPLE_RATE,
      });
      equal(again.queued, true, 'the device was torn down by a stop');
    });

  await offscreen('volume is accepted and does not disturb the queue',
    async ({ ask }) => {
      await ask({
        target: 'offscreen', type: 'audio-chunk',
        index: 0, pcm: silence(0.5), sampleRate: SAMPLE_RATE,
      });
      const before = await ask({ target: 'offscreen', type: 'buffered' });

      equal((await ask({ target: 'offscreen', type: 'set-volume', volume: 0.3 })).ok, true);

      const after = await ask({ target: 'offscreen', type: 'buffered' });
      equal(after.seconds, before.seconds);
    });

  await offscreen('the absent neural tier is named, not just reported empty',
    async ({ ask }) => {
      // An empty list with no explanation is the same message for a missing
      // dictionary and a missing model download, and the two have opposite
      // fixes. Nothing is downloaded in this harness, so this is the
      // no-model case.
      const reply = await ask({ target: 'offscreen', type: 'neural-voices' });

      assert(reply, 'neural-voices answered nothing at all');
      assert('blocked' in reply, 'no reason was given for an empty tier');
      if (reply.blocked) {
        assert(typeof reply.blocked.reason === 'string' && reply.blocked.reason.length > 0,
          JSON.stringify(reply.blocked));
      }
    });

  await offscreen('an error in a handler comes back as an error, not a hang',
    async ({ ask }) => {
      // The worker awaits these. A handler that rejects without answering
      // leaves the read loop waiting on a sentence that will never arrive.
      const reply = await ask({
        target: 'offscreen', type: 'neural-speak',
        index: 0, text: 'hello', voiceKey: 'kokoro:nonexistent', speed: 1, volume: 1,
      });

      assert(reply && reply.error, JSON.stringify(reply));
    });
}
