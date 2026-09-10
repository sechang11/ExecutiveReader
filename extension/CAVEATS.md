# Extension caveats

Constraints, limits and traps that are expensive to rediscover. The desktop half
keeps its own list at the repo root; this one is browser-specific.

Ordered by how much time each would cost someone who did not know it.

---

## 1. The service worker dies, and everything follows from that

Chrome suspends it after roughly thirty seconds idle. It cannot hold playback
state, an audio graph, or a loaded model. Every state transition is mirrored to
session storage and rehydrated on wake, and an open port from the content script
keeps it alive during a read.

The consequence people miss: **an 88 MB inference session cannot live here.**
Neural voices run in the offscreen document, which survives, so the model loads
once per listening session rather than once per pause.

## 2. Audio and inference must share the offscreen document

`chrome.offscreen` with reason `AUDIO_PLAYBACK` is the only Manifest V3 way to
keep a Web Audio graph alive across navigation. Creating one is **not
idempotent** — a second create throws — and the worker that created it may have
been killed since, so "did I create one?" cannot be answered from memory. Every
path re-derives it from `chrome.runtime.getContexts`.

Audio never crosses back to the worker. Moving a megabyte of samples per
sentence would be pointless when the worker cannot hold the graph anyway.

## 3. Teardown order is a real hazard, and nothing tests it

Stop the producer, await it, *then* release the sink. Reversed, you get
detached-node errors and audio that plays past stop. The desktop half hit the
same bug in C and segfaulted at exit **after logging a clean shutdown**.

Tests use a stub sink, so the code that tears down a real device has zero
coverage by construction. Run the extension to completion; do not trust green.

## 4. No remote code, and the failure is silence

Extension pages may not load scripts from the network. The ONNX runtime and
PDF.js are vendored for this reason. Both have a default that fetches their
worker or WebAssembly from a CDN, and when the policy blocks it **there is no
error, only a hang**. Both are explicitly pointed at local paths:

- `ort.env.wasm.wasmPaths`
- `pdfjs.GlobalWorkerOptions.workerSrc`

Model weights are data, not code, so fetching those at runtime is fine.

## 5. Content-script dependencies must be web-accessible

A module the content script imports, or a file it fetches, must appear in
`web_accessible_resources`. A missing one does not fail the build. The reader
simply never starts, with an error only in the page's own console.

This has broken twice. `test/manifest.test.mjs` now walks every import and fetch
from the content entry points and checks each against the resource list.

## 6. Paired identifiers across files have no compiler

A highlight name in CSS and the same string in JavaScript; a port name in two
scripts; an element id in HTML and a lookup in JavaScript; a message type sent
in one file and switched on in another. Nothing type-checks any of these, and a
mismatch produces **silence, not an error**: highlighting stops painting, or the
keep-alive dies with an empty console.

Renaming anything means grepping both sides. The rename to Executive Reader had nine
embedded occurrences, three of which would have failed silently.

## 7. Chrome's PDF viewer is closed to us

It is a separate extension; we cannot inject into it. Reading a PDF means
opening our own viewer, which extracts the text and renders it as ordinary
paragraphs, after which everything downstream treats it as a normal document.

Text-layer PDFs only. A scan is an image and needs OCR, which the desktop half
has and the browser does not.

## 8. PDF reading order is reconstruction, and columns come before lines

A PDF is glyphs at coordinates. There are no paragraphs, no columns, no order.

The ordering trap: in a two-column layout **the columns share baselines**, so
grouping items into lines before splitting columns merges each left line with
the right line beside it, and nothing downstream can undo it. Columns are a
property of x, lines of y, and x must be resolved first.

## 9. Speed belongs in synthesis, not playback

Kokoro takes speed as an input and stretches duration at generation time, so
2.5x keeps its pitch. Applying `playbackRate` to finished audio pitch-shifts it,
which is why fast reading sounds like a chipmunk in most readers. The engine
interface carries `appliesSpeedInternally` so the player only resamples for
engines that cannot do it themselves.

## 10. The phoneme alphabet drops what it does not know

Kokoro's alphabet is 115 symbols and tokenization is character-level. A symbol
outside it is **dropped, not rejected** — the word loses a sound and nothing
reports it.

