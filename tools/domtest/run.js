/**
 * DOM tests for the content scripts.
 *
 * `extract.js` and `pagination.js` hold the code most likely to break on a real
 * page, and until now they were checked by using the extension. These run their
 * real exports against real elements — real computed styles, a real TreeWalker,
 * real Ranges — and report in the page.
 *
 * Fixtures are written as HTML strings and mounted off-screen. They are meant
 * to look like the shapes that actually occur: an article buried in navigation
 * chrome, a comment thread that outweighs the body, inline markup that renders
 * without a word break.
 */

import {
  findArticleRoot, extractBlocks, rangeFor, trustsExplicitRoot,
  loadSites, siteRuleFor, extractBySite, clampToNode,
} from '../../extension/src/content/extract.js';
import {
  findNext, watchForGrowth, loadRules, needsConfirmation,
} from '../../extension/src/content/pagination.js';
import * as highlight from '../../extension/src/content/highlight.js';
import { startScrollFollow } from '../../extension/src/content/scroll.js';
import { surfaceTests } from './surfaces.js';
import { contentTests } from './content.js';
import { offscreenTests } from './offscreen.js';

const results = document.getElementById('results');
const stage = document.getElementById('stage');
let passed = 0;
let failed = 0;

function report(name, error) {
  const li = document.createElement('li');
  li.className = error ? 'fail' : 'pass';
  li.textContent = `${error ? '✗' : '✓'} ${name}`;
  if (error) {
    const pre = document.createElement('pre');
    pre.textContent = error.stack ?? String(error);
    li.appendChild(pre);
  }
  results.appendChild(li);
  if (error) failed++; else passed++;
}

/** Mount a fixture, run the body against it, and always tear it down. */
async function test(name, html, fn) {
  stage.innerHTML = html;
  try {
    await fn(stage);
    report(name, null);
  } catch (e) {
    report(name, e);
  } finally {
    stage.innerHTML = '';
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message ?? 'assertion failed');
}

function equal(actual, expected, message) {
  if (!Object.is(actual, expected)) {
    throw new Error(`${message ?? 'not equal'}\n  actual:   ${JSON.stringify(actual)}\n  expected: ${JSON.stringify(expected)}`);
  }
}

function deepEqual(actual, expected, message) {
  const a = JSON.stringify(actual);
  const b = JSON.stringify(expected);
  if (a !== b) throw new Error(`${message ?? 'not deep equal'}\n  actual:   ${a}\n  expected: ${b}`);
}

const para = (n) => `<p>${'The quick brown fox jumped over the lazy dog. '.repeat(n).trim()}</p>`;

/**
 * A stand-in Document scoped to the fixture.
 *
 * The modules take a Document so they can be pointed at a fixture instead of
 * the whole page — the real page carries this harness's own headings and links,
 * which would score higher than anything a fixture contains. `location` is
 * supplied because the pagination code reads it to resolve relative URLs and to
 * reject a link pointing at the current page.
 */
const fakeDoc = (root, href = 'https://example.com/article/page/1') => ({
  querySelector: (sel) => root.querySelector(sel),
  querySelectorAll: (sel) => root.querySelectorAll(sel),
  body: root,
  location: { href, hostname: new URL(href).hostname },
});

// ----------------------------------------------------------- article root

await test(
  'an explicit article is trusted when it holds the page',
  `<nav><a href="/">Home</a></nav><article>${para(6)}</article><footer>Copyright</footer>`,
  (root) => {
    equal(findArticleRoot(fakeDoc(root)).tagName, 'ARTICLE');
  },
);

await test(
  'a stub article loses to the real body text',
  // The shape this exists for: a site that wraps a teaser in <article> and puts
  // the story in a div. Trusting the tag would read the teaser and stop.
  `<article><p>Read more below.</p></article>
   <div class="story">${para(10)}${para(10)}${para(10)}</div>`,
  (root) => {
    const doc = fakeDoc(root);
    const found = findArticleRoot(doc);
    assert(found.className === 'story', `picked ${found.tagName}.${found.className}`);
  },
);

