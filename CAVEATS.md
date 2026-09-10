# Caveats

Things that constrain this project and are not obvious from the code. Read
before shipping, selling, or making a decision that depends on any of them.

Nothing here is legal advice. Where a licence question has real money attached,
take proper advice.

---

## 1. eSpeak is GPL-3.0, and linking to it is legally contested

**The fact.** Kokoro takes phonemes, not text. The standard way to produce them
is eSpeak NG, which is GPL-3.0. On this machine, `phonemizer` 3.4.0 declares
GPL outright in its own metadata, and `espeakng-loader` ships the actual
`espeak-ng.dll` plus 19 MB of data inside site-packages. That is distribution of
a binary, not linking against a system install. Piper is not an escape route:
`piper-phonemize` embeds the same engine.

**What it means.** Running eSpeak yourself is unrestricted. Copyleft attaches
when you *distribute*. Shipping an application that bundles GPL-3.0 code puts
the whole work under GPL-3.0, which is incompatible with selling it or keeping
the source closed.

**What was decided, and what is now built.** Permissive phonemizer by default,
eSpeak supported as an add-on the user installs themselves.

This is implemented, not planned. The desktop half phonemizes with its own
CMUdict-based dictionary and runs Kokoro directly on onnxruntime, so
`kokoro-onnx`, `phonemizer` and `espeakng-loader` are all uninstalled and out of
`requirements.txt`. No eSpeak file remains anywhere in the environment. A test
asserts none of the three can be imported, so the permissive path cannot quietly
stop being permissive.

**The contested part, and why it matters to you.** Whether a proprietary program
that dynamically links a *user-installed* GPL library forms a derivative work is
genuinely disputed. The Free Software Foundation says yes. Many commercial
products ship this pattern anyway, and it is how GPL codecs are commonly
handled. It has not been definitively settled in court.

**Practical position.** The risk is low while the app works fully without
eSpeak, never bundles it, never downloads it automatically, and treats it as an
optional enhancement. Get a lawyer's view before charging money.

## 2. EbookLib was AGPL, and EPUB no longer uses it

Found by scanning every installed package rather than by suspecting it. EbookLib
0.20 declares the **GNU Affero GPL**, which is stronger copyleft than GPL. It was
being used to read EPUB files, so it carried the same disqualifying consequence
as eSpeak.

An EPUB is a zip of XHTML with a manifest, so the dependency bought convenience
rather than capability. `capture/files.py` now reads it with the standard
library and an HTML parser, in spine order rather than archive order. Verified
against five real books.

The lesson generalises past this package: the licence problem was not in the
thing anyone was thinking about. eSpeak was found by reasoning about the speech
pipeline; this one was two layers away in a file reader nobody suspected. Only a
scan of the whole dependency list finds that class, which is why
`desktop/tests/test_licences.py` now runs one.

## 3. Qt is LGPL, which is usable but has conditions

PySide6 and its companions are LGPL-3.0. Unlike GPL, that permits use from a
proprietary application, which is exactly why Qt offers PySide under LGPL where
PyQt is GPL-or-commercial. The conditions still bind:

- Link dynamically. Do not statically link Qt into a single executable.
- The user must be able to replace the Qt libraries with their own build, so a
  frozen bundle should keep them as separate files rather than embedding them.
- Ship the LGPL text and state that Qt is used under it.

This is the standard arrangement for commercial Python desktop apps and is not
a blocker. It becomes one only if packaging is done carelessly.

## 4. Coverage numbers describe the check, not the code

Three measurements were taken here, and each said something different:

| Question | Answer when first asked |
|---|---|
| Which modules does the suite load? | 32 of 40 |
| Which functions does it call? | 179 of 339 |
| Which of those are public? | 96 never called |

Loading is not calling. The file readers were loaded on every run and asserted
against by nothing, so a module-level scan called them covered while PDF and
EPUB reading rested entirely on having been tried by hand once.

`tools/reachability.py` and `tools/exercised.py` measure by running the suite
under the interpreter's profiler rather than reading the tests. That matters:
a scan that reads test source counts a name in a docstring as a call, and
counts its own list of known gaps as evidence those gaps are covered.

