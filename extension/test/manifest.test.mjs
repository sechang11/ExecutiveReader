/**
 * Manifest integrity.
 *
 * Nothing type-checks a path in JSON against a file on disk, or an import in a
 * module against the list of files a page is allowed to load. Both failures are
 * silent at build time and fatal at run time: a content script whose dependency
 * is not web-accessible simply never loads, with an error only in the page's
 * own console.
 *
 * This caught two real breaks — pagination.js and pagination.json missing from
 * the resource list after an edit — which is why it is a test and not a
 * one-off check.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';

const root = new URL('../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const manifest = JSON.parse(readFileSync(path.join(root, 'manifest.json'), 'utf8'));
const resources = manifest.web_accessible_resources[0].resources;

const rel = (p) => path.join(root, p);

/** Does the resource list allow a page to load this path? */
function reachable(p) {
  return resources.some((r) => {
    if (!r.includes('*')) return r === p;
    const pattern = new RegExp(`^${r.replace(/\./g, '\\.').replace(/\*/g, '[^/]*')}$`);
    return pattern.test(p);
  });
}

test('every declared path exists on disk', () => {
  const declared = [
    manifest.background.service_worker,
    manifest.action.default_popup,
    manifest.side_panel.default_path,
    ...Object.values(manifest.icons),
  ];
  const missing = declared.filter((p) => !existsSync(rel(p)));
  assert.deepEqual(missing, []);
});

test('every resource pattern matches at least one real file', () => {
  let matched = 0;
  const empty = [];
  for (const r of resources) {
    if (!r.includes('*')) {
      if (existsSync(rel(r))) matched++;
      else empty.push(r);
      continue;
    }
    const dir = path.dirname(r);
    const pattern = new RegExp(`^${path.basename(r).replace(/\./g, '\\.').replace(/\*/g, '.*')}$`);
    const hits = existsSync(rel(dir)) ? readdirSync(rel(dir)).filter((f) => pattern.test(f)) : [];
    if (hits.length) matched += hits.length;
    else empty.push(r);
  }
  assert.deepEqual(empty, [], 'a pattern matching nothing is a resource that will 404');
  assert.ok(matched > 20, `only ${matched} files matched; the check is not looking at much`);
});

test('everything a page-loaded module needs is web accessible', () => {
  // Content scripts and the PDF viewer run in a page context, so every module
  // they import and every file they fetch must be declared. A missing one does
  // not fail the build; the reader just never starts.
  const roots = ['src/content/index.js', 'src/pdf/viewer.js'];
  const seen = new Set();
  const missing = [];

  const walk = (file) => {
    if (seen.has(file) || !existsSync(rel(file))) return;
    seen.add(file);
    const src = readFileSync(rel(file), 'utf8');

    for (const m of src.matchAll(/from\s+'(\.[^']+)'/g)) {
      const dep = path.posix.normalize(path.posix.join(path.posix.dirname(file), m[1]));
      if (!reachable(dep)) missing.push(`${file} imports ${dep}`);
      walk(dep);
    }
    for (const m of src.matchAll(/getURL\('([^']+)'\)/g)) {
      if (!reachable(m[1])) missing.push(`${file} fetches ${m[1]}`);
    }
  };

  roots.forEach(walk);
  assert.ok(seen.size >= 8, `walked only ${seen.size} modules; the check found nothing to do`);
  assert.deepEqual(missing, []);
});

test('permissions cover the APIs the worker actually calls', () => {
  const worker = readFileSync(rel('src/background/index.js'), 'utf8');
  const needs = {
    tts: /chrome\.tts\./,
    offscreen: /chrome\.offscreen\./,
    contextMenus: /chrome\.contextMenus\./,
    scripting: /chrome\.scripting\./,
    storage: /chrome\.storage\./,
  };
  for (const [permission, used] of Object.entries(needs)) {
    if (used.test(worker)) {
      assert.ok(manifest.permissions.includes(permission),
        `the worker calls chrome.${permission} but the manifest does not request it`);
    }
  }
});

test('broad host access is optional, not requested at install', () => {
  // Asking for every site up front lengthens store review and costs installs.
  assert.ok(!(manifest.permissions ?? []).some((p) => p.includes('://')),
    'host permissions must be requested on demand');
  assert.ok(manifest.optional_host_permissions?.includes('<all_urls>'));
});

test('every shared file the extension ships is actually read by it', () => {
  // Bytes matching is not behaviour matching. `pronunciation.json` sat in the
  // package for weeks, byte-identical to the canonical copy and passing every
  // sync check, while nothing in the extension read it — the desktop half used
  // it, so the shared-data pattern held everywhere it was looked at and stopped
  // being looked at. Shipping a file is not using it.
  const sharedDir = path.join(root, 'src', 'shared');
  const shipped = readdirSync(sharedDir).filter((f) => f.endsWith('.json'));
  assert.ok(shipped.length >= 4, `only ${shipped.length} shared files found`);

  const sources = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const p = path.join(dir, name);
      if (statSync(p).isDirectory()) { walk(p); continue; }
      if (/\.(m?js|html)$/.test(name)) sources.push(readFileSync(p, 'utf8'));
    }
  };
  walk(path.join(root, 'src'));
  const code = sources.join('\n');

  const unread = shipped.filter((f) => !code.includes(`src/shared/${f}`));
  assert.deepEqual(unread, [],
    'shipped but never read. Wire it up, or stop shipping it');

  // Honest limit: this looks for the filename in source, so it proves the file
  // is fetched, not that the result is used. Code that loads a file and drops
  // it on the floor still passes. It catches the failure that actually
  // happened — a file nothing referenced at all — and no more than that.
});