Affricates are the specific trap: the alphabet has `ʧ` and `ʤ` as single symbols
and no two-character `tʃ`, but `tʃ` tokenizes perfectly happily as `t` then `ʃ`,
which is a different sound. Diphthongs genuinely are two symbols and must be
left alone.

## 11. Match the training data, not the textbook

The first grapheme-to-phoneme implementation marked stress at the syllable
onset, which is correct notation. Output got worse. Kokoro was trained on
eSpeak-shaped input, which marks the vowel: `kwˈɪk`, not `ˈkwɪk`. A model
rewards familiarity, not correctness.

## 12. Nothing copyleft ships, and that is enforced

eSpeak gives better pronunciation and seven more languages, and is GPL-3.0.
Shipping it would make the extension GPL: source on request, and no proprietary
tier later.

Both packages named `phonemizer` carry it — the npm one **declares Apache-2.0
while embedding eSpeak as WebAssembly** — and `piper-phonemize` embeds the same
engine. `test/licence.test.mjs` fails if any of it appears in `vendor/`.

The cost: pronunciation is CMUdict, which misses about 2% of words on real
article text, and the catalogue is 29 English voices rather than 55 across eight
languages.

The mitigation is the pronunciation editor, and it is worth stating that this
file claimed that mitigation existed for some time while nothing in the
extension read the rules file at all. The desktop half used it; the browser half
shipped it and ignored it. A stated mitigation is a claim like any other.

## 13. Store review

Broad host access is requested **on demand**, not at install: asking for every
site up front lengthens review and costs installs. Also required at submission:
a single-purpose statement, a justification per permission, and a privacy
policy. Local-only processing makes that policy short, which is itself worth
something.

## 14. Rules changes invalidate saved positions

Reading positions store the *normalized* sentence text, and normalization is
driven by files we edit. Editing them rewrites the strings positions were
captured from. Adding `w/` to the expansion list did exactly that.

Every position carries the fingerprint from `shared/fingerprint.json`. Use it
for **confidence, never control flow** — always try an exact match first,
whatever the stamp says. See `docs/anchor-vocabulary.md`.

The fingerprint covers only files that change the *text of a sentence*, and each
shared file must declare `_affects_stored_text` or the sync tool refuses to run.
Getting that wrong is quiet in both directions:

| File | Fingerprinted | Why |
|---|---|---|
| `abbreviations.json` | yes | sentence boundaries change the stored text |
| `normalization.json` | yes | rewrites the stored text directly |
| `sites.json` | yes | changes which elements are extracted |
| `pronunciation.json` | no | applied per word at synthesis, never stored |
| `pagination.json` | no | navigation only |

This started as a bug. Everything in `shared/` was hashed, so adding one
per-site pagination selector — which cannot change a single spoken word —
marked every saved position as "rules changed" and downgraded confidence
everywhere for nothing.

When in doubt, declare `true`. A false positive costs a little confidence; a
false negative claims a position is verified when the rules that produced it
have changed.

## 15. Site rules will break, and must break softly

`shared/sites.json` holds selectors for webmail and forums, where the generic
scorer finds only application furniture. Those selectors belong to applications
that change without warning, and no test here can notice.

So a rule that matches nothing **falls back to the generic scorer** rather than
returning an empty document. That fallback is the only reason it is safe to
guess at these selectors at all.

Mail rules must skip quoted replies. Without that, a ten-message thread reads
every message once per reply below it, which is roughly fifty readings of the
same text.

## 16. Named constants disappear from a threshold audit

Extracting a magic number into a named constant is the recommended fix, and it
removes the number from any audit that greps comparisons. The tidier the code
gets, the less such a tool sees. Auditing `length < 200` style expressions found
12 thresholds here; auditing named constants as well found 18 more, including
both of the ones that audit had just prompted me to extract.

Mutating each in turn — change the value, run the suite, restore — sorts them
into pinned and not. As of writing, five remain unpinned **on purpose**:

