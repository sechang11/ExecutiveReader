/**
 * The content script, end to end, in a real page.
 *
 * This is the module that turns a document into sentences, paints them, and
 * answers the worker. Every other part of the reader is downstream of it, and
 * it was the largest thing with no test of its own: the pieces it composes are
 * covered individually, which says nothing about whether it composes them
 * correctly.
 *
 * Fixtures are mounted inside an `<article>` on purpose. `findArticleRoot`
 * trusts an explicit article that holds enough text, so the fixture wins
 * outright over this harness's own prose — which is otherwise a perfectly good
 * article and would be chosen instead.
 *
 * @param {{test: Function, assert: Function, equal: Function, deepEqual: Function, stage: Element}} api
 */
import { pageChrome } from './chrome-stub.js';

const CONTENT_JS = '/extension/src/content/index.js';

const tick = () => new Promise((r) => setTimeout(r, 0));

async function until(condition, message, tries = 400) {
  for (let i = 0; i < tries; i++) {
    if (condition()) return;
    await tick();
  }
  throw new Error(message);
}

/**
 * Prose long enough that the article is trusted on its own length.
 *
 * `trustsExplicitRoot` accepts an explicit article holding 200 characters
 * regardless of what surrounds it, and otherwise wants a quarter of the page.
 * This harness accumulates a long list of passed tests as it runs, so a short
 * fixture loses the share test and the scorer picks the results list instead —
 * which is a correct answer to the wrong document.
 */
const BODY = `
  <p>The first sentence is here and it is quite long enough to matter.</p>
  <p>A second paragraph follows this one, with two sentences inside it.
     This is the second of them, and it runs on a little.</p>
  <p>Then a third paragraph closes the piece off neatly and at length.</p>
`;

