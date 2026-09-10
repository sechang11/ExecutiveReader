/**
 * Turn a page into an ordered list of readable blocks, each keeping a live map
 * back into the DOM so a character offset can become a Range.
 *
 * That mapping is the whole point. Highlighting, click-to-read-from-here, and
 * resuming from history all need to go from "character 412 of this sentence" to
 * an exact position in the document, and they need to survive the page mutating
 * underneath them. We hold real text nodes rather than serialized paths.
 *
 * Readability is not vendored yet, so this is the scoring fallback described in
 * docs/extension-spec.md section 4. It has to exist regardless, because
 * Readability fails on apps, forums, and dashboards.
 */

/**
 * @typedef {{node: Text, start: number, end: number}} Piece
 *   `start`/`end` are offsets into the owning block's `text`.
 * @typedef {{role: string, text: string, pieces: Piece[], el: Element}} Block
 */

const SKIP_TAGS = new Set([
  'SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'SVG', 'CANVAS', 'IFRAME', 'OBJECT',
  'EMBED', 'AUDIO', 'VIDEO', 'MAP', 'AREA', 'SELECT', 'OPTION', 'TEXTAREA',
  'INPUT', 'BUTTON', 'LABEL', 'PROGRESS', 'METER', 'DIALOG',
]);

const SKIP_ROLES = new Set([
  'navigation', 'banner', 'complementary', 'contentinfo', 'search',
  'menu', 'menubar', 'toolbar', 'tablist', 'alert', 'status',
]);

/** Class and id fragments that reliably mark page furniture. */
const JUNK = /(^|[\s_-])(nav|menu|sidebar|side-bar|footer|header|masthead|banner|advert|ads?|sponsor|promo|social|share|comment|disqus|related|recommend|newsletter|subscribe|signup|popup|modal|overlay|cookie|consent|gdpr|breadcrumb|pagination|paginate|toolbar|widget|skip-link|screen-reader|sr-only|visually-hidden)([\s_-]|$)/i;

/** Fragments that mark the thing we actually want. */
const GOOD = /(^|[\s_-])(article|articlebody|post|postbody|entry|entry-content|content|main|story|storybody|blog|markdown|prose|text|body-copy|rich-text)([\s_-]|$)/i;

const BLOCK_ROLE_BY_TAG = {
  H1: 'heading', H2: 'heading', H3: 'heading', H4: 'heading',
  H5: 'heading', H6: 'heading',
  P: 'paragraph', LI: 'listitem', DD: 'listitem', DT: 'heading',
  BLOCKQUOTE: 'quote', PRE: 'code', CODE: 'code',
  FIGCAPTION: 'caption', CAPTION: 'caption',
  TD: 'cell', TH: 'cell', SUMMARY: 'heading',
};

/** @param {Element} el */
function isHidden(el) {
  if (el.hasAttribute('hidden')) return true;
  if (el.getAttribute('aria-hidden') === 'true') return true;
  const s = getComputedStyle(el);
  if (s.display === 'none' || s.visibility === 'hidden') return true;
  if (parseFloat(s.opacity) === 0) return true;
  // The classic off-screen screen-reader trick; it is real text, but it is
  // duplicate text, and reading it twice is worse than missing it.
  const r = el.getBoundingClientRect();
  if (r.width <= 1 && r.height <= 1 && el.textContent.trim().length > 20) return true;
  return false;
}

/** @param {Element} el */
function isSkippable(el) {
  if (SKIP_TAGS.has(el.tagName)) return true;
  const role = el.getAttribute('role');
  if (role && SKIP_ROLES.has(role)) return true;
  if (el.tagName === 'NAV' || el.tagName === 'FOOTER') return true;
  if (el.tagName === 'ASIDE') return true;
  if (el.closest('[contenteditable="true"]') && el.isContentEditable) return false;
  return isHidden(el);
}

/** Proportion of a block's text that sits inside links. High means it is a
 *  menu or a list of related posts, not prose. @param {Element} el */
function linkDensity(el) {
  const total = el.textContent.trim().length;
  if (!total) return 1;
  let linked = 0;
  for (const a of el.querySelectorAll('a')) linked += a.textContent.trim().length;
  return linked / total;
}

/** Per-host rules, for applications the generic scorer cannot read. */
let SITES = { hosts: {} };

export function loadSites(rules) {
  SITES = { hosts: {}, ...rules };
}

/**
 * The rule for a host, if there is one.
 *
 * Matches the host itself and any parent domain, so one entry covers
 * `mail.google.com` without needing every regional variant listed.
 *
 * @param {string} host
 */
export function siteRuleFor(host) {
  if (!host) return null;
  const parts = host.toLowerCase().split('.');
  for (let i = 0; i < parts.length - 1; i++) {
    const candidate = parts.slice(i).join('.');
    if (SITES.hosts[candidate]) return { host: candidate, ...SITES.hosts[candidate] };
  }
  return null;
}