| Constant | Why it is not pinned |
|---|---|
| `SAVE_EVERY` | needs `chrome.storage`; it trades write quota against staleness |
| `RESUME_AFTER_MS` | needs scroll and wheel events |
| `DB_VERSION` | a schema version, not a judgment. Pinning it would only assert it equals itself |
| `MAX_ENTRIES` | pinning the default honestly needs 501 entries written, which buys little for the time |
| `SAMPLE_RATE` | a fact about the model, and module-private. Wrong, every voice is pitch-shifted and nothing errors |

The list is the point. A constant that survives mutation is a question, not a
verdict: some encode a judgment and should be pinned, some encode a fact and
should not, and the two are indistinguishable until someone looks.

## 17. Shipped is not read, and bytes matching is not behaviour matching

The pronunciation rules sat in the package for weeks, byte-identical to the
canonical copy, passing every sync and drift check, while nothing in the
extension read them. The desktop half used the file, so the shared-data pattern
held everywhere anyone looked and stopped being looked at.

The sync tests compared bytes. Bytes matching is not behaviour matching, which
is the stale-duplicate lesson pointed the other way: there, identical bytes hid
a stale source; here, identical bytes hid an unused one.

Two guards, one weak and one strong.

`test/manifest.test.mjs` asserts every shared file is referenced somewhere in
the source. That is a filename appearing in a string, and it would pass for code
that fetches a file and drops it on the floor.

`test/shared-data.test.mjs` blanks each file in turn and requires the behaviour
it drives to change. If emptying a rule set changes nothing, it is not being
consulted. Adapted from the desktop half, which uses the same shape for the
quieter version of this failure: there a missing file falls through to a
built-in fallback and everything keeps working slightly worse.

Its limit is stated in the test: it proves the data is consulted, not that it is
consulted correctly.

## 18. Shared data is copied, not owned — and a stale copy answers

`shared/cmudict/` and `shared/kokoro/` are canonical. The matching directories
under `extension/vendor/` are copies made by `tools/sync-shared.mjs` and
**committed**, because a Chrome extension can only load files inside its own
folder and Load Unpacked must work from a checkout without running any tool.

They moved out of the extension because the desktop half reads the same data,
and a deliverable that cannot be packaged without reaching into another
deliverable's directory is the wrong shape.

**A copy fails in two ways, and both are silent.**

It stops being made, or is made somewhere else. That happened immediately: the
sync loop logged success while writing one directory too high, because its
destination was computed relative to the JSON output path rather than to the
extension root.

Worse, it goes stale. **A stale duplicate does not break — it answers.** Both
halves would agree with each other, both conformance harnesses would pass, and
words would be pronounced by an older rule with nothing anywhere reporting it.
The desktop half hit precisely this: its loader searched two paths, neither was
the new canonical location, and it silently fell through to the extension's copy
and kept working.

So `test/standalone.test.mjs` checks presence, byte-equality against the
canonical version, and usability rather than existence — the dictionary must
decompress to more than 120,000 entries, and the phoneme alphabet must hold
exactly 115 symbols. An empty dictionary is silent too: every word falls through
to letter-to-sound and it still speaks.

The directories are discovered rather than listed, so a third one is covered the
day it appears rather than the day someone remembers to add it.

## 19. A true premise is not a checked conclusion

"A Chrome extension can only package files inside its own folder" is true. "So
the canonical copy cannot live elsewhere" does not follow, and I asserted it
anyway. The constraint governs *loading*; it says nothing about ownership, and
the copy is what bridges them.

That error survived review precisely because the true half made the whole
statement feel verified. It is harder to catch than a plainly wrong fact,
because nothing in it looks doubtful.

**It happened three times in one day, in three different disguises.** All three
were claims about *behaviour* derived from something true about *structure*, and
structure is exactly what feels like it does not need checking:

| The claim | The true part | What did not follow |
|---|---|---|
| The canonical dictionary cannot move | an extension only loads files inside its own folder | that governs loading, not ownership |
| Pronunciation overrides mitigate the dictionary's misses | true of the desktop half, which reads that file | the extension shipped it and read nothing |
| The idempotence test subsumes the pairwise one | both check for rule chaining | measured, the pairwise one strictly dominates |

None of the three felt like a guess while being written. Each read as a
structural property, which is the costume the problem wears.

