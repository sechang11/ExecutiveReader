/**
 * The PDF reading surface.
 *
 * Extracts the text, renders it as ordinary paragraphs, and then loads the
 * normal content script over the result. From that point nothing knows this
 * was a PDF: the same extraction, segmentation, highlighting and history apply,
 * and a position saved here resumes the same way as one saved on a web page.
 */

import { extractPdf } from './extract.js';

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const source = params.get('url');

function fail(message, detail) {
  $('meta').textContent = '';
  $('error').textContent = message;
  $('error').hidden = false;
  if (detail) console.error('[executive-reader]', detail);
}

async function run() {
  if (!source) return fail('No PDF was given to open.');

  document.title = 'Executive Reader PDF';
  $('meta').textContent = 'Reading the document…';

  let doc;
  try {
    doc = await extractPdf(source, (done, total) => {
      $('meta').textContent = `Reading page ${done} of ${total}…`;
    });
  } catch (e) {
    return fail(
      'Could not open this PDF. It may be password-protected, or the site may '
      + 'not allow it to be downloaded.', e,
    );
  }

  if (doc.empty) {
    $('title').textContent = doc.title;
    return fail(
      `This PDF has no text layer, so there is nothing to read aloud. It is `
      + `probably a scan of ${doc.pageCount} page${doc.pageCount === 1 ? '' : 's'}. `
      + `Recognising scanned text needs OCR, which the desktop app can do.`,
    );
  }

  $('title').textContent = doc.title;
  document.title = `${doc.title} — Executive Reader`;

  const frag = document.createDocumentFragment();
  for (const page of doc.pages) {
    const section = document.createElement('section');
    section.dataset.page = String(page.page);

    // A page marker for orientation, hidden from the reader: it is navigation,
    // not prose, and hearing "page 12" mid-sentence is exactly the furniture
    // the extractor works to remove.
    const marker = document.createElement('div');
    marker.className = 'page-mark';
    marker.setAttribute('aria-hidden', 'true');
    marker.textContent = `Page ${page.page}`;
    section.append(marker);

    for (const para of page.text.split(/\n{2,}/)) {
      if (!para.trim()) continue;
      const p = document.createElement('p');
      p.textContent = para.trim();
      section.append(p);
    }
    frag.append(section);
  }
  $('doc').replaceChildren(frag);

  const words = doc.pages.reduce((n, p) => n + (p.text.match(/\S+/g)?.length ?? 0), 0);
  $('meta').textContent = `${doc.pageCount} page${doc.pageCount === 1 ? '' : 's'}, `
    + `about ${words.toLocaleString()} words. Press Alt+P to read.`;

  // Hand over to the normal reader. Loaded after the text is in the DOM,
  // because extraction runs immediately on load.
  await import(chrome.runtime.getURL('src/content/index.js'));
}

run().catch((e) => fail('Something went wrong opening this PDF.', e));
