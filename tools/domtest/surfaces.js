/**
 * The surfaces a user actually touches: the popup and the side panel.
 *
 * `ui-wiring.test.mjs` already checks that every element a script reaches for
 * exists in its page. That catches a typo, and not a button wired to the wrong
 * message — and a button wired to the wrong message looks exactly like a
 * working one. These press the controls and read what was sent.
 *
 * Kept in its own file rather than in run.js because it needs a second kind of
 * fixture: run.js tests functions against real elements, and this runs whole
 * pages against a stand-in browser.
 *
 * @param {{test: Function, assert: Function, equal: Function, deepEqual: Function, stage: Element}} api
 */
import { pageChrome, mountSurface } from './chrome-stub.js';

const POPUP = '/extension/src/popup/popup.html';
const POPUP_JS = '/extension/src/popup/popup.js';
const PANEL = '/extension/src/sidepanel/panel.html';
const PANEL_JS = '/extension/src/sidepanel/panel.js';
const OPTIONS = '/extension/src/options/options.html';
const OPTIONS_JS = '/extension/src/options/options.js';
const WELCOME = '/extension/src/welcome/welcome.html';
const WELCOME_JS = '/extension/src/welcome/welcome.js';

/** Mid-read, so both the playing and the position branches are exercised. */
const READING = {
  status: 'speaking',
  index: 3,
  texts: ['a', 'b', 'c', 'd', 'e'],
  exact: [true, true, true, true, true],
  speed: 1.5,
  voiceKey: null,
  autoAdvance: false,
  url: 'https://example.com/article',
  title: 'An Article',
};

const VOICES = [
  { key: 'system:Aria', name: 'Aria', lang: 'en-US' },
  { key: 'system:Google UK', name: 'Google UK', lang: 'en-GB', note: 'needs a connection' },
];

const tick = () => new Promise((r) => setTimeout(r, 0));

/** Wait for a condition, for the cases that depend on a fetch rather than a click. */
async function until(condition, message, tries = 200) {
  for (let i = 0; i < tries; i++) {
    if (condition()) return;
    await tick();
  }
  throw new Error(message);
}