**The true half is the active ingredient.** A wholly wrong claim invites
checking; a claim with a correct premise attached actively suppresses it. That
is why this is worse than being plainly mistaken rather than better.

There is no procedure here, unlike every other item in this file, which all
reduce to measuring what a check covers. Noticing a conclusion is present at all
is the hard step, and a sound premise is what stops it looking like one.

The only thing that caught all three was the same accident: someone implemented
against the claim and measured, rather than reading it and agreeing. That is not
a procedure, but it is an argument for writing conclusions down where somebody
will act on them.

## 20. Name the specific absence, not the category

Neural voices can be unavailable because the pronunciation dictionary did not
load, or because the model has not been downloaded. Those have opposite fixes,
and one shared message sends people the wrong way: someone who has just waited
for an 88 MB download and reads "unavailable" will download it again.

`kokoroEngine.blockedBy()` returns a reason code and a sentence naming the
actual absence. Any new failure mode here gets its own reason rather than
joining an existing one.

## 21. A capability check that a real attempt was supposed to replace

`worker.js` tried the WebGPU execution provider first and fell back to
WebAssembly on failure, deliberately as a real session build rather than a
feature test. The comment explaining that is still there, and the reasoning is
still right: drivers advertise support they cannot deliver, and only building a
session finds out.

What it missed is that failing and stalling are different outcomes. In a browser
where `navigator.gpu` exists but `requestAdapter()` resolves `null` — a virtual
machine, or the feature flagged on with no driver behind it — the WebGPU session
build did not throw. It was still running after eight minutes, with the page
showing "Starting the engine…" and no error to fall back from.

One adapter request ahead of the loop fixes it, and costs a millisecond. That is
a capability check, which the comment above it argues against. Both are correct:
skip the attempt when there is provably nothing to attempt, and make a real
attempt whenever there might be.

The general shape: **"try it and see" only terminates if failure is prompt.**
Any fallback ladder that cannot bound how long a rung takes needs a cheap
precondition in front of it, or the fallback is unreachable.

## 22. The demo was worse than the product it advertised

`site/demo.js` called onnxruntime on the page's own thread while the extension
had always used a worker. On the WebAssembly path this froze the tab outright:
not slow, unresponsive, for the whole of session creation and synthesis.

The demo exists to argue for the extension, so a visitor's only measurement of
the product was of code the product does not run. The header comment claimed the
extension's modules were reused rather than reimplemented; that was true of the
phonemizer and the tokenizer, and false of the part that does the work.

The same gap hid a second one. The demo fed the whole passage to the model as a
single utterance, where the extension segments first. Kokoro's cost grows faster
than its input, so this was not a small difference: the default demo text had
not finished after six minutes, and the same text sentence by sentence takes
about a minute, with the first sentence audible after eleven seconds.

The demo now loads the extension's worker, segmenter and normalizer, copied by
`tools/sync-shared.mjs` and checked for drift in `standalone.test.mjs` alongside
the other shared files. Reuse claims are worth only as much of the pipeline as
they actually cover, and the uncovered part is reliably the expensive one.

## 23. A guard in one branch of a ladder is not a guard

`findNext` tries strategies in order: `rel=next`, then aria labels, then class
names, then link text, then incrementing a number in the URL. The first branch
rejected a link pointing at the page we are already on, with a comment saying
so. The text branch, two rules later, did not.

So a page with `<a href="/current-page">Next</a>` was rejected by the strategy
that checks and accepted by the one that does not, and following it re-reads the
same page — forever, with auto-continue on. The guard was present, correct, and
in the wrong place, which is why reading the file does not find it: the branch
you happen to read has the check in it.

Found by `tools/domtest/`, on its first run, because a fallback ladder is only
testable against a document and nothing had ever run one against it.

**A precondition that applies to every branch belongs above the loop.** It is
now one predicate used by all three, which is also the shape that makes the next
strategy inherit it for free.

## 24. Enforcing a contract is not loosening a comparison

The anchor ladder compared stored quotes to live sentences as raw strings, so a
sentence that gained a double space resolved as `fuzzy` — "probably the right
place" — when it was certainly the right place. The desktop half reported
`exact`. Its conformance harness found eight such disagreements; no test on
either side covered any of them.

