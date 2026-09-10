/**
 * Settings.
 *
 * Two things here are not cosmetic. Users must be able to remove the 88 MB
 * voice download — carrying storage you cannot see or reclaim is not
 * acceptable — and they must be able to change highlight colours, because a
 * fixed pair cannot suit every kind of colour vision.
 */

const $ = (id) => document.getElementById(id);
const send = (msg) => chrome.runtime.sendMessage(msg).catch(() => null);

/** Matches the defaults in content/highlight.css. */
const DEFAULT_COLOURS = { sentence: '#ffd666', word: '#ffa726' };

const PREF_KEY = 'prefs';

/** @type {Record<string, any>} */
let prefs = {};

function say(message) {
  $('status').textContent = message;
}

async function loadPrefs() {
  const got = await chrome.storage.local.get(PREF_KEY).catch(() => ({}));
  prefs = got[PREF_KEY] ?? {};
}

async function savePrefs(patch) {
  prefs = { ...prefs, ...patch };
  await chrome.storage.local.set({ [PREF_KEY]: prefs });
  // The content script reads colours from storage on each read, so a change
  // takes effect on the next sentence rather than needing a reload.
  chrome.runtime.sendMessage({ type: 'prefs-changed', prefs }).catch(() => {});
}

// ------------------------------------------------------------------- voice

async function fillVoices() {
  const voices = await send({ type: 'get-voices' });
  const select = $('voice');
  select.replaceChildren();

  if (!Array.isArray(voices) || !voices.length) {
    select.append(new Option('No voices found', ''));
    return;
  }

  const byEngine = new Map();
  for (const v of voices) {
    const group = v.engineId === 'kokoro' ? 'Natural voices (on this device)' : 'System voices';
    if (!byEngine.has(group)) byEngine.set(group, []);
    byEngine.get(group).push(v);
  }

  for (const [label, list] of byEngine) {
    const group = document.createElement('optgroup');
    group.label = label;
    for (const v of list) {
      const opt = new Option(v.note ? `${v.name} — ${v.note}` : v.name, v.key);
      group.append(opt);
    }
    select.append(group);
  }

  const state = await send({ type: 'get-state' });
  if (state?.voiceKey) select.value = state.voiceKey;
}

async function refreshNeural() {
  const res = await send({ type: 'neural-status' });
  const blocked = res?.blocked;

  if (!blocked) {
    const backend = res?.backend === 'webgpu' ? 'using the GPU' : 'using the processor';
    $('neural-state').textContent = `Natural voices are ready, ${backend}.`;
    $('download').hidden = true;
    $('evict').hidden = false;
    return;
  }

  // Name the specific absence. "Unavailable" for both a missing dictionary and
  // a missing download sends people to the wrong fix.
  $('neural-state').textContent = blocked.detail;
  $('download').hidden = blocked.reason !== 'no-model';
  $('evict').hidden = true;
}

$('download').addEventListener('click', async () => {
  $('download').disabled = true;
  $('progress-wrap').hidden = false;
  $('progress-text').textContent = 'Starting…';

  const res = await send({ type: 'neural-download' });
  $('download').disabled = false;
  $('progress-wrap').hidden = true;
  say(res?.ok ? 'Natural voices are ready.' : 'The download did not finish.');
  await refreshNeural();
  await fillVoices();
});

$('evict').addEventListener('click', async () => {
  await send({ type: 'neural-evict' });
  say('Downloaded voices removed.');
  await refreshNeural();
  await fillVoices();
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type !== 'neural-progress') return;
  const { loaded, total } = msg;
  const pct = total ? Math.round((loaded / total) * 100) : null;
  $('progress').value = pct ?? 0;
  if (pct === null) $('progress').removeAttribute('value'); // indeterminate
  $('progress-text').textContent = pct === null
    ? `${(loaded / 1e6).toFixed(0)} MB downloaded`
    : `${pct}% — ${(loaded / 1e6).toFixed(0)} of ${(total / 1e6).toFixed(0)} MB`;
});

// ----------------------------------------------------------------- wiring

$('voice').addEventListener('change', (e) => {
  send({ type: 'set-voice', voiceKey: e.target.value });
  say('Voice changed.');
});

$('speed').addEventListener('input', (e) => {
  $('speed-out').textContent = `${Number(e.target.value).toFixed(2)}×`;
});
$('speed').addEventListener('change', (e) => {
  send({ type: 'set-speed', speed: Number(e.target.value) });
});

$('auto-advance').addEventListener('change', (e) => {
  send({ type: 'set-auto-advance', on: e.target.checked });
});

for (const id of ['code', 'urls']) {
  $(id).addEventListener('change', (e) => savePrefs({ [id]: e.target.value }));
}

for (const [id, key] of [['sentence-colour', 'sentenceColour'], ['word-colour', 'wordColour']]) {
  $(id).addEventListener('change', (e) => {
    savePrefs({ [key]: e.target.value });
    say('Highlight colour updated. It applies from the next sentence.');
  });
}

