/**
 * Every element a UI script reaches for must exist in its page.
 *
 * These five surfaces are plain HTML plus a script that finds elements by id.
 * There is no framework and no build step to catch a typo, so a renamed id or a
 * deleted element produces `null`, and `null.addEventListener` throws once, in
 * the page, where nothing reports it. The button simply does nothing.
 *
 * This is the cheapest real check available for code that otherwise needs a
 * browser: it does not run the pages, it compares what they contain against
 * what their scripts ask for.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';

const extRoot = new URL('../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');

/** Each page and the script it loads. */
const SURFACES = [
  ['src/popup/popup.html', 'src/popup/popup.js'],
  ['src/options/options.html', 'src/options/options.js'],
  ['src/sidepanel/panel.html', 'src/sidepanel/panel.js'],
  ['src/welcome/welcome.html', 'src/welcome/welcome.js'],
  ['src/pdf/viewer.html', 'src/pdf/viewer.js'],
];

const read = (rel) => readFileSync(path.join(extRoot, rel), 'utf8');

/** Ids the page defines. */
function idsIn(html) {
  return new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
}

/**
 * Ids the script asks for.
 *
 * Only literal lookups: a computed id cannot be checked from here, and
 * pretending otherwise would make this report confidently on something it
 * cannot see. Template literals are excluded for the same reason.
 */
function idsWanted(js) {
  const stripped = js
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
  const wanted = [
    ...[...stripped.matchAll(/getElementById\(\s*'([^']+)'\s*\)/g)].map((m) => m[1]),
    ...[...stripped.matchAll(/querySelector\(\s*'#([A-Za-z][\w-]*)'\s*\)/g)].map((m) => m[1]),
  ];

  // Every one of these pages defines the same one-character helper for
  // getElementById. Missing it made the first version of this scan report zero
  // lookups on all five files — a check that finds nothing passes everything,
  // which is why the count is asserted below rather than assumed.
  if (/const \$ = \(id\) => document\.getElementById\(id\)/.test(stripped)) {
    wanted.push(...[...stripped.matchAll(/\$\(\s*'([^']+)'\s*\)/g)].map((m) => m[1]));
  }
  return new Set(wanted);
}

test('there are surfaces to check', () => {
  // Guards the guard: a wrong path list would make every test below vacuous.
  for (const [html, js] of SURFACES) {
    assert.ok(existsSync(path.join(extRoot, html)), `${html} is missing`);
    assert.ok(existsSync(path.join(extRoot, js)), `${js} is missing`);
  }
});

for (const [htmlPath, jsPath] of SURFACES) {
  test(`${path.basename(htmlPath)} contains everything ${path.basename(jsPath)} asks for`, () => {
    const have = idsIn(read(htmlPath));
    const want = idsWanted(read(jsPath));

    assert.ok(want.size >= 3, `${jsPath}: only ${want.size} literal id lookups found; is the scan working?`);

    const missing = [...want].filter((id) => !have.has(id));
    assert.deepEqual(missing, [], `${jsPath} looks for ids that ${htmlPath} does not define`);
  });

  test(`${path.basename(htmlPath)} loads its own script and nothing else`, () => {
    const html = read(htmlPath);
    const scripts = [...html.matchAll(/<script[^>]*src="([^"]+)"/g)].map((m) => m[1]);
    assert.ok(scripts.length > 0, 'the page loads no script');

    for (const src of scripts) {
      // Manifest V3 forbids remote code, and a store review rejects a page that
      // pulls a script from anywhere but the package.
      assert.ok(!/^https?:|^\/\//.test(src), `${htmlPath} loads remote code: ${src}`);
      const resolved = path.join(extRoot, path.dirname(htmlPath), src);
      assert.ok(existsSync(resolved), `${htmlPath} loads ${src}, which does not exist`);
    }
  });

  test(`${path.basename(htmlPath)} has no inline script for the CSP to reject`, () => {
    const html = read(htmlPath);
    // An inline handler or an inline <script> block is silently dead under the
    // extension CSP: the page renders and the control does nothing.
    assert.ok(!/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?\S[\s\S]*?<\/script>/.test(html),
      `${htmlPath} contains an inline script block`);
    assert.equal(/\son(click|change|input|submit)=/.exec(html), null,
      `${htmlPath} contains an inline event handler`);
  });

  test(`${path.basename(htmlPath)} labels every control it shows`, () => {
    // A reader for people who cannot comfortably read is the last place an
    // unlabelled control belongs.
    const html = read(htmlPath);
    const controls = [...html.matchAll(/<(button|input|select|textarea)\b([^>]*)>/g)];
    if (!controls.length) return; // the PDF viewer is a surface with no controls

    const named = new Set([...html.matchAll(/<label[^>]*\bfor="([^"]+)"/g)].map((m) => m[1]));

    // A label may wrap its control instead of naming it, and here that is the
    // more common form. Missing it made the first version of this test report
    // a correctly labelled checkbox as unlabelled.
    const wrapped = new Set(
      [...html.matchAll(/<label\b[^>]*>([\s\S]*?)<\/label>/g)]
        .flatMap((m) => [...m[1].matchAll(/\bid="([^"]+)"/g)].map((n) => n[1])),
    );

    const unlabelled = [];

    for (const [, tag, attrs] of controls) {
      if (/\btype="(hidden|submit)"/.test(attrs)) continue;
      const id = /\bid="([^"]+)"/.exec(attrs)?.[1];
      const hasAria = /\baria-label(?:ledby)?="/.test(attrs);
      const hasTitle = /\btitle="/.test(attrs);
      const labelled = Boolean(id) && (named.has(id) || wrapped.has(id));
      // A <button> carries its own text, which this scan cannot see across the
      // tag boundary; those are checked by having a label, aria-label or title.
      if (hasAria || hasTitle || labelled) continue;
      if (tag === 'button') continue;
      unlabelled.push(id ?? attrs.trim().slice(0, 40));
    }

    assert.deepEqual(unlabelled, [], `${htmlPath} has controls with no accessible name`);
  });
}

test('every page declares a language', () => {
  // A page with no `lang` is announced by a screen reader in whatever voice it
  // was last using, which for this audience is the whole experience. No
  // viewport assertion: a popup and a side panel are sized by Chrome rather
  // than by the document, so requiring one would be a rule invented here.
  for (const [htmlPath] of SURFACES) {
    assert.match(read(htmlPath), /<html[^>]*\blang="/, `${htmlPath} has no lang attribute`);
  }
});
