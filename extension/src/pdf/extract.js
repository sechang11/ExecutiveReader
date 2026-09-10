/**
 * PDF text extraction, driving PDF.js.
 *
 * Chrome's own PDF viewer is a closed extension that we cannot inject into, so
 * reading a PDF means opening our own viewer for it. That turns out well rather
 * than badly: the viewer renders the extracted text as ordinary HTML, so
 * segmentation, highlighting, scroll-follow and history all work on a PDF
 * without knowing it is one.
 *
 * Only PDFs with a text layer. A scanned page is an image, and recognising it
 * needs OCR, which the desktop half has and the browser does not.
 */

import { pageLines, linesToText, furnitureFilter } from './layout.js';

/** @typedef {{page: number, text: string}} Page */

let pdfjs = null;

async function lib() {
  if (pdfjs) return pdfjs;
  pdfjs = await import(chrome.runtime.getURL('vendor/pdfjs/pdf.min.mjs'));
  // Without this the worker is fetched from a CDN, which the extension
  // content-security-policy blocks, and the failure is a hang rather than an
  // error.
  pdfjs.GlobalWorkerOptions.workerSrc = chrome.runtime.getURL('vendor/pdfjs/pdf.worker.min.mjs');
  return pdfjs;
}

/**
 * @param {string} url
 * @param {(done: number, total: number) => void} [onProgress]
 * @param {AbortSignal} [signal]
 * @returns {Promise<{title: string, pages: Page[], pageCount: number, empty: boolean}>}
 */
export async function extractPdf(url, onProgress, signal) {
  const lib_ = await lib();

  const task = lib_.getDocument({
    url,
    // Font and character-map data are not vendored, since they matter for
    // rendering rather than for reading. Text extraction degrades on some CJK
    // documents without them, which is a known limit rather than a bug.
    isEvalSupported: false,
    useSystemFonts: true,
  });
  signal?.addEventListener('abort', () => task.destroy(), { once: true });

  const doc = await task.promise;
  const pageCount = doc.numPages;

  // Two passes. Running heads can only be recognised by seeing them repeat, so
  // every page's lines are collected before any page's text is assembled.
  const perPage = [];
  for (let n = 1; n <= pageCount; n++) {
    if (signal?.aborted) break;
    const page = await doc.getPage(n);
    const [content, viewport] = await Promise.all([
      page.getTextContent(),
      Promise.resolve(page.getViewport({ scale: 1 })),
    ]);

    const items = content.items
      .filter((i) => typeof i.str === 'string')
      .map((i) => ({
        str: i.str,
        x: i.transform[4],
        y: i.transform[5],
        width: i.width ?? 0,
        height: i.height || Math.abs(i.transform[3]) || 10,
      }));

    perPage.push(pageLines(items, viewport.width));
    page.cleanup();
    onProgress?.(n, pageCount);
  }

  const keep = furnitureFilter(perPage);
  const pages = perPage
    .map((lines, i) => ({ page: i + 1, text: linesToText(lines.filter((l) => keep(l, i))) }))
    .filter((p) => p.text.trim());

  const meta = await doc.getMetadata().catch(() => null);
  const title = meta?.info?.Title?.trim() || filenameFrom(url);

  await doc.destroy();

  return {
    title,
    pages,
    pageCount,
    // Distinguishing "no text layer" from "failed" matters: the first has a
    // clear explanation for the user, the second does not.
    empty: pages.length === 0,
  };
}

/** @param {string} url */
function filenameFrom(url) {
  try {
    const name = decodeURIComponent(new URL(url).pathname.split('/').pop() || '');
    return name.replace(/\.pdf$/i, '') || 'PDF document';
  } catch {
    return 'PDF document';
  }
}

/** Does this URL look like a PDF we should offer to read? */
export function looksLikePdf(url) {
  if (!url) return false;
  try {
    const u = new URL(url);
    if (!/^https?:$/.test(u.protocol) && u.protocol !== 'file:') return false;
    return /\.pdf($|[?#])/i.test(u.pathname + u.search);
  } catch {
    return false;
  }
}