The reflex is to treat this as a choice between strict and lenient. It is not.
`_output_contract` in `shared/normalization.json` already requires both halves
to collapse runs of spaces and tabs and trim the ends, so two strings differing
only in that respect **cannot both be legal output of the pipeline**. Comparing
them as different was reporting a violated invariant as evidence about the page.
Three of the eight were that, and they are gone.

The other five are the opposite finding. `docs/anchor-vocabulary.md` defines
`exact` as character for character, and the same contract says newlines are left
alone; a non-breaking space is neither a space nor a tab. So a quote differing
by a newline, a non-breaking space, a capital letter or an apostrophe style
differs in characters, and `exact` is not available for it. Those five are the
desktop half's deviation, or a change to the vocabulary that neither half makes
alone — and the vocabulary document says so itself.

**Before conforming to the other implementation, check what the contract
actually says.** Two halves agreeing on something neither is allowed to do is
the same failure as two halves disagreeing, minus the symptom.

## 25. An argument about reachability rots; a test does not

The desktop half also reported that this half's segmenter treats a newline as an
ordinary character, and warned it would not survive PDFs, where hard wrapping is
universal. The behaviour is real. The consequence is not: `extractBlocks`
collapses every whitespace run inside a block, and the PDF viewer splits
`linesToText` output on blank lines and renders one `<p>` per paragraph, so the
segmenter is never handed a newline from either path.

Note where the work happens. Rejoining a wrapped line is a decision about the
gap between two baselines, and only the PDF geometry code knows that. A newline
heuristic in the segmenter would be guessing at what was already measured.

The reply to that report could have been this paragraph. Instead it is two tests
— one in `tools/domtest/` asserting no block text carries a line break, one in
`pdf-layout.test.mjs` asserting no rejoined paragraph does. Prose explaining why
a defect cannot be reached is exactly the kind of true-sounding claim section 19
is about, and it stops being true the day someone adds a third input path.

## 26. A flag nobody reads is worse than a flag nobody set

`shared/normalization.json` carried a `collapse` section — `smart_quotes_to_plain`,
`soft_hyphens`, `zero_width`, `footnote_markers`, `dot_leaders`,
`repeated_punctuation`, `emoji` — every flag set, for the whole of the project.
Neither half read it. The extension's normalizer implemented expansions,
currency and symbols and stopped.

What that cost is not tidiness. The phonemizer looks words up in CMUdict by
literal text, so a word carrying a curly apostrophe misses its entry and falls
through to the letter rules. Measured on the shipped dictionary:

| written | spoken |
|---|---|
| `don’t` | dawn tee |
| `it’s` | it ess |
| `won{soft hyphen}derful` | wun DERful |

Nearly every professionally typeset page uses curly apostrophes. This was most
contractions on most articles, in the neural voices that are the entire pitch.

Three things kept it invisible. The extension's own tests used straight quotes,
because they were typed in a code editor. `conformance.mjs` never called the
full `normalize()`, only the three stages that were implemented. And its corpus
contained no curly quotes, no soft hyphens and no dashes — so a seven-character
difference between the two normalizers sat next to a passing cross-language
harness for weeks.

**A specification the code does not read is not a specification, it is a
comment.** The `collapse` keys looked exactly like the `symbols` and
`expansions` keys beside them, which are read. Nothing distinguished decoration
from contract, and nothing failed.

The scan that would have caught it is the same one this file keeps arriving at
from other directions: for each key in a shared file, does any consumer
reference it? The extension now implements every key in `collapse`, and each has
a definition in `_collapse_rules` precise enough for both halves to converge on.

## 27. Implemented on both sides is not compared on either

The `collapse` section landed in both halves within a day of each other, both
with tests, both green. Nothing compared them. `conformance_py.py` did not
export the stage, so `conformance.mjs` printed a polite note saying so and moved
on — a note is not a check.

Wiring the stage in took four minutes and found a real defect immediately, in
this half, that every test here had passed:

    ©2026 Acme  ->  2026 Acme
    Widget™     ->  Widget