/**
 * Extract using a site rule.
 *
 * Returns null when the selectors find nothing, which is the important case:
 * these belong to applications that change without warning, and a stale rule
 * has to degrade to the generic scorer rather than yield an empty document.
 *
 * @param {{roots: string[], skip?: string[]}} rule
 * @param {Document} doc
 * @returns {Block[]|null}
 */
export function extractBySite(rule, doc = document) {
  const skip = new Set();
  for (const sel of rule.skip ?? []) {
    for (const el of doc.querySelectorAll(sel)) skip.add(el);
  }

  const seen = new Set();
  const blocks = [];
  for (const sel of rule.roots ?? []) {
    for (const root of doc.querySelectorAll(sel)) {
      if (seen.has(root) || isHidden(root)) continue;
      if ([...skip].some((s) => s.contains(root))) continue;
      seen.add(root);
      blocks.push(...extractBlocks(root, { skipElements: skip }));
    }
  }
  return blocks.length ? blocks : null;
}

/**
 * Find the element most likely to hold the article. Scores every candidate on
 * how much paragraph-shaped text it directly contains, then walks up while the
 * parent is still better, which catches articles split across sibling wrappers.
 * @returns {Element}
 */
/**
 * Should an explicit `<article>` or `<main>` be trusted as the root?
 *
 * The question is whether this element *holds the page's content*, not whether
 * the page has a lot of content. An earlier version required 400 characters,
 * which is a "is there much text" floor answering a "is this the container"
 * question, and it rejected any genuinely short page — a news brief, a
 * definition, a poem — sending it to the scoring walk, which on a short page
 * can find nothing and fall back to the whole body with its navigation.
 *
 * So: trust the element when it is substantial in itself, *or* when it holds a
 * real share of the page however short the page is. A tiny `<main>` on a large
 * page is still rejected, which is the case the floor was really guarding.
 *
 * Pure, so it can be tested without a DOM.
 *
 * @param {number} ownChars text length inside the candidate
 * @param {number} bodyChars text length of the whole document
 */
export function trustsExplicitRoot(ownChars, bodyChars) {
  // No `ownChars <= 0` guard: it read as defensive and did nothing, since the
  // share test below already rejects zero. A line that cannot change an
  // outcome invites the next reader to assume a case is handled somewhere it
  // is not.
  if (ownChars >= SUBSTANTIAL_CHARS) return true;
  return ownChars / Math.max(bodyChars, 1) >= MIN_PAGE_SHARE;
}

/** Enough text to be an article regardless of what surrounds it: roughly a
 *  paragraph. Deliberately low — the share test below is what rejects a stub. */
const SUBSTANTIAL_CHARS = 200;

/** Otherwise, the candidate must hold this much of the page's text. */
const MIN_PAGE_SHARE = 0.25;

export function findArticleRoot(doc = document) {
  const explicit = doc.querySelector('article, [role="article"], main, [role="main"]');
  if (explicit) {
    const own = explicit.textContent.trim().length;
    const body = doc.body?.textContent.trim().length ?? own;
    if (trustsExplicitRoot(own, body)) return explicit;
  }

  /** @type {Map<Element, number>} */
  const scores = new Map();

  for (const p of doc.querySelectorAll('p, li, blockquote, pre')) {
    const len = p.textContent.trim().length;
    if (len < 25) continue;
    let score = Math.min(len / 100, 5) + Math.min(len / 25, 3);
    if (linkDensity(p) > 0.5) score *= 0.2;

    // Credit the parent and grandparent, halving as we go, so the common
    // ancestor of many paragraphs accumulates the most.
    let el = p.parentElement;
    for (let depth = 0; el && depth < 3; depth++, el = el.parentElement) {
      const bonus = score / (depth + 1);
      scores.set(el, (scores.get(el) ?? 0) + bonus);
    }
  }

  let best = null;
  let bestScore = 0;
  for (const [el, raw] of scores) {
    const ident = `${el.className} ${el.id}`;
    let score = raw;
    if (JUNK.test(ident)) score *= 0.25;
    if (GOOD.test(ident)) score *= 1.5;
    score *= 1 - Math.min(linkDensity(el), 0.9);
    if (score > bestScore) { bestScore = score; best = el; }
  }

  return best ?? doc.body;
}

/**
 * Collect readable blocks under `root`, in document order.
 * @param {Element} root
 * @param {{readAltText?: boolean, codeMode?: 'read'|'announce'|'skip'}} [opts]
 * @returns {Block[]}
 */
