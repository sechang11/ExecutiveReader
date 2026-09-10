/**
 * Reading positions that survive the page changing.
 *
 * Storing a sentence index is useless: pages gain paragraphs, lose ads, and get
 * re-edited, and index 47 tomorrow is not the sentence you stopped on today. So
 * an anchor stores the sentence *text* plus its neighbours, and resolution
 * walks a ladder from exact to approximate.
 *
 * The order is deliberate and was argued out with the desktop half, which
 * implements the same contract in Python. The tempting shortcut — when the rule
 * fingerprint has changed, skip the exact comparison and go straight to fuzzy —
 * is wrong. A rules edit rewrites some sentences, not all, so an exact hit on an
 * untouched sentence is still correct; and skipping exact throws away the
 * tiebreak that separates repeated sentences:
 *
 *     ["Intro.", "Same line.", "Middle.", "Same line.", "Tail."]
 *     saved on index 3 -> exact-first gives 3, fuzzy-only gives 1
 *
 * So the fingerprint governs *confidence*, never control flow. See spec 7.1.
 */

import { tidy } from './normalize.js';

/** How many neighbouring sentences to keep either side, for disambiguation. */
const CONTEXT = 2;

/**
 * Two thresholds, doing genuinely different jobs. Conflating them into one
 * number is easy and wrong, because they want opposite properties.
 *
 * ACCEPT decides whether any candidate is a match at all. It should be strict:
 * a wrong confident answer is worse than admitting the sentence is gone, since
 * the fallback is the recorded index, which is decent evidence.
 *
 * TIE_MARGIN ranks candidates already judged plausible. It should be forgiving,
 * because its job is to notice that two candidates are too close to separate on
 * score alone and hand the decision to context instead. Taking the first
 * maximum is the obvious implementation and it is quietly wrong on duplicated
 * sentences.
 */

const ACCEPT = 0.5;
const TIE_MARGIN = 0.05;

/**
 * A sentence in the form the normalization contract says both halves emit.
 *
 * Imported rather than reimplemented: `tidy` is the contract's own
 * implementation, and a second copy here would be free to drift from the thing
 * it claims to agree with. That is the failure this whole file exists to
 * prevent, one level down.
 */
const contractual = (s) => tidy(s ?? '');

/**
 * @typedef {{
 *   quote: string, before: string[], after: string[],
 *   index: number, total: number, stamp: string|null
 * }} Anchor
 * @typedef {{
 *   index: number,
 *   how: 'exact'|'ambiguous'|'boundary'|'fuzzy'|'hint',
 *   verified: boolean,
 *   stampChanged: boolean,
 * }} Located
 *
 * `how` and `verified` are the vocabulary shared with the desktop half, defined
 * in docs/anchor-vocabulary.md. Each side words its own message from that pair,
 * because prose does not belong in shared data: a tray notification and a side
 * panel have genuinely different tone and length budgets.
 *
 * Read that document before changing these names. We previously shipped the
 * same four labels meaning different things — a repeated sentence resolved as
 * `exact` on one side and `context` on the other — and each half believed the
 * two agreed.
 */

/**
 * @param {string[]} sentences
 * @param {number} index
 * @param {string|null} stamp `combined` from shared/fingerprint.json
 * @returns {Anchor|null}
 */
export function capture(sentences, index, stamp = null) {
  if (!sentences.length || index < 0 || index >= sentences.length) return null;
  return {
    quote: sentences[index],
    before: sentences.slice(Math.max(0, index - CONTEXT), index),
    after: sentences.slice(index + 1, index + 1 + CONTEXT),
    index,
    total: sentences.length,
    stamp,
  };
}

/** Character bigrams, for Dice similarity. Cheap, order-tolerant, and good
 *  enough to survive a few words being rewritten. */
function bigrams(s) {
  const t = s.toLowerCase().replace(/\s+/g, ' ').trim();
  const out = new Map();
  for (let i = 0; i < t.length - 1; i++) {
    const g = t.slice(i, i + 2);
    out.set(g, (out.get(g) ?? 0) + 1);
  }
  return out;
}

