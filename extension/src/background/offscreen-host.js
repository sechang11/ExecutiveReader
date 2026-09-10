/**
 * Service-worker side of the offscreen document.
 *
 * Creating an offscreen document is not idempotent — a second create throws —
 * and the worker that created it may since have been killed and restarted, so
 * "did I create one?" cannot be answered from memory. Every path here re-derives
 * the answer from the browser instead.
 *
 * See spec sections 2 and 3.2.
 */

const PATH = 'src/offscreen/offscreen.html';

/** In-flight creation, so concurrent callers await one attempt rather than
 *  racing to create a second document and throwing. */
let creating = null;

async function exists() {
  // getContexts is the reliable check; hasDocument() is older and narrower.
  if (chrome.runtime.getContexts) {
    const contexts = await chrome.runtime.getContexts({
      contextTypes: ['OFFSCREEN_DOCUMENT'],
    });
    return contexts.length > 0;
  }
  return chrome.offscreen.hasDocument?.() ?? false;
}

/** Create the document if it is not already there. Safe to call repeatedly and
 *  from several places at once. */
export async function ensureOffscreen() {
  if (await exists()) return;
  if (creating) { await creating; return; }

  creating = chrome.offscreen.createDocument({
    url: PATH,
    reasons: ['AUDIO_PLAYBACK'],
    justification: 'Play synthesized speech, which must survive the service worker being suspended.',
  }).catch((e) => {
    // Another context won the race between our check and our create. That is
    // success, not failure; anything else is real.
    if (!String(e).includes('Only a single offscreen')) throw e;
  }).finally(() => { creating = null; });

  await creating;
}

/**
 * Send to the offscreen document, creating it first if needed.
 * @param {string} type
 * @param {object} [payload]
 */
export async function callOffscreen(type, payload = {}) {
  await ensureOffscreen();
  return chrome.runtime.sendMessage({ target: 'offscreen', type, ...payload });
}

/**
 * Release the audio device and close the document.
 *
 * Ask for an ordered shutdown first and only then close, because closing the
 * document destroys the graph without draining it. The desktop half hit exactly
 * this: it released the device with a write still in flight and crashed at exit
 * having already logged a clean shutdown. Best-effort throughout, since a
 * document that has already gone is the outcome we wanted.
 */
export async function closeOffscreen() {
  if (!(await exists())) return;
  try {
    await chrome.runtime.sendMessage({ target: 'offscreen', type: 'shutdown' });
  } catch {
    // Document already gone or not listening. Closing below is still correct.
  }
  try {
    await chrome.offscreen.closeDocument();
  } catch {
    // Raced with another close.
  }
}
