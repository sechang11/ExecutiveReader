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

export async function surfaceTests({ test, assert, equal, deepEqual, stage }) {
  /** Mount a page, run the body against it, and always tear it down. */
  async function surface(name, page, script, replies, fn) {
    await test(name, '', async () => {
      const h = pageChrome(replies);
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
}
