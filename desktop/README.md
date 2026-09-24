# Executive Reader

Reads your screen, your files and your Claude Code sessions out loud, on
Windows, entirely offline.

This is one of the two products in this repository. The other is a Chrome
extension, which is where most people read and which gets the page DOM for
free. This half covers what a browser tab cannot reach: any other window, whole
documents without scrolling them, and Claude Code sessions. Both are real
products, and neither subsumes the other. See the root README for how they
divide, and `docs/extension-spec.md` for the other half.

The two share language rules through `shared/`, so they split sentences and
pronounce words identically. A later version will let the extension hand
synthesis to this app to borrow its GPU, which is designed for in section 8 of
the extension spec and deliberately not built yet.

## Why not just OCR the screen

Screen OCR only sees what is visible, so a long PDF means scrolling and
stitching, and every page costs a recognition pass that can get words wrong.
Reading the actual text is better in every way, so OCR is the last resort
rather than the default. The app tries six routes in order and stops at the
first that yields text. Rung 0 is not part of the ladder the other rungs form:
it is chosen because you are reading Claude, not fallen back to.

| Rung | Source | Reads text hidden off screen |
|---|---|---|
| 0 | Claude Code transcript | yes, and knows reply from thinking |
| 1 | Highlighted selection | yes |
| 2 | The file the window has open | yes, all pages at once |
| 3 | Window text via accessibility | yes |
| 4 | Clipboard | yes |
| 5 | Screen OCR | no, visible area only |

A 252-page PDF parses in about a third of a second and yields every page in
order. No scrolling, no screenshots.

## Setup

Dependencies are already installed in `desktop/.venv`. It is kept separate from
your global Python on purpose: the neural voice engine requires numpy 2, while
your TensorFlow, matplotlib, numba and ultralytics installs require numpy 1.

Start it in the tray:

```powershell
desktop/executive-reader.cmd
```

Use `executive-reader-console.cmd` instead if you want to see errors in a window.

Read something without the tray:

```powershell
desktop/executive-reader-console.cmd --file "C:\path\to\book.pdf"
```

## Chrome needs one flag

Chrome does not build a page accessibility tree unless it is asked to, so
without this flag the app can read Chrome's toolbar but none of the page. This
was measured, not assumed: an unflagged window exposed 145 characters of
toolbar labels, a flagged one exposed the article.

Add the flag to your Chrome shortcut, in Target, after `chrome.exe"`:

```
--force-renderer-accessibility
```

Then restart Chrome. Everything else, including Electron apps and PDFs opened
from disk, works without it. Discord needs the same treatment.

Until you do that, Chrome pages still work through the clipboard and OCR
routes, and PDFs work by opening the file directly rather than the tab.

## Voices

Two tiers, and the app falls back down them rather than failing.

- **Windows SAPI** works immediately with no download. Your machine has David
  and Zira, which are the old low-quality voices. Fine for a quick listen.
- **Kokoro** is the one to use. Open the Voices tab and press Download, which
  fetches about 330 MB once. Model and voice packs are Apache 2.0.

Avoid XTTS-v2 entirely for anything you plan to sell. Its licence is
non-commercial.

Speed is a synthesis parameter for Kokoro, not a playback rate, so 2.5x keeps
its natural pitch. SAPI has only integer rate steps and tops out near 3x; the
mapping was measured against the real engine rather than guessed.

### Why English only, and what the add-on unlocks

Kokoro speaks phonemes rather than text. Every off-the-shelf way to produce
them uses eSpeak NG, which is GPL-3.0, and the Python packaging of it ships the
library binary and 19 MB of data inside site-packages. Depending on any of that
would put copyleft over this whole application.

So this app phonemizes with its own dictionary and runs the model directly on
onnxruntime. Nothing copyleft is installed or distributed, which a test
enforces rather than leaves to habit. The cost is that the dictionary covers
English, so 29 of Kokoro's 55 voices are usable out of the box.

Installing eSpeak NG yourself unlocks the rest: Spanish, French, Italian,
Portuguese, Hindi, Japanese and Mandarin. The app detects it and never ships
it. Read the licence note in [CAVEATS.md](../CAVEATS.md) before selling
anything that relies on it.

