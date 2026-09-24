/**
 * Which modules any test can reach.
 *
 * Not a coverage percentage — a list. Most of this codebase talks to a DOM or
 * to `chrome.*`, and those parts are genuinely hard to test in Node. That is a
 * defensible position. What is not defensible is not knowing which parts they
 * are, so this pins the set and fails when it grows.
 *
 * The point is the direction of the failure. A new module that no test can
 * reach is caught the day it is written, when adding a fake is cheap, rather
 * than months later when someone wonders why a bug shipped.
 *
 * Two of these were closed by writing fakes rather than by declaring them
 * untestable: `core/history.js` against a fake storage and database, and
 * `offscreen/player.js` against a fake audio graph. The second mattered — it
 * found a line in teardown that implied a guarantee it did not provide.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync, existsSync } from 'node:fs';
import path from 'node:path';

const extRoot = new URL('../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');

const walk = (dir) => readdirSync(dir).flatMap((n) => {
  const p = path.join(dir, n);
  return statSync(p).isDirectory() ? walk(p) : [p];
});

/**
 * Modules reachable from a test, following static *and* dynamic imports.
 *
 * Following only static ones is the same defect this file exists to find: the
 * first version of this scan reported `core/history.js` as untested because its
 * test imports it dynamically, and it has ninety of its own assertions.
 */
function reachable() {
  const seen = new Set();
  const visit = (file) => {
    if (seen.has(file) || !existsSync(file)) return;
    seen.add(file);
    const src = readFileSync(file, 'utf8');
    const specs = [
      ...[...src.matchAll(/from\s+'(\.[^']+)'/g)].map((m) => m[1]),
      ...[...src.matchAll(/import\(\s*'(\.[^']+)'\s*\)/g)].map((m) => m[1]),
      // A template literal, up to the first interpolation or query string.
      // Two modules are imported that way and for the same reason: they install
      // listeners at load, so each case must have a fresh instance, which means
      // a cache-busting suffix. The scan reported both as unreachable, and the
      // list grew an excuse each time rather than the scan growing an eye.
      ...[...src.matchAll(/import\(\s*`(\.[^`?$]+)/g)].map((m) => m[1]),
    ];
    for (const s of specs) visit(path.normalize(path.join(path.dirname(file), s)));
  };
  for (const t of walk(path.join(extRoot, 'test'))) visit(t);
  return seen;
}

/**
 * Modules no test reaches, and why. Every entry is a decision, not an
 * oversight: removing one from this list should mean writing a fake, not
 * deleting the line.
 */
const KNOWN_UNREACHED = {
  // Three entries left this list at once, and only one of them by being
  // tested. background/index.js is now driven directly by
  // test/background.test.mjs against a stand-in for the chrome APIs; the reason
  // that used to sit here — "orchestration over chrome.tabs, storage.session
  // and messaging" — described the obstacle rather than giving a reason, and
  // the obstacle was a hundred lines of fixture.
  //
  // offscreen-host.js and pdf/extract.js followed it out because the worker
  // imports them, so the scan now sees them. Reached is not exercised: what
  // actually runs of them is closeOffscreen on a stop and looksLikePdf on a
  // URL that is not one. KNOWN_UNEXERCISED below is the finer measure, and
  // this list has never claimed to be it.
  'src/content/index.js': 'live DOM, ranges and chrome messaging; driven end to end in tools/domtest/',
  'src/content/loader.js': 'four lines of dynamic import into a page context',
  'src/content/highlight.js': 'CSS Custom Highlight API, which has no Node equivalent; covered by tools/domtest/',
  'src/content/scroll.js': 'scroll position and wheel events; covered by tools/domtest/',
  // kokoro/index.js is deliberately absent: a test now imports it to check the
  // engine's declared sample rate and speed ceiling. Reachable is not the same
  // as exercised — almost none of its behaviour is covered — but this list
  // tracks reachability, and KNOWN_UNEXERCISED below is the finer measure.
  // model-store.js is absent because test/model-store.test.mjs now drives it
  // directly, against a fake CacheStorage and a fetch that streams in chunks.
  // worker.js IS tested — test/worker.test.mjs drives its whole message
  // protocol against a fake ONNX Runtime — but this scan cannot see it. The
  // test imports it through a computed URL, because the module installs
  // self.onmessage at load time and must be loaded fresh per case, and the
  // scan only resolves literal specifiers. Left listed rather than removed,
  // with the truth written down: a list that says "unreachable" where the
  // answer is "unresolvable by this scan" is the kind of true-sounding claim
  // CAVEATS section 19 is about.
  'src/engines/kokoro/worker.js': 'covered by test/worker.test.mjs; imported through a computed URL this scan cannot follow',

  'src/offscreen/offscreen.js': 'message wiring between the worker and the audio graph',
  'src/options/options.js': 'settings page DOM; the page is mounted and used in tools/domtest/',
  'src/pdf/viewer.js': 'viewer page DOM',
  'src/popup/popup.js': 'popup DOM; the page is mounted and clicked in tools/domtest/',
  'src/sidepanel/panel.js': 'side panel DOM; the page is mounted and clicked in tools/domtest/',
  'src/welcome/welcome.js': 'first-run page DOM; the page is mounted and used in tools/domtest/',
};

