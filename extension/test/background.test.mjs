/**
 * The service worker: the only thing that decides what gets read next.
 *
 * It had no tests. The coverage list carried it as "orchestration over
 * chrome.tabs, storage.session and messaging", which describes the obstacle
 * rather than giving a reason, and the obstacle turned out to be a hundred
 * lines of stand-in. Every failure this file can have is invisible from
 * outside and miserable to diagnose from inside: playback stopping a sentence
 * early, a skip landing in the wrong place, a position lost exactly when it
 * mattered.
 *
 * Nothing in the module under test is replaced. It is imported fresh per case,
 * because a service worker holds its transitions in `chrome.storage.session`
 * precisely so it can be killed and restarted — so a cached module instance
 * would test something Chrome never runs.
 */

import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { makeChrome, ask } from './fixtures/fake-chrome.mjs';

const root = new URL('../', import.meta.url);

/** Files the worker fetches from inside the package at wake. */
const PACKAGED = {
  'src/shared/pronunciation.json': JSON.parse(
    readFileSync(new URL('src/shared/pronunciation.json', root), 'utf8'),
  ),
  'src/shared/fingerprint.json': JSON.parse(
    readFileSync(new URL('src/shared/fingerprint.json', root), 'utf8'),
  ),
};

/** A page of four sentences, which is the shape every test starts from. */
const PAGE = {
  count: 4,
  texts: ['One two three.', 'Four five six.', 'Seven eight nine.', 'Ten eleven twelve.'],
  exact: [true, true, true, true],
  url: 'https://example.com/article',
  title: 'An Article',
};

let h;

/**
 * Load the worker against a fresh stand-in.
 *
 * @param {{document?: object|null, session?: object}} [setup]
 */
async function wake(setup = {}) {
  h = makeChrome();
  globalThis.chrome = h.chrome;
  globalThis.fetch = async (url) => {
    const rel = String(url).replace('chrome-extension://test/', '');
    if (!(rel in PACKAGED)) throw new Error(`no packaged file ${rel}`);
    return { ok: true, json: async () => PACKAGED[rel] };
  };

  if (setup.session) await h.chrome.storage.session.set(setup.session);
  h.setDocument(setup.document === undefined ? PAGE : setup.document);
  // A tab answers unless a test is specifically about injecting into one that
  // does not.
  h.setContentScript(setup.contentScript ?? true);

  await import(`../src/background/index.js?t=${Math.random()}`);
  // The worker's own startup awaits a packaged fetch before it can speak.
  await new Promise((r) => setImmediate(r));
  return h;
}

/**
 * Let the worker run until it stops changing anything.
 *
 * A fixed number of turns is the obvious implementation and it is a slow race:
 * each sentence costs several awaits plus a timer, so "enough turns" depends on
 * the case, and the failure mode is a test that passes on a fast machine.
 * Waiting for quiet instead makes the test independent of how many steps a read
 * happens to take.
 */
async function settle(quietTurns = 12, cap = 2000) {
  const fingerprint = () => `${h.calls.tabMessages.length}:${h.calls.spoken.length}`
    + `:${h.calls.tabUpdates.length}:${JSON.stringify(h.chrome.storage.session.data)}`;
  let last = fingerprint();
  let quiet = 0;
  for (let i = 0; i < cap && quiet < quietTurns; i++) {
    await new Promise((r) => setTimeout(r, 0));
    const now = fingerprint();
    if (now === last) quiet++;
    else { quiet = 0; last = now; }
  }
}

const state = () => ask(h, { type: 'get-state' });

beforeEach(() => {
  delete globalThis.chrome;
  delete globalThis.fetch;
});

// ------------------------------------------------------------ reading

test('reading a page walks every sentence and then stops', async () => {
  await wake();
  await ask(h, { type: 'toggle-play' });
  await settle();

  const s = await state();
  assert.equal(s.status, 'idle', 'the loop must end rather than spin');
  assert.equal(s.index, PAGE.texts.length);

  // The page is told which sentence to paint, once each, in order.
  const painted = h.calls.tabMessages
    .filter((m) => m.type === 'paint-sentence')
    .map((m) => m.index);
  assert.deepEqual(painted, [0, 1, 2, 3]);
});