Two limits worth knowing. Words the dictionary lacks, mostly proper nouns and
product names, get an approximation from letter-to-sound rules, which is
patchable by adding entries. Words spelled alike but said differently are a
harder problem: "he read the book" has no cue a dictionary can use. Both halves
ship a rule-of-thumb using the preceding word, and it is not complete.

### Using a voice you trained

Fine-tuning happens outside this app, in Python, on your GPU. Once you have an
exported Piper model, drop the `.onnx` and its `.onnx.json` into:

```
%APPDATA%\Earmark\voices\piper\
```

It then appears in the voice picker like any other. That folder keeps the
old product name deliberately; see "Where your data lives" below.

## Reading Claude Code

Claude Code writes each session to a JSONL transcript as it goes, so the app
reads the file rather than the screen. That gives exact text, with thinking and
tool calls as separate fields, so tool traffic can be skipped entirely.

Thinking text is present in some sessions and redacted in others. Across the 72
transcripts on this machine, 11,179 thinking blocks had text and 19,974 were
empty. When a session redacts it, only the visible replies can be read.

This is the first tab and it is on by default, because it is the job the app
exists for and the only route that needs nothing set up. The screen recogniser
sat on the first tab for a long time with all the buttons on it, which led
anyone following the interface to the worst way of doing the main job.

### Which conversation

The hard part is not reading a conversation, it is knowing which one. A
machine running several Claude sessions has several transcripts being appended
to at once, and most of the appending is done by agents nobody is watching, so
"the newest file" belongs to a robot.

What decides it is a person typing. A typed message is a user entry whose
content is a string; a tool result is also a user entry, but its content is a
list of `tool_result` blocks, and there are roughly twenty of those for every
typed line. A session nobody has ever typed in is never followed by accident.

By default the reader moves with you as you switch conversations. Click one in
the list to stay on it. The list names them the way the sidebar does, from the
`agent-name` and `custom-title` entries in the transcript.

Deciding this means looking backwards through a transcript for the last typed
message. Measured on this machine, transcripts run from one to eighty megabytes
and that message sits between a tenth of a megabyte and six megabytes from the
end, so the search window is two megabytes: enough for nearly all of them, and
a conversation buried under more tool output than that is not the one you just
typed in. What has been read is remembered, so a refresh costs only the bytes
added since.

### What a reply sounds like

An answer is markdown, and markdown read literally is punctuation. A table is
read as its rows with the header said once, rather than as a row of vertical
bars. A fenced code block is announced with its language and length --
"PowerShell code block, two lines" -- following the `code_blocks` mode in
`shared/normalization.json`, rather than being read out or dropped in silence.
A heading is given a terminator so the voice stops rising at the end of it.

The opening segment is capped shorter than the rest. Nothing is heard until it
has been synthesized, and synthesis costs about a second and a half plus a
little per character, so the first sentence alone decides how long the silence
before a reply is. On a real reply that took it from 6.3 seconds to 3.4. The
voice is also loaded at startup rather than on the first reply, which was four
seconds more.

## Shortcuts

| Action | Default |
|---|---|
| Read this window | Ctrl+Alt+R |
| Read clipboard | Ctrl+Alt+V |
| Force screen OCR | Ctrl+Alt+O |
| Play or pause | Ctrl+Alt+P |
| Stop | Ctrl+Alt+X |
| Previous or next sentence | Ctrl+Alt+Left / Right |
| Faster or slower | Ctrl+Alt+Up / Down |
| Bookmark here | Ctrl+Alt+B |

Shortcuts another app already owns are reported in the Settings tab instead of
failing silently. Edit them in `%APPDATA%\Earmark\config.json`.

## Where your data lives