export function extractBlocks(root, opts = {}) {
  const codeMode = opts.codeMode ?? 'announce';
  /** Extra elements to exclude, supplied by a site rule. */
  const skipElements = opts.skipElements ?? null;
  /** @type {Block[]} */
  const blocks = [];
  /** @type {Map<Element, Block>} */
  const byEl = new Map();

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      for (let el = node.parentElement; el && el !== root.parentElement; el = el.parentElement) {
        if (isSkippable(el)) return NodeFilter.FILTER_REJECT;
        if (skipElements?.has(el)) return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });

  /** Whether the previous text node in the current block ended in whitespace.
   *  Keyed per block, since blocks interleave in document order. */
  const trailingSpace = new Map();

  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const text = /** @type {Text} */ (n);
    const host = blockAncestor(text.parentElement, root);
    if (!host) continue;

    let block = byEl.get(host);
    if (!block) {
      block = { role: roleOf(host), text: '', pieces: [], el: host };
      byEl.set(host, block);
      blocks.push(block);
    }

    // Collapse whitespace the way rendering does, and keep offsets honest by
    // recording where each surviving run of this node's text landed.
    const raw = text.nodeValue;
    const cleaned = raw.replace(/\s+/g, ' ').trim();
    if (!cleaned) {
      // A node of pure whitespace still separates its neighbours.
      if (block.text.length) trailingSpace.set(block, true);
      continue;
    }

    // Join with a space only where the source actually had one. `<p>a<b>b</b></p>`
    // renders as "ab", so inserting a space there would invent a word break and
    // desynchronise every offset after it.
    const gap = block.text.length > 0
      && (trailingSpace.get(block) === true || /^\s/.test(raw));
    if (gap) block.text += ' ';

    const start = block.text.length;
    block.text += cleaned;
    block.pieces.push({ node: text, start, end: block.text.length });
    trailingSpace.set(block, /\s$/.test(raw));
  }

  return blocks.filter((b) => {
    if (b.text.trim().length < 2) return false;
    if (b.role === 'code' && codeMode === 'skip') return false;
    return true;
  });
}

/** Nearest ancestor that renders as its own block. @param {Element|null} el */
function blockAncestor(el, root) {
  for (; el && el !== root.parentElement; el = el.parentElement) {
    if (BLOCK_ROLE_BY_TAG[el.tagName]) return el;
    const d = getComputedStyle(el).display;
    if (d === 'block' || d === 'list-item' || d === 'table-cell' || d === 'flex' || d === 'grid') {
      return el;
    }
  }
  return root;
}

/** @param {Element} el */
function roleOf(el) {
  return BLOCK_ROLE_BY_TAG[el.tagName] ?? 'paragraph';
}

/**
 * Map a character span of `block.text` onto a live DOM Range.
 * Returns null when the underlying nodes have since been replaced, which is
 * how callers detect that the page changed under them.
 *
 * @param {Block} block
 * @param {number} charStart
 * @param {number} charEnd
 * @returns {Range|null}
 */
export function rangeFor(block, charStart, charEnd) {
  let startNode = null; let startOffset = 0;
  let endNode = null; let endOffset = 0;

  for (const piece of block.pieces) {
    if (!piece.node.isConnected) continue;
    if (startNode === null && charStart < piece.end) {
      startNode = piece.node;
      startOffset = clampToNode(piece, Math.max(charStart, piece.start));
    }
    if (charEnd <= piece.end) {
      endNode = piece.node;
      endOffset = clampToNode(piece, Math.max(charEnd, piece.start));
      break;
    }
    endNode = piece.node;
    endOffset = piece.node.nodeValue.length;
  }

  if (!startNode || !endNode) return null;
  try {
    const r = document.createRange();
    r.setStart(startNode, startOffset);
    r.setEnd(endNode, endOffset);
    return r;
  } catch {
    return null; // node was swapped out mid-read
  }
}

/**
 * Block offsets index whitespace-collapsed text; DOM offsets index the raw text
 * node. Re-walk the raw value applying exactly the transformation that built
 * the block text — leading whitespace trimmed, each internal run collapsed to
 * one space — so the two stay aligned inside nodes with ragged whitespace.
 *
 * Getting this wrong shifts every highlight in the block by a few characters,
 * which looks like the highlighter lagging the voice.
 *
 * Exported for tests; it touches only `piece.node.nodeValue`, so it can be
 * exercised without a DOM.
 *
 * @param {Piece} piece
 * @param {number} blockOffset offset into the block's collapsed text
 * @returns {number} offset into piece.node.nodeValue
 */
export function clampToNode(piece, blockOffset) {
  const want = blockOffset - piece.start;
  const raw = piece.node.nodeValue;

  // Skip the leading whitespace that .trim() removed.
  let i = 0;
  while (i < raw.length && /\s/.test(raw[i])) i++;
  if (want <= 0) return i;

  let seen = 0;
  while (i < raw.length) {
    if (seen === want) return i;
    if (/\s/.test(raw[i])) {
      // A whole run of whitespace became a single space in the block text.
      seen++;
      while (i < raw.length && /\s/.test(raw[i])) i++;
    } else {
      seen++;
      i++;
    }
  }
  return raw.length;
}
