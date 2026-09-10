/**
 * Store listing consistency.
 *
 * The listing is written once and read by a reviewer who will compare it
 * against the manifest. A permission present in the code but absent from the
 * justifications reads as concealment; a justification for a permission that
 * was removed reads as carelessness. Neither is caught by anything else here,
 * and both cost a review round trip measured in weeks.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';

const extRoot = new URL('../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const repoRoot = path.join(extRoot, '..');

const manifest = JSON.parse(readFileSync(path.join(extRoot, 'manifest.json'), 'utf8'));
const listingPath = path.join(repoRoot, 'docs', 'store-listing.md');
const listing = readFileSync(listingPath, 'utf8');

test('every requested permission is justified in the listing', () => {
  const missing = manifest.permissions.filter((p) => !listing.includes(`\`${p}\``));
  assert.deepEqual(missing, [],
    'a permission with no justification reads to a reviewer as something being hidden');
});

test('the listing justifies nothing the manifest does not request', () => {
  // Rows are of the form | `permission` | justification |
  const rows = [...listing.matchAll(/^\| `([a-zA-Z<>/:*.-]+)`(?: \(optional\))? \|/gm)].map((m) => m[1]);
  assert.ok(rows.length >= manifest.permissions.length, `only found ${rows.length} justification rows`);

  const known = new Set([...manifest.permissions, ...(manifest.optional_host_permissions ?? [])]);
  const stale = rows.filter((r) => !known.has(r));
  assert.deepEqual(stale, [], 'a justification for a permission we no longer request is stale');
});

test('broad host access stays optional in both the manifest and the listing', () => {
  assert.ok(!manifest.permissions.some((p) => p.includes('://') || p === '<all_urls>'),
    'requesting every site at install lengthens review and costs installs');
  assert.match(listing, /Not requested at install/i);
});

test('the extension name matches the listing', () => {
  const named = listing.match(/```\n(Executive Reader[^\n]*)\n```/);
  assert.ok(named, 'the listing must state the name in a code block');
  assert.equal(named[1].trim(), manifest.name);
});

test('the short description fits the store limit', () => {
  // 132 characters, and the form silently truncates rather than warning.
  const block = listing.split('## Short description')[1];
  const quoted = block.match(/```\n([\s\S]*?)\n```/);
  assert.ok(quoted, 'the short description must be in a code block');
  const text = quoted[1].trim();
  assert.ok(text.length <= 132, `short description is ${text.length} characters`);
  assert.ok(text.length > 60, `short description is only ${text.length} characters; the space is free`);
});

test('a privacy policy exists and describes where data leaves the device', () => {
  const policyPath = path.join(extRoot, 'PRIVACY.md');
  assert.ok(existsSync(policyPath), 'the store requires a privacy policy');
  const policy = readFileSync(policyPath, 'utf8');

  // The two real cases. If either disappears from the policy while the code
  // still does it, the policy has become untrue.
  assert.match(policy, /needs a connection/i, 'remote system voices must be disclosed');
  assert.match(policy, /Hugging Face/i, 'the model download must be disclosed');
  assert.match(policy, /no analytics|No analytics/, 'the absence of telemetry should be stated plainly');
});

test('the claim of no telemetry is true in the code', () => {
  // Asserting it in a policy is worth nothing on its own. If a fetch to some
  // other host appears later, the policy silently becomes untrue, and nothing
  // else here would notice.
  const ALLOWED = /(^|\.)huggingface\.co$/;
  // Documentation links in comments are not network calls.
  const DOCS = /(^|\.)(w3\.org|apache\.org|mozilla\.org|chrome\.com|web\.dev|example\.com|github\.com|adobe\.com|iso\.org)$/;

  const offenders = [];
  const walk = (dir) => {
    const full = path.join(extRoot, dir);
    if (!existsSync(full)) return;
    for (const name of readdirSync(full)) {
      const p = path.join(full, name);
      if (statSync(p).isDirectory()) { walk(path.join(dir, name)); continue; }
      if (!/\.(m?js|html)$/.test(name)) continue;
      for (const m of readFileSync(p, 'utf8').matchAll(/https?:\/\/([a-z0-9.-]+)/gi)) {
        const host = m[1].toLowerCase();
        if (ALLOWED.test(host) || DOCS.test(host)) continue;
        offenders.push(`${path.join(dir, name)} references ${host}`);
      }
    }
  };

  walk('src');
  assert.deepEqual(offenders, [],
    'an unexpected host in our own code would make the privacy policy untrue');
});