The residual gap is real and mostly needs hardware: window reading, screen OCR,
a live audio device, a Windows message loop.

## 5. Mutation testing lies if bytecode is cached

A tool that edits a source file, runs the tests, and restores it can do both
writes inside one filesystem timestamp tick. Python then treats the `.pyc` it
compiled for the *mutated* source as valid for the restored one, and the next
run executes code that is no longer on disk.

This happened here and produced confident, wrong verdicts in both directions:
constants reported as unpinned that were pinned, and the reverse. It was caught
only because one assertion failed that arithmetic said should pass.

`tools/thresholds.py` now clears cached bytecode before starting and runs every
child with `PYTHONDONTWRITEBYTECODE=1`. Anything else that mutates source and
re-runs tests needs the same, or its results are about code that never ran.

A second, quieter version of the same problem: extracting a magic number into a
named constant, which is the recommended fix, removed it from the audit until
the tool learned to read named constants too. The tidier the code got, the less
the tool could see.

## 6. The permissive phonemizer is English-only

Kokoro publishes 55 voices across eight languages. A voice is only usable if its
language can be pronounced, and the permissive dictionary covers English, so the
usable catalogue is 29 voices until a user installs eSpeak.

Dropped without eSpeak: Spanish, French, Italian, Portuguese, Hindi, Japanese,
Mandarin.

## 7. Both halves must pronounce identically

Two independent implementations of one pronunciation pipeline will drift, and
the symptom is a reader saying the same name two ways depending on which half
spoke, with nothing in either log to explain it. `tools/conformance_g2p.mjs`
runs 85 inputs through both and fails on any difference. Run it after touching
either phonemizer.

## 8. Two different pronunciation failures, and only one is fixable with data

- **Missing words** (proper nouns, product names, coinages) fall to
  letter-to-sound rules and get an approximation. This degrades predictably and
  is patchable by adding dictionary entries.
- **Homographs** are confidently wrong on ordinary English: "he read the book"
  becomes "reed" because a dictionary has no context. Adding entries never fixes
  it. Real disambiguation needs part-of-speech tagging, which is a dependency
  rather than a data file. Both halves ship a cue-based approximation covering
  the cases that occur in prose; it is not complete.

## 9. Voice model licences differ, and the model is not the whole pipeline

- **Kokoro** model and voice packs: Apache 2.0. Clean.
- **Piper** voices: licences vary *per voice* because they come from different
  source datasets. Check the model card of any voice before shipping it. The
  `piper-tts` package is not used, because it depends on `piper-phonemize`,
  which embeds the same GPL eSpeak. The models run directly instead, which is
  also what keeps a voice you trained yourself usable.
- **XTTS-v2**: non-commercial licence. Do not use it in anything you sell.

The trap is that "the model is Apache 2.0" says nothing about the phonemizer,
the runtime, or the voice pack. Check each separately.

## 10. Chrome hides page text unless you ask for it

Chrome does not build an accessibility tree unless started with
`--force-renderer-accessibility`. Measured, not assumed: an unflagged window
exposed 145 characters of toolbar labels; a flagged one exposed the article.

Without the flag the desktop app can still read Chrome pages through the
clipboard and screen OCR, and PDFs by opening the file directly rather than the
tab. Everything else, including Electron apps and Office, works without it.
Discord needs the same treatment.

## 11. Do not pip install into the global Python

The global interpreter carries a large ML stack pinned to numpy 1.x
(TensorFlow, matplotlib, numba, ultralytics, mediapipe). Installing
`kokoro-onnx` there silently upgraded numpy to 2.x and broke all of them. It was
restored to 1.26.4. Desktop dependencies live in `desktop/.venv` for exactly
this reason.

## 12. There is no version control

Two sessions have been editing one working tree. That produced real confusion:
files were read mid-edit and failures reported that never existed as a coherent
state. A repository would end this class of problem entirely.

## 13. Reading Claude's thinking depends on the session

Claude Code transcripts sometimes store thinking text and sometimes redact it.
Across the 72 transcripts on this machine, 11,179 thinking blocks had text and
19,974 were empty. Visible replies are always exact; thinking is not always
there to read.

