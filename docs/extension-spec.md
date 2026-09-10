# Executive Reader for Chrome — architecture spec

Status: v1.0, all questions closed. Ready to build.

Sibling project: `desktop/` (Windows screen reader, Python/PySide6). The two share
rule data via `shared/`, and — see section 8 — the desktop app doubles as the
extension's high-end voice engine.

**Decisions**

| Question | Decision |
|---|---|
| Voice strategy | System voices plus Kokoro-82M in-browser; adapter designed for a later desktop bridge |
| Monetization | Free at launch. No billing, no accounts, no telemetry |
| Highlighting | On-page via the Custom Highlight API; side panel is fallback and reading mode |
| Content scope | Articles, PDFs, and webmail in v1. Google Docs deliberately excluded |
| Audience | Accessibility and productivity both, with accessible defaults out of the box |
| Desktop bridge | Interface designed for it now, built after launch |
| Product name | **Executive Reader** |

**Why Executive Reader.** The working name was Aloud, which could not ship: the incumbent
we measure against in section 1 is called Read Aloud, one word away. That is a
discoverability problem more than a legal one. A Web Store search for "read
aloud" returns the incumbent with its install count and review history, and a
near-identically-named newcomer ranks underneath it. Executive Reader competes on its own
terms in search instead, and the ear-plus-bookmark reading names the two things
the product actually does.

### Renaming lessons, for whenever a name is embedded again

The desktop half renamed first and hit a bug worth recording. Moving its data
folder was not enough, because the database file inside it also carried the old
name, so the app created a fresh empty database beside the populated one and
started with no history. Every test passed. The app reported success. The user's
data was silently abandoned — the same signature as the audio teardown bug in
section 3.2, where every log line said fine.

So: **enumerate every place the old name is embedded before changing any of
them.** On this half there were nine, and three would have failed silently
rather than loudly:

| Kind | Where | Failure if missed |
|---|---|---|
| Highlight identifiers | `highlight.js` **and** `highlight.css` | silent: highlighting stops painting |
| Port name | `background/index.js` **and** `content/index.js` | silent: keep-alive breaks, playback dies between sentences |
| CSS custom properties | `highlight.css` | silent: colours fall back to nothing |
| Context menu id | `background/index.js` | loud |
| Injection guard | `content/loader.js` | loud |
| Display names | manifest, popup, offscreen titles | visible |
| Package name | `package.json` | loud |
| Log prefixes | several | cosmetic |
| Cross-repo path references | `normalize.js`, `abbreviations.json` | stale docs |

The three silent ones are all paired identifiers that must match across two
files. Nothing type-checks them, and no test covers them, so grep for the pair
and confirm both sides changed together.

Stored data needed no migration here, which was luck rather than design: the only
persisted key is `state` in session storage, and no IndexedDB database exists
yet. Phase 2 adds both a database and history keys, so the next rename would
carry a real migration burden. Name them now with that in mind.

---

## 1. What we're beating

Read Aloud (`hdhinadidafjejdhmfkjgnolgimiaplp`) is the benchmark. Its real
weaknesses, from the reviews and from using it:

| Their weakness | Our answer |
|---|---|
| Wraps text in injected spans to highlight | CSS Custom Highlight API — zero DOM mutation |
| Reads nav bars, cookie banners, footers | Readability-based extraction with a block classifier |
| Free voices are robotic; good ones need a subscription | Kokoro neural voices running locally, free and offline |
| No history, no resume | Durable history with fuzzy sentence re-anchoring |
| Mirrored panel is the only highlight surface | On-page highlight primary, panel as fallback |
| Stops dead at the end of a page | Next-page detection plus infinite-scroll capture |
| 15-second cutoffs from the Web Speech bug | Own audio pipeline in an offscreen document |

---

## 2. Hard constraints (Manifest V3)

These shape everything below. They are not negotiable.

1. **The service worker dies.** Chrome terminates it after roughly 30 seconds
   idle. It cannot own playback state or hold an audio element. All durable
   state goes to `chrome.storage.session` and IndexedDB, and is rehydrated on wake.