`©`, `®` and `™` are all `Extended_Pictographic`, so `emoji: "skip"` deleted
them, and with them the words `copyright`, `registered` and `trademark` that the
symbols list exists to produce. The rule now excludes anything the symbols or
currency lists speak, because those lists are the authority on what gets spoken.

The comment in `_collapse_rules` had already claimed the exception — "so
ordinary symbols handled by the symbols list above are untouched" — while the
code did not implement it. Section 19 again, in the same file that documents it.

The second finding is subtler and is why this entry exists. The bracketed
footnote disagreement — the desktop half removes `[1]`, this half keeps it —
was written down by both sides and escalated to the user, and is *still*
invisible to every harness, because the desktop's removal happens further along
its `normalize()` than any compared stage reaches. Adding the case produced
agreement, not divergence.

**A disagreement both sides have documented can still be untested.** Writing it
down feels like handling it. `conformance.mjs` now has a `KNOWN` map like the
other two harnesses, currently empty, with that stale entry's story kept in it —
because the empty map is the honest record of what is not yet checkable.

## 28. Two implementations reasoning from vocabularies is not evidence

The bracketed footnote marker was argued in both directions for three exchanges.
This half kept `[1]` because it cannot be told from a reference the reader
wants. The desktop half removed it because a number injected mid-sentence is an
interruption. Both positions were reasoned, both were written down, and neither
was measured.

When it was measured it took minutes and was not close:

| engine | without `[1]` | with `[1]` |
|---|---|---|
| Microsoft David (default) | 2.924s | 3.129s |
| Kokoro | 2.975s | 2.975s |

Audible interruption on one engine, literally invisible on the other. Removing
it is better in one place and free in the other, and keeping it wins nowhere.

The same exchange produced a second reversal in the other direction. This half
argued that `…` should be left alone because the model vocabulary contains a
token for it — so the model "can voice it". Measured, the difference between
`.` and `…` on Kokoro is 0.05s, which is nothing. The conclusion survived, but
the reason did not: what actually justifies leaving it alone is that on the
system voice, collapsing to a period makes the pause *longer* by 0.485s, turning
a trailing-off into a firmer stop than the author wrote.

**A token being in a vocabulary proves the model was trained with one, not that
its output differs.** Both halves reached for that inference and both got a
different answer from measurement.

`tools/voicelab/` exists so the neural half of this question is a page load
rather than an argument. It synthesizes variants of a sentence and reports the
audio duration, deliberately skipping `normalize()`, because the point is
usually to measure what a normalization rule would change.

## 29. The end-of-pipeline tidy hides disagreements in the middle of it

Adding two boundary inputs to the conformance corpus — `Wait.. what?` and
`Wait.... what?` — turned up a difference nobody had seen. On four spaced
periods the two halves produced:

    python:  "Wait  what?"     (two spaces)
    js:      "Wait what?"      (one)

The dot-leader rule says a run of periods "optionally separated by spaces"
becomes a single space. This half also swallowed the whitespace *after* the
final dot. That is one more character than "separated by" describes, and the
Python reading was the faithful one.

It could not show up in the finished text, because `tidy()` runs at the end of
the pipeline and collapses the double space. Only a stage-by-stage comparison
could see it — which is the mirror of the lesson that arrived in the same round
from the other direction, that a stage compared in isolation is not the
pipeline.

A third case arrived immediately after, from the desktop half, and completes the
set. Its `apply_collapse` correctly returned `Wait… what happened?`, every unit
test passed, and all 510 cross-language comparisons were identical. Then it timed
the finished sentence on a real voice and the number had not moved from the
collapse-to-a-period figure: NFKC, running later, decomposes U+2026 straight back
into three periods, and the repeat rule turns those into one. The authored
trailing-off was being restored and destroyed inside a single function.

The stage comparison could not see it because the stage was right. The
end-to-end comparison could not see it because both halves were wrong the same
way. Only timing the output on an engine could.

**Three checks, each blind to what the other two find:**

| check | catches | misses |
|---|---|---|
| stage by stage | differences a later stage normalises away | a stage that is right in a pipeline that is not |
| end to end | ordering damage between correct stages | anything both halves get wrong identically |
| measured on an engine | a rule that produces no audible change | nothing here, and it is the slow one |

Neither of the first two substitutes for the third, and the third is the one
that gets skipped because it needs audio.

