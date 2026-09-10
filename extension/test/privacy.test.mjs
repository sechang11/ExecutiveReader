/**
 * The published privacy policy must be the shipped one.
 *
 * The store listing points at site/privacy.html while the extension ships
 * PRIVACY.md. Two copies of the same promise, and the one people can hold us to
 * is the published one, so it is generated rather than written.
 *
 * These tests check the generator's output against the file on disk, and check
 * that the generator does not quietly drop content: a renderer that silently
 * skips a block it does not understand would produce a policy missing a
 * disclosure, which is the one failure worth catching here.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { render } from '../../tools/build-privacy.mjs';

const extRoot = new URL('../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const repoRoot = path.join(extRoot, '..');

const md = readFileSync(path.join(extRoot, 'PRIVACY.md'), 'utf8');
const html = readFileSync(path.join(repoRoot, 'site', 'privacy.html'), 'utf8');

test('the published page is what the generator produces', () => {
  assert.equal(
    render(md).replace(/\r\n/g, '\n'),
    html.replace(/\r\n/g, '\n'),
    'site/privacy.html has drifted from extension/PRIVACY.md; re-run tools/build-privacy.mjs',
  );
});

test('every sentence of the policy survives rendering', () => {
  // The specific fear: a block the renderer does not recognize disappears, and
  // what disappears is a disclosure. Comparing words rather than markup keeps
  // this from breaking on formatting while still catching a dropped paragraph.
  const words = (s) => s
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/[#*`|]/g, ' ')
    .replace(/^\s*[-:]+\s*$/gm, ' ')
    .split(/\s+/)
    .filter(Boolean);

  const rendered = new Set(words(html));
  const missing = words(md).filter((w) => !rendered.has(w) && !/^[-:]+$/.test(w));
  assert.deepEqual(missing, [], 'words in PRIVACY.md that never reached the page');
});

test('markdown wrapping does not break sentences', () => {
  // The page this replaced put every source line in its own paragraph, so
  // sentences split in the middle. A paragraph that starts lowercase or ends
  // without punctuation is that bug returning.
  const paragraphs = [...html.matchAll(/<p>([\s\S]*?)<\/p>/g)]
    .map((m) => m[1].replace(/<[^>]+>/g, '').trim())
    .filter(Boolean);
  assert.ok(paragraphs.length >= 5, `only ${paragraphs.length} paragraphs found`);
  for (const p of paragraphs) {
    assert.match(p, /^[A-Z"“]/, `paragraph starts mid-sentence: ${p.slice(0, 60)}`);
    // A digit ends the "Last updated" line legitimately. What the old page
    // produced was paragraphs ending in a lowercase word — "…every case where
    // data" — and that is what this rejects.
    assert.match(p, /[.:!?"”0-9]$/, `paragraph ends mid-sentence: ${p.slice(-60)}`);
  }
});

test('the two disclosures both reach the page', () => {
  // Named individually rather than counted, because the count is the thing a
  // future edit changes and the disclosures are the thing it must not lose.
  assert.match(html, /needs a connection/i);
  assert.match(html, /Hugging Face/);
  assert.match(html, /leaves your device, and there are only two/);
});
