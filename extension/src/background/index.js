/**
 * Service worker: the only thing that decides what gets read next.
 *
 * It must survive being killed. Chrome suspends this worker after roughly
 * thirty seconds idle, so nothing here may live only in module scope — every
 * transition is mirrored into chrome.storage.session and rehydrated on wake.
 * While a read is running the content script holds an open port, which keeps
 * the worker alive for the duration. See spec section 2.
 */

import { systemEngine } from '../engines/system.js';
import { callOffscreen, closeOffscreen } from './offscreen-host.js';
import * as history from '../core/history.js';
import { capture, locate } from '../core/anchor.js';
import { looksLikePdf } from '../pdf/extract.js';
import { loadBuiltin, loadUser, pronounce } from '../core/pronounce.js';

/**
 * Pronunciation overrides, loaded once per worker wake.
 *
 * These had shipped in the package and been read by nothing: the desktop half
 * used them and the extension did not, while the README claimed they mitigated
 * the dictionary's misses. They apply at synthesis, after normalization, so the
 * text stored and highlighted is untouched and only the sound changes.
 */
const pronunciationReady = (async () => {
  const shipped = await fetch(chrome.runtime.getURL('src/shared/pronunciation.json'))
    .then((r) => r.json()).catch(() => ({}));
  loadBuiltin(shipped);
  const { userPronunciations } = await chrome.storage.local.get('userPronunciations');
  loadUser(userPronunciations ?? []);
})().catch(() => { /* speech still works, just less correctly */ });

/**
 * @typedef {{
 *   status: 'idle'|'loading'|'speaking'|'paused',
 *   tabId: number|null,
 *   index: number,
 *   texts: string[],
 *   exact: boolean[],
 *   voiceKey: string|null,
 *   speed: number,
 *   volume: number,
 *   url: string|null,
 *   title: string|null,
 * }} State
 */

/** @type {State} */
const BLANK = {
  status: 'idle', tabId: null, index: 0, texts: [], exact: [],
  voiceKey: null, speed: 1, volume: 1, url: null, title: null,
  // Off by default: reading on past the end of the page is a power-user
  // behaviour, and the accessible default is not to navigate unasked.
  // See spec section 15.
  autoAdvance: false,
};

/** Aborts the in-flight sentence. Not persisted; recreated on resume. */
let currentAbort = null;

/** @returns {Promise<State>} */
async function getState() {
  const { state } = await chrome.storage.session.get('state');
  return { ...BLANK, ...(state ?? {}) };
}

/** @param {Partial<State>} patch */
async function setState(patch) {
  const next = { ...(await getState()), ...patch };
  await chrome.storage.session.set({ state: next });
  chrome.runtime.sendMessage({ type: 'state', state: next }).catch(() => {});
  return next;
}

// ---------------------------------------------------------------- engines

const ENGINES = [systemEngine];

/** @param {string|null} key */
async function resolveVoice(key) {
  for (const engine of ENGINES) {
    if (!(await engine.available())) continue;
    const voices = await engine.voices();
    if (key) {
      const found = voices.find((v) => v.key === key);
      if (found) return { engine, voice: found };
    }
    if (!key && voices.length) {
      // Prefer a voice matching the browser's UI language, then any local one.
      const ui = chrome.i18n.getUILanguage();
      const best = voices.find((v) => v.lang === ui)
        ?? voices.find((v) => v.lang?.startsWith(ui.split('-')[0]))
        ?? voices[0];
      return { engine, voice: best };
    }
  }
  return null;
}

// ---------------------------------------------------------------- history

/** Cached for the life of the worker; the file ships with the extension and
 *  cannot change under a running instance. */
let stampPromise = null;

/** `combined` from shared/fingerprint.json, stamped onto saved positions so a
 *  later rules change is detectable rather than silent. See spec 7.1. */
function rulesStamp() {
  stampPromise ??= fetch(chrome.runtime.getURL('src/shared/fingerprint.json'))
    .then((r) => r.json())
    .then((f) => f.combined ?? null)
    .catch(() => null); // an unknown stamp is unknown, never "changed"
  return stampPromise;
}

/**
 * Write the current position to history.
 *
 * Called on every stop and periodically while reading, not on every sentence:
 * extension local storage has a write-rate quota, and a position that is a few
 * sentences stale is a far smaller problem than a throttled write that loses
 * the position entirely.
 */
async function savePosition() {
  const state = await getState();
  if (!state.url || !state.texts.length) return;
  await history.record({
    url: state.url,
    title: state.title,
    total: state.texts.length,
    voiceKey: state.voiceKey,
    anchor: capture(state.texts, state.index, await rulesStamp()),
  });
}

