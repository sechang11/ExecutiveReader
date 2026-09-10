/**
 * On-page highlighting via the CSS Custom Highlight API.
 *
 * The important property is that this mutates nothing. Every competitor wraps
 * spoken text in injected <span>s, which reflows the page, breaks sites whose
 * CSS targets element structure, and fights React and Vue for control of the
 * DOM. `CSS.highlights` paints ranges at the rendering layer instead, so the
 * document the site shipped is the document that stays.
 *
 * Styling lives in highlight.css. Note that ::highlight() accepts only a narrow
 * set of properties — color, background-color, text-decoration, text-shadow,
 * and -webkit-text-stroke — so there is no box model to work with here. That
 * constraint is why the active sentence is a background wash rather than a
 * bordered box.
 */

const SENTENCE = 'executive-reader-sentence';
const WORD = 'executive-reader-word';

export const supported = typeof CSS !== 'undefined' && 'highlights' in CSS;

/** @type {Highlight|null} */
let sentenceHl = null;
/** @type {Highlight|null} */
let wordHl = null;

function ensure() {
  if (!supported) return false;
  if (!sentenceHl) {
    sentenceHl = new Highlight();
    CSS.highlights.set(SENTENCE, sentenceHl);
  }
  if (!wordHl) {
    wordHl = new Highlight();
    // Registered after the sentence highlight so it paints on top; the API
    // resolves overlaps by registration order, not by any z-index.
    CSS.highlights.set(WORD, wordHl);
  }
  return true;
}

/** @param {Range|null} range */
export function setSentence(range) {
  if (!ensure()) return;
  sentenceHl.clear();
  wordHl.clear();
  if (range) sentenceHl.add(range);
}

/** @param {Range|null} range */
export function setWord(range) {
  if (!ensure()) return;
  wordHl.clear();
  if (range) wordHl.add(range);
}

export function clear() {
  sentenceHl?.clear();
  wordHl?.clear();
}

/** Remove the highlights entirely, on teardown. */
export function destroy() {
  if (!supported) return;
  CSS.highlights.delete(SENTENCE);
  CSS.highlights.delete(WORD);
  sentenceHl = null;
  wordHl = null;
}
