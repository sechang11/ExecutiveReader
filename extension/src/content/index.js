/**
 * Content script: owns the page side of a reading session.
 *
 * Responsibilities are deliberately narrow. It knows how to turn this document
 * into sentences, how to paint one of them, and how to keep it on screen. It
 * holds no playback state and makes no decisions about what to read next; the
 * service worker drives. That split is what lets playback survive the content
 * script being torn down by a navigation.
 */

import {
  findArticleRoot, extractBlocks, rangeFor, loadSites, siteRuleFor, extractBySite,
} from './extract.js';
import { segment, loadAbbreviations } from '../core/segment.js';
import { normalize, loadNormalization } from '../core/normalize.js';
import * as hl from './highlight.js';
import { startScrollFollow } from './scroll.js';
import { findNext, needsConfirmation, watchForGrowth, loadRules } from './pagination.js';

/** @typedef {{index: number, text: string, block: number, start: number, end: number}} Sentence */

/** @type {import('./extract.js').Block[]} */
let blocks = [];
/** @type {Sentence[]} */
let sentences = [];
let scroller = null;
/** Sentence currently painted, so word offsets resolve against the right span. */
let activeIndex = -1;

/** Shared rule data. Both loads are best-effort: without them the reader still
 *  works, it just splits and speaks a little less well. */
const rulesReady = Promise.all([
  fetch(chrome.runtime.getURL('src/shared/abbreviations.json'))
    .then((r) => r.json()).then(loadAbbreviations).catch(() => {}),
  fetch(chrome.runtime.getURL('src/shared/normalization.json'))
    .then((r) => r.json()).then(loadNormalization).catch(() => {}),
  fetch(chrome.runtime.getURL('src/shared/pagination.json'))
    .then((r) => r.json()).then(loadRules).catch(() => {}),
  fetch(chrome.runtime.getURL('src/shared/sites.json'))
    .then((r) => r.json()).then(loadSites).catch(() => {}),
]);

/**
 * Build the sentence list for this page.
 * @param {{selectionOnly?: boolean}} opts
 */
async function buildDocument(opts = {}) {
  await rulesReady;

  const sel = window.getSelection();
  const useSelection = opts.selectionOnly && sel && !sel.isCollapsed;

  const root = useSelection
    ? (sel.getRangeAt(0).commonAncestorContainer.nodeType === Node.ELEMENT_NODE
        ? /** @type {Element} */ (sel.getRangeAt(0).commonAncestorContainer)
        : sel.getRangeAt(0).commonAncestorContainer.parentElement)
    : findArticleRoot(document);

  currentRoot = root ?? document.body;
  // A selection always wins: asking for the selected text on a mail page means
  // that text, not the whole thread.
  currentSiteRule = useSelection ? null : siteRuleFor(location.hostname);
  rebuild(currentRoot, currentSiteRule);

  // Watch for the page growing under us. Most sites paginate by appending now
  // rather than navigating, and a reader that stops at the fold looks broken.
  stopWatching?.();
  stopWatching = useSelection ? null : watchForGrowth(currentRoot, onGrow);

  return {
    url: location.href,
    title: document.title,
    count: sentences.length,
    // The service worker needs the text to synthesize; it does not need, and
    // must not hold, the DOM Ranges.
    //
    // What gets spoken is the normalized text, but highlight offsets index the
    // ORIGINAL text, because that is what maps to the DOM. Normalization
    // changes lengths — "$50" becomes "50 dollars" — so a word event reported
    // against the spoken string would land in the wrong place in the original.
    // Rather than paint a highlight we know is wrong, `exact` tells the worker
    // which sentences are safe to highlight word-by-word. Sentence highlighting
    // is unaffected and always exact. A proper offset map between the two
    // strings is phase 2 work, alongside the engines whose timings we generate
    // ourselves.
    ...sentences.reduce((acc, s) => {
      const spoken = normalize(s.text);
      acc.texts.push(spoken);
      acc.exact.push(spoken === s.text);
      return acc;
    }, { texts: [], exact: [] }),
  };
}