2. **Audio needs an offscreen document.** `chrome.offscreen` with reason
   `AUDIO_PLAYBACK` is the only MV3-legal way to keep a Web Audio graph alive
   across navigation. This is the single most important structural decision.
3. **No remote code.** We may fetch model *weights* at runtime, since they are
   data. We may not fetch or evaluate JavaScript. WASM needs `wasm-unsafe-eval`
   in the extension CSP.
4. **`speechSynthesis` is unreliable.** Chrome's Web Speech implementation cuts
   off around fifteen seconds and needs a pause/resume hack. We use `chrome.tts`
   for system voices instead, and our own pipeline for everything else.
5. **Broad host permissions slow review.** Asking for all-URLs access at install
   invites a long review. We request host access on demand instead.

---

## 3. Component map

```
service worker  ──  orchestration, commands, history, licensing
      │             (stateless-tolerant; rehydrates from storage)
      ├── offscreen document  ── audio graph, buffer queue, neural inference
      ├── content script      ── extraction, highlight, scroll, next-page
      ├── side panel          ── mirrored text, history list, controls
      ├── popup               ── transport controls
      └── options page        ── voices, pronunciation, per-site rules
```

### 3.1 Service worker — `extension/src/background/`

Owns the reading state machine (idle, extracting, speaking, paused), routes
keyboard commands and context menus, writes history, and brokers messages. Every
state transition is mirrored into session storage, so a worker restart resumes
mid-sentence rather than mid-nothing.

### 3.2 Offscreen document — `extension/src/offscreen/`

The real engine. Holds a Web Audio graph, a queue of synthesized sentence
buffers, and a prefetch loop that stays two to three sentences ahead so playback
never gaps. Speed changes are applied at synthesis time where the engine
supports it, preserving pitch, and via playback rate only as a fallback. Neural
inference runs here in a Web Worker so the audio thread never blocks.

**Teardown ordering is a real hazard here, not a tidiness concern.** Stop and
drain the producer before releasing the sink: end the worker's in-flight
synthesis, await it, then disconnect the audio graph and close the context. The
desktop side hit the equivalent bug in the opposite order, closing its audio
device while a write was still in flight, which segfaulted at interpreter exit
*after* printing that shutdown was clean. The browser will not segfault, but the
same ordering produces detached-node exceptions and audio that keeps playing
after stop. It only shows up when the program actually runs to completion, which
is to say never in unit tests.

### 3.3 Content script — `extension/src/content/`

Four jobs, described in their own sections below: extract, highlight, scroll,
advance.

### 3.4 Side panel — `extension/src/sidepanel/`

`chrome.sidePanel`, Chrome 114 and later. Chosen over a popup because a popup
closes the moment you click the page, which makes it useless for a reader. Holds
the mirrored text view, font size and line height controls, the voice picker,
the speed slider, and the history list.

---

## 4. Text extraction — the quality differentiator

Reading order and junk rejection are what separate a good reader from a bad one.

Pipeline:

1. Clone the document and run Mozilla Readability (Apache-2.0) to find the
   article container. Keep a mapping from cloned nodes back to live nodes.
2. If Readability fails, as it does on apps, dashboards, and forums, fall back to
   a scoring walk: text density per block, penalties for navigation, aside,
   footer, and header roles, for ARIA-hidden subtrees, for link-heavy blocks, and
   for fixed positioning.
3. Emit an ordered block list. Each block carries a role: heading, paragraph,
   list item, quote, code, caption, or table cell.
4. Segment each block into sentences using the shared rules in
   `shared/segmentation.json`, covering abbreviations, decimals, ellipses, and
   initials.
5. Each sentence keeps a live DOM Range plus a text hash. The Range drives
   highlighting. The hash drives history re-anchoring after a page changes.

Explicitly handled: shadow DOM via a recursive walk, same-origin iframes, image
alt text as an option, MathML and LaTeX either spoken or skipped, and code blocks
either spoken, skipped, or summarized as "code block, twelve lines".