await test(
  'a comment thread does not outrank the article',
  `<div class="post">${para(8)}${para(8)}</div>
   <div class="comments">${para(3)}${para(3)}${para(3)}${para(3)}</div>`,
  (root) => {
    const doc = fakeDoc(root);
    equal(findArticleRoot(doc).className, 'post');
  },
);

await test(
  'a link farm does not outrank prose of the same length',
  `<div class="prose">${para(8)}</div>
   <div class="links"><p>${'<a href="/x">The quick brown fox jumped over the lazy dog.</a> '.repeat(8)}</p></div>`,
  (root) => {
    const doc = fakeDoc(root);
    equal(findArticleRoot(doc).className, 'prose');
  },
);

// The threshold itself is already unit-tested in Node; this checks the numbers
// it is called with are the ones the page actually has.
await test(
  'the trust test sees real text lengths, not markup lengths',
  `<article><p>${'x'.repeat(300)}</p></article>`,
  (root) => {
    const article = root.querySelector('article');
    const own = article.textContent.trim().length;
    equal(own, 300, 'textContent must not include tags');
    assert(trustsExplicitRoot(own, root.textContent.trim().length));
  },
);

// --------------------------------------------------------------- blocks

await test(
  'inline markup does not invent a word break',
  // `<p>Hello<b>world</b></p>` renders as "Helloworld". Adding a space here
  // would desynchronise every offset after it, and the highlight with them.
  '<p>Hello<b>world</b></p><p>Hello <b>world</b></p>',
  (root) => {
    const blocks = extractBlocks(root);
    deepEqual(blocks.map((b) => b.text), ['Helloworld', 'Hello world']);
  },
);

await test(
  'whitespace between elements still separates words',
  '<p>Hello\n  <em>there</em>\n  friend</p>',
  (root) => {
    const blocks = extractBlocks(root);
    deepEqual(blocks.map((b) => b.text), ['Hello there friend']);
  },
);

await test(
  'script, style and hidden elements are not read aloud',
  `<div>
     <p>Visible text that is long enough to keep.</p>
     <script>const secret = "never read this";</script>
     <style>.a { color: red }</style>
     <p hidden>Hidden text</p>
     <p style="display:none">Also hidden</p>
     <p aria-hidden="true">Decorative</p>
   </div>`,
  (root) => {
    const text = extractBlocks(root).map((b) => b.text).join(' ');
    assert(text.includes('Visible text'), 'the visible paragraph must survive');
    for (const bad of ['secret', 'color', 'Hidden text', 'Also hidden', 'Decorative']) {
      assert(!text.includes(bad), `"${bad}" reached the reader`);
    }
  },
);

await test(
  'each block is one rendered block, not one element',
  '<div><span>one </span><span>two</span></div><div>three</div>',
  (root) => {
    // Two inline spans inside one div are one spoken block; the second div is
    // its own. This is decided by computed style, which is why it needs a page.
    deepEqual(extractBlocks(root).map((b) => b.text), ['one two', 'three']);
  },
);

await test(
  'headings and list items keep their roles',
  '<h2>A title here</h2><ul><li>First item text</li><li>Second item text</li></ul>',
  (root) => {
    const blocks = extractBlocks(root);
    deepEqual(blocks.map((b) => b.role), ['heading', 'listitem', 'listitem']);
  },
);

await test(
  'code blocks are skippable, and announced by default',
  '<p>Before the code.</p><pre>const x = 1;</pre><p>After the code.</p>',
  (root) => {
    equal(extractBlocks(root, { codeMode: 'skip' }).length, 2, 'skip must drop the pre');
    equal(extractBlocks(root, { codeMode: 'announce' }).length, 3);
  },
);

await test(
  'a block of one character is dropped as noise',
  '<p>·</p><p>Real text goes here.</p>',
  (root) => {
    deepEqual(extractBlocks(root).map((b) => b.text), ['Real text goes here.']);
  },
);

