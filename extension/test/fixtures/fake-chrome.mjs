/**
 * Enough of the `chrome.*` surface to run the service worker in Node.
 *
 * The worker is the only thing that decides what gets read next, and until now
 * nothing exercised it: the coverage list carried it as "orchestration over
 * chrome.tabs, storage.session and messaging", which is a description of the
 * obstacle rather than a reason. Every failure it can have — playback stopping
 * a sentence early, a skip landing in the wrong place, a position lost on
 * pause — is invisible from outside and miserable to diagnose from inside.
 *
 * Written to the shapes the worker actually uses and no further. A stub that
 * answers calls the code never makes invites tests for behaviour that does not
 * exist, and a stub that is *more* forgiving than Chrome hides real bugs: the
 * two that matter here are `chrome.offscreen.createDocument` throwing on a
 * second create, and `chrome.tabs.sendMessage` rejecting when no content
 * script is listening. Both are modelled.
 */

/** A listener list with the addListener/dispatch pair the worker relies on. */
function event() {
  const fns = [];
  const ev = {
    addListener: (fn) => fns.push(fn),
    removeListener: (fn) => {
      const i = fns.indexOf(fn);
      if (i >= 0) fns.splice(i, 1);
    },
    /** @returns {any[]} whatever the listeners returned */
    dispatch: (...args) => fns.map((fn) => fn(...args)),
    get count() { return fns.length; },
  };
  return ev;
}

/** chrome.storage.local / .session, including the areas the worker reads. */
function storageArea(initial = {}) {
  const data = { ...initial };
  return {
    data,
    async get(keys) {
      if (keys == null) return { ...data };
      if (typeof keys === 'string') return keys in data ? { [keys]: data[keys] } : {};
      if (Array.isArray(keys)) {
        return Object.fromEntries(keys.filter((k) => k in data).map((k) => [k, data[k]]));
      }
      // An object argument supplies defaults for absent keys.
      return Object.fromEntries(
        Object.entries(keys).map(([k, dflt]) => [k, k in data ? data[k] : dflt]),
      );
    },
    async set(patch) { Object.assign(data, patch); },
    async remove(keys) {
      for (const k of [].concat(keys)) delete data[k];
    },
    async clear() { for (const k of Object.keys(data)) delete data[k]; },
  };
}

/**
 * @param {object} [opts]
 * @param {Record<string, any>} [opts.files] responses for chrome.runtime.getURL fetches
 * @param {boolean} [opts.contentScript] whether a tab answers sendMessage
 */