Explicitly not handled in v1: canvas-rendered text, cross-origin iframes, and DRM
readers.

---

## 5. Highlighting — on-page, not mirrored

You asked whether page highlighting beats the mirrored panel. It does, and it is
doable. The CSS Custom Highlight API, available since Chrome 105, paints ranges
without touching the DOM:

```js
CSS.highlights.set('executive-reader-sentence', new Highlight(...ranges));
```

No injected spans means nothing breaks in React, Vue, or any site whose CSS
targets element structure. This is the thing Read Aloud gets wrong, and it is the
single clearest technical win available to us.

Two layers: a soft wash on the active sentence, a stronger mark on the active
word. Word timing comes from `chrome.tts` boundary events for system voices, from
provider timestamps for cloud voices, and from proportional token duration for
local neural voices.

The first fallback, for pages where ranges misbehave, is an absolutely positioned
overlay computed from client rects. The second fallback is the side panel, which
doubles as our reading mode for ugly pages.

So we build both surfaces. The panel earns its place as a reading mode and
history browser rather than as the primary highlight surface.

---

## 6. Scroll and page advance

**Scroll-follow.** Keep the active sentence in the middle third of the viewport.
Cancel auto-scroll for four seconds whenever the user scrolls manually, detected
by wheel, touch, and keyboard events. Respect reduced-motion preferences.

**Next page.** In priority order: a next link relation in the document head, an
anchor with a next relation, a per-site selector from `shared/pagination.json`,
link text matching Next, Older, or Continue, then a numeric URL increment guess.
Never guess silently. The panel shows what it is about to load and offers a
cancel.

**Infinite scroll.** A mutation observer on the article container appends newly
rendered blocks to the queue with no navigation at all. This is how most modern
sites paginate, and Read Aloud does not handle it.

**Cross-navigation resume.** The service worker persists queue position keyed by
URL, re-injects on the new page, and continues.

---

## 7. History and resume

Per entry: URL, title, favicon, first-read and last-read timestamps, voice used,
sentence index reached, total sentences, seconds listened, and the text of the
sentence you stopped on.

Clicking an entry reopens the URL, re-extracts, and re-anchors by fuzzy-matching
that stored sentence text. Index alone is fragile, because sites change. Matching
on content survives edits, shifting ads, and layout changes.

Storage splits by size: metadata in extension local storage, full article text in
IndexedDB. Local storage caps at ten megabytes without the unlimited-storage
permission, which is far too little for article bodies.

### 7.1 Anchors must record which rules produced them

An anchor stores the text of a sentence, but that text is the *normalized* text,
and normalization is driven by files we edit. Editing them rewrites the very
strings the anchors were captured from, so a position saved yesterday can stop
matching today through no fault of the page.

This is not hypothetical. Adding `w/` and `->` to the expansion list broke every
stored anchor whose sentence contained either, turning an exact match into a
near-miss. Fuzzy matching absorbs it, but a reader that silently falls back to
sentence zero looks like it lost your place.

So every stored position carries the `combined` value from
`shared/fingerprint.json`, which `tools/sync-shared.mjs` derives from the content
of the rule files. The fingerprint has no timestamp in it, so re-running the sync
with no edits produces no diff and the stamp cannot drift from what it describes.

**Use the stamp for confidence, never for control flow.** The obvious reading is
that a changed stamp should skip the verbatim comparison and go straight to fuzzy
matching. That is wrong, and it was my first instinct. A rules edit rewrites
*some* sentences, not all of them, so an exact hit on an untouched sentence is
still correct. Worse, skipping verbatim throws away the tiebreak that separates
repeated sentences:

```
segments = ["Intro.", "Same line.", "Middle.", "Same line.", "Tail."]
position saved on index 3
  verbatim first  -> 3   correct
  fuzzy only      -> 1   wrong, takes the first maximum
```

So: always try verbatim, then context, then fuzzy. Report which path succeeded,
and treat the position as verified only when the quote matched verbatim *and* the
stamps agree. An unverified position still resolves, it is just labelled, and the
reader can say the rules changed and the position is approximate rather than
silently landing somewhere odd. Credit to the desktop side, which tested this
instead of accepting my version of it.