/** Dice coefficient over character bigrams: 0 unrelated, 1 identical. */
export function similarity(a, b) {
  if (a === b) return 1;
  if (!a || !b) return 0;
  const A = bigrams(a);
  const B = bigrams(b);
  if (!A.size || !B.size) return 0;
  let shared = 0;
  let total = 0;
  for (const n of A.values()) total += n;
  for (const [g, n] of B) {
    total += n;
    shared += Math.min(n, A.get(g) ?? 0);
  }
  return (2 * shared) / total;
}

/**
 * Smallest overlap worth calling a boundary change. Below this, a stub sentence
 * like "Yes." is inside half the document and every resume would report a
 * confident boundary result for what is really coincidence.
 *
 * Counted in words, not characters. A character floor is the obvious guard and
 * it is wrong: "The cat sat down." is a perfectly good half of a split sentence
 * at sixteen characters, so any floor high enough to reject "Yes." also rejects
 * real splits. Four words separates them cleanly. The character minimum only
 * catches pathological input like four one-letter words.
 */
const MIN_CONTAINMENT_WORDS = 4;
const MIN_CONTAINMENT_CHARS = 12;

/**
 * Sentences whose text overlaps the quote by containment in either direction:
 * the quote holds a current sentence (it was split), or a current sentence holds
 * the quote (it was merged, or gained a clause).
 *
 * @param {string} quote
 * @param {string[]} sentences
 * @returns {number[]} candidate indices
 */
function boundaryCandidates(quote, sentences) {
  // Compare on words, not raw text. Splitting a sentence *changes the
  // punctuation at the seam* — "the cat sat down and then it slept" becomes
  // "The cat sat down." plus "And then it slept." — so a raw substring test
  // finds nothing and every boundary change falls through to fuzzy.
  const q = words(quote);
  if (!q) return [];

  const out = [];
  for (let i = 0; i < sentences.length; i++) {
    const s = words(sentences[i]);
    if (!s || s === q) continue;
    const [inner, outer] = s.length < q.length ? [s, q] : [q, s];
    if (inner.length < MIN_CONTAINMENT_CHARS) continue;
    if (inner.split(' ').length < MIN_CONTAINMENT_WORDS) continue;
    // Pad both sides so containment lands on whole words: "the cat" must not
    // match inside "the cattle".
    if (` ${outer} `.includes(` ${inner} `)) out.push(i);
  }
  return out;
}

