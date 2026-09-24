# Executive Reader

Text read aloud in natural voices that run on your own machine. Two products
sharing one philosophy: nothing you read is sent to a server.

```
extension/   Chrome extension — reads web pages, PDFs, and webmail
desktop/     Windows reader — reads any window, any file, and Claude Code sessions
shared/      Rule data both halves consume, so they never drift apart
docs/        Specs
tools/       Small scripts: sync shared data, draw icons, build the privacy page
```

Each half has its own README. Start there:

- [`extension/`](docs/extension-spec.md) — architecture spec and build order
- [`desktop/README.md`](desktop/README.md) — setup, voices, shortcuts, capture ladder

Constraints worth knowing before changing either half are collected in
[`extension/CAVEATS.md`](extension/CAVEATS.md) and [`CAVEATS.md`](CAVEATS.md).

## Why two

They cover different ground and neither subsumes the other.

The extension is where most people read, and it gets the DOM for free: exact
text, exact reading order, and precise positions to highlight, with no
configuration. The desktop app reaches everything outside a browser tab, reads
whole documents without scrolling them, and has a GPU available, so it can run
voice models far larger than a browser tab can.

The measured case for both: Chrome exposes no page text over Windows
accessibility unless it is launched with `--force-renderer-accessibility`. An
unflagged window yields 145 characters of toolbar labels; a flagged one yields
the article. The desktop app can ask users for that flag. The extension never
needs it.

After the extension launches, the plan is for it to detect the desktop app and
hand synthesis over to it, which unlocks Chatterbox-class voices for free. That
is designed for but deliberately not built yet. See section 8 of the extension
spec.

## Status

| Half | State |
|---|---|
| `extension/` | Phases 1–4 done. Submission-ready except artwork and a policy URL |
| `desktop/` | Working. Tray app, five capture routes, three voice tiers |

## Shared rule data

Python and TypeScript cannot share code, but they can share the rules, and rules
are where drift actually hurts. Sentence-split exceptions, pronunciation
overrides, and text normalization live in `shared/` as JSON, read by both.

The division between the two language files matters and is easy to get wrong:

- `normalization.json` decides what a token **means** when spoken. It runs
  first. Say-as expansion, symbols, currency, and number formatting live here.
- `pronunciation.json` decides how a word **sounds**. It runs after.

No regular expressions appear in either file's builtin entries. Python's `re`
and JavaScript's `RegExp` disagree on lookbehind width, named groups, and
backreference syntax, so a shared pattern is a shared bug waiting to happen.
Rules state a condition from a small fixed vocabulary instead, and both engines
implement that vocabulary identically.

A Chrome extension can only load files inside its own folder, so after editing
anything in `shared/`:

```powershell
node tools/sync-shared.mjs
```

A test on the desktop side fails if the two copies drift.

### Proving the two halves agree

Shared data is worth nothing unless something checks that both halves actually
read it the same way. Two people implementing one spec in two languages will
diverge on whatever the spec left implicit.

```powershell
node tools/conformance.mjs
```

It runs the same inputs through the Python and JavaScript normalizers and
reports three outcomes: identical, equivalent (same speech, different
whitespace), and differing. Only differing is a failure. Equivalent still
matters for anything mapping character offsets, which is why the schema carries
an output contract requiring both sides to collapse spaces the same way.

## Running the extension

No build step. The scripts below only copy files, draw icons and render one
markdown file, so what a store reviewer reads is what actually runs.

```powershell
node tools/sync-shared.mjs; node tools/make-icons.mjs; node tools/build-privacy.mjs
```

`build-privacy.mjs` renders `site/privacy.html` from `extension/PRIVACY.md`. The
store listing points at the published page while the extension ships the
markdown, and the published one is what people can hold the project to, so it is
generated rather than kept in step by hand.

Then open `chrome://extensions`, turn on Developer mode, choose **Load
unpacked**, and pick the `extension` folder. Open any article and press Alt+P.

| Action | Shortcut |
|---|---|
| Play or pause | Alt+P |
| Read the selection | Alt+S |
| Previous or next sentence | Alt+Left / Alt+Right |
| Read from a paragraph | Alt+click it |

```powershell
node --test extension/test/
```

Sentence segmentation and offset mapping are the pieces worth pinning down
hardest, but the suite also covers the service worker's whole read loop, the
anchor ladder, the phonemizer, PDF layout, the inference worker's fallback
path, the voice model cache, the system voice engine, licence compliance across
every vendored byte, and whether the shipped copies of shared data have drifted
from their originals. GitHub Actions runs it, and re-runs both generators to
check neither has lapsed.