/** How often, in sentences, to persist while reading. */
const SAVE_EVERY = 10;

// ------------------------------------------------------------ page advance

/**
 * Follow the next-page link, if there is one we trust.
 *
 * Confidence gating lives here rather than in the content script because this
 * is where the consequence lands: following a bad guess navigates away from
 * what someone was reading, and they may not know what happened or how to get
 * back. A low-confidence candidate is surfaced instead, and the read ends.
 *
 * @param {State} state
 * @returns {Promise<boolean>} true when reading should continue
 */
async function advancePage(state) {
  if (state.tabId == null) return false;

  let next = null;
  try {
    next = await chrome.tabs.sendMessage(state.tabId, { type: 'find-next' });
  } catch {
    return false; // content script gone
  }
  if (!next?.url) return false;

  if (needsConfirmationFor(next)) {
    chrome.runtime.sendMessage({
      type: 'confirm-next-page', target: 'panel', next,
    }).catch(() => {});
    return false;
  }

  // Save where we finished before navigating, or the position is lost the
  // moment the document is replaced.
  await savePosition().catch(() => {});

  await chrome.tabs.update(state.tabId, { url: next.url });
  await waitForLoad(state.tabId);

  const doc = await prepareTab(state.tabId);
  if (!doc?.count) return false;

  await setState({
    index: 0,
    texts: doc.texts,
    exact: doc.exact ?? [],
    url: doc.url,
    title: doc.title,
  });
  return true;
}

/** Mirrors the rule in shared/pagination.json without loading the file twice:
 *  the content script owns the strategies, the worker only reads the verdict. */
function needsConfirmationFor(next) {
  return next.confidence !== 'high' && next.confidence !== 'medium';
}

/** @param {number} tabId */
function waitForLoad(tabId) {
  return new Promise((resolve) => {
    const done = (id, info) => {
      if (id !== tabId || info.status !== 'complete') return;
      chrome.tabs.onUpdated.removeListener(done);
      clearTimeout(timer);
      resolve();
    };
    // A page that never reports complete must not hang the read loop forever.
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(done);
      resolve();
    }, 15_000);
    chrome.tabs.onUpdated.addListener(done);
  });
}

// ---------------------------------------------------------------- reading

/**
 * Attach to a tab and turn it into a sentence list.
 * @param {number} tabId
 * @param {{selectionOnly?: boolean}} opts
 */
async function prepareTab(tabId, opts = {}) {
  // A tab that already answers is one of our own pages — the PDF viewer loads
  // the reader itself, and injecting into an extension page is not permitted
  // anyway. Asking first is cheaper than special-casing the URL.
  try {
    const existing = await chrome.tabs.sendMessage(tabId, { type: 'build', ...opts });
    if (existing) return existing;
  } catch {
    // Nothing listening yet, which is the ordinary case.
  }

  await chrome.scripting.insertCSS({
    target: { tabId },
    files: ['src/content/highlight.css'],
  });
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ['src/content/loader.js'],
  });
  return chrome.tabs.sendMessage(tabId, { type: 'build', ...opts });
}

/** Our reading view for a PDF, since Chrome's own viewer is closed to us. */
function viewerUrl(pdfUrl) {
  return chrome.runtime.getURL(`src/pdf/viewer.html?url=${encodeURIComponent(pdfUrl)}`);
}

/** @param {number} tabId @param {{selectionOnly?: boolean, index?: number}} opts */
async function startReading(tabId, opts = {}) {
  await stopReading({ keepDocument: false });

  // A PDF has to be opened in our own viewer first: Chrome's built-in one is a
  // separate extension we cannot inject into. Navigating rather than opening a
  // new tab keeps the back button meaningful.
  if (!opts.selectionOnly) {
    const tab = await chrome.tabs.get(tabId).catch(() => null);
    if (tab && looksLikePdf(tab.url)) {
      await chrome.tabs.update(tabId, { url: viewerUrl(tab.url) });
      await waitForLoad(tabId);
    }
  }

  await setState({ status: 'loading', tabId });
  let doc;
  try {
    doc = await prepareTab(tabId, opts);
  } catch (e) {
    await setState({ ...BLANK, status: 'idle' });
    throw e;
  }
  if (!doc || doc.error || !doc.count) {
    await setState({ ...BLANK, status: 'idle' });
    return;
  }

  await setState({
    status: 'speaking',
    tabId,
    index: opts.index ?? 0,
    texts: doc.texts,
    exact: doc.exact ?? [],
    url: doc.url,
    title: doc.title,
  });

  runLoop().catch((e) => console.error('[earmark] read loop failed', e));
}