export async function surfaceTests({ test, assert, equal, deepEqual, stage }) {
  /** Mount a page, run the body against it, and always tear it down. */
  async function surface(name, page, script, replies, fn, stored = {}) {
    await test(name, '', async () => {
      const h = pageChrome(replies, stored);
      try {
        await mountSurface(stage, page, script, h);
        await fn(h);
      } finally {
        delete window.chrome;
      }
    });
  }

  const popupReplies = {
    'get-state': READING,
    'get-voices': VOICES,
    'toggle-play': { ...READING, status: 'paused' },
    stop: { ...READING, status: 'idle', texts: [] },
    skip: READING,
  };

  // ------------------------------------------------------------- the popup

  await surface('the popup says where in the page the reader is', POPUP, POPUP_JS,
    popupReplies, () => {
      // Off by one here is the difference between "sentence 4" and "sentence
      // 3", which nobody reports and everybody notices.
      const status = document.getElementById('status').textContent;
      assert(status.includes('sentence 4 of 5'), status);
      assert(status.startsWith('Playing'), status);
    });

  await surface('the play button is a pause button while playing', POPUP, POPUP_JS,
    popupReplies, () => {
      equal(document.getElementById('play').getAttribute('aria-label'), 'Pause',
        'a screen reader announces this label, so it must say what pressing does');
    });

  await surface('pressing play sends one intent and repaints from the answer',
    POPUP, POPUP_JS, popupReplies, async (h) => {
      document.getElementById('play').click();
      await tick();

      equal(h.sent.filter((m) => m.type === 'toggle-play').length, 1);
      equal(document.getElementById('play').getAttribute('aria-label'), 'Play',
        'the button must follow the state the worker returned, not a local guess');
    });

  await surface('previous and next send opposite deltas', POPUP, POPUP_JS, popupReplies,
    async (h) => {
      document.getElementById('prev').click();
      document.getElementById('next').click();
      await tick();

      deepEqual(h.sent.filter((m) => m.type === 'skip').map((m) => m.delta), [-1, 1]);
    });

  await surface('the transport is disabled when nothing is loaded', POPUP, POPUP_JS,
    { ...popupReplies, 'get-state': { ...READING, status: 'idle', texts: [] } },
    () => {
      for (const id of ['prev', 'next', 'stop']) {
        equal(document.getElementById(id).disabled, true, `${id} must be disabled when idle`);
      }
    });

  await surface('a remote voice is labelled in the picker', POPUP, POPUP_JS, popupReplies,
    () => {
      // The privacy policy names this as one of only two cases where text
      // leaves the machine. This label is how a user can tell which is which.
      const options = [...document.querySelectorAll('#voice option')].map((o) => o.textContent);
      assert(options.some((o) => o.includes('needs a connection')), options.join(' | '));
    });

  await surface('voices are grouped by language', POPUP, POPUP_JS, popupReplies, () => {
    // Forty system voices in one flat list is not a list anyone can use.
    const groups = [...document.querySelectorAll('#voice optgroup')].map((g) => g.label);
    deepEqual(groups, ['en-GB', 'en-US']);
  });

  await surface('choosing a voice sends its key, not its label', POPUP, POPUP_JS,
    popupReplies, async (h) => {
      const select = document.getElementById('voice');
      select.value = 'system:Google UK';
      select.dispatchEvent(new Event('change'));
      await tick();

      equal(h.last('set-voice')?.voiceKey, 'system:Google UK');
    });

  await surface('dragging the speed slider does not send on every pixel',
    POPUP, POPUP_JS, popupReplies, async (h) => {
      // `input` fires continuously while dragging; `change` means the handle
      // was let go. Sending on input posts a message per pixel of travel.
      const speed = document.getElementById('speed');
      speed.value = '2';
      speed.dispatchEvent(new Event('input'));
      await tick();

      equal(h.last('set-speed'), undefined, 'a drag in progress is not a decision');
      equal(document.getElementById('speed-out').textContent, '2.00×',
        'but the readout must follow the handle');

      speed.dispatchEvent(new Event('change'));
      await tick();
      equal(h.last('set-speed')?.speed, 2);
    });

  await surface('a state broadcast repaints a popup that is already open',
    POPUP, POPUP_JS, popupReplies, async (h) => {
      // The popup is usually opened mid-read and left open. Without this it
      // shows the position it was opened at until it is closed and reopened.
      h.pushState({ ...READING, index: 0, status: 'paused' });
      await tick();

      const status = document.getElementById('status').textContent;
      assert(status.startsWith('Paused'), status);
      assert(status.includes('sentence 1 of 5'), status);
    });

  // -------------------------------------------------------- the side panel

  const panelReplies = {
    'get-state': READING,
    'get-voices': VOICES,
    'get-history': [],
    'toggle-play': READING,
    skip: READING,
  };

  await surface('the panel sends auto-advance through the worker, not locally',
    PANEL, PANEL_JS, panelReplies, async (h) => {
      const box = document.getElementById('auto-advance');
      box.checked = true;
      box.dispatchEvent(new Event('change'));
      await tick();
      equal(h.last('set-auto-advance')?.on, true);

      // Both directions, because sending a hard `true` passes the first half
      // of this and leaves a checkbox that cannot be unticked. A mutation to
      // exactly that effect survived until this line existed.
      box.checked = false;
      box.dispatchEvent(new Event('change'));
      await tick();
      equal(h.last('set-auto-advance')?.on, false);
    });

  await surface('the panel offers a next page rather than the reader losing it',
    PANEL, PANEL_JS, panelReplies, async (h) => {
      // The worker declines to follow a weak guess and broadcasts it instead.
      // If nothing surfaces that, the feature silently does nothing on exactly
      // the sites where a next link is hardest to recognise.
      h.push({
        type: 'confirm-next-page',
        target: 'panel',
        next: { url: 'https://example.com/2', label: 'Next', confidence: 'low' },
      });
      await tick();

      const prompt = document.getElementById('next-prompt');
      assert(prompt && !prompt.hidden, 'the reader was never told there was a next page');
    });

  await surface('declining a next page dismisses the offer', PANEL, PANEL_JS, panelReplies,
    async (h) => {
      h.push({
        type: 'confirm-next-page',
        target: 'panel',
        next: { url: 'https://example.com/2', label: 'Next', confidence: 'low' },
      });
      await tick();

      document.getElementById('next-no').click();
      await tick();

      equal(document.getElementById('next-prompt').hidden, true,
        'an offer that cannot be dismissed is a permanent piece of furniture');
    });

  await surface('the panel shows the same position as the popup', PANEL, PANEL_JS,
    panelReplies, () => {
      // Two surfaces rendering the same state differently is a bug report
      // nobody can describe.
      const text = stage.textContent;
      assert(/4\s*(of|\/)\s*5/.test(text) || text.includes('sentence 4 of 5'),
        'the panel does not show the position the popup does');
    });

  // ---------------------------------------------------------- the settings

  const optionsReplies = {
    'get-voices': VOICES,
    'neural-status': { model: false, voices: {} },
    'neural-evict': { ok: true },
    'prune-history': { removed: 3 },
    'clear-history': { ok: true },
    'pronunciations-changed': { ok: true },
  };

  await surface('a pronunciation the user adds is stored and shown back',
    OPTIONS, OPTIONS_JS, optionsReplies, async (h) => {
      // The likeliest source of a bad first review is a mispronounced name,
      // and this is the two-second fix. It shipped once with nothing reading
      // it at all.
      document.getElementById('say-word').value = 'Kokoro';
      document.getElementById('say-as').value = 'ko ko ro';
      document.getElementById('say-add').click();
      await tick();

      deepEqual(h.stored.userPronunciations, [{ match: 'Kokoro', say: 'ko ko ro' }]);
      assert(document.getElementById('say-list').textContent.includes('Kokoro'),
        'the rule was stored but never shown back');
    });

  await surface('the worker is told at once, not at its next wake',
    OPTIONS, OPTIONS_JS, optionsReplies, async (h) => {
      // Otherwise the change takes effect whenever the worker happens to
      // restart, which looks exactly like the setting not working.
      document.getElementById('say-word').value = 'Kokoro';
      document.getElementById('say-as').value = 'ko ko ro';
      document.getElementById('say-add').click();
      await tick();

      assert(h.last('pronunciations-changed'), 'the worker was never told');
    });

  await surface('editing a word replaces its rule rather than shadowing it',
    OPTIONS, OPTIONS_JS, optionsReplies, async (h) => {
      // A second rule for the same word is shadowed by the first, so the edit
      // looks as though it did not take.
      for (const as of ['ko ko ro', 'kaw kaw raw']) {
        document.getElementById('say-word').value = 'Kokoro';
        document.getElementById('say-as').value = as;
        document.getElementById('say-add').click();
        await tick();
      }

      deepEqual(h.stored.userPronunciations, [{ match: 'Kokoro', say: 'kaw kaw raw' }]);
    });

  await surface('a rule that says nothing is refused, with a reason',
    OPTIONS, OPTIONS_JS, optionsReplies, async (h) => {
      document.getElementById('say-word').value = 'Kokoro';
      document.getElementById('say-as').value = '';
      document.getElementById('say-add').click();
      await tick();

      equal(h.stored.userPronunciations, undefined);
      assert(document.getElementById('status').textContent.length > 0,
        'a control that silently does nothing reads as broken');
    });

  await surface('a word mapped to itself is refused', OPTIONS, OPTIONS_JS,
    optionsReplies, async (h) => {
      document.getElementById('say-word').value = 'Kokoro';
      document.getElementById('say-as').value = 'Kokoro';
      document.getElementById('say-add').click();
      await tick();

      equal(h.stored.userPronunciations, undefined);
    });

  await surface('removing a rule removes exactly that one', OPTIONS, OPTIONS_JS,
    optionsReplies, async (h) => {
      const remove = [...document.querySelectorAll('#say-list button')]
        .find((b) => /kokoro/i.test(b.getAttribute('aria-label') ?? ''));
      assert(remove, 'the stored rule had no remove button');

      remove.click();
      await tick();

      deepEqual(h.stored.userPronunciations, [{ match: 'Piper', say: 'pie per' }]);
    },
    {
      userPronunciations: [
        { match: 'Kokoro', say: 'ko ko ro' },
        { match: 'Piper', say: 'pie per' },
      ],
    });

  await surface('the shipped rules are listed but cannot be removed',
    OPTIONS, OPTIONS_JS, optionsReplies, async () => {
      // They come from the package, so a Remove button on one would be a
      // control that cannot do what it says.
      //
      // Waited for rather than assumed: this list arrives from a fetch of the
      // packaged rules, so a fixed pause would make the test depend on how
      // fast the page is served.
      const list = document.getElementById('say-builtin');
      await until(() => list.querySelectorAll('li').length > 0,
        'the shipped rules were never shown');

      assert(!list.textContent.includes('Nothing here yet'), list.textContent);
      equal(list.querySelectorAll('button').length, 0);
    });

  await surface('removing the voice download says so', OPTIONS, OPTIONS_JS,
    optionsReplies, async (h) => {
      // Someone who tried neural voices and went back should not be left
      // carrying eighty-eight megabytes they cannot see or remove.
      document.getElementById('evict').click();
      await tick();

      assert(h.last('neural-evict'), 'nothing was asked to delete the download');
      assert(document.getElementById('status').textContent.length > 0,
        'a deletion with no acknowledgement reads as a dead button');
    });

  await surface('download progress is reported against the real total',
    OPTIONS, OPTIONS_JS, optionsReplies, async (h) => {
      h.push({ type: 'neural-progress', loaded: 46000000, total: 92000000 });
      await tick();

      equal(document.getElementById('progress').value, 50);
      const text = document.getElementById('progress-text').textContent;
      assert(text.includes('50%'), text);
    });

  await surface('an unknown download size shows an indeterminate bar, not zero',
    OPTIONS, OPTIONS_JS, optionsReplies, async (h) => {
      // Content-Length is absent on some responses. A bar pinned at zero for
      // ninety seconds is indistinguishable from a hang.
      h.push({ type: 'neural-progress', loaded: 46000000, total: 0 });
      await tick();

      equal(document.getElementById('progress').hasAttribute('value'), false);
    });

  // ------------------------------------------------------------ first run

  await surface('the welcome page can start the voice download', WELCOME, WELCOME_JS,
    { 'neural-download': { ok: true }, 'get-voices': VOICES }, async (h) => {
      document.getElementById('get-voices').click();
      await tick();

      assert(h.sent.length > 0, 'the first thing a new user presses did nothing');
    });
}