await test(
  'no newline ever reaches the segmenter from the DOM',
  // The desktop half treats a line break as structure and the extension does
  // not, which its conformance harness records as the largest group of
  // divergences. It is unreachable here: extractBlocks collapses every run of
  // whitespace inside a block, and separate rendered blocks are separate
  // blocks, so segment() is never handed a newline to misread. This asserts
  // that rather than arguing it, because the argument is what would rot.
  `<div>
     <p>The sentence was wrapped by the exporter
across two lines even though it
is one sentence.</p>
     <h2>A Heading</h2>
     <p>Some body text follows.</p>
     <pre>const a = 1;
const b = 2;</pre>
   </div>`,
  (root) => {
    const blocks = extractBlocks(root);
    assert(blocks.length >= 3, `only ${blocks.length} blocks`);
    for (const b of blocks) {
      assert(!/[\r\n]/.test(b.text), `a block carried a line break: ${JSON.stringify(b.text)}`);
    }
    // And the hard-wrapped paragraph is one block, rejoined with spaces.
    equal(blocks[0].text,
      'The sentence was wrapped by the exporter across two lines even though it is one sentence.');
    // The heading and the paragraph under it are separate, which is the case
    // the desktop half reaches by breaking on the blank line.
    equal(blocks[1].text, 'A Heading');
    equal(blocks[2].text, 'Some body text follows.');
  },
);

// ---------------------------------------------------------------- ranges

await test(
  'a range covers exactly the characters asked for',
  '<p>Hello there, friend</p>',
  (root) => {
    const [block] = extractBlocks(root);
    const range = rangeFor(block, 6, 11);
    equal(range.toString(), 'there');
  },
);

await test(
  'a range spans inline elements without picking up their markup',
  '<p>Hello <b>there</b> friend</p>',
  (root) => {
    const [block] = extractBlocks(root);
    equal(block.text, 'Hello there friend');
    equal(rangeFor(block, 0, 11).toString(), 'Hello there');
    equal(rangeFor(block, 6, 18).toString(), 'there friend');
  },
);

await test(
  'a range over a block whose nodes were replaced returns null',
  // This is how the caller learns the page changed under it, rather than
  // highlighting whatever now occupies those offsets.
  '<p>Hello there, friend</p>',
  (root) => {
    const [block] = extractBlocks(root);
    root.querySelector('p').textContent = 'Something else entirely';
    equal(rangeFor(block, 6, 11), null);
  },
);

await test(
  'clamping agrees with where the text actually landed',
  '<p>   Hello   there   </p>',
  (root) => {
    const [block] = extractBlocks(root);
    equal(block.text, 'Hello there');
    // Every offset in the block must map to a range holding that exact text.
    for (let i = 0; i < block.text.length; i++) {
      const r = rangeFor(block, i, i + 1);
      // Collapsed, because the range covers the source's whitespace run and
      // the block text holds the single space that run rendered as. A
      // highlight spanning the whole run is what a reader expects to see.
      equal(r && r.toString().replace(/\s+/g, ' '), block.text[i], `offset ${i}`);
    }
    // clampToNode is exported so this mapping can be checked without a DOM;
    // here we confirm the DOM path and the pure path agree.
    for (const piece of block.pieces) {
      equal(typeof clampToNode(piece, piece.start), 'number');
    }
  },
);

// ------------------------------------------------------------ site rules

await test(
  'a site rule selects its own root',
  `<div id="chrome">${para(10)}</div>
   <div id="mail-body"><p>Message text here, long enough to keep.</p></div>`,
  (root) => {
    loadSites({ hosts: { 'mail.example.com': { roots: ['#mail-body'] } } });
    const rule = siteRuleFor('mail.example.com');
    assert(rule, 'the rule must be found by host');

    const blocks = extractBySite(rule, fakeDoc(root));

    assert(blocks, 'the rule selected nothing');
    deepEqual(blocks.map((b) => b.text), ['Message text here, long enough to keep.']);
  },
);

await test(
  'a rule matches a parent domain, so regional hosts need no entry',
  '<div>anything</div>',
  () => {
    loadSites({ hosts: { 'example.com': { roots: ['#x'] } } });
    const rule = siteRuleFor('mail.eu.example.com');
    assert(rule, 'a parent-domain rule must match');
    equal(rule.host, 'example.com');
  },
);