test('the page is told to stop when the document runs out', async () => {
  await wake();
  await ask(h, { type: 'toggle-play' });
  await settle();
  assert.ok(h.calls.tabMessages.some((m) => m.type === 'stop'),
    'the highlight would otherwise stay lit on the last sentence forever');
});

test('a page with nothing readable does not enter the speaking state', async () => {
  await wake({ document: { count: 0, texts: [], exact: [] } });
  await ask(h, { type: 'toggle-play' });
  await settle();

  const s = await state();
  assert.equal(s.status, 'idle');
  assert.deepEqual(s.texts, []);
});

test('reading a tab with no content script injects one', async () => {
  await wake({ contentScript: false });

  await ask(h, { type: 'toggle-play' });
  await settle();

  assert.ok(h.calls.executed.some((e) => e.css), 'the highlight stylesheet must go in');
  assert.ok(h.calls.executed.some((e) => e.js), 'the loader must go in');
});

// ------------------------------------------------------- pause and resume

test('pausing stops the loop and keeps the place', async () => {
  await wake();
  await ask(h, { type: 'toggle-play' });
  await new Promise((r) => setImmediate(r));
  await ask(h, { type: 'toggle-play' });
  await settle();

  const s = await state();
  assert.equal(s.status, 'paused');
  assert.ok(s.index < PAGE.texts.length, 'pausing at the end is not pausing');
  assert.deepEqual(s.texts, PAGE.texts, 'the document must survive a pause');
});

test('resuming continues from where it paused, not from the top', async () => {
  await wake({
    session: {
      state: {
        status: 'paused', tabId: 1, index: 2, texts: PAGE.texts, exact: PAGE.exact,
        voiceKey: null, speed: 1, volume: 1, url: PAGE.url, title: PAGE.title,
        autoAdvance: false,
      },
    },
  });

  await ask(h, { type: 'toggle-play' });
  await settle();

  const painted = h.calls.tabMessages
    .filter((m) => m.type === 'paint-sentence').map((m) => m.index);
  assert.deepEqual(painted, [2, 3], 'the first two sentences must not be read again');
});

// ---------------------------------------------------------------- skip

test('skipping moves one sentence and repaints', async () => {
  await wake({
    session: {
      state: {
        status: 'paused', tabId: 1, index: 1, texts: PAGE.texts, exact: PAGE.exact,
        voiceKey: null, speed: 1, volume: 1, url: PAGE.url, title: PAGE.title,
        autoAdvance: false,
      },
    },
  });

  await ask(h, { type: 'skip', delta: 1 });
  assert.equal((await state()).index, 2);

  await ask(h, { type: 'skip', delta: -1 });
  assert.equal((await state()).index, 1);

  const painted = h.calls.tabMessages
    .filter((m) => m.type === 'paint-sentence').map((m) => m.index);
  assert.deepEqual(painted, [2, 1], 'a skip while paused still moves the highlight');
});

test('skipping cannot run off either end', async () => {
  await wake({
    session: {
      state: {
        status: 'paused', tabId: 1, index: 0, texts: PAGE.texts, exact: PAGE.exact,
        voiceKey: null, speed: 1, volume: 1, url: PAGE.url, title: PAGE.title,
        autoAdvance: false,
      },
    },
  });

  await ask(h, { type: 'skip', delta: -5 });
  assert.equal((await state()).index, 0, 'a negative index would read nothing at all');

  await ask(h, { type: 'skip', delta: 99 });
  assert.equal((await state()).index, PAGE.texts.length - 1,
    'past the end would end the read instead of moving to the last sentence');
});

test('skipping with no document does nothing rather than throwing', async () => {
  await wake();
  await ask(h, { type: 'skip', delta: 1 });
  assert.equal((await state()).index, 0);
});