The content scripts and the extension's own pages need a real document — a
TreeWalker over live elements, computed styles, Ranges, a mutation observer, a
popup with buttons to press — so their tests run in a browser instead:

```powershell
node tools/serve-repo.mjs
```

Then open <http://localhost:8124/tools/domtest/>. It reports in the page. To run
the same suite headlessly, which is what CI does:

```powershell
node tools/run-domtests.mjs
```

It drives the Chrome already on the machine and exits non-zero on failure.

Both suites are worth running, and both have earned it. The browser suite found
on its first run that a "Next" link pointing at the current page was followed,
which re-reads the same page forever. The first test ever written against the
service worker found that "continue onto the next page" did nothing: the
setting was cleared by the same reset that clears the document, so pressing
play turned it off again.

### Working now

Text extraction with junk rejection, sentence segmentation, text normalization,
system voices, in-place highlighting of the sentence and the spoken word,
scroll-follow that yields to manual scrolling, and speed from 0.5x to 4x.

A popup for transport, and a side panel holding mirrored text, typography
controls, and history. Reading positions are saved and resume against a fresh
extraction rather than a stored copy, so they survive the page changing — see
[`docs/anchor-vocabulary.md`](docs/anchor-vocabulary.md) for what each outcome
means and how confident it is.

An offscreen audio pipeline for engines that produce buffers, which is what the
service worker cannot hold itself.

Neural voices from Kokoro-82M, running on the reader's own machine. WebGPU where
available and WebAssembly otherwise, with the runtime vendored because Manifest
V3 forbids loading code from the network, and the weights fetched on first use
because they are data rather than code.

Pronunciation is dictionary-based and permissively licensed. eSpeak is better
and is GPL-3.0, so it is not shipped. On real article text the dictionary misses
about 2% of words, almost entirely technical terms and foreign names.

For those there is a pronunciation editor in settings: add a word with how it
should sound and it is fixed from the next sentence on. Forty-five common
developer terms ship already. This matters more than the percentage suggests,
because a listener forgives a missed comma and does not forgive their own
company name.

Reading on past the end of a page, either by following a next-page link or by
noticing the page grow underneath. Off by default, and a link we are not
confident about becomes a question rather than a navigation.

PDFs, through our own viewer. Chrome's built-in one is a separate extension we
cannot inject into, so a PDF opens in ours instead, where the extracted text
becomes ordinary paragraphs and everything downstream treats it as a normal
document. Text-layer PDFs only; a scan needs OCR, which the desktop half has.

Webmail, through per-site extraction rules for Gmail and Outlook Web, with
quoted replies skipped so a thread is not read once per reply. A rule that stops
matching degrades to the generic scorer rather than to an empty page.

A settings page for voice, speed, highlight colours, history retention, and
removing the voice download. A first-run page with one thing to try and one
decision to make.

### Not yet

Designed icon artwork — the current mark is drawn by a script — screenshots
from the real surfaces, and a hosted URL for the privacy policy.
Submission copy is written in [`docs/store-listing.md`](docs/store-listing.md).

The screenshots cannot be automated: `--load-extension` is refused by release
builds of Chrome, which answer with *"--load-extension is not allowed in Google
Chrome, ignoring"* and carry on without it. So the store images have to come
from a hand-driven browser with the unpacked extension loaded, and the
browser-based suite covers those surfaces instead — it mounts the real pages
and presses their controls, which is the part a screenshot could not have
checked anyway.

### What is vendored, and why

| Path | Licence | Size | Why it is here |
|---|---|---|---|
| `vendor/onnxruntime` | MIT | 27 MB | Code; MV3 forbids fetching it |
| `vendor/pdfjs` | Apache-2.0 | 1.7 MB | PDF text extraction |
| `vendor/cmudict` | BSD-style | 864 KB | Pronunciation dictionary |
| `vendor/kokoro` | Apache-2.0 | 2 KB | The model's phoneme alphabet |

Nothing here is copyleft. eSpeak would give better pronunciation and seven more
languages, and is GPL-3.0, so it is not shipped.

Model weights, about 88 MB, and voice packs at roughly 510 KB each are fetched
on demand and cached. They are data, not code, so the remote-code rule does not
reach them, and a user who never turns on neural voices never downloads them.

## A warning about Python here

Do not `pip install` into the global interpreter. Installing `kokoro-onnx`
silently upgrades numpy to 2.x and breaks the TensorFlow, matplotlib, numba, and
ultralytics installs on this machine, which are pinned to numpy 1.x. Desktop
dependencies live in `desktop/.venv` for exactly this reason.