/**
 * Speak sentences until we run out, are paused, or are stopped.
 * Re-reads state each iteration rather than closing over it, so a skip or a
 * speed change lands on the very next sentence.
 */
async function runLoop() {
  for (;;) {
    const state = await getState();
    if (state.status !== 'speaking') return;
    if (state.index >= state.texts.length) {
      // Out of sentences on this page. Try to continue rather than stopping
      // dead at the fold, which is what the incumbent does.
      if (state.autoAdvance && await advancePage(state)) continue;
      await setState({ status: 'idle' });
      notifyTab(state.tabId, { type: 'stop' });
      return;
    }

    notifyTab(state.tabId, { type: 'paint-sentence', index: state.index });

    await pronunciationReady;
    // Applied here, not in the content script: this changes only what is
    // spoken. The text the DOM ranges index is untouched, so sentence
    // highlighting stays exact.
    const spoken = pronounce(state.texts[state.index]);
    const text = spoken.text;
    // Word offsets would now point into a different string, so a sentence an
    // override rewrote gets sentence highlighting only — the same rule
    // normalization already uses.
    const wordSafe = state.exact[state.index] !== false && !spoken.changed;

    // A neural voice is synthesized and played entirely inside the offscreen
    // document. Audio never crosses back here, because moving a megabyte of
    // samples per sentence through the worker would be pointless when the
    // worker cannot hold the audio graph anyway.
    if (state.voiceKey?.startsWith('kokoro:')) {
      currentAbort = new AbortController();
      try {
        await callOffscreen('neural-speak', {
          index: state.index,
          text,
          voiceKey: state.voiceKey,
          speed: state.speed,
          volume: state.volume,
        });
      } catch (e) {
        console.warn('[earmark] neural sentence failed, skipping', e);
      }
      if (currentAbort.signal.aborted) return;
      const afterNeural = await getState();
      if (afterNeural.status !== 'speaking') return;
      await setState({ index: afterNeural.index + 1 });
      if ((afterNeural.index + 1) % SAVE_EVERY === 0) await savePosition().catch(() => {});
      continue;
    }

    const resolved = await resolveVoice(state.voiceKey);
    if (!resolved) {
      await setState({ status: 'idle' });
      return;
    }
    const { engine, voice } = resolved;

    currentAbort = new AbortController();

    try {
      for await (const chunk of engine.synthesize(text, {
        voice,
        speed: state.speed,
        volume: state.volume,
        signal: currentAbort.signal,
      })) {
        // Word offsets index the spoken text. When normalization changed the
        // sentence, those offsets do not line up with the original the DOM
        // Ranges were built from, so the highlight would land on the wrong
        // words. Better no word highlight than a wrong one; the sentence
        // highlight is exact either way.
        if (chunk.kind === 'timings' && wordSafe) {
          for (const w of chunk.words) {
            notifyTab(state.tabId, {
              type: 'paint-word', charStart: w.charStart, charEnd: w.charEnd,
            });
          }
        }
        // Buffered engines hand us samples to schedule; the system engine
        // speaks on its own and only reports `external` and `timings`.
        if (chunk.kind === 'audio') {
          await callOffscreen('audio-chunk', {
            index: state.index,
            // Structured clone handles a typed array, but not every path
            // preserves the view, so send a plain buffer.
            pcm: chunk.pcm.buffer,
            sampleRate: chunk.sampleRate,
            playbackRate: engine.appliesSpeedInternally ? 1 : state.speed,
          });
        }
      }
    } catch (e) {
      console.warn('[earmark] sentence failed, skipping', e);
    }

    if (currentAbort.signal.aborted) return; // a skip or stop already moved us
    const after = await getState();
    if (after.status !== 'speaking') return;
    await setState({ index: after.index + 1 });
    if ((after.index + 1) % SAVE_EVERY === 0) await savePosition().catch(() => {});
  }
}

/** @param {{keepDocument?: boolean}} opts */
async function stopReading(opts = {}) {
  currentAbort?.abort();
  currentAbort = null;
  chrome.tts.stop();
  // Before clearing state, not after: stopping is exactly when the position
  // matters most, and BLANK would erase what we are trying to remember.
  await savePosition().catch(() => {});
  const state = await getState();
  notifyTab(state.tabId, { type: 'stop' });
  // Cut queued audio, but keep the document when a read continues — a skip or a
  // voice change stops and immediately restarts, and recreating the document
  // each time would add an audible gap.
  if (opts.keepDocument) await callOffscreen('stop').catch(() => {});
  else await closeOffscreen();
  if (opts.keepDocument) await setState({ status: 'paused' });
  else await setState({ ...BLANK });
}