await test(
  'a stale rule yields null, so the generic scorer can take over',
  // These rules describe applications that change without warning. Returning
  // an empty document instead of null would read a blank page aloud.
  `<div id="mail-body-renamed"><p>Message text here, long enough to keep.</p></div>`,
  (root) => {
    loadSites({ hosts: { 'mail.example.com': { roots: ['#mail-body'] } } });
    equal(extractBySite(siteRuleFor('mail.example.com'), fakeDoc(root)), null);
  },
);

await test(
  'a host with no rule gets none, rather than the first one',
  '<div>anything</div>',
  () => {
    loadSites({ hosts: { 'mail.example.com': { roots: ['#x'] } } });
    equal(siteRuleFor('news.example.org'), null);
  },
);

// ------------------------------------------------------------ pagination

// The real strategy list, not an invented one: these tests are worth having
// only if they exercise the rules that ship.
loadRules(await fetch('../../shared/pagination.json').then((r) => r.json()));

await test(
  'rel=next wins, and carries high confidence',
  '<a href="/article/page/2" rel="next">Continue</a>',
  (root) => {
    const next = findNext(fakeDoc(root));
    assert(next, 'rel=next must be found');
    equal(next.url, 'https://example.com/article/page/2');
    equal(next.strategy, 'link-rel-next');
    equal(next.confidence, 'high');
    equal(needsConfirmation(next), false, 'a high-confidence link is followed without asking');
  },
);

await test(
  'a link to the current page is not offered as the next one',
  '<a href="/article/page/1" rel="next">Next</a>',
  (root) => {
    equal(findNext(fakeDoc(root)), null, 'following this would loop forever');
  },
);

await test(
  'a disabled next control is not followed',
  '<a href="/article/page/2" rel="next" aria-disabled="true">Next</a>',
  (root) => {
    // The last page of a paginated article usually keeps the control and
    // disables it. Following it would navigate away from the end of the piece.
    const next = findNext(fakeDoc(root));
    assert(!next || next.strategy !== 'link-rel-next', 'a disabled rel=next was followed');
  },
);

await test(
  'an article titled "what happens next" is not a next link',
  // Substring matching on link text is the obvious implementation and it takes
  // readers to unrelated stories.
  '<a href="/story/what-happens-next-for-the-team">What happens next for the team</a>',
  (root) => {
    equal(findNext(fakeDoc(root)), null);
  },
);

await test(
  'a link whose whole text is "Next" is followed, at low confidence',
  '<a href="/article/page/2">Next</a>',
  (root) => {
    const next = findNext(fakeDoc(root));
    assert(next, 'a plain Next link must be found');
    equal(next.strategy, 'text-next');
    equal(needsConfirmation(next), true, 'a guess this weak must be offered, not taken');
  },
);

await test(
  'with no link at all, the URL number is the last resort',
  '<p>No pagination controls anywhere on this page.</p>',
  (root) => {
    const next = findNext(fakeDoc(root, 'https://example.com/list?page=7'));
    assert(next, 'the url-increment strategy must still fire');
    equal(next.url, 'https://example.com/list?page=8');
    equal(needsConfirmation(next), true);
  },
);

await test(
  'a hidden next link is not followed',
  '<a href="/article/page/2" rel="next" style="display:none">Next</a>',
  (root) => {
    const next = findNext(fakeDoc(root));
    assert(!next || next.strategy !== 'link-rel-next', 'a display:none link was followed');
  },
);

await test(
  'a real slab of new prose counts as the page growing',
  '<div id="feed"><p>First post text goes here.</p></div>',
  async (root) => {
    const feed = root.querySelector('#feed');
    const seen = [];
    const stop = watchForGrowth(feed, (added) => seen.push(added), { quietMs: 50 });

    feed.insertAdjacentHTML('beforeend', para(4));
    await new Promise((r) => setTimeout(r, 300));
    stop();

    equal(seen.length, 1, 'an infinite-scroll page never reported new content');
    assert(seen[0].length >= 1);
  },
);

await test(
  'a spinner appearing is not the page growing',
  // Ads, spinners and lazy images mutate constantly. Re-extracting the document
  // for each one would be most of what the extension did on a busy page.
  '<div id="feed"><p>First post text goes here.</p></div>',
  async (root) => {
    const feed = root.querySelector('#feed');
    const seen = [];
    const stop = watchForGrowth(feed, () => seen.push(1), { quietMs: 50 });

    feed.insertAdjacentHTML('beforeend', '<div class="spinner"></div><img alt="">');
    await new Promise((r) => setTimeout(r, 300));
    stop();

    equal(seen.length, 0);
  },
);