test('reading from a chosen sentence starts there', async () => {
  // This is what clicking a history entry does, and what alt-clicking a
  // paragraph does.
  await wake({
    session: {
      state: {
        status: 'paused', tabId: 1, index: 0, texts: PAGE.texts, exact: PAGE.exact,
        voiceKey: null, speed: 1, volume: 1, url: PAGE.url, title: PAGE.title,
        autoAdvance: false,
      },
    },
  });

  await ask(h, { type: 'read-from', index: 2 });
  await settle();

  const painted = h.calls.tabMessages
    .filter((m) => m.type === 'paint-sentence').map((m) => m.index);
  assert.deepEqual(painted, [2, 3]);
});

// --------------------------------------------------------------- speed

test('speed steps are clamped to what the engines accept', async () => {
  await wake();

  for (let i = 0; i < 20; i++) h.events.onCommand.dispatch('speed-up');
  await settle();
  assert.ok((await state()).speed <= 4, 'above 4 is unintelligible on every engine');

  for (let i = 0; i < 40; i++) h.events.onCommand.dispatch('speed-down');
  await settle();
  assert.ok((await state()).speed >= 0.5, 'below 0.5 is slower than any engine renders well');
});

test('a speed step lands on a value the settings page can show', async () => {
  // Rounded to a twentieth, or the readout drifts into figures like 1.1500001.
  await wake();
  h.events.onCommand.dispatch('speed-up');
  await settle();

  const { speed } = await state();
  assert.equal(speed, Math.round(speed * 20) / 20);
  assert.ok(speed > 1, 'speed-up must actually speed up');
});

test('setting a speed while paused does not start speaking', async () => {
  await wake();
  await ask(h, { type: 'set-speed', speed: 2 });
  assert.equal((await state()).status, 'idle');
  assert.equal((await state()).speed, 2);
});

// -------------------------------------------------------- page advance

test('auto-advance is off by default, so a read ends at the foot of the page', async () => {
  // The accessible default: navigating unasked loses the reader's place in a
  // way they may not know how to undo.
  await wake();
  h.setNextPage({ url: 'https://example.com/page/2', confidence: 'high' });

  await ask(h, { type: 'toggle-play' });
  await settle();

  assert.equal((await state()).status, 'idle');
  assert.deepEqual(h.calls.tabUpdates, [], 'the tab must not have been navigated');
});

test('with auto-advance on, a confident next page is followed', async () => {
  await wake();
  await ask(h, { type: 'set-auto-advance', on: true });
  h.setNextPage({ url: 'https://example.com/page/2', confidence: 'high' });

  await ask(h, { type: 'toggle-play' });
  await settle();

  assert.ok(h.calls.tabUpdates.some((u) => u.url === 'https://example.com/page/2'),
    'a high-confidence link is followed without asking');
});

test('a low-confidence next page is offered rather than taken', async () => {
  // Following a bad guess navigates away from what someone was reading, and
  // they may not know what happened or how to get back.
  await wake();
  await ask(h, { type: 'set-auto-advance', on: true });
  h.setNextPage({ url: 'https://example.com/maybe', confidence: 'low' });

  await ask(h, { type: 'toggle-play' });
  await settle();

  assert.deepEqual(h.calls.tabUpdates, [], 'a guess this weak must not navigate');
  assert.ok(
    h.calls.runtimeMessages.some((m) => m.type === 'confirm-next-page'),
    'and the reader must be told there is a next page to choose',
  );
});

// ------------------------------------------------------------- messaging

test('a message meant for the offscreen document is not answered here', async () => {
  // Both listen on the same channel and both understand 'stop'. Without the
  // guard, telling the offscreen document to stop stops the whole read.
  await wake({
    session: {
      state: {
        status: 'speaking', tabId: 1, index: 1, texts: PAGE.texts, exact: PAGE.exact,
        voiceKey: null, speed: 1, volume: 1, url: PAGE.url, title: PAGE.title,
        autoAdvance: false,
      },
    },
  });

  const answered = h.events.onMessage.dispatch(
    { type: 'stop', target: 'offscreen' }, {}, () => {},
  );

  assert.ok(!answered.some((r) => r === true), 'the worker claimed a message not addressed to it');
  assert.equal((await state()).status, 'speaking', 'and it stopped the read');
});