test('no module has quietly become untestable', () => {
  const seen = reachable();
  const sources = walk(path.join(extRoot, 'src'))
    .filter((f) => f.endsWith('.js'))
    .map((f) => path.relative(extRoot, f).split(path.sep).join('/'));

  assert.ok(sources.length > 20, `only ${sources.length} source modules found`);

  const unreached = sources.filter(
    (f) => !seen.has(path.normalize(path.join(extRoot, f))),
  );
  const surprises = unreached.filter((f) => !(f in KNOWN_UNREACHED));

  assert.deepEqual(surprises, [],
    'a new module no test can reach. Write a fake, or add it to KNOWN_UNREACHED with the reason');
});

test('the untested list has no stale entries', () => {
  // The species the desktop half named: an entry that was deliberate once
  // becomes a claim nobody rechecks. If a module became testable, the list
  // should shrink rather than quietly overstate the gap.
  const seen = reachable();
  const stale = Object.keys(KNOWN_UNREACHED).filter(
    (f) => seen.has(path.normalize(path.join(extRoot, f))) || !existsSync(path.join(extRoot, f)),
  );
  assert.deepEqual(stale, [],
    'these are listed as unreachable but are now reached, or no longer exist');
});

/**
 * Exported names a test actually uses, as opposed to merely imports.
 *
 * Reachability says a module was pulled in. It says nothing about whether
 * anything was checked. The desktop half found a file loaded by every run and
 * asserted against by none, which its scan reported as covered — the larger
 * gap, and invisible to the measurement that was being trusted.
 *
 * Import lines are stripped before searching, or every imported name would
 * count itself. Constants are matched by mention rather than by call, since
 * referencing one is how it gets exercised.
 */
function exercised(module) {
  const abs = path.join(extRoot, module);
  const names = [...new Set([...readFileSync(abs, 'utf8').matchAll(
    /export\s+(?:async\s+)?(?:function|class)\s+(\w+)|export\s+(?:const|let)\s+(\w+)/g,
  )].flatMap((m) => [m[1], m[2]].filter(Boolean)))];

  const bodies = walk(path.join(extRoot, 'test'))
    .filter((f) => f.endsWith('.mjs'))
    // This file names the unexercised exports in order to track them, which
    // made the scan report every one of them as used. A check that reads its
    // own list as evidence measures itself.
    .filter((f) => path.basename(f) !== 'coverage.test.mjs')
    .map((f) => readFileSync(f, 'utf8'))
    .filter((src) => src.includes(path.basename(module)))
    // Strip imports: a name in an import line is not a name being tested.
    .map((src) => src.replace(/^import[\s\S]*?from\s+'[^']+';$/gm, ''))
    // Strip comments. A name mentioned in prose about the code is not the code
    // being exercised — `extractBlocks` appeared only in a sentence explaining
    // what another test mirrors, and that was enough to report it as covered.
    // Same shape as a licence scan calling a library copyleft because its
    // notice quotes a licence it does not use.
    .map((src) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, ''))
    .join('\n');

  const used = names.filter((n) => new RegExp(`\\b${n}\\b`).test(bodies));
  return { names, used, unused: names.filter((n) => !used.includes(n)) };
}