$('reset-colours').addEventListener('click', () => {
  $('sentence-colour').value = DEFAULT_COLOURS.sentence;
  $('word-colour').value = DEFAULT_COLOURS.word;
  savePrefs({ sentenceColour: DEFAULT_COLOURS.sentence, wordColour: DEFAULT_COLOURS.word });
  say('Highlight colours reset.');
});

$('retain-days').addEventListener('input', (e) => {
  $('retain-out').textContent = `${e.target.value} days`;
});
$('retain-days').addEventListener('change', (e) => savePrefs({ retainDays: Number(e.target.value) }));

$('retain-count').addEventListener('input', (e) => {
  $('count-out').textContent = `${e.target.value} pages`;
});
$('retain-count').addEventListener('change', (e) => savePrefs({ retainCount: Number(e.target.value) }));

$('prune').addEventListener('click', async () => {
  const removed = await send({ type: 'prune-history' });
  say(removed ? `Removed ${removed} old ${removed === 1 ? 'entry' : 'entries'}.` : 'Nothing old enough to remove.');
});

$('clear-history').addEventListener('click', async () => {
  await send({ type: 'clear-history' });
  say('All history deleted.');
});

// ------------------------------------------------------------------- init

await loadPrefs();

$('code').value = prefs.code ?? 'announce';
$('urls').value = prefs.urls ?? 'domain';
$('sentence-colour').value = prefs.sentenceColour ?? DEFAULT_COLOURS.sentence;
$('word-colour').value = prefs.wordColour ?? DEFAULT_COLOURS.word;
$('retain-days').value = String(prefs.retainDays ?? 90);
$('retain-out').textContent = `${prefs.retainDays ?? 90} days`;
$('retain-count').value = String(prefs.retainCount ?? 500);
$('count-out').textContent = `${prefs.retainCount ?? 500} pages`;

const state = await send({ type: 'get-state' });
if (state) {
  $('speed').value = String(state.speed ?? 1);
  $('speed-out').textContent = `${Number(state.speed ?? 1).toFixed(2)}×`;
  $('auto-advance').checked = Boolean(state.autoAdvance);
}

const manifest = chrome.runtime.getManifest();
$('version').textContent = `${manifest.name}, version ${manifest.version}.`;

await fillVoices();
await refreshNeural();

// ----------------------------------------------------- pronunciation editor

/**
 * The single most likely source of a bad first review is a mispronounced name,
 * because voice quality is what a reader is judged on and a dictionary cannot
 * know a word nobody has published. This turns that from a complaint into a
 * two-second fix.
 */
async function loadSay() {
  const got = await chrome.storage.local.get('userPronunciations').catch(() => ({}));
  return got.userPronunciations ?? [];
}

async function saveSay(list) {
  await chrome.storage.local.set({ userPronunciations: list });
  // Tell the worker to reload; otherwise the change takes effect only after it
  // next wakes, which looks like the setting not working.
  await send({ type: 'pronunciations-changed' });
}

function renderSayList(el, list, { removable }) {
  el.replaceChildren();
  if (!list.length) {
    el.append(Object.assign(document.createElement('li'), {
      className: 'empty-note', textContent: 'Nothing here yet.',
    }));
    return;
  }
  for (const rule of list) {
    const li = document.createElement('li');
    const word = document.createElement('code');
    word.textContent = rule.match;
    const arrow = document.createElement('span');
    arrow.textContent = ' becomes ';
    arrow.className = 'muted';
    const say = document.createElement('strong');
    say.textContent = rule.say;
    li.append(word, arrow, say);

    if (removable) {
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'link';
      del.textContent = 'Remove';
      del.setAttribute('aria-label', `Remove the pronunciation for ${rule.match}`);
      del.addEventListener('click', async () => {
        const next = (await loadSay()).filter((r) => r.match !== rule.match);
        await saveSay(next);
        renderSayList(el, next, { removable: true });
        say2('Removed.');
      });
      li.append(del);
    }
    el.append(li);
  }
}

const say2 = (m) => { $('status').textContent = m; };

$('say-add').addEventListener('click', async () => {
  const match = $('say-word').value.trim();
  const as = $('say-as').value.trim();
  if (!match || !as) { say2('Enter both a word and how it should sound.'); return; }
  if (match === as) { say2('That is the same as the word itself.'); return; }

  const list = await loadSay();
  // Replace rather than duplicate: a second rule for the same word would be
  // shadowed and look like the edit had not taken.
  const next = [...list.filter((r) => r.match.toLowerCase() !== match.toLowerCase()),
    { match, say: as }];
  await saveSay(next);
  renderSayList($('say-list'), next, { removable: true });
  $('say-word').value = '';
  $('say-as').value = '';
  $('say-word').focus();
  say2(`“${match}” will be read as “${as}”.`);
});

renderSayList($('say-list'), await loadSay(), { removable: true });

fetch(chrome.runtime.getURL('src/shared/pronunciation.json'))
  .then((r) => r.json())
  .then((d) => renderSayList($('say-builtin'), d.builtin ?? [], { removable: false }))
  .catch(() => {});
