/**
 * Keep the sentence being read on screen, without fighting the user.
 *
 * The failure mode to avoid is the one every auto-scrolling reader has: you
 * scroll up to re-read something, and the page yanks you back. So any manual
 * scroll suspends following for a few seconds, and following resumes quietly
 * rather than snapping. Reduced-motion preferences are honoured, which matters
 * because smooth auto-scroll is a migraine and nausea trigger for some readers.
 */

const RESUME_AFTER_MS = 4000;
/** Fraction of the viewport height that counts as comfortably visible. */
const KEEP_BAND = [0.25, 0.7];

export function startScrollFollow() {
  let suspendedUntil = 0;
  let programmatic = false;

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  const onManualScroll = () => {
    // Our own smooth scroll fires scroll events too; ignore those.
    if (programmatic) return;
    suspendedUntil = Date.now() + RESUME_AFTER_MS;
  };

  // Only user-initiated gestures suspend following. Listening to `scroll`
  // alone cannot tell ours from theirs reliably.
  window.addEventListener('wheel', onManualScroll, { passive: true });
  window.addEventListener('touchmove', onManualScroll, { passive: true });
  window.addEventListener('keydown', (e) => {
    if (['PageUp', 'PageDown', 'ArrowUp', 'ArrowDown', 'Home', 'End', ' '].includes(e.key)) {
      onManualScroll();
    }
  }, true);

  return {
    /** @param {Range} range */
    follow(range) {
      if (Date.now() < suspendedUntil) return;

      const rect = range.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) return; // collapsed or hidden

      const vh = window.innerHeight;
      const top = rect.top / vh;
      const bottom = rect.bottom / vh;
      if (top >= KEEP_BAND[0] && bottom <= KEEP_BAND[1]) return; // already fine

      const target = window.scrollY + rect.top - vh * KEEP_BAND[0];
      programmatic = true;
      window.scrollTo({
        top: target,
        behavior: reduceMotion.matches ? 'auto' : 'smooth',
      });
      // Release the guard once the scroll has settled.
      setTimeout(() => { programmatic = false; }, reduceMotion.matches ? 50 : 600);
    },

    /** Let the user take over deliberately, e.g. after pressing pause. */
    suspend(ms = RESUME_AFTER_MS) {
      suspendedUntil = Date.now() + ms;
    },
  };
}
