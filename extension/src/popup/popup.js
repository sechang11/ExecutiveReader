/**
 * Popup transport. Holds no state of its own: it renders whatever the service
 * worker says the state is, and sends intents back. That keeps the popup
 * correct after it has been closed and reopened mid-read, which is the common
 * case rather than the edge case.
 */

const $ = (id) => document.getElementById(id);
const send = (msg) => chrome.runtime.sendMessage(msg).catch(() => null);

const STATUS_TEXT = {
  idle: 'Ready',
  loading: 'Reading the page…',
  speaking: 'Playing',
  paused: 'Paused',
};

/** @param {{status: string, index: number, texts: string[], speed: number, voiceKey: string|null}} state */
function render(state) {
  if (!state) return;

  const total = state.texts?.length ?? 0;
  const position = total ? ` — sentence ${state.index + 1} of ${total}` : '';
  $('status').textContent = (STATUS_TEXT[state.status] ?? 'Ready') + position;

  const playing = state.status === 'speaking';
  $('play').textContent = playing ? '⏸' : '▶';
  $('play').setAttribute('aria-label', playing ? 'Pause' : 'Play');

  const idle = state.status === 'idle';
  for (const id of ['prev', 'next', 'stop']) $(id).disabled = idle;

  $('speed').value = String(state.speed ?? 1);
  $('speed-out').textContent = `${Number(state.speed ?? 1).toFixed(2)}×`;
  if (state.voiceKey) $('voice').value = state.voiceKey;
}

async function loadVoices() {
  const voices = await send({ type: 'get-voices' });
  if (!Array.isArray(voices)) return;

  const select = $('voice');
  select.replaceChildren();

  // Group by language so a list of forty system voices stays navigable.
  const byLang = new Map();
  for (const v of voices) {
    const lang = v.lang || 'other';
    if (!byLang.has(lang)) byLang.set(lang, []);
    byLang.get(lang).push(v);
  }

  for (const [lang, list] of [...byLang].sort((a, b) => a[0].localeCompare(b[0]))) {
    const group = document.createElement('optgroup');
    group.label = lang;
    for (const v of list) {
      const opt = document.createElement('option');
      opt.value = v.key;
      opt.textContent = v.note ? `${v.name} (${v.note})` : v.name;
      group.append(opt);
    }
    select.append(group);
  }
}

$('play').addEventListener('click', async () => render(await send({ type: 'toggle-play' })));
$('stop').addEventListener('click', async () => render(await send({ type: 'stop' })));
$('prev').addEventListener('click', async () => render(await send({ type: 'skip', delta: -1 })));
$('next').addEventListener('click', async () => render(await send({ type: 'skip', delta: 1 })));

$('speed').addEventListener('input', (e) => {
  $('speed-out').textContent = `${Number(e.target.value).toFixed(2)}×`;
});
$('speed').addEventListener('change', (e) => {
  send({ type: 'set-speed', speed: Number(e.target.value) });
});

$('voice').addEventListener('change', (e) => {
  send({ type: 'set-voice', voiceKey: e.target.value });
});

/**
 * Resolved when the popup loads, not when the button is clicked.
 *
 * chrome.sidePanel.open() must run inside a user gesture, and awaiting anything
 * first spends it — the call then fails with "user gesture required" even though
 * a real click started it. So the tab id has to be in hand before the handler
 * runs, which means looking it up now.
 */
let activeTabId = null;

$('open-panel').addEventListener('click', () => {
  if (activeTabId == null) return;
  chrome.sidePanel.open({ tabId: activeTabId })
    .then(() => window.close()) // else the popup covers what it just opened
    .catch((e) => {
      $('status').textContent = 'Could not open the panel.';
      console.error('[executive-reader]', e);
    });
});

// Live updates while the popup happens to be open.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'state') render(msg.state);
});

await loadVoices();
render(await send({ type: 'get-state' }));

chrome.tabs.query({ active: true, currentWindow: true })
  .then(([tab]) => { activeTabId = tab?.id ?? null; })
  .catch(() => { $('open-panel').disabled = true; });