/** Lowercased words only, punctuation dropped, whitespace collapsed. */
function words(s) {
  return (s ?? '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, '')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * How well the sentences around `index` match the neighbours we recorded.
 * Returns 0..1. This is the tiebreak that separates repeated sentences.
 */
function contextScore(anchor, sentences, index) {
  let score = 0;
  let checked = 0;

  anchor.before.forEach((want, i) => {
    const at = index - anchor.before.length + i;
    if (at < 0 || at >= sentences.length) return;
    checked++;
    score += similarity(want, sentences[at]);
  });

  anchor.after.forEach((want, i) => {
    const at = index + 1 + i;
    if (at < 0 || at >= sentences.length) return;
    checked++;
    score += similarity(want, sentences[at]);
  });

  return checked ? score / checked : 0;
}

/** Of several candidate indices, the one whose neighbours fit best. Ties break
 *  toward the recorded index, which is the best remaining evidence. */
function bestByContext(anchor, sentences, candidates) {
  let best = candidates[0];
  let bestScore = -1;
  for (const i of candidates) {
    const s = contextScore(anchor, sentences, i)
      // Nudge toward the original position, enough to settle a true tie and not
      // enough to overrule real context.
      - Math.abs(i - anchor.index) / (sentences.length * 1000);
    if (s > bestScore) { bestScore = s; best = i; }
  }
  return best;
}

/**
 * Find where this anchor points in a freshly extracted document.
 *
 * @param {Anchor|null} anchor
 * @param {string[]} sentences
 * @param {string|null} currentStamp
 * @returns {Located}
 */
export function locate(anchor, sentences, currentStamp = null) {
  if (!anchor || !sentences.length) {
    return { index: 0, how: 'hint', verified: false, stampChanged: false };
  }

  const stampsAgree = anchor.stamp != null && anchor.stamp === currentStamp;
  // An absent stamp is unknown provenance, not changed provenance. Positions
  // saved before stamping existed must not be reported as rewritten.
  const stampChanged = anchor.stamp != null && currentStamp != null && !stampsAgree;

  // Compare on the form the normalization contract says both halves produce:
  // runs of spaces and tabs collapsed, ends trimmed, newlines left alone. Two
  // strings that differ only in that respect cannot both be legal output of the
  // pipeline, so treating them as different was not strictness — it was
  // treating a violated invariant as evidence about the page.
  //
  // What this deliberately does not do is fold case or drop punctuation.
  // `exact` means character for character, and a sentence that was recapitalised
  // or had its apostrophe restyled genuinely is not the same characters. Those
  // belong to `fuzzy`, which is what it is for.
  const quote = contractual(anchor.quote);
  const current = sentences.map(contractual);

  // 1. Exact. Always attempted, whatever the stamp says.
  const exact = [];
  for (let i = 0; i < current.length; i++) {
    if (current[i] === quote) exact.push(i);
  }
  if (exact.length === 1) {
    return { index: exact[0], how: 'exact', verified: stampsAgree, stampChanged };
  }
  if (exact.length > 1) {
    // The quote is genuine but appears more than once; neighbours decide which
    // copy. Correct sentence, uncertain copy — a real reduction in confidence
    // that must not hide inside `exact`.
    return {
      index: bestByContext(anchor, sentences, exact),
      how: 'ambiguous',
      verified: false,
      stampChanged,
    };
  }

  // 2. Boundary. The sentence was split, merged, extended or trimmed, so no
  //    whole-sentence match survives but the text did. More precise than fuzzy,
  //    not a degraded form of it, so it is tried first.
  const touching = boundaryCandidates(anchor.quote, sentences);
  if (touching.length) {
    return {
      index: touching.length === 1
        ? touching[0]
        : bestByContext(anchor, sentences, touching),
      how: 'boundary',
      verified: false,
      stampChanged,
    };
  }

  // 3. Fuzzy, with a tie band rather than a single argmax.
  let bestScore = 0;
  const scores = sentences.map((s, i) => {
    const score = similarity(anchor.quote, s);
    if (score > bestScore) bestScore = score;
    return { i, score };
  });

  // Below ACCEPT the "match" is noise and the recorded index is better evidence.
  if (bestScore >= ACCEPT) {
    const tied = scores.filter((s) => s.score >= bestScore - TIE_MARGIN).map((s) => s.i);
    return {
      index: tied.length === 1 ? tied[0] : bestByContext(anchor, sentences, tied),
      how: 'fuzzy',
      verified: false,
      stampChanged,
    };
  }

  // 4. Nothing recognisable. Fall back to the recorded position, clamped —
  //    the article was probably replaced entirely.
  return {
    index: Math.min(anchor.index, sentences.length - 1),
    how: 'hint',
    verified: false,
    stampChanged,
  };
}

/**
 * What to tell the user, or null when the position needs no explanation.
 *
 * Branch on `how`, not on `verified`. An earlier version returned "the position
 * is approximate" for any unverified result, which made an exact verbatim hit
 * under a changed fingerprint announce doubt about a position that was in fact
 * correct. If the quoted sentence is present word for word, the position is
 * right whatever the stamp says: the stamp is provenance, not correctness, and
 * letting it override a fact already established is backwards. Found by the
 * desktop half, which had the same defect.
 *
 * `verified` still means what it meant, and callers may use it to decide how
 * much to trust a position. It just does not get to contradict an exact match.
 *
 * @param {Located} located
 * @returns {string|null}
 */
export function describe(located) {
  switch (located.how) {
    case 'exact':
      return null;
    case 'ambiguous':
      return 'This sentence appears more than once, so this is the closest match.';
    case 'boundary':
      return 'The sentence is longer or shorter than when you saved this, so this is the closest match.';
    case 'fuzzy':
      return located.verified === false && located.stampChanged
        ? 'The reading rules changed since you saved this, so this is the nearest sentence.'
        : 'The page changed since you were last here, so this is the nearest sentence.';
    default:
      return 'Could not find where you stopped, so reading starts from the saved position.';
  }
}