Fuzzy matching needs its own tiebreak for the same reason. Score every segment,
collect everything within a small margin of the best, and disambiguate on
surrounding context rather than taking the first maximum.

### 7.2 Building the offset map

Word-level highlighting needs to map a position in the spoken text back to a
position in the original, because the original is what the DOM Ranges index.
Until that map exists, sentences that normalization altered get sentence
highlighting only, flagged per sentence by `exact`.

Build the map in the same pass that does the rewriting, not by diffing the two
strings afterwards. Every rule already knows the span it consumed and the length
it produced, so the map falls out for free; recovering it later from two finished
strings is a harder problem solved worse. Credit for that observation goes to the
desktop side.

Retention defaults to ninety days or five hundred entries, whichever comes first.
Both are configurable, with a one-click purge.

---

## 8. Voices — decided, with one important consequence

You asked for open-source voices at ElevenLabs quality. Those models exist and
their licenses are clean, but there is a hard ceiling on what a browser tab can
run, and it lands in an awkward place. The honest numbers:

| Model | License | Size | Browser real-time? |
|---|---|---|---|
| System voices | n/a | none | Yes, instant |
| Kokoro-82M | Apache-2.0 | 82M params, ~80 MB quantized | Yes, roughly 10x real-time on WebGPU |
| Chatterbox | MIT | 0.5B params | No |
| Higgs Audio V2 | Apache-2.0 | multi-billion | No |

Chatterbox is the one that actually clears the bar you named: in blind listening
tests it was preferred over ElevenLabs by about 64 percent of listeners, and it
is MIT licensed, so we can ship it commercially. But at half a billion parameters
it will not synthesize faster than you can listen inside a Chrome tab, and the
download is far past what anyone tolerates from an extension.

Kokoro is the best model that genuinely runs in a browser. It is very good. It is
not Chatterbox.

**The consequence, and it is the most interesting idea in this spec:** the thing
that *can* run Chatterbox comfortably is sitting in the next folder. Your desktop
app already has ONNX inference, a GPU, and a voice engine abstraction. So the
extension should detect it and offload to it.

That gives us four tiers, all free, none requiring a server:

**Tier 0, system voices.** Instant, offline, zero download. On Windows, the
Microsoft Natural voices installed through Settings are genuinely decent and
appear here automatically. We ship a one-screen guide for installing them, which
costs us nothing and lifts the floor for every user.

**Tier 1, in-browser neural.** Kokoro-82M through ONNX Runtime Web, WebGPU where
available and WASM otherwise, fetched on first use into the origin private file
system rather than bundled. This is the default good experience and the reason to
switch from Read Aloud: natural voices, free, offline, no text leaving the
machine.

**Tier 2, the desktop companion — designed for now, built after launch.** If the
Executive Reader desktop app is installed, the extension discovers it over Chrome native
messaging and hands off synthesis. The desktop side runs Chatterbox or Higgs
Audio on the GPU and streams audio back: ElevenLabs-class voices, free, private,
fully offline, no per-character cost to anyone. Nothing in the Chrome Web Store
does this.

We are not building it before launch. What we *are* doing now is making sure the
engine adapter in 8.2 can express a streaming, out-of-process engine, so adding
the bridge later is one new adapter rather than a rewrite of the audio pipeline.
Concretely that means the interface must support chunked results and
asynchronously-arriving word timings from day one, even though no launch engine
needs them.

The extension must stay fully useful without the companion. The bridge is an
upgrade path, never a dependency, and the store listing must never imply
otherwise.

**Tier 3, bring-your-own cloud key.** Azure, ElevenLabs, OpenAI, Google, for
people who already have keys. Costs us no infrastructure. After launch.

### 8.1 The native messaging bridge (post-launch design note)

Chrome's native messaging is the MV3-legal path: a small JSON manifest registered
in the Windows registry points Chrome at the desktop executable, and the two talk
over stdio with length-prefixed JSON. The alternative, a localhost HTTP server
plus host permissions, is easier to build but worse — it opens a port any page
could probe, and it draws reviewer attention.