test('an unknown message is declined rather than swallowed', async () => {
  await wake();
  const answered = h.events.onMessage.dispatch({ type: 'no-such-thing' }, {}, () => {});
  assert.ok(!answered.some((r) => r === true));
});

// ----------------------------------------------------------- persistence

test('state survives the worker being killed mid-read', async () => {
  // The property the whole module is shaped around: nothing may live only in
  // module scope, because Chrome suspends this after about thirty seconds.
  await wake();
  await ask(h, { type: 'toggle-play' });
  await new Promise((r) => setImmediate(r));
  await ask(h, { type: 'toggle-play' }); // pause
  await settle();

  const before = await state();
  const session = { ...h.chrome.storage.session.data };

  // A fresh worker, with only what was written to session storage.
  await wake({ session });
  const after = await state();

  assert.equal(after.status, before.status);
  assert.equal(after.index, before.index);
  assert.deepEqual(after.texts, before.texts);
});

test('the reading position is written to history when a read stops', async () => {
  await wake();
  await ask(h, { type: 'toggle-play' });
  await settle();
  await ask(h, { type: 'stop' });
  await settle();

  const stored = Object.keys(h.chrome.storage.local.data);
  assert.ok(stored.length > 0, 'stopping is exactly when the position matters most');
});

test('stopping clears the document so the next read starts clean', async () => {
  await wake();
  await ask(h, { type: 'toggle-play' });
  await settle();
  await ask(h, { type: 'stop' });

  const s = await state();
  assert.equal(s.status, 'idle');
  assert.deepEqual(s.texts, []);
  assert.equal(s.tabId, null);
});

test('losing the page stops the read rather than speaking into nothing', async () => {
  await wake({
    session: {
      state: {
        status: 'speaking', tabId: 1, index: 1, texts: PAGE.texts, exact: PAGE.exact,
        voiceKey: null, speed: 1, volume: 1, url: PAGE.url, title: PAGE.title,
        autoAdvance: false,
      },
    },
  });

  const port = {
    name: 'executive-reader-session',
    onDisconnect: { addListener(fn) { port._fire = fn; } },
  };
  h.events.onConnect.dispatch(port);
  await port._fire();
  await settle();

  assert.equal((await state()).status, 'idle');
});

test('a port that is not ours is ignored', async () => {
  await wake();
  const port = { name: 'something-else', onDisconnect: { addListener() {} } };
  h.events.onConnect.dispatch(port);
  // Nothing to assert beyond not throwing: a stray port must not wire itself
  // into the read lifecycle.
  assert.equal((await state()).status, 'idle');
});

// ------------------------------------------------------------- first run

test('installing creates the context menu with the wording users see first', async () => {
  await wake();
  const created = [];
  h.chrome.contextMenus.create = (info) => created.push(info);

  h.events.onInstalled.dispatch({ reason: 'install' });

  assert.equal(created.length, 1);
  assert.equal(created[0].title, 'Read this aloud',
    'a blanket rename once shipped "Read this earmark" here');
  assert.deepEqual(created[0].contexts, ['selection']);
});

test('an update does not open a tab, only a first install does', async () => {
  await wake();
  h.events.onInstalled.dispatch({ reason: 'update' });
  await settle();
  assert.deepEqual(h.calls.tabsCreated, [],
    'opening a tab on every update gets extensions uninstalled');

  h.events.onInstalled.dispatch({ reason: 'install' });
  await settle();
  assert.equal(h.calls.tabsCreated.length, 1);
  assert.match(h.calls.tabsCreated[0].url, /welcome/);
});

/**
 * Settings are not part of the state of a read.
 *
 * `BLANK` is spread over the state on every stop, to clear the current
 * document. `autoAdvance` was listed there, so turning on "continue onto the
 * next page" and then pressing play reset it to false before the read began —
 * the setting could not be used at all unless it was toggled *during* a read.
 *
 * Found by the first test ever written against this file, which is the whole
 * argument for having written it: the feature had shipped, had a checkbox on
 * two surfaces, and did nothing.
 */