async function togglePlay() {
  const state = await getState();
  if (state.status === 'speaking') {
    currentAbort?.abort();
    chrome.tts.stop();
    await setState({ status: 'paused' });
    return;
  }
  if (state.status === 'paused' && state.texts.length) {
    await setState({ status: 'speaking' });
    runLoop().catch(() => {});
    return;
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab?.id) await startReading(tab.id);
}

/** @param {number} delta */
async function skip(delta) {
  const state = await getState();
  if (!state.texts.length) return;
  const index = Math.min(Math.max(state.index + delta, 0), state.texts.length - 1);
  await setState({ index });
  currentAbort?.abort();
  chrome.tts.stop();
  if (state.status === 'speaking') runLoop().catch(() => {});
  else notifyTab(state.tabId, { type: 'paint-sentence', index });
}

/** @param {number} factor */
async function changeSpeed(factor) {
  const state = await getState();
  const speed = Math.round(Math.min(Math.max(state.speed * factor, 0.5), 4) * 20) / 20;
  await setState({ speed });
  if (state.status === 'speaking') {
    currentAbort?.abort();
    chrome.tts.stop();
    runLoop().catch(() => {});
  }
}

function notifyTab(tabId, msg) {
  if (tabId == null) return;
  chrome.tabs.sendMessage(tabId, msg).catch(() => {});
}

// ---------------------------------------------------------------- wiring

// No chrome.action.onClicked listener: a default_popup is declared, so that
// event never fires. The popup's play button routes through 'toggle-play'.

chrome.commands.onCommand.addListener((command) => {
  const run = {
    'toggle-play': () => togglePlay(),
    'stop': () => stopReading(),
    'next-sentence': () => skip(1),
    'prev-sentence': () => skip(-1),
    'speed-up': () => changeSpeed(1.15),
    'speed-down': () => changeSpeed(1 / 1.15),
    'read-selection': async () => {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab?.id) await startReading(tab.id, { selectionOnly: true });
    },
  }[command];
  run?.().catch((e) => console.error('[earmark] command failed', command, e));
});