Protocol sketch, deliberately thin so either side can be rewritten:

```
-> { type: "hello", version }
<- { type: "hello", version, voices: [...], gpu: true }
-> { type: "synth", id, text, voice, speed }
<- { type: "audio", id, seq, pcm, sampleRate, wordTimings }
<- { type: "done",  id }
```

Audio comes back as chunked PCM so the offscreen document can start playing
before synthesis finishes. Word timings come from the desktop aligner when it has
them, and are estimated from token duration when it does not.

**Open stdio as binary, decode explicitly, never let a default pick.** Native
messaging is already a byte protocol, a 32-bit little-endian length header
followed by a UTF-8 JSON body, so using it as specified avoids most of this. The
trap is reaching for text mode on the Python end, where `sys.stdin` decodes using
the Windows locale encoding and silently mangles every non-ASCII character. That
cost an hour in this project already, on a different surface: a conformance
harness piping JSON between the halves reported two dozen false disagreements
that were entirely cp1252, and a window title crashed on a private-use glyph for
the same reason. Both were the same bug wearing different hats, and a third is
waiting here.

This also settles a question hanging over the desktop app: it stops being a
separate product and becomes the engine room for both.

### 8.1a Phonemes: match the training data, not the textbook

Kokoro takes IPA phonemes, not text, so something must convert. Two findings
from building that conversion, both of which cost time and generalise beyond
this model.

**Correct linguistics can be the wrong input.** The first grapheme-to-phoneme
implementation here placed stress at the syllable onset, which is how stress is
properly notated. Output got worse. Kokoro was trained on eSpeak-shaped input,
and eSpeak marks the vowel: `kwˈɪk`, not `ˈkwɪk`. After matching the convention,
and adding length marks on stressed long vowels because eSpeak emits those too,
the dictionary path agreed with eSpeak almost symbol for symbol on ordinary
words. A model does not reward correctness, it rewards familiarity, so the
question to ask is always what its training data looked like.

**The alphabet is 115 symbols and unknown ones are dropped, not rejected.** A
bad mapping never errors; the word simply loses a sound. Affricates are the
specific trap: the alphabet has `ʧ` and `ʤ` as single symbols and no
two-character `tʃ` or `dʒ`, but the two-character form tokenizes perfectly
happily as `t` then `ʃ`, which is a different sound. Diphthongs genuinely are
two symbols and must be left alone. Normalizing to ligatures is therefore not
cosmetic, and the tokenizer reports what it dropped so a bad mapping is visible
rather than merely audible.

### 8.1b The phoneme source is a licensing decision

The standard converter is eSpeak NG, which is GPL-3.0. Shipping it makes the
extension GPL: source on request, and no proprietary tier later on this
codebase. Both packages named `phonemizer` carry it — the JavaScript one
declares Apache-2.0 while embedding eSpeak as WebAssembly, and the Python one is
GPL outright. Piper is not an escape route, since `piper-phonemize` embeds the
same engine.

The permissive alternative is a pronunciation dictionary such as CMUdict, which
is BSD-style. It has two failure modes that differ **in kind**, not merely in
degree:

- **Missing words** — proper nouns and coinages get rule-based guesses. This
  degrades predictably, and is patchable by adding entries. The pronunciation
  override system in section 11 already exists for exactly this.
- **Homographs** — "he read the book" comes out as "reed", because a dictionary
  has no context. This is confidently wrong on *ordinary* English, and no amount
  of dictionary growth fixes it. Correcting it needs part-of-speech tagging,
  which is a dependency rather than a data file.

It also decides scope. Kokoro publishes 55 voices across eight languages, and a
voice is only usable if its language can be pronounced: an English-only
dictionary reduces the catalogue to 29 English voices. That gate lives in the
voice catalogue rather than in the picker, so the two cannot drift.

### 8.2 Engine adapter

Every engine sits behind one interface, so adding a provider is one file:

```ts
interface TtsEngine {
  id: string;
  available(): Promise<boolean>;
  voices(): Promise<Voice[]>;
  synthesize(text: string, opts: SynthOpts): Promise<SynthResult>;
  supportsWordBoundaries: boolean;
  maxNaturalSpeed: number;
}
```

Speed spans half to four times normal, applied at synthesis time where supported
so pitch stays natural at two and a half times, which is where heavy users live.

---

## 9. Monetization — free at launch

No billing code, no accounts, no backend, no license checks. This is the right
call and it is also load-bearing for the architecture: because every tier runs on
the user's own hardware, we carry no per-character cost, so free is sustainable
rather than a subsidy we later have to claw back.

Consequences we should keep true from day one, since reversing them is what makes
users angry:

- No telemetry beyond an optional, off-by-default crash report.
- No account, ever, for the local tiers.
- If a paid tier appears later it covers hosted cloud voices or sync, and
  everything that works offline today keeps working offline for free.

Ship free, watch reviews, and let the requests tell us what is worth charging for.
Revisit once install numbers are real. The Chrome Web Store stopped processing
payments in 2020, so a paid tier would need ExtensionPay or Stripe when it comes.

---

## 10. Permissions and store review

Requested at install: storage, active tab, scripting, text-to-speech, offscreen,
side panel, context menus, and unlimited storage.

Requested on demand: host permissions, needed for auto-advance and background
reading. Asking at install for access to all sites lengthens review and scares
installs.

Also required by the store: a single-purpose statement, a justification for each
permission, and a privacy policy. Local-only processing makes that policy short
and honest, which is itself a selling point.

---

## 11. Shared with the desktop app

Code cannot be shared across Python and TypeScript, but the rules can, and rules
are where drift actually hurts. `shared/` holds:

- `pronunciation.json` — user and built-in overrides
- `abbreviations.json` — sentence-split exceptions
- `normalization.json` — numbers, dates, currency, units, URL handling
- `pagination.json` — per-site next-page selectors
- `history-schema.json` — one schema, so syncing later is only a transport problem

---

## 12. Build order

**Phase 1, a reader that works.** Manifest, service worker state machine,
offscreen audio pipeline, the system-voice engine, extraction, Custom Highlight,
popup transport, and scroll-follow. Usable end to end on articles.

**Phase 2, the differentiators.** Kokoro in a worker with cached model weights,
the side panel with mirrored text and font controls, history with fuzzy resume,
next-page detection, and infinite scroll. At the end of this phase the extension
is already better than what we are replacing, and could ship.

**Phase 3, reach.** PDF through PDF.js interception, then webmail extraction
rules. Both are additive and neither touches the audio pipeline.

**Phase 4, ship.** Accessibility audit against section 15, store assets, privacy
policy, permission justifications, and onboarding. No billing work.

**After launch.** The desktop companion bridge, bring-your-own-key cloud
adapters, the pronunciation editor, per-site rules, and multilingual voice
switching.

---

## 13. Content formats — decided

In v1: **articles, PDFs, and webmail.** Google Docs is out.

- **Articles.** The core. Blogs, news, docs sites, Wikipedia, forums, Substack.
- **PDF.** Chrome's built-in viewer is closed to extensions, so we intercept PDF
  navigations and render with PDF.js ourselves. Roughly a week, self-contained,
  heavily requested. Text-layer PDFs only. Scanned ones need OCR, which the
  desktop app already has and the browser does not, so that is a natural second
  job for the companion bridge later.
- **Webmail.** Gmail and Outlook Web, via per-site extraction rules rather than
  new machinery. The cheapest item on the list.

**Google Docs is excluded on purpose.** It renders to canvas, so the visible text
is genuinely unreachable and we would have to read its hidden accessibility
layer. That breaks whenever Google ships a change, which makes it an open-ended
maintenance cost rather than a one-time build. Revisit only if users ask loudly.

---

## 14. Testing: check that the check checks

Worth stating separately because it produced more real bugs in this project than
any amount of writing code did. Three defects were found not by testing the
system but by testing the tests:

