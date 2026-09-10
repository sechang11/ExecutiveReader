# Executive Reader privacy policy

Last updated: 9 September 2026

Executive Reader reads web pages aloud. This policy describes every case where data
leaves your device, and there are only two.

## What Executive Reader stores, and where

All of it stays in your browser's own storage on this computer. None of it is
transmitted anywhere.

| Stored | Where | Why |
|---|---|---|
| Reading history: page address, title, how far you read, when | Extension storage | So you can pick up where you stopped |
| Extracted page text of pages you have read | Browser database (IndexedDB) | So history entries can show what they were |
| Settings: voice, speed, colours, retention | Extension storage | To remember your preferences |
| Downloaded voice model and voice packs | Browser cache | So natural voices work offline |

You can delete history at any time from the settings page or the side panel, and
remove the downloaded voice files from the same place. Removing the extension
deletes all of it.

## Where data leaves your device

**Two cases, both avoidable, both described plainly.**

### 1. System voices marked "needs a connection"

Some voices your operating system or browser provides are processed on a remote
server rather than on your computer. When you choose one, the text being read is
sent to whoever provides that voice — for most Chrome installations, Google —
under their privacy policy, not this one.

These voices are labelled **"needs a connection"** in the voice list. Voices
without that label run entirely on your device.

The natural voices Executive Reader downloads run entirely on your device and send
nothing, ever.

### 2. Downloading the voice model

If you turn on natural voices, Executive Reader downloads a voice model, roughly 88 MB,
plus about 510 KB for each voice you use. These come from Hugging Face.

That download tells Hugging Face your network address and that you requested
those files, in the same way visiting any website does. It contains nothing
about you, nothing about what you read, and it happens once. If you never turn
on natural voices, it never happens at all.

## What Executive Reader never does

- **No analytics, telemetry, or usage tracking.** None. There is no server to
  send it to.
- **No account.** There is nothing to sign in to.
- **No advertising, and no data sold or shared** with anyone.
- **No reading of pages you have not asked it to read.** Executive Reader only looks at a
  page when you press play, use a shortcut, or choose "Read this aloud".

## Permissions

Chrome shows a list of permissions at install. What each is actually for:

| Permission | Used for |
|---|---|
| Storage, unlimited storage | Keeping your history and settings on this device |
| Active tab, scripting | Reading the text of the page you asked to be read |
| Text to speech | Speaking with the voices your system provides |
| Offscreen | Keeping audio playing while you move between pages |
| Side panel | The reading panel and history list |
| Context menus | The "Read this aloud" right-click item |
| Access to websites | **Requested only when you turn on continuing to the next page.** It is not requested at install |

## Children

Executive Reader is not directed at children and collects no personal information from
anyone, of any age.

## Changes

If this policy changes, the updated version will be published with the extension
and the date above will change. A change that would cause data to leave your
device in a new way will be described in the extension itself before it takes
effect, not only here.

## Contact

Questions about this policy can be raised on the project's issue tracker.