export function makeChrome(opts = {}) {
  const calls = {
    tabMessages: [],
    offscreenCalls: [],
    ttsStops: 0,
    spoken: [],
    tabsCreated: [],
    tabUpdates: [],
    executed: [],
    runtimeMessages: [],
  };

  /** What a tab replies to `{type:'build'}`. Tests overwrite this. */
  let buildReply = null;
  /** What a tab replies to `{type:'find-next'}`. */
  let nextPageReply = null;
  let hasContentScript = opts.contentScript ?? false;

  const onMessage = event();
  const onCommand = event();
  const onConnect = event();
  const onInstalled = event();
  const onClickedMenu = event();
  const onUpdated = event();

  let offscreenDocument = false;
  /** Replies from the offscreen document, keyed by message type. */
  const offscreenReplies = new Map();

  const chrome = {
    runtime: {
      id: 'test-extension',
      getURL: (p) => `chrome-extension://test/${p}`,
      onMessage,
      onConnect,
      onInstalled,
      lastError: null,
      async getContexts() {
        return offscreenDocument ? [{ contextType: 'OFFSCREEN_DOCUMENT' }] : [];
      },
      async sendMessage(msg) {
        calls.runtimeMessages.push(msg);
        // The offscreen document is the only other listener the worker talks
        // to this way; a panel message with nothing open rejects, as it does
        // in Chrome.
        if (msg?.target === 'offscreen') {
          if (!offscreenDocument) throw new Error('no offscreen document');
          calls.offscreenCalls.push(msg);
          const reply = offscreenReplies.get(msg.type);
          return typeof reply === 'function' ? reply(msg) : reply;
        }
        throw new Error('Could not establish connection');
      },
    },

    storage: { local: storageArea(), session: storageArea() },

    tabs: {
      onUpdated,
      async query() { return [{ id: 1, url: 'https://example.com/article' }]; },
      async create(info) { calls.tabsCreated.push(info); return { id: 99, ...info }; },
      async get(id) { return { id, url: 'https://example.com/article' }; },
      async update(id, props) {
        calls.tabUpdates.push({ id, ...props });
        // Chrome fires this once the new document is complete; the worker waits
        // for it, so firing synchronously here would deadlock nothing but does
        // need to happen after the caller has attached its listener.
        setTimeout(() => onUpdated.dispatch(id, { status: 'complete' }), 0);
        return { id, ...props };
      },
      async sendMessage(tabId, msg) {
        calls.tabMessages.push({ tabId, ...msg });
        if (!hasContentScript) throw new Error('Receiving end does not exist');
        if (msg.type === 'build') return buildReply;
        if (msg.type === 'find-next') return nextPageReply;
        return undefined;
      },
    },

    scripting: {
      async insertCSS(arg) { calls.executed.push({ css: arg.files }); },
      async executeScript(arg) {
        calls.executed.push({ js: arg.files });
        // Injecting the loader is what makes the tab answer.
        hasContentScript = true;
      },
    },

    offscreen: {
      async createDocument() {
        // Not idempotent in Chrome, and the worker's whole offscreen-host
        // module exists because of that.
        if (offscreenDocument) throw new Error('Only a single offscreen document may be created');
        offscreenDocument = true;
      },
      async closeDocument() {
        if (!offscreenDocument) throw new Error('No current offscreen document');
        offscreenDocument = false;
      },
      hasDocument: () => offscreenDocument,
    },

    /**
     * Speech, modelled as the callback stream Chrome actually provides.
     *
     * The worker awaits the engine's async iterable, which does not finish
     * until an `end` event arrives — so a stub that merely records the call
     * would hang the read loop rather than fail it. Events fire on a later
     * turn, as they do in Chrome, which is also what makes a mid-sentence
     * pause or skip reachable from a test.
     */
    tts: {
      getVoices(cb) {
        cb([{ voiceName: 'Test Voice', lang: 'en-US', gender: 'female', remote: false }]);
      },
      speak(text, opts) {
        calls.spoken.push({ text, rate: opts.rate, voiceName: opts.voiceName });
        const fire = opts.onEvent;
        setTimeout(() => {
          if (calls.cancelled) return;
          fire?.({ type: 'start' });
          fire?.({ type: 'word', charIndex: 0, length: Math.min(3, text.length) });
          fire?.({ type: 'end' });
        }, 0);
      },
      stop() { calls.ttsStops++; },
    },

    i18n: { getUILanguage: () => 'en-US' },
    commands: { onCommand },
    contextMenus: { create() {}, removeAll(cb) { cb?.(); }, onClicked: onClickedMenu },
    sidePanel: { setPanelBehavior: async () => {}, open: async () => {} },
    action: { setBadgeText: async () => {}, setTitle: async () => {} },
  };

  return {
    chrome,
    calls,
    events: { onMessage, onCommand, onConnect, onInstalled, onUpdated },
    /** What the page hands back when asked to build a document. */
    setDocument(doc) { buildReply = doc; },
    setNextPage(next) { nextPageReply = next; },
    setContentScript(present) { hasContentScript = present; },
    /** Reply for one offscreen message type; a function receives the message. */
    setOffscreenReply(type, reply) { offscreenReplies.set(type, reply); },
    get offscreenOpen() { return offscreenDocument; },
  };
}

/**
 * Send a message the way the popup and panel do, and await the reply.
 *
 * The worker's listener returns `true` and answers through the callback, which
 * is the Chrome contract for an async handler; a test that awaited the return
 * value would silently assert nothing.
 */
export function ask(harness, msg) {
  return new Promise((resolve, reject) => {
    const returned = harness.events.onMessage.dispatch(msg, {}, resolve);
    if (!returned.some((r) => r === true)) {
      reject(new Error(`no handler kept the channel open for ${msg.type}`));
    }
  });
}
