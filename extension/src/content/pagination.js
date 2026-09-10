/**
 * Finding the next page, and noticing when a page grows instead.
 *
 * Two different things wear the same name. Classic pagination navigates to a new
 * document; infinite scroll appends to the current one. Most modern sites do the
 * second, and the incumbent reader handles neither, so a long article stops dead
 * at the fold.
 *
 * Guessing wrong here is expensive: a bad guess navigates away from what someone
 * was reading. So every strategy carries a confidence, and anything below the
 * threshold in `shared/pagination.json` is offered rather than taken.
 */

/** @typedef {{url: string, label: string, strategy: string, confidence: string}} NextPage */

const RANK = { high: 3, medium: 2, low: 1 };

/** @type {any} */
let RULES = { strategies: [], confirm_below: 'medium', sites: {}, infinite_scroll: { hosts: [] } };

export function loadRules(rules) {
  RULES = { ...RULES, ...rules };
}

/** @param {string} a @param {string} b */
function atLeast(a, b) {
  return (RANK[a] ?? 0) >= (RANK[b] ?? 0);
}

/**
 * Does this candidate need the user to confirm before we follow it?
 * @param {NextPage} candidate
 */
export function needsConfirmation(candidate) {
  return !atLeast(candidate.confidence, RULES.confirm_below ?? 'medium');
}

/**
 * Increment the page number in a URL.
 *
 * Pure, and the strategy most likely to be wrong: plenty of URLs contain a
 * number that has nothing to do with paging. It is deliberately last and
 * deliberately low confidence.
 *
 * @param {string} url
 * @param {string} pattern
 * @returns {string|null}
 */
export function incrementUrl(url, pattern) {
  let re;
  try {
    re = new RegExp(pattern, 'g');
  } catch {
    return null; // a rules file newer than this code
  }
  const m = re.exec(url);
  if (!m) return null;

  const next = String(Number(m[0]) + 1);
  // Preserve zero padding: page=007 should become page=008, not page=8.
  const padded = m[0].length > next.length && m[0].startsWith('0')
    ? next.padStart(m[0].length, '0')
    : next;
  return url.slice(0, m.index) + padded + url.slice(m.index + m[0].length);
}

/** @param {Element} el */
function visible(el) {
  if (el.getAttribute('aria-disabled') === 'true') return false;
  if (el.hasAttribute('disabled')) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 || r.height > 0 || el.tagName === 'LINK';
}

/**
 * @param {Document} doc
 * @returns {NextPage|null}
 */
export function findNext(doc = document) {
  const host = doc.location?.hostname ?? '';
  const overrides = RULES.sites?.[host];
  const strategies = overrides?.strategies ?? RULES.strategies ?? [];

  const here = doc.location?.href ?? location.href;
  /**
   * A link to the page we are already on is not a next page.
   *
   * This guard used to live only in the selector branch, so a plain "Next"
   * link pointing at the current page was rejected by `rel=next` and then
   * accepted by the text strategy two rules later — and following it re-reads
   * the same page, forever. Comparing without the fragment as well, because
   * `#comments` is the same document.
   */
  const isHere = (url) => url.split('#')[0] === here.split('#')[0];

  for (const s of strategies) {
    if (s.selector) {
      for (const el of doc.querySelectorAll(s.selector)) {
        const href = el.getAttribute('href');
        if (!href || !visible(el)) continue;
        const url = new URL(href, here).toString();
        if (isHere(url)) continue;
        return {
          url,
          label: (el.textContent || el.getAttribute('aria-label') || 'Next').trim().slice(0, 60),
          strategy: s.id,
          confidence: s.confidence ?? 'low',
        };
      }
    }

    if (s.textMatch) {
      for (const a of doc.querySelectorAll('a[href]')) {
        const text = (a.textContent || '').trim().toLowerCase();
        // Match whole words, not substrings: "nextdoor" is not a next link,
        // and neither is an article titled "what happens next".
        if (!text || text.length > 30) continue;
        if (!s.textMatch.some((w) => text === w || text.startsWith(`${w} `) || text.endsWith(` ${w}`))) continue;
        if (!visible(a)) continue;
        const url = new URL(a.getAttribute('href'), here).toString();
        if (isHere(url)) continue;
        return { url, label: text.slice(0, 60), strategy: s.id, confidence: s.confidence ?? 'low' };
      }
    }

    if (s.pattern) {
      const url = incrementUrl(here, s.pattern);
      if (url && !isHere(url)) return { url, label: 'Next page', strategy: s.id, confidence: s.confidence ?? 'low' };
    }
  }

  return null;
}

/**
 * Watch for content appended to the article rather than navigated to.
 *
 * Fires with newly added block elements once they settle. Debounced because
 * frameworks append in bursts and a callback per node would re-extract the
 * document dozens of times for one page of new text.
 *
 * @param {Element} root
 * @param {(added: Element[]) => void} onGrow
 * @param {{quietMs?: number, minChars?: number}} [opts]
 */
export function watchForGrowth(root, onGrow, opts = {}) {
  const quietMs = opts.quietMs ?? 400;
  const minChars = opts.minChars ?? 120;

  let pending = [];
  let timer = null;

  const flush = () => {
    timer = null;
    const added = pending;
    pending = [];
    const text = added.reduce((n, el) => n + (el.textContent?.length ?? 0), 0);
    // Ignore the constant churn of ads, spinners and lazy images; only a real
    // slab of new prose counts as the page having grown.
    if (text >= minChars) onGrow(added);
  };

  const observer = new MutationObserver((records) => {
    for (const r of records) {
      for (const node of r.addedNodes) {
        if (node.nodeType !== Node.ELEMENT_NODE) continue;
        pending.push(/** @type {Element} */ (node));
      }
    }
    if (pending.length) {
      clearTimeout(timer);
      timer = setTimeout(flush, quietMs);
    }
  });

  observer.observe(root, { childList: true, subtree: true });
  return () => { clearTimeout(timer); observer.disconnect(); };
}
