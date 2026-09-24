# Chrome Web Store submission

Everything the listing form asks for, written out. Copy from here rather than
composing at the form, so the wording stays consistent and the permission
justifications match what the code actually does.

---

## Name

```
Executive Reader — Natural Text to Speech
```

45 characters visible in search results. "Executive Reader" carries the brand; the rest
carries the search terms people actually type. Deliberately not "Read Aloud"
adjacent: the incumbent owns that phrase, and a near-identical name ranks
beneath it while inviting confusion.

## Short description

Maximum 132 characters. This one is 129.

```
Reads pages aloud in natural voices that run on your own device. Free, offline, and nothing you read leaves your computer.
```

## Detailed description

```
Executive Reader reads web pages, PDFs and email aloud, in voices that run on your own
computer.

NATURAL VOICES, WITHOUT A SUBSCRIPTION
Most readers give you robotic voices for free and charge monthly for good ones,
because the good ones run on someone else's servers. Executive Reader runs a neural voice
model on your machine instead. It is free, it works offline, and the text never
leaves your computer.

HIGHLIGHTS THE PAGE, NOT A COPY OF IT
The sentence being read is highlighted in place, on the real page, along with
the word being spoken. Nothing is rewritten or re-laid-out, so pages keep
working normally while you listen.

PICKS UP WHERE YOU STOPPED
Every page you read is remembered along with your position. Come back a week
later and it resumes at the right sentence, even if the page has been edited
since.

KEEPS GOING
Follows the page as it scrolls, notices when more content loads, and can
continue onto the next page of an article. Reading speed goes from half to four
times normal, applied when the voice is generated so it never sounds like a
chipmunk.

READS MORE THAN WEB PAGES
PDFs open in a built-in reader that pulls out the text in the right order, even
in two-column papers. Gmail and Outlook are read as messages rather than as
application clutter, with quoted replies skipped.

BUILT TO BE USABLE
Full keyboard control, adjustable text size, spacing and typeface in the reading
panel, and highlight colours you can change. The spoken word is underlined as
well as coloured, so the cue never depends on colour vision.

PRIVATE BY CONSTRUCTION
No account, no analytics, no servers. Your reading history stays on your device
and you can delete it at any time.
```

## Category

Accessibility. Not Productivity: accessibility is both the more defensible
positioning and the more accurate one, and the defaults were chosen for it.

## Single purpose

The store requires one sentence describing a single purpose.

```
Executive Reader converts the text of web pages, PDFs and email into speech, and reads it
aloud to the user.
```

## Permission justifications

The form asks for one per permission. Each must describe the actual use;
reviewers reject vague answers, and they should.

| Permission | Justification |
|---|---|
| `storage` | Stores the user's voice, speed and appearance settings, and their reading history, locally in the browser. |
| `unlimitedStorage` | Reading history includes the extracted text of pages, so that history entries remain meaningful. The 10 MB default is exhausted after a few hundred articles. |
| `activeTab` | Reads the text of the page the user has asked to be read aloud, at the moment they ask. |
| `scripting` | Injects the reader into the active tab to extract its text and highlight sentences as they are spoken. |
| `tts` | Speaks text using the voices provided by the user's operating system and browser. |
| `offscreen` | Plays synthesized audio and runs the local voice model. Manifest V3 service workers are suspended after about thirty seconds idle and cannot hold audio playback or a loaded model. |
| `sidePanel` | Provides the reading panel, which shows the text being read, typography controls, and the history list. |
| `contextMenus` | Adds a "Read this aloud" item to the right-click menu for selected text. |
| `<all_urls>` (optional) | Requested only when the user turns on continuing to the next page, which requires reading the following page after navigating to it. Not requested at install. |

### Remote code

```
This extension executes no remote code. The ONNX Runtime and PDF.js libraries
are included in the package at pinned versions with checksums recorded. Voice
model weights are downloaded at runtime, but they are data consumed by the
included runtime, not executable code.
```

## Privacy disclosures

The form asks what user data is collected. The honest answers:

| Question | Answer |
|---|---|
| Personally identifiable information | No |
| Health information | No |
| Financial information | No |
| Authentication information | No |
| Personal communications | **Yes** — Executive Reader can read email aloud when the user asks it to. The text is processed on the device and never transmitted. |
| Location | No |
| Web history | **Yes** — pages the user has chosen to have read aloud are stored locally so reading can resume. Never transmitted. |
| User activity | No |
| Website content | **Yes** — page text is read in order to speak it. Processed on the device. |

Certifications: not sold to third parties; used only for the single purpose
above; not used for creditworthiness or lending.

Privacy policy URL: host `extension/PRIVACY.md` at a stable public address before
submitting. The store requires a URL, not a file.

---

## Before submitting

- [ ] Replace the placeholder icons in `extension/assets/` with real artwork
- [ ] Screenshots: 1280×800 or 640×400, at least one, at most five.
      These have to be taken by hand. Release builds of Chrome refuse
      `--load-extension` — the log line is "--load-extension is not allowed in
      Google Chrome, ignoring" — so the extension cannot be driven from a
      script for this. Load it unpacked, open an article, and capture the page
      mid-read with the highlight lit, the popup, and the side panel showing
      history.
- [ ] Host the privacy policy and put its URL in the listing
- [ ] Verify the trademark and domain for "Executive Reader" are clear
- [ ] Run `node tools/sync-shared.mjs`, then confirm `node --test extension/test/` is green
- [ ] Load the packed zip unpacked once, from a clean profile, and read a page end to end

## What review will likely ask about

**The 27 MB package.** It is the ONNX Runtime WebAssembly binary. Expect a
question, and answer with the remote-code paragraph above: it is vendored
precisely *because* Manifest V3 forbids fetching it.

**Personal communications.** Declaring email access truthfully is more likely to
pass than omitting it and being found out. The mitigation is that it happens
only on request and stays on the device.

**The optional host permission.** Requesting `<all_urls>` on demand rather than
at install is the reason review should be straightforward. Do not move it into
the install-time list to save a prompt.