- A sync test located the repo root by counting parent directories. Moving the
  test folder made it find nothing, and it would have passed while comparing
  zero files. Silent green is worse than red.
- The cross-language conformance harness could have been green because it
  compared nothing. Deliberately removing the boundary rule from one side, and
  confirming the harness reproduced the exact corruption the other side had
  shipped, is what made its passing mean something.
- A boundary rule that no test covered was wrong on both sides simultaneously,
  and stayed wrong until a test existed at all.

The practices that follow, all cheap:

1. **Assert a floor on how much a check examined.** A test that compares files
   should fail when it finds fewer than it expects, not pass quietly.
2. **Mutate the code and confirm the test fails.** A test never observed failing
   is a test with an unknown pass condition. For a checker spanning two
   implementations, mutate *each* side once: a harness that only ever compares
   in one direction looks exactly like a working harness until the day the other
   side breaks. Two mutations, one per language, closes that cheaply.
3. **Paired identifiers across file boundaries have no compiler.** A highlight
   name in a CSS file and the same string in a JavaScript file; a port name in
   two scripts; a storage key written in one place and read in another. Nothing
   type-checks a string against a string in a different language, and a mismatch
   usually produces silence rather than an error: highlighting that stops
   painting, a keep-alive that dies with an empty console. Renaming anything
   means grepping both sides and confirming both moved. This class is distinct
   from data migration, where the consequence is at least loud.
4. **Prefer a loud dependency to a silent fallback.** The conformance harness
   calls the Python entry point directly with no `getattr` fallback: if that
   function is removed, an error is the correct outcome, because the alternative
   is comparing the wrong thing forever.
5. **Compare stages, not just pipelines.** Two implementations agreed exactly end
   to end while one stage disagreed on 16 of 46 inputs, because a difference in
   where whitespace was collapsed cancelled out downstream. Whole-pipeline
   testing cannot see that class of defect by construction.
6. **A wrong assertion defends the defect it encodes.** One test here required
   every unverified resume path to warn the user, which locked in a message that
   should never have fired. Anyone fixing the code would have been stopped by the
   test. This is strictly worse than no test, because it converts a bug into a
   requirement.
7. **An unreadable failure defends it nearly as well.** On the desktop half, a
   failing assertion left a SQLite connection open, so Windows refused to delete
   the temporary directory and the cleanup error replaced the assertion message
   in the output. The next person sees a filesystem error and goes looking in the
   wrong place entirely. Make teardown unconditional so the real failure is what
   gets reported.
8. **A failure names where the symptom surfaced, not where the fault is.** A
   missing constant in the resolution path here surfaced as two wording strings
   colliding, in a function two modules away that was entirely correct. The
   assertion was perfectly readable and pointed straight at the wrong file. Read
   the failing test's subject directly before believing it is the culprit.

---

## 15. Accessibility — the default, not a setting

We are building for both accessibility users and productivity readers, and the
out-of-box experience is the accessible one. Speed and auto-advance are things
power users turn up, not things impaired users have to turn down. An accessible
product is usable by everyone; the reverse is not true.

What this commits us to:

- **Keyboard-complete.** Every function reachable without a mouse, with visible
  focus rings and documented shortcuts. No mouse-only controls anywhere.
- **Our own UI is screen-reader correct.** Proper roles and labels, live regions
  for state changes, and no focus traps. A reading tool that a screen reader
  cannot operate is an embarrassment.
- **Highlight contrast meets WCAG AA**, in light and dark, and never relies on
  color alone. Users can change highlight colors, because the defaults will not
  suit everyone.
- **Dyslexia-friendly typography in the panel.** Font choice including OpenDyslexic,
  adjustable size, line height, letter spacing, and line width.
- **Conservative defaults.** Normal speed, auto-advance off, motion respecting
  the reduced-motion preference.
- **No timed interactions.** Nothing disappears on a timer the user cannot
  control.

This also happens to be the strongest thing we can put in the store listing, and
the most defensible reason for the product to exist.
