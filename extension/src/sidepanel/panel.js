/**
 * Side panel: reading mode, typography controls, and history.
 *
 * Like the popup, it renders whatever the service worker says the state is and
 * sends intents back, holding none of its own. Unlike the popup it survives
 * clicking the page, which is why the history list and the mirrored text live
 * here rather than there.
 */

const $ = (id) => document.getElementById(id);
const send = (msg) => chrome.runtime.sendMessage(msg).catch(() => null);

const FONTS = {
  system: 'system-ui, -apple-system, "Segoe UI", sans-serif',
  serif: 'Georgia, "Iowan Old Style", "Times New Roman", serif',
  mono: 'ui-monospace, "Cascadia Code", Consolas, monospace',
  // Falls back cleanly when the reader has not installed it; the option label
  // says "if installed" rather than pretending otherwise.
  dyslexic: '"OpenDyslexic", "Comic Sans MS", system-ui, sans-serif',
};

const STATUS = {
  idle: 'Nothing playing',
  loading: 'Reading the page…',
  speaking: 'Playing',
  paused: 'Paused',
};

/** Sentence texts for the page being read, so the mirror can be rebuilt. */
let sentences = [];
let activeIndex = -1;

// ------------------------------------------------------------------ tabs

function showTab(which) {
  const reading = which === 'reading';
  $('tab-reading').setAttribute('aria-selected', String(reading));
  $('tab-history').setAttribute('aria-selected', String(!reading));
  $('view-reading').hidden = !reading;
  $('view-history').hidden = reading;
  if (!reading) renderHistory();
}

$('tab-reading').addEventListener('click', () => showTab('reading'));
$('tab-history').addEventListener('click', () => showTab('history'));

// ------------------------------------------------------------- typography

/** @param {string} key @param {string|number} value */
function applyType(key, value) {
  document.documentElement.style.setProperty(`--text-${key}`, String(value));
}

const typeControls = [
  { id: 'size', prop: 'size', unit: 'px', fmt: (v) => `${v}px` },
  { id: 'height', prop: 'height', unit: '', fmt: (v) => Number(v).toFixed(1) },
  { id: 'spacing', prop: 'spacing', unit: 'em', fmt: (v) => `${Number(v).toFixed(2)}em` },
  { id: 'width', prop: 'width', unit: 'em', fmt: (v) => `${v}em` },
];

for (const c of typeControls) {
  $(c.id).addEventListener('input', (e) => {
    const v = e.target.value;
    applyType(c.prop, v + c.unit);
    $(`${c.id}-out`).textContent = c.fmt(v);
    saveType();
  });
}

$('font').addEventListener('change', (e) => {
  applyType('family', FONTS[e.target.value] ?? FONTS.system);
  saveType();
});

/** Per-viewer convenience, so it belongs in local storage rather than in the
 *  reading state the worker owns. */
function saveType() {
  const type = { font: $('font').value };
  for (const c of typeControls) type[c.id] = $(c.id).value;
  chrome.storage.local.set({ 'panel-type': type }).catch(() => {});
}

async function loadType() {
  const got = await chrome.storage.local.get('panel-type').catch(() => ({}));
  const type = got['panel-type'];
  if (!type) return;
  if (type.font) {
    $('font').value = type.font;
    applyType('family', FONTS[type.font] ?? FONTS.system);
  }
  for (const c of typeControls) {
    if (type[c.id] == null) continue;
    $(c.id).value = type[c.id];
    applyType(c.prop, type[c.id] + c.unit);
    $(`${c.id}-out`).textContent = c.fmt(type[c.id]);
  }
}

// ------------------------------------------------------------ mirrored text

function renderText() {
  const el = $('text');
  if (!sentences.length) {
    el.replaceChildren(Object.assign(document.createElement('p'), {
      className: 'empty',
      textContent: 'Press play, or open a page and use Alt+P.',
    }));
    return;
  }

  const frag = document.createDocumentFragment();
  const p = document.createElement('p');
  sentences.forEach((text, i) => {
    // A button, not a styled span: clicking a sentence to read from there must
    // be reachable by keyboard like any other control.
    const s = document.createElement('button');
    s.className = 's';
    s.type = 'button';
    s.dataset.index = String(i);
    s.textContent = `${text} `;
    p.append(s);
  });
  frag.append(p);
  el.replaceChildren(frag);
  markActive();
}

