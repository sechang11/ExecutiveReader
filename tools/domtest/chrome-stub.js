/**
 * A `chrome` stand-in for the extension's own pages.
 *
 * The popup, the side panel, the settings page and the welcome page are the
 * surfaces a user actually touches, and none of them had a behavioural test.
 * `ui-wiring.test.mjs` checks that every element a script reaches for exists,
 * which catches a typo but not a button wired to the wrong message — and a
 * button wired to the wrong message looks exactly like a working button.
 *
 * These pages are pure transport: they render whatever the worker says the
 * state is and send intents back. That makes them testable with a stand-in for
 * one function, `chrome.runtime.sendMessage`, plus the listener the worker
 * pushes state through.
 *
 * Deliberately not shared with the Node fixture for the service worker. That
 * one models Chrome's awkward edges — an offscreen document that throws on a
 * second create, a tab that rejects when nothing is listening — because the
 * worker's job is to survive them. A page's job is to render and to send, and a
 * fixture carrying machinery neither page can reach would invite tests for
 * behaviour that does not exist.
 */

/** @param {Record<string, any|((msg: any) => any)>} replies keyed by message type */
export function pageChrome(replies = {}, stored = {}) {
  const sent = [];
  const listeners = [];
  const opened = [];

  const chrome = {
    runtime: {
      getURL: (p) => `/extension/${p}`,
      // The settings page prints the version it is showing settings for.
      getManifest: () => ({ version: '0.1.0', name: 'Executive Reader' }),
      async sendMessage(msg) {
        sent.push(msg);
        const reply = replies[msg.type];
        return typeof reply === 'function' ? reply(msg) : reply;
      },
      onMessage: { addListener: (fn) => listeners.push(fn) },
    },
    tabs: {
      async query() { return [{ id: 7, url: 'https://example.com/article' }]; },
      async create(info) { opened.push(info); return { id: 8, ...info }; },
    },
    sidePanel: {
      async open(arg) { opened.push(arg); },
    },
    // A real store, not a stub that forgets. The settings page writes a
    // preference and then reads it back on the next interaction, so a `set`
    // that goes nowhere makes every persistence test pass by accident.
    storage: {
      local: {
        async get(keys) {
          if (keys == null) return { ...stored };
          if (typeof keys === 'string') return keys in stored ? { [keys]: stored[keys] } : {};
          if (Array.isArray(keys)) {
            return Object.fromEntries(keys.filter((k) => k in stored).map((k) => [k, stored[k]]));
          }
          return Object.fromEntries(
            Object.entries(keys).map(([k, d]) => [k, k in stored ? stored[k] : d]),
          );
        },
        async set(patch) { Object.assign(stored, patch); },
        async remove(keys) { for (const k of [].concat(keys)) delete stored[k]; },
      },
    },
  };

  return {
    chrome,
    /** Whatever the page has written to local storage. */
    stored,
    /** Every message the page has sent, oldest first. */
    sent,
    /** Anything the page asked the browser to open. */
    opened,
    /** The last message of a given type, or undefined. */
    last: (type) => [...sent].reverse().find((m) => m.type === type),
    /** Push a state update the way the worker does while a page is open. */
    pushState(state) {
      for (const fn of listeners) fn({ type: 'state', state });
    },
    /** Push any other broadcast. */
    push(msg) {
      for (const fn of listeners) fn(msg);
    },
  };
}

/**
 * Mount one of the extension's pages into the test stage and run its script.
 *
 * The page's own `<script>` tags are dropped rather than left in place: the
 * module is imported explicitly below, with a cache-busting suffix, because
 * these scripts wire up listeners at load and every case needs a fresh set.
 * Leaving the tag would load it a second time against a stale relative path.
 *
 * @param {Element} stage
 * @param {string} htmlPath served path of the page
 * @param {string} scriptPath served path of its module
 * @param {ReturnType<pageChrome>} harness
 */
export async function mountSurface(stage, htmlPath, scriptPath, harness) {
  const html = await fetch(htmlPath).then((r) => r.text());
  const parsed = new DOMParser().parseFromString(html, 'text/html');
  for (const tag of parsed.querySelectorAll('script')) tag.remove();

  stage.replaceChildren(...parsed.body.children);
  window.chrome = harness.chrome;
  await import(`${scriptPath}?t=${Math.random()}`);
  // The scripts finish by asking for state and voices; let those settle.
  await new Promise((r) => setTimeout(r, 0));
}