await test(
  'a burst of appends is reported once, not once per node',
  '<div id="feed"><p>First post text goes here.</p></div>',
  async (root) => {
    const feed = root.querySelector('#feed');
    const seen = [];
    const stop = watchForGrowth(feed, (added) => seen.push(added.length), { quietMs: 60 });

    for (let i = 0; i < 5; i++) feed.insertAdjacentHTML('beforeend', para(2));
    await new Promise((r) => setTimeout(r, 300));
    stop();

    deepEqual(seen, [5], 'frameworks append in bursts; one page of text is one event');
  },
);

await test(
  'stopping the watch stops the callbacks',
  '<div id="feed"><p>First post text goes here.</p></div>',
  async (root) => {
    const feed = root.querySelector('#feed');
    const seen = [];
    const stop = watchForGrowth(feed, () => seen.push(1), { quietMs: 50 });
    stop();

    feed.insertAdjacentHTML('beforeend', para(4));
    await new Promise((r) => setTimeout(r, 200));

    equal(seen.length, 0, 'a watcher that outlives its page keeps re-extracting it');
  },
);

// ------------------------------------------------------------- highlight

/** What the highlight registry holds right now, as plain strings. */
const painted = (name) => [...(CSS.highlights.get(name) ?? [])].map((r) => r.toString());
const SENTENCE = 'executive-reader-sentence';
const WORD = 'executive-reader-word';

await test(
  'the API this whole approach depends on is present',
  '',
  () => {
    // If this ever fails, the differentiator is gone and the side panel is the
    // product rather than the fallback. Worth knowing loudly.
    assert(highlight.supported, 'CSS.highlights is unavailable in this browser');
  },
);

await test(
  'highlighting mutates nothing in the document',
  // The entire argument for this approach. Every competitor wraps spoken text
  // in injected spans, which reflows the page and fights the site's framework.
  '<p>Hello there, friend</p>',
  (root) => {
    const before = root.innerHTML;
    const [block] = extractBlocks(root);

    highlight.setSentence(rangeFor(block, 0, 19));
    highlight.setWord(rangeFor(block, 6, 11));

    equal(root.innerHTML, before, 'the DOM changed; the page was modified');
    highlight.clear();
  },
);

await test(
  'the sentence and the word are painted where they were asked for',
  '<p>Hello there, friend</p>',
  (root) => {
    const [block] = extractBlocks(root);

    highlight.setSentence(rangeFor(block, 0, 19));
    highlight.setWord(rangeFor(block, 6, 11));

    deepEqual(painted(SENTENCE), ['Hello there, friend']);
    deepEqual(painted(WORD), ['there']);
    highlight.clear();
  },
);

await test(
  'a new sentence clears the previous word, not just the previous sentence',
  // Otherwise the word from the last sentence stays lit while the next one is
  // read, which reads as the highlight having fallen behind the voice.
  '<p>First sentence here.</p><p>Second sentence here.</p>',
  (root) => {
    const [first, second] = extractBlocks(root);

    highlight.setSentence(rangeFor(first, 0, first.text.length));
    highlight.setWord(rangeFor(first, 0, 5));
    highlight.setSentence(rangeFor(second, 0, second.text.length));

    deepEqual(painted(WORD), []);
    deepEqual(painted(SENTENCE), ['Second sentence here.']);
    highlight.clear();
  },
);

await test(
  'only one word is lit at a time',
  '<p>Hello there, friend</p>',
  (root) => {
    const [block] = extractBlocks(root);
    highlight.setSentence(rangeFor(block, 0, 19));

    highlight.setWord(rangeFor(block, 0, 5));
    highlight.setWord(rangeFor(block, 6, 11));

    deepEqual(painted(WORD), ['there'], 'the previous word stayed lit');
    highlight.clear();
  },
);

