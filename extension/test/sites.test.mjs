/**
 * Per-site extraction rules.
 *
 * Host matching is pure and testable; the selectors themselves are not, since
 * they belong to applications we cannot run here. What matters most is the
 * degradation path: a stale rule must fall back to the generic scorer rather
 * than produce an empty document, because these selectors will break and we
 * will not find out from a test.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { loadSites, siteRuleFor } from '../src/content/extract.js';

const rules = JSON.parse(
  readFileSync(new URL('../src/shared/sites.json', import.meta.url), 'utf8'),
);
loadSites(rules);

test('matches a host exactly', () => {
  const rule = siteRuleFor('mail.google.com');
  assert.ok(rule, 'Gmail must have a rule');
  assert.equal(rule.kind, 'mail');
  assert.ok(rule.roots.length);
});

test('matches a parent domain, so regional subdomains are covered', () => {
  // One entry has to cover mail.google.com without listing every variant.
  const rule = siteRuleFor('deep.sub.outlook.office.com');
  assert.ok(rule, 'a subdomain must inherit its parent rule');
  assert.equal(rule.host, 'outlook.office.com');
});

test('returns nothing for an ordinary site', () => {
  assert.equal(siteRuleFor('example.com'), null);
  assert.equal(siteRuleFor('en.wikipedia.org'), null);
  assert.equal(siteRuleFor(''), null);
  assert.equal(siteRuleFor(undefined), null);
});

test('a single-label host never matches a rule', () => {
  // Matching a bare label would let one entry named "com" apply its selectors
  // to the entire internet. Testing this against the real rules proves nothing,
  // because no such entry exists — the check needs a rule set that has one.
  //
  // Restored in a finally block: this mutates module-level state, and an
  // assertion that throws on the way past would leave every later test running
  // against the fake rules. That is exactly how it failed the first time.
  try {
    loadSites({
      hosts: {
        com: { kind: 'article', roots: ['.x'] },
        localhost: { kind: 'article', roots: ['.y'] },
      },
    });
    assert.equal(siteRuleFor('com'), null, 'a bare TLD must not match');
    assert.equal(siteRuleFor('localhost'), null, 'a single-label host must not match');
    // Nor does a two-label host inherit from its TLD, for the same reason.
    assert.equal(siteRuleFor('a.com'), null, 'a.com must not inherit a "com" rule');
  } finally {
    loadSites(rules);
  }
});

test('host matching is case-insensitive', () => {
  assert.ok(siteRuleFor('MAIL.GOOGLE.COM'));
});

test('every rule is a shape the extractor understands', () => {
  for (const [host, rule] of Object.entries(rules.hosts)) {
    assert.ok(Array.isArray(rule.roots) && rule.roots.length,
      `${host} has no roots, so it can never extract anything`);
    assert.ok(['article', 'mail'].includes(rule.kind), `${host} has an unknown kind`);
    if (rule.skip) assert.ok(Array.isArray(rule.skip), `${host} skip must be a list`);
  }
});

test('every selector parses as CSS', () => {
  // A malformed selector throws inside querySelectorAll and takes the whole
  // extraction with it, on a site we may not test by hand.
  const check = (sel, where) => {
    // Node has no DOM, so validate the shape the browser would reject on:
    // unbalanced brackets and quotes are what a hand-written rule gets wrong.
    const balanced = (open, close) => (sel.split(open).length === sel.split(close).length);
    assert.ok(balanced('[', ']'), `${where}: unbalanced brackets in ${sel}`);
    assert.ok(balanced('(', ')'), `${where}: unbalanced parens in ${sel}`);
    assert.ok(sel.split('"').length % 2 === 1, `${where}: unbalanced quotes in ${sel}`);
    assert.ok(sel.trim(), `${where}: empty selector`);
  };
  for (const [host, rule] of Object.entries(rules.hosts)) {
    for (const s of rule.roots) check(s, host);
    for (const s of rule.skip ?? []) check(s, host);
  }
});

test('mail rules skip quoted replies', () => {
  // Without this a thread reads every message once per reply below it, so a
  // ten-message thread is read roughly fifty times over.
  for (const [host, rule] of Object.entries(rules.hosts)) {
    if (rule.kind !== 'mail') continue;
    const skips = (rule.skip ?? []).join(' ');
    assert.ok(/quote|blockquote/i.test(skips),
      `${host} is a mail rule that does not skip quoted text`);
  }
});

test('site rules are fingerprinted, because they change the sentences', () => {
  // Changing which elements are extracted changes the text a stored position
  // was captured against, so positions must read as unverified afterwards.
  assert.equal(rules._affects_stored_text, true);
});