test('turning on auto-advance survives pressing play', async () => {
  await wake();
  await ask(h, { type: 'set-auto-advance', on: true });

  await ask(h, { type: 'toggle-play' });

  assert.equal((await state()).autoAdvance, true,
    'starting a read cleared the setting that governs how it ends');
});

test('auto-advance survives a stop, which clears everything else', async () => {
  await wake();
  await ask(h, { type: 'set-auto-advance', on: true });
  await ask(h, { type: 'toggle-play' });
  await settle();
  await ask(h, { type: 'stop' });

  const s = await state();
  assert.equal(s.autoAdvance, true);
  assert.deepEqual(s.texts, [], 'the document must still be cleared');
});

test('auto-advance survives the browser restarting', async () => {
  // Session storage is emptied on restart, so a preference kept only there is
  // a preference that resets every morning.
  await wake();
  await ask(h, { type: 'set-auto-advance', on: true });

  const local = { ...h.chrome.storage.local.data };
  await wake();                       // a fresh worker, empty session storage
  await h.chrome.storage.local.set(local);

  assert.equal((await state()).autoAdvance, true);
});

test('turning it off again is remembered too', async () => {
  // The obvious half-fix is to let the setting only ever become true.
  await wake();
  await ask(h, { type: 'set-auto-advance', on: true });
  await ask(h, { type: 'set-auto-advance', on: false });

  const local = { ...h.chrome.storage.local.data };
  await wake();
  await h.chrome.storage.local.set(local);

  assert.equal((await state()).autoAdvance, false);
});

/**
 * Auto-advance must not cycle.
 *
 * Two pages that each name the other as "next" is an ordinary shape for a
 * two-part article. The content script refuses a link pointing at the page it
 * is on, which says nothing about the page before it, so with auto-advance on
 * the reader alternated between them indefinitely.
 *
 * Found by a mutation that HUNG the suite instead of failing it: removing the
 * auto-advance condition made the loop follow a next page forever, which is
 * only possible because nothing bounded it.
 */
test('a next page already read in this session ends the read', async () => {
  await wake();
  await ask(h, { type: 'set-auto-advance', on: true });
  // The page names itself as next, one hop later: A -> B -> A.
  h.setNextPage({ url: PAGE.url, confidence: 'high' });

  // Without this a regression does not fail, it hangs: the loop follows the
  // cycle until the harness gives up, and a test that hangs reads as a broken
  // machine rather than a broken guard.
  const update = h.chrome.tabs.update.bind(h.chrome.tabs);
  let hops = 0;
  h.chrome.tabs.update = async (id, props) => {
    if (++hops > 3) throw new Error('auto-advance is cycling between pages');
    return update(id, props);
  };

  await ask(h, { type: 'toggle-play' });
  await settle();

  assert.ok(hops <= 3, 'the read cycled');

  assert.deepEqual(h.calls.tabUpdates, [],
    'the read began here, so coming back is a cycle rather than a next page');
  assert.equal((await state()).status, 'idle');
});

test('a genuine chain of pages is still followed', async () => {
  // The guard rules out revisiting, not advancing. A limit on hops would have
  // been a guess about how long an article may be; this is not.
  await wake();
  await ask(h, { type: 'set-auto-advance', on: true });

  // The tab reports a new page each time one is built, and names the next one
  // until the fourth. Starting at zero so the first build is page one.
  let page = 0;
  const original = h.chrome.tabs.sendMessage;
  h.chrome.tabs.sendMessage = async (tabId, msg) => {
    if (msg.type === 'build') {
      page++;
      return { ...PAGE, url: `https://example.com/page/${page}` };
    }
    if (msg.type === 'find-next') {
      return page < 4
        ? { url: `https://example.com/page/${page + 1}`, confidence: 'high' }
        : null;
    }
    return original.call(h.chrome.tabs, tabId, msg);
  };

  await ask(h, { type: 'toggle-play' });
  await settle();

  const followed = h.calls.tabUpdates.map((u) => u.url).filter(Boolean);
  assert.deepEqual(followed, [
    'https://example.com/page/2',
    'https://example.com/page/3',
    'https://example.com/page/4',
  ]);
});