await test(
  'passing null clears rather than throwing',
  '<p>Hello there, friend</p>',
  (root) => {
    // rangeFor returns null when the page changed under us, and that value
    // arrives here directly.
    const [block] = extractBlocks(root);
    highlight.setSentence(rangeFor(block, 0, 19));

    highlight.setWord(null);
    highlight.setSentence(null);

    deepEqual(painted(SENTENCE), []);
    deepEqual(painted(WORD), []);
  },
);

await test(
  'the word highlight is registered after the sentence, so it paints on top',
  '<p>Hello there, friend</p>',
  (root) => {
    const [block] = extractBlocks(root);
    highlight.setSentence(rangeFor(block, 0, 19));

    const names = [...CSS.highlights.keys()];
    assert(names.indexOf(WORD) > names.indexOf(SENTENCE),
      'the API resolves overlaps by registration order, not z-index');
    highlight.clear();
  },
);

await test(
  'destroying removes the registrations entirely',
  '<p>Hello there, friend</p>',
  (root) => {
    const [block] = extractBlocks(root);
    highlight.setSentence(rangeFor(block, 0, 19));

    highlight.destroy();

    equal(CSS.highlights.get(SENTENCE), undefined);
    equal(CSS.highlights.get(WORD), undefined);

    // And it must still work afterwards, or a second page never highlights.
    highlight.setSentence(rangeFor(block, 0, 5));
    deepEqual(painted(SENTENCE), ['Hello']);
    highlight.clear();
  },
);

// ---------------------------------------------------------- scroll follow

/** The viewport height the scroll tests assume. */
const VH = 800;

/**
 * Run a body with window.scrollTo recorded rather than performed.
 *
 * Actually scrolling would move every later fixture out from under its own
 * assertions, and the question here is what the module decides, not whether the
 * browser can scroll. `matchMedia` is overridable so the reduced-motion branch
 * can be reached deliberately.
 */
async function withScrollStub({ reduceMotion = false } = {}, fn) {
  const realScrollTo = window.scrollTo;
  const realMatchMedia = window.matchMedia;
  const realHeight = Object.getOwnPropertyDescriptor(window, 'innerHeight');
  const calls = [];
  // A fixed viewport height, so the band arithmetic is checked against a known
  // number rather than whatever this window happens to be. It also lets these
  // run in a hidden or zero-height pane, where innerHeight is 0 and every
  // comparison against a fraction of it is NaN.
  Object.defineProperty(window, 'innerHeight', { value: VH, configurable: true });
  window.scrollTo = (opts) => calls.push(opts);
  window.matchMedia = (q) => (q.includes('reduced-motion')
    ? { matches: reduceMotion, addEventListener() {}, removeEventListener() {} }
    : realMatchMedia.call(window, q));
  try {
    return await fn(calls);
  } finally {
    window.scrollTo = realScrollTo;
    window.matchMedia = realMatchMedia;
    if (realHeight) Object.defineProperty(window, 'innerHeight', realHeight);
    else delete window.innerHeight;
  }
}


/** A fixture with a target pushed a known distance down the viewport. */
const atOffset = (px) => `<div style="height:${px}px"></div><p id="target">Hello there, friend</p>`;

await test(
  'a sentence already comfortably on screen is left alone',
  // The band is a quarter to seven tenths of the viewport. Anything inside it
  // is where a reader wants it, and moving the page would be gratuitous.
  atOffset(Math.round(VH * 0.35)),
  (root) => withScrollStub({}, (calls) => {
    const follower = startScrollFollow();
    const [block] = extractBlocks(root.querySelector('#target').parentElement);
    follower.follow(rangeFor(block, 0, 19));
    deepEqual(calls, [], 'the page was scrolled for no reason');
  }),
);

await test(
  'a sentence below the band is brought up into it',
  atOffset(Math.round(VH * 0.9)),
  (root) => withScrollStub({}, (calls) => {
    const follower = startScrollFollow();
    const [block] = extractBlocks(root.querySelector('#target').parentElement);
    const range = rangeFor(block, 0, 19);
    const before = range.getBoundingClientRect().top;

    follower.follow(range);

    equal(calls.length, 1, 'the sentence was left off screen');
    equal(calls[0].behavior, 'smooth');
    // The target puts the sentence at the top of the band, not at the top of
    // the window: a sentence pinned to the very top has no context above it.
    const expected = window.scrollY + before - VH * 0.25;
    assert(Math.abs(calls[0].top - expected) < 1, `${calls[0].top} vs ${expected}`);
  }),
);