function markActive() {
  const el = $('text');
  for (const s of el.querySelectorAll('.s.now')) s.classList.remove('now');
  const active = el.querySelector(`.s[data-index="${activeIndex}"]`);
  if (!active) return;
  active.classList.add('now');
  active.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

$('text').addEventListener('click', (e) => {
  const s = e.target.closest('.s');
  if (s) send({ type: 'read-from', index: Number(s.dataset.index) });
});

// ------------------------------------------------------------------ history

function ago(ts) {
  const mins = Math.floor((Date.now() - ts) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

async function renderHistory() {
  const entries = await send({ type: 'get-history' });
  const list = $('history');

  if (!Array.isArray(entries) || !entries.length) {
    list.replaceChildren(Object.assign(document.createElement('li'), {
      className: 'empty-note',
      textContent: 'Nothing read yet.',
    }));
    return;
  }

  const frag = document.createDocumentFragment();
  for (const e of entries) {
    const at = e.anchor?.index ?? 0;
    const pct = e.total ? Math.round((at / e.total) * 100) : 0;

    const btn = document.createElement('button');
    btn.className = 'entry';
    btn.type = 'button';
    btn.addEventListener('click', () => send({ type: 'resume', id: e.id }));

    const title = document.createElement('span');
    title.className = 't';
    title.textContent = e.title || e.url;

    const meta = document.createElement('span');
    meta.className = 'm';
    meta.textContent = e.total
      ? `${ago(e.lastReadAt)} · ${pct}% of ${e.total} sentences`
      : ago(e.lastReadAt);

    const bar = document.createElement('span');
    bar.className = 'bar';
    const fill = document.createElement('i');
    fill.style.width = `${pct}%`;
    bar.append(fill);

    // The accessible name has to carry the progress too; a bare title plus a
    // decorative bar tells a screen-reader user nothing about where they were.
    btn.setAttribute('aria-label', `${e.title || e.url}, ${pct} percent read, ${ago(e.lastReadAt)}`);
    btn.append(title, meta, bar);

    const li = document.createElement('li');
    li.append(btn);
    frag.append(li);
  }
  list.replaceChildren(frag);
}

$('prune').addEventListener('click', async () => {
  const removed = await send({ type: 'prune-history' });
  $('status').textContent = removed
    ? `Removed ${removed} old ${removed === 1 ? 'entry' : 'entries'}.`
    : 'Nothing old enough to remove.';
  renderHistory();
});

$('clear').addEventListener('click', async () => {
  await send({ type: 'clear-history' });
  $('status').textContent = 'History cleared.';
  renderHistory();
});

// ------------------------------------------------------------------- state

function render(state) {
  if (!state) return;

  const total = state.texts?.length ?? 0;
  $('status').textContent = (STATUS[state.status] ?? STATUS.idle)
    + (total ? ` — sentence ${state.index + 1} of ${total}` : '');

  const playing = state.status === 'speaking';
  $('play').textContent = playing ? '⏸' : '▶';
  $('play').setAttribute('aria-label', playing ? 'Pause' : 'Play');

  const idle = state.status === 'idle';
  $('prev').disabled = idle;
  $('next').disabled = idle;

  $('speed').value = String(state.speed ?? 1);
  $('speed-out').textContent = `${Number(state.speed ?? 1).toFixed(2)}×`;
  $('auto-advance').checked = Boolean(state.autoAdvance);

  // Rebuild the mirror only when the document actually changed; rebuilding on
  // every sentence would fight the user's scroll position.
  const changed = total !== sentences.length || state.texts?.[0] !== sentences[0];
  if (changed) {
    sentences = state.texts ?? [];
    renderText();
  }
  if (state.index !== activeIndex) {
    activeIndex = state.index;
    markActive();
  }
}

$('play').addEventListener('click', async () => render(await send({ type: 'toggle-play' })));
$('prev').addEventListener('click', async () => render(await send({ type: 'skip', delta: -1 })));
$('next').addEventListener('click', async () => render(await send({ type: 'skip', delta: 1 })));

$('speed').addEventListener('input', (e) => {
  $('speed-out').textContent = `${Number(e.target.value).toFixed(2)}×`;
});
$('speed').addEventListener('change', (e) => {
  send({ type: 'set-speed', speed: Number(e.target.value) });
});

$('auto-advance').addEventListener('change', (e) => {
  send({ type: 'set-auto-advance', on: e.target.checked });
});

/** A next-page link we found but did not trust enough to follow unasked. */
let pendingNext = null;

function askAboutNext(next) {
  pendingNext = next;
  $('next-label').textContent = `Reached the end of this page. Continue to “${next.label}”?`;
  $('next-prompt').hidden = false;
  $('next-go').focus(); // it is an alert; keyboard users should land on it
}

$('next-go').addEventListener('click', () => {
  if (!pendingNext) return;
  send({ type: 'follow-next', url: pendingNext.url });
  $('next-prompt').hidden = true;
  pendingNext = null;
});

$('next-no').addEventListener('click', () => {
  $('next-prompt').hidden = true;
  pendingNext = null;
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.target && msg.target !== 'panel') return;
  if (msg.type === 'state') render(msg.state);
  if (msg.type === 'confirm-next-page') askAboutNext(msg.next);
});

await loadType();
render(await send({ type: 'get-state' }));