## 14. A stale duplicate does not break, it answers

Shared data lives in `shared/` and is copied into `extension/vendor/` because
Chrome can only package files inside an extension's own directory. That copy is
the dangerous kind of redundancy.

A **missing** copy fails loudly the first time something loads it. A **stale**
one is consistent: both halves read old data, both agree with each other, and
every cross-language check passes because they agree on the same wrong thing.
Nothing in either log says so, and the symptom is only that a word is
pronounced by a rule that was changed weeks ago.

This already happened here in miniature. After the dictionary moved to
`shared/`, the desktop loader still looked in the old place, fell through to the
extension's copy, and kept returning correct answers from the wrong source. It
was found by listing the directory, not by any test.

Guards now in place: the loader prefers canonical and treats the copy as a last
resort, a test asserts data is read from `shared/` rather than the mirror, and a
test hashes every mirrored file against its original and fails if it compared
fewer than three.

## 15. A stated mitigation is a claim like any other

Both halves documented that the dictionary's mispronunciations were "mitigated
by the pronunciation override system". On the desktop side that was true. On the
extension side the file shipped in the package and nothing read it: not exposed
to content scripts, no module importing it, all 45 overrides inert. The claim
had been repeated between the two sessions several times.

The mechanism is the unearned-conclusion problem wearing a different hat. The
premise was true somewhere, and a shared file made shared behaviour feel like a
consequence rather than something to check.

Two bugs surfaced while building the missing half, both worth knowing because
they are silent:

- A user override must **replace** the shipped rule for that word, not merely
  precede it. Ordering it first still lets a shipped rule further down match
  what the user's rule just produced and undo it. On the desktop side the bug
  was worse: a duplicate was skipped, so the user's entry was discarded and the
  setting saved without doing anything.
- Editing a rule must discard audio already synthesized under the old one.
  Otherwise the change lands several sentences later, whenever the prefetch
  queue happens to run dry, which is indistinguishable from it not working.

## 16. A claim about behaviour derived from structure feels checked

Three separate times on this project, one of us stated something that read as a
structural fact and was wrong. None felt like a guess when written.

| Claim | Why it looked settled | What was actually true |
|---|---|---|
| A Chrome extension can only package files inside its own folder, so the shared data cannot move out | The premise is true | It constrains where the extension *loads* from, not where the original lives |
| The dictionary's mispronunciations are mitigated by the pronunciation override system | True on the desktop half | The extension shipped the file and nothing read it |
| The fixed-point test subsumes the pairwise one | Follows from how chains compose | Rules apply sequentially, so pairwise strictly dominates |

The common feature: each was a claim about **behaviour** inferred from something
genuinely true about **structure**. Structure is exactly what feels like it does
not need checking, and the true half makes the whole statement read as verified.

Unlike everything else in this file, this has no procedure. Every other lesson
reduces to measuring what a check covers, which is mechanical. Noticing that a
conclusion is present at all is the hard step, and a sound premise is precisely
what stops it looking like one.

The only thing that caught all three was the same accident: the other party
implemented against the claim and measured, rather than reading it and agreeing.
That is an argument for stating conclusions where someone will act on them
rather than where someone will read them.

## 17. A sound premise with an unearned conclusion resists checking

The only entry here about a reasoning error rather than a technical one.

The claim was: "a Chrome extension can only package files inside its own folder,
so the shared data cannot move out of it." The first half is true. The second
does not follow — the constraint governs where the extension *loads* from, not
where the original *lives*, and a synced copy satisfies it.

It survived scrutiny for a while precisely because the premise was checkable and
correct. A wrong fact invites verification. A true fact with an unearned
conclusion attached does not, because nothing in it looks doubtful.

Worth watching for wherever a technical constraint is used to close a design
question, which in this project is often.

## 18. Scanned PDFs have no text layer

The app detects this and says so rather than reading nothing. Screen OCR is the
only route for those, and it reads only what is visible.

## 19. Speed above 3x on Windows system voices

SAPI has integer rate steps and tops out near 3.04x, measured against the real
engine. The neural voices take speed as a synthesis parameter and keep natural
pitch to 4x.