/** @param {number} index */
function paintSentence(index) {
  const s = sentences[index];
  if (!s) return;
  activeIndex = index;
  const range = rangeFor(blocks[s.block], s.start, s.end);
  hl.setSentence(range);
  if (range) scroller?.follow(range);
}

/**
 * @param {number} charStart offsets into the active sentence's own text
 * @param {number} charEnd
 */
function paintWord(charStart, charEnd) {
  const s = sentences[activeIndex];
  if (!s) return;
  const range = rangeFor(blocks[s.block], s.start + charStart, s.start + charEnd);
  hl.setWord(range);
}

let stopWatching = null;
/** Article root of the current read, so growth is watched in the right place. */
let currentRoot = null;
/** Site rule in force for the current read, so a rebuild after growth uses it too. */
let currentSiteRule = null;

function teardown() {
  hl.clear();
  activeIndex = -1;
  stopWatching?.();
  stopWatching = null;
}

/**
 * Re-segment after the page appended content, and tell the worker how much
 * longer the document just got.
 *
 * Rebuilding from the root rather than only parsing the new nodes keeps reading
 * order right: sites append into the middle as often as the end, and a queue
 * built by concatenation would read the page out of sequence.
 */
function onGrow() {
  if (!currentRoot) return;
  const before = sentences.length;
  rebuild(currentRoot, currentSiteRule);
  if (sentences.length > before) {
    chrome.runtime.sendMessage({
      type: 'document-grew',
      texts: sentences.map((s) => normalize(s.text)),
      exact: sentences.map((s) => normalize(s.text) === s.text),
    }).catch(() => {});
  }
}

/**
 * @param {Element} root
 * @param {{roots: string[], skip?: string[]}|null} [siteRule] when this host has
 *   its own rules — webmail and forums, where the generic scorer finds only
 *   application furniture
 */
function rebuild(root, siteRule = null) {
  // A stale site rule must degrade to the generic scorer, never to nothing.
  blocks = (siteRule && extractBySite(siteRule)) || extractBlocks(root);
  sentences = [];
  blocks.forEach((block, bi) => {
    for (const s of segment(block.text)) {
      sentences.push({ index: sentences.length, text: s.text, block: bi, start: s.start, end: s.end });
    }
  });
}

/** Click a paragraph to start reading from there. */
function onClick(ev) {
  if (!sentences.length) return;
  if (ev.altKey === false) return; // Alt+click, so ordinary clicking still works
  const caret = document.caretRangeFromPoint?.(ev.clientX, ev.clientY);
  if (!caret) return;
  const idx = sentenceAtNode(caret.startContainer, caret.startOffset);
  if (idx >= 0) {
    ev.preventDefault();
    chrome.runtime.sendMessage({ type: 'read-from', index: idx });
  }
}

/** @param {Node} node @param {number} offset */
function sentenceAtNode(node, offset) {
  for (let i = 0; i < sentences.length; i++) {
    const s = sentences[i];
    const r = rangeFor(blocks[s.block], s.start, s.end);
    if (r && r.comparePoint(node, offset) === 0) return i;
  }
  return -1;
}

chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  switch (msg.type) {
    case 'build':
      buildDocument(msg).then(respond, (e) => respond({ error: String(e) }));
      return true; // async
    case 'paint-sentence':
      paintSentence(msg.index);
      break;
    case 'paint-word':
      paintWord(msg.charStart, msg.charEnd);
      break;
    case 'stop':
      teardown();
      break;
    case 'find-next':
      // The worker asks only when it has run out of sentences, so this never
      // costs anything mid-read.
      respond(findNext(document));
      break;
    default:
      break;
  }
  return false;
});

// An open port keeps the service worker from being suspended mid-sentence.
// Without this, Chrome tears the worker down after roughly thirty seconds idle
// and playback stops between sentences. See spec section 2.
const port = chrome.runtime.connect({ name: 'executive-reader-session' });
port.onDisconnect.addListener(teardown);

scroller = startScrollFollow();
document.addEventListener('click', onClick, true);
window.addEventListener('pagehide', teardown);