export async function contentTests({ test, assert, equal, deepEqual, stage }) {
  /**
   * Mount a page, load the content script against a stand-in browser, and give
   * back a way to send it the messages the worker sends.
   */
  /**
   * Select the fixture, so a build can be scoped to it.
   *
   * Short fixtures cannot win the article-root test against a harness page
   * that grows as it runs, and padding each one to 200 characters would put
   * the length of the prose in charge of what is being tested. Selecting is
   * also a path the reader really has: Alt+S reads the selection.
   */
  function selectFixture() {
    const range = document.createRange();
    range.selectNodeContents(stage.querySelector('article'));
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  async function content(name, html, replies, fn, { visible = false } = {}) {
    await test(name, `<article>${html}</article>`, async () => {
      const h = pageChrome(replies);
      const ports = [];
      h.chrome.runtime.connect = (info) => {
        const port = { name: info.name, onDisconnect: { addListener() {} } };
        ports.push(port);
        return port;
      };
      // Alt-clicking needs coordinates that land on the text, and the stage
      // lives off-canvas so fixtures cannot disturb the page.
      const parked = stage.style.left;
      if (visible) stage.style.left = '0';

      window.chrome = h.chrome;
      const listeners = [];
      h.chrome.runtime.onMessage.addListener = (fn2) => listeners.push(fn2);

      try {
        await import(`${CONTENT_JS}?t=${Math.random()}`);
        /** Send a message as the worker does, and await the reply. */
        const ask = (msg) => new Promise((resolve) => {
          let answered = false;
          const kept = listeners.map((l) => l(msg, {}, (r) => { answered = true; resolve(r); }));
          if (!kept.some((k) => k === true) && !answered) resolve(undefined);
        });
        await fn({ ...h, ask, ports, selectFixture });
      } finally {
        window.getSelection().removeAllRanges();
        stage.style.left = parked;
        delete window.chrome;
      }
    });
  }

  await content('building a document returns the sentences of the article', BODY, {},
    async ({ ask }) => {
      const doc = await ask({ type: 'build' });
      assert(doc, 'the worker got no answer at all');
      equal(doc.count, 4, 'four sentences across three paragraphs');
      equal(doc.texts.length, 4);
      assert(doc.texts[0].startsWith('The first sentence'), doc.texts[0]);
    });

  await content('the text handed over is the text that will be spoken',
    '<p>She didn’t stop… the meeting ran on past five.</p>', {},
    async ({ ask, selectFixture }) => {
      selectFixture();
      // Normalisation runs here, not in the worker, because the worker has no
      // DOM to map offsets against. A curly apostrophe reaching the phonemizer
      // is the difference between "didn't" and "didn tee".
      const doc = await ask({ type: 'build', selectionOnly: true });
      assert(doc.texts[0].includes("didn't"), doc.texts[0]);
      assert(!doc.texts[0].includes('’'), doc.texts[0]);
    });

  await content('a sentence normalisation rewrote is flagged as unsafe to track',
    '<p>The widget costs $50 and arrives on Tuesday morning.</p>', {},
    async ({ ask, selectFixture }) => {
      selectFixture();
      // Word offsets index the spoken string. "$50" becomes "50 dollars", so
      // every offset after it is wrong against the original the DOM ranges
      // were built from. Better no word highlight than one on the wrong word.
      const doc = await ask({ type: 'build', selectionOnly: true });
      assert(doc.texts[0].includes('50 dollars'), doc.texts[0]);
      equal(doc.exact[0], false, 'this sentence must not be highlighted word by word');
    });

  await content('an untouched sentence stays safe to track',
    '<p>The widget arrives on Tuesday morning as promised.</p>', {},
    async ({ ask, selectFixture }) => {
      selectFixture();
      const doc = await ask({ type: 'build', selectionOnly: true });
      equal(doc.exact[0], true);
    });

  await content('painting a sentence highlights exactly that sentence', BODY, {},
    async ({ ask }) => {
      const doc = await ask({ type: 'build' });
      await ask({ type: 'paint-sentence', index: 1 });

      const painted = [...(CSS.highlights.get('executive-reader-sentence') ?? [])]
        .map((r) => r.toString());
      equal(painted.length, 1);
      equal(painted[0], doc.texts[1]);
    });

  await content('painting a word highlights inside the active sentence', BODY, {},
    async ({ ask }) => {
      await ask({ type: 'build' });
      await ask({ type: 'paint-sentence', index: 0 });
      await ask({ type: 'paint-word', charStart: 4, charEnd: 9 });

      const word = [...(CSS.highlights.get('executive-reader-word') ?? [])]
        .map((r) => r.toString());
      deepEqual(word, ['first']);
    });

  await content('a word offset is read against the sentence, not the page', BODY, {},
    async ({ ask }) => {
      // The offsets the engine reports start at zero for each sentence. Taken
      // as page offsets they light a word in the wrong paragraph, which is the
      // defect that looks like the highlight lagging the voice.
      //
      // Sentence two is the *second half* of the second paragraph, so offset
      // zero is only its own first letter if the offsets are being read
      // against the sentence.
      await ask({ type: 'build' });
      await ask({ type: 'paint-sentence', index: 2 });
      await ask({ type: 'paint-word', charStart: 0, charEnd: 4 });

      const word = [...(CSS.highlights.get('executive-reader-word') ?? [])]
        .map((r) => r.toString());
      deepEqual(word, ['This'], 'offset zero landed somewhere other than this sentence');
    });

  await content('stopping clears both highlights', BODY, {}, async ({ ask }) => {
    await ask({ type: 'build' });
    await ask({ type: 'paint-sentence', index: 0 });
    await ask({ type: 'paint-word', charStart: 0, charEnd: 3 });

    await ask({ type: 'stop' });

    for (const name of ['executive-reader-sentence', 'executive-reader-word']) {
      equal([...(CSS.highlights.get(name) ?? [])].length, 0, `${name} stayed lit`);
    }
  });

  await content('the page holds a port open, or the worker is suspended mid-sentence',
    BODY, {}, async ({ ports }) => {
      // Chrome tears the worker down after about thirty seconds idle. Without
      // this port, playback stops between sentences on any page long enough to
      // matter.
      equal(ports.length, 1);
      equal(ports[0].name, 'executive-reader-session');
    });

  await content('the page answers where the next one is', BODY, {}, async ({ ask }) => {
    // Asked only when the worker has run out of sentences, so it costs nothing
    // mid-read. Returning undefined here ends the read at the foot of the page.
    const next = await ask({ type: 'find-next' });
    equal(next ?? null, null, 'this fixture has no next link to find');
  });

  await content('alt-clicking a paragraph asks to read from there',
    BODY, {}, async ({ ask, last }) => {
      await ask({ type: 'build' });
      const target = stage.querySelectorAll('p')[2];
      const box = target.getBoundingClientRect();

      target.dispatchEvent(new MouseEvent('click', {
        bubbles: true, altKey: true,
        clientX: Math.round(box.left + 5),
        clientY: Math.round(box.top + box.height / 2),
      }));
      await tick();

      const msg = last('read-from');
      assert(msg, 'alt-click sent nothing');
      equal(msg.index, 3, 'the third paragraph is the fourth sentence');
    }, { visible: true });

  await content('an ordinary click is left alone', BODY, {}, async ({ ask, last }) => {
    // Alt is the modifier precisely so that following a link, selecting text
    // and pressing a button all still work on a page being read.
    await ask({ type: 'build' });
    const target = stage.querySelectorAll('p')[2];
    const box = target.getBoundingClientRect();

    target.dispatchEvent(new MouseEvent('click', {
      bubbles: true,
      clientX: Math.round(box.left + 5),
      clientY: Math.round(box.top + box.height / 2),
    }));
    await tick();

    equal(last('read-from'), undefined, 'ordinary clicking stopped working on the page');
  }, { visible: true });

  await content('content appended to the page is reported, once it settles',
    '<div id="feed"><p>The first post is here and it is long enough to keep, '
    + 'with a second sentence so the fixture has some weight to it. A third '
    + 'sentence carries it past the two hundred characters at which an explicit '
    + 'article is trusted outright, which matters because the growth watcher is '
    + 'attached to whichever element was chosen as the root.</p></div>', {},
    async ({ ask, sent }) => {
      // Most sites paginate by appending now rather than navigating, and a
      // reader that stops at the fold looks broken.
      await ask({ type: 'build' });
      const feed = stage.querySelector('#feed');
      // Past the minimum a growth watcher treats as real prose: below that it
      // is ads, spinners and lazy images, and re-extracting for each would be
      // most of what the extension did on a busy page.
      feed.insertAdjacentHTML('beforeend',
        '<p>A second post arrives later, appended by the page itself rather '
        + 'than navigated to. It carries enough text to count as the article '
        + 'having grown rather than as furniture settling.</p>');

      await until(() => sent.some((m) => m.type === 'document-grew'),
        'the page grew and the worker was never told');

      const grew = [...sent].reverse().find((m) => m.type === 'document-grew');
      assert(grew.texts.length > 1, 'the new text was not included');
    });
}