chrome.runtime.onMessage.addListener((msg, sender, respond) => {
  // The offscreen document listens on this same channel and shares type names
  // with us — both understand 'stop'. Without this guard, telling the offscreen
  // document to stop would also stop the whole read here.
  if (msg?.target && msg.target !== 'worker') return false;

  const handlers = {
    'get-state': async () => getState(),
    'get-voices': async () => {
      const out = [];
      for (const engine of ENGINES) {
        if (await engine.available()) out.push(...(await engine.voices()));
      }
      // Neural voices live in the offscreen document, because an 88 MB session
      // cannot survive this worker being suspended. Asking for them creates
      // that document, which is why it is a query rather than a preload.
      try {
        const res = await callOffscreen('neural-voices');
        if (res?.voices) out.push(...res.voices);
      } catch {
        // No offscreen document, or the model failed to load. System voices
        // still work, so this is a missing tier rather than an error.
      }
      return out;
    },
    'toggle-play': async () => { await togglePlay(); return getState(); },
    'stop': async () => { await stopReading(); return getState(); },
    'skip': async () => { await skip(msg.delta); return getState(); },
    'set-speed': async () => setState({ speed: msg.speed }),
    'set-voice': async () => {
      await setState({ voiceKey: msg.voiceKey });
      const s = await getState();
      if (s.status === 'speaking') { currentAbort?.abort(); chrome.tts.stop(); runLoop().catch(() => {}); }
      return s;
    },
    'read-from': async () => {
      const s = await getState();
      await setState({ index: msg.index, status: 'speaking' });
      currentAbort?.abort();
      chrome.tts.stop();
      if (s.status !== 'speaking') runLoop().catch(() => {});
      return getState();
    },
    'set-auto-advance': async () => setState({ autoAdvance: Boolean(msg.on) }),

    /**
     * The user edited their pronunciations.
     *
     * Reloaded immediately rather than on the worker's next wake, because a
     * setting that takes effect at some unpredictable later point is
     * indistinguishable from one that does not work.
     */
    'pronunciations-changed': async () => {
      const { userPronunciations } = await chrome.storage.local.get('userPronunciations');
      loadUser(userPronunciations ?? []);
      return { ok: true, count: (userPronunciations ?? []).length };
    },

    /** Whether neural voices are ready, and if not, precisely why. */
    'neural-status': async () => {
      try {
        return await callOffscreen('neural-voices');
      } catch (e) {
        return { voices: [], blocked: { reason: 'unavailable', detail: String(e) } };
      }
    },

    /** Fetch the voice model, reporting progress to whoever asked. */
    'neural-download': async () => {
      try {
        await callOffscreen('neural-prepare');
        return { ok: true };
      } catch (e) {
        return { ok: false, error: String(e) };
      }
    },

    'neural-evict': async () => {
      try {
        await callOffscreen('neural-evict');
        return { ok: true };
      } catch {
        return { ok: false };
      }
    },

    /** Settings changed; the content script picks colours up on the next read. */
    'prefs-changed': async () => {
      const tabs = await chrome.tabs.query({});
      for (const t of tabs) {
        if (t.id != null) chrome.tabs.sendMessage(t.id, { type: 'prefs', prefs: msg.prefs }).catch(() => {});
      }
      return { ok: true };
    },

    /** The page grew under us, so extend the queue rather than restarting.
     *  The index is untouched: whatever is being spoken keeps being spoken. */
    'document-grew': async () => {
      const s = await getState();
      if (s.tabId !== sender.tab?.id) return { ok: false };
      await setState({ texts: msg.texts, exact: msg.exact ?? [] });
      return { ok: true, total: msg.texts.length };
    },

    /** Follow a next-page link the user confirmed after we declined to guess. */
    'follow-next': async () => {
      const s = await getState();
      if (s.tabId == null || !msg.url) return { ok: false };
      await savePosition().catch(() => {});
      await chrome.tabs.update(s.tabId, { url: msg.url });
      await waitForLoad(s.tabId);
      const doc = await prepareTab(s.tabId);
      if (!doc?.count) return { ok: false };
      await setState({
        status: 'speaking', index: 0, texts: doc.texts,
        exact: doc.exact ?? [], url: doc.url, title: doc.title,
      });
      runLoop().catch(() => {});
      return { ok: true };
    },

    'get-history': async () => history.list(),
    'prune-history': async () => history.prune(),
    'clear-history': async () => { await history.clear(); return { ok: true }; },

    /**
     * Reopen a history entry and pick up where it left off.
     *
     * The saved position is resolved against a *fresh* extraction rather than
     * the stored body, because the page may have changed since. See
     * docs/anchor-vocabulary.md for what each outcome means.
     */
    'resume': async () => {
      const entry = await history.getEntry(msg.id);
      if (!entry) return { error: 'no such entry' };

      const tab = await chrome.tabs.create({ url: entry.url, active: true });
      // Wait for the page before injecting, or extraction runs against a blank
      // document and finds nothing.
      await new Promise((resolve) => {
        const done = (id, info) => {
          if (id !== tab.id || info.status !== 'complete') return;
          chrome.tabs.onUpdated.removeListener(done);
          resolve();
        };
        chrome.tabs.onUpdated.addListener(done);
      });

      const doc = await prepareTab(tab.id);
      if (!doc?.count) return { error: 'nothing to read' };

      const found = locate(entry.anchor, doc.texts, await rulesStamp());
      await setState({
        status: 'speaking',
        tabId: tab.id,
        index: found.index,
        texts: doc.texts,
        exact: doc.exact ?? [],
        url: doc.url,
        title: doc.title,
      });
      runLoop().catch(() => {});
      return { ...(await getState()), resume: found };
    },

    'read-here': async () => {
      const tabId = sender.tab?.id
        ?? (await chrome.tabs.query({ active: true, currentWindow: true }))[0]?.id;
      if (tabId) await startReading(tabId, msg);
      return getState();
    },
  };
  const handler = handlers[msg.type];
  if (!handler) return false;
  handler().then(respond, (e) => respond({ error: String(e) }));
  return true;
});

// The session port exists purely to hold the worker open while reading.
chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== 'earmark-session') return;
  port.onDisconnect.addListener(async () => {
    const state = await getState();
    // The page went away underneath us; do not keep speaking into nothing.
    if (state.status === 'speaking') await stopReading();
  });
});

chrome.runtime.onInstalled.addListener((details) => {
  chrome.contextMenus.create({
    id: 'earmark-read-selection',
    // "aloud" here is the English word, not the old product name. A blanket
    // rename turned this into "Read this earmark" and shipped it as the only
    // wording a user sees before installing anything else.
    title: 'Read this aloud',
    contexts: ['selection'],
  });
  chrome.storage.session.set({ state: BLANK });

  // First install only. Opening a tab on every update is the behaviour that
  // gets extensions uninstalled.
  if (details.reason === 'install') {
    chrome.tabs.create({ url: chrome.runtime.getURL('src/welcome/welcome.html') });
  }
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === 'earmark-read-selection' && tab?.id) {
    startReading(tab.id, { selectionOnly: true }).catch((e) => console.error('[earmark]', e));
  }
});
