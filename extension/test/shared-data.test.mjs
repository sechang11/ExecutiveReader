/**
 * Is each shared file actually load-bearing?
 *
 * The manifest test asserts every shipped file is *referenced* somewhere in the
 * source. That is a filename appearing in a string, and it would pass for code
 * that fetches a file and drops it on the floor.
 *
 * This is the stronger version, taken from the desktop half: blank each file in
 * turn and require the behaviour it drives to change. If emptying a rule set
 * changes nothing, the extension is not using it — which is exactly how
 * `pronunciation.json` sat inert in the package while every sync and drift
 * check passed.
 *
 * Its limit, stated rather than implied: this proves the data is consulted, not
 * that it is consulted correctly.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

const url = (f) => new URL(`../src/shared/${f}`, import.meta.url);
const read = (f) => JSON.parse(readFileSync(url(f), 'utf8'));

test('abbreviations.json changes how sentences are split', async () => {
  const { segment, loadAbbreviations } = await import('../src/core/segment.js');

  loadAbbreviations(read('abbreviations.json'));
  const withRules = segment('Dr. Chen arrived.').length;

  loadAbbreviations({});
  const without = segment('Dr. Chen arrived.').length;

  assert.equal(withRules, 1, 'the title must not end a sentence');
  assert.ok(without > withRules, 'emptying the list must change the split, or it is not consulted');
  loadAbbreviations(read('abbreviations.json'));
});

test('normalization.json changes what is spoken', async () => {
  const N = await import('../src/core/normalize.js');

  N.loadNormalization(read('normalization.json'));
  const withRules = N.normalize('It cost $50, i.e. 20% over.');

  N.loadNormalization({});
  const without = N.normalize('It cost $50, i.e. 20% over.');

  assert.notEqual(withRules, without, 'emptying the rules must change the output');
  assert.match(withRules, /dollars/);
  N.loadNormalization(read('normalization.json'));
});

test('pronunciation.json changes how words sound', async () => {
  const P = await import('../src/core/pronounce.js');

  P.loadBuiltin(read('pronunciation.json'));
  P.loadUser([]);
  const withRules = P.pronounce('The GIF from nginx.').text;

  P.loadBuiltin({});
  const without = P.pronounce('The GIF from nginx.').text;

  assert.notEqual(withRules, without,
    'this is the file that shipped inert; emptying it must be visible');
  P.loadBuiltin(read('pronunciation.json'));
});

test('pagination.json drives next-page detection', async () => {
  const { loadRules, needsConfirmation } = await import('../src/content/pagination.js');
  const rules = read('pagination.json');

  loadRules(rules);
  const withRules = needsConfirmation({ confidence: 'medium' });

  // With no confirm threshold, every candidate needs asking about.
  loadRules({ strategies: [], confirm_below: 'high' });
  const without = needsConfirmation({ confidence: 'medium' });

  assert.equal(withRules, false, 'medium confidence is followed without asking');
  assert.notEqual(withRules, without, 'the threshold must come from the file');
  loadRules(rules);
});

test('sites.json drives per-site extraction', async () => {
  const { loadSites, siteRuleFor } = await import('../src/content/extract.js');
  const rules = read('sites.json');

  loadSites(rules);
  const withRules = siteRuleFor('mail.google.com');

  loadSites({ hosts: {} });
  const without = siteRuleFor('mail.google.com');

  assert.ok(withRules, 'Gmail must resolve to a rule');
  assert.equal(without, null, 'emptying the file must remove it');
  loadSites(rules);
});

/**
 * Every file shipped under `src/shared/`, and what checks it.
 *
 * `blanked` means a test above empties it and requires behaviour to change.
 * `elsewhere` names the test that covers it instead. `not-data` means it is not
 * a rule set and nothing should consult it.
 *
 * Unclassified fails, so a new file cannot join unexamined — which is exactly
 * how the pronunciation rules got in and sat inert. Classified-but-absent fails
 * too, because an entry describing a file that no longer exists is fiction, and
 * without the reasons recorded the honest state and the inert state look
 * identical to whoever reads this next.
 */
const CLASSIFIED = {
  'abbreviations.json': ['blanked', 'drives sentence splitting'],
  'normalization.json': ['blanked', 'drives what is spoken'],
  'pronunciation.json': ['blanked', 'drives how words sound'],
  'pagination.json': ['blanked', 'drives the next-page confidence threshold'],
  'sites.json': ['blanked', 'drives per-site extraction'],
  'fingerprint.json': ['elsewhere', 'generated; covered by standalone.test.mjs and the sync tool'],
};

test('every shared file is classified, and every classification is real', () => {
  const dir = new URL('../src/shared/', import.meta.url);
  const present = readdirSync(dir).filter((f) => f.endsWith('.json'));
  assert.ok(present.length >= 5, `only ${present.length} shared files found`);

  const unclassified = present.filter((f) => !(f in CLASSIFIED));
  assert.deepEqual(unclassified, [],
    'a shared file with no load-bearing check could be inert and nothing would say so');

  // The other direction, which is the half I would not have written unprompted.
  const fictional = Object.keys(CLASSIFIED).filter((f) => !present.includes(f));
  assert.deepEqual(fictional, [],
    'classified but absent: the entry describes a file that no longer exists');
});

test('every file marked blanked really is blanked by a test here', () => {
  // Otherwise the classification is a claim about this file that this file does
  // not honour — the same species as a comment describing behaviour the code
  // stopped having.
  const source = readFileSync(new URL(import.meta.url), 'utf8');
  const missing = Object.entries(CLASSIFIED)
    .filter(([, [status]]) => status === 'blanked')
    .map(([file]) => file)
    .filter((file) => !source.includes(`read('${file}')`));

  assert.deepEqual(missing, [],
    'marked blanked but no test above empties it');
});