/**
 * Exports this Node suite does not exercise, and why.
 *
 * Every one of these needs a live DOM: a TreeWalker over real elements, ranges
 * with real client rects, computed styles, a mutation observer. Faking that
 * convincingly is a larger project than the code being tested, and a
 * half-faithful fake would assert against a DOM that does not behave like the
 * one in Chrome.
 *
 * They are covered instead by `tools/domtest/`, which runs the same exports in
 * a browser against real elements — see the README. That suite found a real
 * defect on its first run, so the previous version of this comment, which said
 * these were checked only by using the extension, was describing a gap rather
 * than a decision.
 *
 * This list therefore means "not exercised by `node --test`", which is what the
 * scan below can actually see. Both suites have to be run.
 */
const KNOWN_UNEXERCISED = {
  'src/content/extract.js': ['findArticleRoot', 'extractBlocks', 'extractBySite', 'rangeFor'],
  'src/content/pagination.js': ['findNext', 'watchForGrowth'],
  // Not a gap: three tests delete bodies, through remove(), clear() and
  // prune(), and assert the body is gone afterwards. The scan looks for the
  // export's name in a test file and no test names this one, because callers
  // reach it through the operations a user actually performs. Testing it by
  // name would be testing it twice, so the entry stays and the reason is
  // written down rather than the coverage being overstated.
  'src/core/history.js': ['deleteBodies'],
  'src/engines/kokoro/g2p-en.js': ['init'],
};

test('no export has quietly stopped being exercised', () => {
  const surprises = [];
  for (const module of Object.keys(KNOWN_UNEXERCISED).concat([
    'src/core/segment.js', 'src/core/normalize.js', 'src/core/anchor.js',
    'src/pdf/layout.js', 'src/engines/kokoro/tokenize.js',
  ])) {
    const { unused } = exercised(module);
    const allowed = new Set(KNOWN_UNEXERCISED[module] ?? []);
    for (const n of unused) {
      if (!allowed.has(n)) surprises.push(`${module} exports ${n}, which no test uses`);
    }
  }
  assert.deepEqual(surprises, [],
    'an export nothing exercises. Test it, or add it to KNOWN_UNEXERCISED with the reason');
});

test('the unexercised list has no stale entries', () => {
  const stale = [];
  for (const [module, names] of Object.entries(KNOWN_UNEXERCISED)) {
    const { used, names: all } = exercised(module);
    for (const n of names) {
      if (used.includes(n)) stale.push(`${module}:${n} is listed as unexercised but is now used`);
      if (!all.includes(n)) stale.push(`${module}:${n} is listed but is no longer exported`);
    }
  }
  assert.deepEqual(stale, []);
});

test('the pieces where a mistake is silent are covered', () => {
  // Not everything needs a test. These do, because their failure mode is
  // silence rather than an error: wrong sounds, wrong reading order, a position
  // that resolves to the wrong sentence, audio that outlives a stop.
  const mustCover = [
    'src/core/segment.js',
    'src/core/normalize.js',
    'src/core/anchor.js',
    'src/core/history.js',
    'src/content/extract.js',
    'src/content/pagination.js',
    'src/pdf/layout.js',
    'src/engines/kokoro/tokenize.js',
    'src/engines/kokoro/g2p-en.js',
    'src/offscreen/player.js',
  ];
  const seen = reachable();
  const missing = mustCover.filter((f) => !seen.has(path.normalize(path.join(extRoot, f))));
  assert.deepEqual(missing, []);
});