The rule text now spells out that the separators are between the dots. That
sentence exists because one character of ambiguity produced two implementations.

## 30. `git checkout --` is not an undo

While restoring a file after a mutation test, `git checkout -- <file>` discarded
an hour of uncommitted work in it. The mutation harness in this project writes
the original back itself; reaching for git as well was belt-and-braces reasoning
about a file the script had already restored, and it destroyed the changes the
script was never touching.

Nothing was lost that could not be retyped, and the tests said immediately what
was missing. The general point stands: **the mutation-testing pattern used
throughout this file — write a mutation, run, write the original back — must
restore from the string it captured, never from the index.** The working tree is
where the uncommitted work is.

## 31. Reasoning that expires when a rule lands

The ellipsis split — `Wait… what happened?` becoming two segments here and one
on the desktop half — was recorded as a known divergence with the reasoning
"outside a collapsed ellipsis the case is rare, which is why it is recorded
rather than chased".

That was true when it was written and false a day later. Landing the rule that
turns every authored `...` into U+2026 moved the case from rare to *every
document with a trailing-off in it*, and nothing connected the two: the entry
still read as a considered decision, and the change that invalidated it was in a
different file.

A recorded divergence carries a judgement about how much it matters, and that
judgement has dependencies the entry does not name. **The staleness check catches
an entry whose behaviour changed; nothing catches an entry whose reasoning
changed.** The only thing that caught this one was the other half re-reading the
sentence while landing an unrelated rule.

### A partial fix, from the desktop half

An entry may now carry a `premise` beside its recorded shapes: the claim its
tolerability rests on, written as a predicate instead of as prose. Three
outcomes replace two — behaviour changed, *reason expired*, or both still hold —
and the middle one fails the build with a message saying the argument has gone
rather than the behaviour.

All four entries carrying one here rest on a sentence in a *different file*,
which is the distance that let the last expired reason survive:

| entry | premise |
|---|---|
| newline is not an exact match | the output contract still says newlines are left alone |
| the two capitalisation entries | the vocabulary still defines `exact` twice, and differently |
| the layout group | `pdf/viewer.js` still splits paragraphs on blank lines |

Each was verified by breaking the premise while leaving both halves' behaviour
untouched, and watching it fail: resolving the vocabulary contradiction on
paper, editing the contract, and making the viewer hand a whole page to the
segmenter.

One of the four was wrong in a way worth keeping. The layout premise first
searched `pdf/viewer.js` for the literal string `split(/
{2,}/)`. That checks
the *spelling* of the behaviour, not the behaviour: reformat the regex, extract
it to a constant, change the flags, and the premise fails while the reasoning is
untouched. The desktop half pointed out why that is worse than neutral — a
premise that cries wolf trains exactly the lazy repair the failure message warns
against, faster than anything else could.

Fixing it meant moving the rule rather than rewriting the check. Paragraph
splitting is now `toParagraphs` in `pdf/layout.js`, beside the function whose
blank lines it consumes, so the premise can call it. Two things fell out that
were already wrong: the rule had been inline in a page script, where nothing
could test it, and `pdf-layout.test.mjs` therefore held a *copy* of the regex
with a comment saying it mirrored the viewer. A second copy of a rule, in the
test that exists to protect it.

Verified both directions: replacing the split with `[text]` fails the premise;
reformatting it across four lines does not. **A premise that matches source text
is a premise about the source text.**

Its limits, because overstating them would be the same error again. It catches
only reasoning that was *expressible* — "this shape never occurs in production"
is; "neither behaviour is obviously wrong" is not. And the lazy repair is to
edit the predicate until it passes, which is why the failure message says not
to. **It converts a class of silent expiry into a loud one. It does not convert
judgement into arithmetic.**

The rule is now implemented rather than recorded: `terminators` in
`shared/abbreviations.json`. It is deliberately not generalised to the plain
period, which is the wider rule the desktop half applies. The argument looks
identical, but informal all-lowercase writing — chat logs, forum posts, notes —
is real content for a reader, and there the general rule swallows every boundary
in the piece and reads it as one utterance with no pauses in it. The ellipsis has
no such counterexample.