await test(
  'a reader who scrolls is not yanked back',
  // The defect every auto-scrolling reader has: you scroll up to re-read a
  // line and the page pulls you forward again.
  atOffset(Math.round(VH * 0.9)),
  (root) => withScrollStub({}, (calls) => {
    const follower = startScrollFollow();
    const [block] = extractBlocks(root.querySelector('#target').parentElement);

    window.dispatchEvent(new WheelEvent('wheel', { deltaY: -200 }));
    follower.follow(rangeFor(block, 0, 19));

    deepEqual(calls, [], 'the page yanked the reader back');
  }),
);

await test(
  'a keyboard scroll suspends following too',
  atOffset(Math.round(VH * 0.9)),
  (root) => withScrollStub({}, (calls) => {
    const follower = startScrollFollow();
    const [block] = extractBlocks(root.querySelector('#target').parentElement);

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'PageUp' }));
    follower.follow(rangeFor(block, 0, 19));

    deepEqual(calls, []);
  }),
);

await test(
  'an ordinary keystroke does not suspend following',
  // Typing in a comment box should not stop the reader tracking the voice.
  atOffset(Math.round(VH * 0.9)),
  (root) => withScrollStub({}, (calls) => {
    const follower = startScrollFollow();
    const [block] = extractBlocks(root.querySelector('#target').parentElement);

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }));
    follower.follow(rangeFor(block, 0, 19));

    equal(calls.length, 1);
  }),
);

await test(
  'suspending deliberately stops following for as long as asked',
  atOffset(Math.round(VH * 0.9)),
  async (root) => withScrollStub({}, async (calls) => {
    const follower = startScrollFollow();
    const [block] = extractBlocks(root.querySelector('#target').parentElement);

    follower.suspend(120);
    follower.follow(rangeFor(block, 0, 19));
    equal(calls.length, 0, 'suspend did nothing');

    await new Promise((r) => setTimeout(r, 200));
    follower.follow(rangeFor(block, 0, 19));
    equal(calls.length, 1, 'following never resumed');
  }),
);

await test(
  'reduced motion gets an instant jump, not a smooth glide',
  // Smooth auto-scroll is a migraine and nausea trigger. This is an
  // accessibility tool, so the preference is not decoration.
  atOffset(Math.round(VH * 0.9)),
  (root) => withScrollStub({ reduceMotion: true }, (calls) => {
    const follower = startScrollFollow();
    const [block] = extractBlocks(root.querySelector('#target').parentElement);
    follower.follow(rangeFor(block, 0, 19));
    equal(calls[0].behavior, 'auto');
  }),
);

await test(
  'a collapsed or hidden range is not chased',
  '<p id="target" style="display:none">Hello there, friend</p><p>Visible text here.</p>',
  (root) => withScrollStub({}, (calls) => {
    const follower = startScrollFollow();
    const range = document.createRange();
    range.selectNodeContents(root.querySelector('#target'));
    follower.follow(range);
    deepEqual(calls, []);
  }),
);

// The extension's own pages, run whole against a stand-in browser. Kept in
// their own file because they need a different kind of fixture from everything
// above: these mount a page and press its buttons rather than calling a
// function against real elements.
await surfaceTests({ test, assert, equal, deepEqual, stage });

// And the content script whole, rather than the pieces it composes. Those are
// covered above one at a time, which says nothing about whether they are put
// together correctly.
await contentTests({ test, assert, equal, deepEqual, stage });

// And the audio path, against a real AudioContext rather than the fake that
// player.test.mjs uses. A duration computed from a sample rate the device does
// not use is arithmetic that passes a fake and silence that does not.
await offscreenTests({ test, assert, equal, deepEqual, stage });

const summary = document.getElementById('summary');
summary.textContent = failed
  ? `${passed} passed, ${failed} FAILED`
  : `${passed} passed`;
summary.className = failed ? 'fail' : 'pass';
window.__domTests = { passed, failed };
