# Anchor resolution vocabulary

A contract between the two halves. Both implement it; neither owns it.

This exists because we shipped the same four label names meaning different
things, and each believed the other agreed. On one side a repeated sentence
resolved as `exact` and said nothing; on the other it resolved as `context` and
warned. Both were defensible in isolation. Together they would have described the
same page differently to the same person, and the bridge would have surfaced it
as a bug in whichever half spoke second.

Prose stays local — a tray notification and a side panel have different tone and
length budgets. The **situation** crosses. That means the names need definitions,
not just agreement that they exist.

## The five situations

Resolution tries them in this order and stops at the first that matches. The
order is from most to least certain, so an earlier match is never worse evidence
than a later one.

| Label | Condition | Certainty |
|---|---|---|
| `exact` | The saved quote appears **once**, character for character | The position is right |
| `ambiguous` | The quote appears **more than once**; neighbours chose which | The sentence is right, the copy is a judgment |
| `boundary` | No whole-sentence match, but the quote contains a current sentence or is contained by one | The text is right, the sentence edges moved |
| `fuzzy` | No containment; the best similarity score clears the acceptance threshold | Probably the right place |
| `hint` | Nothing clears the threshold; fall back to the recorded index | We do not know |

### exact

Exactly one sentence equals the quote. Say nothing to the user, whatever the rule
fingerprint says: if the sentence is present word for word, the position is
correct, and the fingerprint records provenance rather than correctness.

### ambiguous

Two or more sentences equal the quote. The quote alone cannot choose between
them, so the saved neighbours decide. Correct sentence, uncertain copy — a real
reduction in confidence that must not hide inside `exact`.

When the copies sit in **identical** surroundings, which generated tables and
repeated boilerplate produce routinely, the neighbours cannot decide either and
the tiebreak falls through to the recorded index. The label is still right and
the message is still honest, but nothing was actually chosen: do not describe
this outcome to the user as though context resolved it. Both implementations
behave this way; it was verified on each rather than assumed.

### boundary

The sentence was split, merged, extended, or trimmed since it was saved. Detected
by containment in either direction: the quote contains a current sentence (it was
split), or a current sentence contains the quote (it was merged, or a clause was
added). Requires the contained text to be substantial, so a short sentence like
"Yes." cannot match half the document.

This is a *more* precise finding than `fuzzy`, not a degraded one. It says the
words survived and only the punctuation between them moved, which is worth
telling the reader accurately rather than as a vague "the page changed".

Containment is tested in **both** directions, and both are confirmed on both
implementations:

| Shape | Relationship | Example |
|---|---|---|
| split | the quote holds a current sentence | "…sat down and then it slept" becomes two sentences |
| merge | a current sentence holds the quote | two sentences become one |

Comparing raw text finds neither. A split changes the punctuation at the seam,
so the substring test fails on the new full stop. Compare lowercased words with
punctuation stripped, and pad both sides when testing containment so "the cat sat
down" does not match inside "the cat sat downstream".

### fuzzy

No verbatim relationship survives, but something scores above the acceptance
threshold. Two distinct causes worth separating in the message: the page changed,
or our own rule files changed and rewrote the saved text. The rule fingerprint
distinguishes them.

### hint

Nothing scores above the threshold. The article was probably replaced. Fall back
to the recorded index, clamped to the document, and say so.

## Two thresholds, not one

Keep these separate. They want opposite properties, and a single number
conflates them.

- **Acceptance** decides whether any candidate is a match at all. Strict: a
  confident wrong answer is worse than admitting the sentence is gone, because
  the fallback is the recorded index, which is decent evidence.
- **Tie margin** ranks candidates already judged plausible. Forgiving: its job is
  to notice two candidates are too close to separate on score alone and hand the
  decision to context instead. Taking the first maximum is the obvious
  implementation and it is quietly wrong on duplicated sentences.

## verified

True only when `how` is `exact` **and** the stored fingerprint equals the current
one. It expresses provenance, and it may never contradict an exact match: use
`how` to decide what to tell the user, and `verified` only to decide how much to
trust a position elsewhere.

An **absent** fingerprint is unknown provenance, not changed provenance. A
position saved before stamping existed must never be reported as rewritten.

## Implement from this document, not from the other half's code

Porting the working implementation is faster and it defeats the purpose. The
second half inherits every choice the first made silently, including the ones the
document does not actually pin down, and the two agree by copying rather than by
agreeing.

Implementing from the prose forces a decision at each point the prose is
underspecified, which is what makes an ambiguity *visible* instead of inherited.
The both-directions question above surfaced exactly that way: one half asked
whether the other tested containment symmetrically, because the definition
covered both shapes in a single sentence and could honestly be read either way.
Copying the code would have hidden it. That is a better argument for keeping this
file than the drift it was originally written to fix.

## Changing this document

Both halves implement against it, so neither should edit it alone. Adding a label
is the expensive change: it means one side reports a situation the other folds
elsewhere, which is exactly the drift this document was written to end.
