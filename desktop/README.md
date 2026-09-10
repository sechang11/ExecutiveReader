# Earmark

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
rather than the default. The app tries five routes in order and stops at the
first that yields text:

| Rung | Source | Reads text hidden off screen |
|---|---|---|
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
C:\Users\Kashix\Documents\CS\Projects\ChromeReaderExtension\desktop\earmark.cmd
```

Use `earmark-console.cmd` instead if you want to see errors in a window.

Read something without the tray:

```powershell
C:\Users\Kashix\Documents\CS\Projects\ChromeReaderExtension\desktop\earmark-console.cmd --file "C:\path\to\book.pdf"
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

It then appears in the voice picker like any other.

## Reading Claude Code

Claude Code writes each session to a JSONL transcript as it goes, so the app
reads the file rather than the screen. That gives exact text, with thinking and
tool calls as separate fields, so tool traffic can be skipped entirely.

Thinking text is present in some sessions and redacted in others. Across the 72
transcripts on this machine, 11,179 thinking blocks had text and 19,974 were
empty. When a session redacts it, only the visible replies can be read.

Turn on "Read new Claude Code replies aloud" in Settings and new replies are
spoken as they land.

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
node C:\Users\Kashix\Documents\CS\Projects\ChromeReaderExtension\tools\sync-shared.mjs
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
  src/earmark/
    app.py          wiring, free of Qt so it can be driven headlessly
    capture/        selection, files, accessibility, clipboard, OCR, Claude
    tts/            SAPI, Kokoro, Piper behind one interface
    player/         playback thread, prefetch, speed, pause mid-sentence
    textproc/       normalisation, sentence splitting, pronunciation, symbols
    store/          SQLite history and bookmarks, text-quote anchors
    ui/             tray, mini player, library
  tests/            57 tests, no audio device needed
```

Sitting alongside, outside this folder: `extension/` is the Chrome extension,
`shared/` is the rule data both halves read, and `docs/` holds the specs.

## Tests

```powershell
C:\Users\Kashix\Documents\CS\Projects\ChromeReaderExtension\desktop\.venv\Scripts\python.exe C:\Users\Kashix\Documents\CS\Projects\ChromeReaderExtension\desktop\tests\test_reader.py
```

One of them runs tools/conformance.mjs, which feeds the same inputs through
this Python and the extension JavaScript and fails if the two disagree. It
skips when Node is absent. Both sides have been checked by deliberately
breaking a rule and confirming the harness reports it, rather than trusting a
green result.

Swap the filename for `test_textproc.py` or `test_store.py` for the other two.
They find the package and the repository root by searching upward for a marker
rather than by counting directories, so they run from any working directory and
survive being moved again.

`desktop/tests/smoke_audio.py` is separate because it needs speakers and makes
noise.

## Known limits

- Chrome and Discord need their accessibility flag, as above.
- UWP apps such as Windows Security expose nothing readable.
- Scanned PDFs have no text layer; the app detects this and says so. Screen OCR
  is the only route for those.
- Sentence highlighting in the source window is not implemented. The mini
  player shows the current sentence instead.