/**
 * Only one read loop may be running.
 *
 * `skip` aborts the sentence in flight and then starts the loop again. That is
 * correct while a sentence is being spoken, and there is a window where it is
 * not: between one sentence finishing and the next controller being created,
 * `currentAbort` refers to a sentence that has already ended, so aborting it
 * stops nothing and the original loop carries on — alongside the new one.
 *
 * Two loops paint and speak the same document at once. The window is a few
 * milliseconds per sentence, which is small and is exactly the size of the gap
 * a person hits by pressing skip while listening.
 */
test('a skip between sentences does not start a second reader', async () => {
  await wake();

  // Skip once, at the moment a sentence ends and before the next begins.
  let skipped = false;
  const speak = h.chrome.tts.speak.bind(h.chrome.tts);
  h.chrome.tts.speak = (text, opts) => {
    const wrapped = {
      ...opts,
      onEvent: (e) => {
        opts.onEvent(e);
        if (e.type === 'end' && !skipped) {
          skipped = true;
          ask(h, { type: 'skip', delta: 1 }).catch(() => {});
        }
      },
    };
    speak(text, wrapped);
  };

  await ask(h, { type: 'toggle-play' });
  await settle();

  const painted = h.calls.tabMessages
    .filter((m) => m.type === 'paint-sentence').map((m) => m.index);
  const duplicates = painted.filter((v, i) => painted.indexOf(v) !== i);

  assert.deepEqual(duplicates, [],
    `two readers painted the same sentences: ${painted.join(',')}`);
});

/**
 * Three presses of skip move three sentences.
 *
 * Every message handler is its own async function, so three presses in quick
 * succession interleave: each reads the index before any of them has written
 * one, all three compute the same next index, and the reader moves one
 * sentence for three presses. The same shape applies to the speed steps.
 *
 * This is a lost update, not a race in the read loop, and it needed its own
 * fix: the loop generation above settles which reader survives and says
 * nothing about what the index should be when they all started from the same
 * one.
 */
test('three skips in quick succession move three sentences', async () => {
  await wake({
    session: {
      state: {
        status: 'paused', tabId: 1, index: 0, texts: PAGE.texts, exact: PAGE.exact,
        voiceKey: null, speed: 1, volume: 1, url: PAGE.url, title: PAGE.title,
        autoAdvance: false,
      },
    },
  });

  await Promise.all([
    ask(h, { type: 'skip', delta: 1 }),
    ask(h, { type: 'skip', delta: 1 }),
    ask(h, { type: 'skip', delta: 1 }),
  ]);

  assert.equal((await state()).index, 3, 'presses were lost to each other');
});

test('speed steps in quick succession all count', async () => {
  await wake();
  const before = (await state()).speed;

  await Promise.all([
    ask(h, { type: 'set-speed', speed: before }),
    new Promise((r) => { h.events.onCommand.dispatch('speed-up'); setImmediate(r); }),
    new Promise((r) => { h.events.onCommand.dispatch('speed-up'); setImmediate(r); }),
  ]);
  await settle();

  const after = (await state()).speed;
  assert.ok(after > before * 1.2, `speed went ${before} -> ${after}; a step was lost`);
});

test('a read that finished does not resume when a later one is stopped', async () => {
  // A retired loop must stay retired. Bumping the generation on every start is
  // only half of it; the check has to happen at the top of each iteration, not
  // only where a sentence is awaited.
  await wake();
  await ask(h, { type: 'toggle-play' });
  await settle();
  const afterFirst = h.calls.tabMessages.filter((m) => m.type === 'paint-sentence').length;

  await ask(h, { type: 'stop' });
  await settle();

  const afterStop = h.calls.tabMessages.filter((m) => m.type === 'paint-sentence').length;
  assert.equal(afterStop, afterFirst, 'something kept painting after the read was stopped');
});