`%APPDATA%\Earmark\`, which keeps the old product name on purpose.

The folder holds the settings file, the SQLite database with your reading
history and bookmarks, and any downloaded voice models. Renaming it means moving
a folder *and* the database file inside it, and getting only the folder right is
how a previous rename here silently started a fresh, empty history while every
test passed. The internal URI scheme is frozen for the same reason: it appears
inside saved positions, so changing it would orphan them.

If it is ever renamed, it needs the migration already in `config.py` and
`store/db.py` rather than a search and replace, and it needs verifying against
real data afterwards rather than against a fixture.

## Resuming, bookmarks and history

Positions are stored as the sentence text plus surrounding context, not as an
index, so they survive the document changing. Insert two paragraphs into a page
and the bookmark still lands on the right sentence. Everything lives in one
SQLite file at `%APPDATA%\Earmark\earmark.db`, on this machine only.

## Pronunciation

The Pronunciation tab overrides how particular words are said. It ships with
45 developer terms so nginx, PostgreSQL and Kubernetes come out right. This is
the cheapest quality lever in the app.

## Shared rule data

Language data lives in `shared/*.json` and is read by both halves of the
project, so the desktop app and the Chrome extension split sentences and
pronounce words identically instead of drifting apart. Engine code stays on
each side; only data crosses.

- `shared/abbreviations.json` holds the 127 tokens that end in a period without
  ending a sentence.
- `shared/pronunciation.json` holds the word overrides.

Python reads these directly and falls back to built-in defaults if the folder
is missing. The extension needs its own copy because a Chrome extension can
only load files inside its own folder, so after editing anything in `shared/`:

```powershell
node tools/sync-shared.mjs
```

A test fails if the two copies drift.

## Text handling

Most of the difference between a reader you keep and one you uninstall is what
happens before the voice sees the text. The pipeline removes navigation
furniture, markdown syntax, footnote markers and dot leaders, drops page
numbers, reads links as their domain, and rejoins hard-wrapped prose. On the
252-page test PDF that last step alone removed 740 false sentence breaks.

## Layout

```
desktop/
  src/executive_reader/
    app.py          wiring, free of Qt so it can be driven headlessly
    capture/        selection, files, accessibility, clipboard, OCR, Claude
    tts/            SAPI, Kokoro, Piper behind one interface
    player/         playback thread, prefetch, speed, pause mid-sentence
    textproc/       normalisation, sentence splitting, pronunciation, symbols
    store/          SQLite history and bookmarks, text-quote anchors
    ui/             tray, mini player, library
  tests/            202 tests, no audio device needed
```

Sitting alongside, outside this folder: `extension/` is the Chrome extension,
`shared/` is the rule data both halves read, and `docs/` holds the specs.

## Tests

```powershell
desktop/.venv/Scripts/python.exe desktop/tests/test_reader.py
```

Swap the filename for any other `test_*.py`. They find the package and the
repository root by searching upward for a marker rather than by counting
directories, so they run from any working directory and survive being moved.

### Three things both halves must agree about

Each has a harness that feeds the same inputs through this Python and the
extension JavaScript and fails if they disagree. All three skip when Node is
absent, and all three have been checked by deliberately breaking a rule and
confirming the harness reports it, rather than by trusting a green result.

| Harness | Guards |
|---|---|
| `tools/conformance.mjs` | symbols, currency, abbreviation expansion |
| `tools/conformance_segment.mjs` | where sentences begin and end |
| `tools/conformance_anchor.mjs` | where a bookmark lands, and what it is called |

The last two are new, and each found real disagreements on its first run. Both
list the ones still outstanding in a `KNOWN` map inside the harness, with the
reason. That list is not a way to ignore them: an unrecorded disagreement fails,
and so does a recorded one that stops happening, so fixing a divergence forces
the entry to be deleted rather than left describing behaviour the code no longer
has. See CAVEATS.md entries 20 and 22 for what is outstanding and why.

`desktop/tests/smoke_audio.py` is separate because it needs speakers and makes
noise.

## Known limits

- Chrome and Discord need their accessibility flag, as above.
- UWP apps such as Windows Security expose nothing readable.
- Scanned PDFs have no text layer; the app detects this and says so. Screen OCR
  is the only route for those.
- Sentence highlighting in the source window is not implemented. The mini
  player shows the current sentence instead.
