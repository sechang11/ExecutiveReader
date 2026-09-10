/**
 * Classic content script whose only job is to pull in the real one as a module.
 *
 * Content scripts injected via chrome.scripting.executeScript cannot use static
 * `import`, but they can use dynamic `import()` against an extension URL, and
 * the loaded module runs in the same isolated world with full chrome.* access.
 * This is the standard way to keep an unbundled, no-build-step content script,
 * which in turn keeps the code a store reviewer reads identical to the code
 * that runs.
 *
 * Injection is idempotent: the service worker re-injects on every read, and a
 * second run must not register a second set of listeners.
 */

(() => {
  if (window.__earmarkLoaded) return;
  window.__earmarkLoaded = true;

  import(chrome.runtime.getURL('src/content/index.js')).catch((err) => {
    window.__earmarkLoaded = false;
    console.error('[earmark] failed to load reader', err);
  });
})();
