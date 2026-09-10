"""Position bookmarks that survive the page changing.

A segment index breaks the moment a page gains a paragraph or a PDF is
re-exported. Instead each bookmark stores the sentence itself plus a little
context either side, the same shape the W3C text-quote selector uses, and the
position is found again by matching that text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: How much neighbouring text an anchor stores either side of its sentence.
#:
#: Deliberately not pinned by a test, and listed here rather than left looking
#: covered. Separating 48 from 96 needs a fixture where the shorter window
#: catches one neighbouring sentence and the longer catches two, and the two
#: disagree about which copy to resolve to. That test would be pinning its own
#: fixture rather than the judgment, which is worse than an honest gap.
_CONTEXT = 48
_WS = re.compile(r"\s+")

# Two thresholds doing opposite jobs. They were both bare numbers inline, which
# invited someone to nudge one thinking they were tuning the other.
#
# ACCEPT decides whether any candidate is real at all, and wants to be strict:
# below it, say the sentence is gone rather than jump somewhere confidently
# wrong. A wrong position delivered without doubt is worse than admitting the
# position was lost.
ACCEPT = 0.5
# TIE_BAND decides whether two candidates are too close to separate on score,
# and wants to be forgiving: its job is to hand the decision to the surrounding
# text rather than to a third decimal place.
TIE_BAND = 0.05

# The five situations from docs/anchor-vocabulary.md, a contract shared with
# the extension. Worst to best. Neither half may add one alone: a new label
# means one side reports a situation the other folds elsewhere, which is the
# drift the document exists to end.
HINT = "hint"            # nothing cleared acceptance; fell back to the index
FUZZY = "fuzzy"          # no verbatim relationship, but a score cleared it
BOUNDARY = "boundary"    # the words survived; the sentence edges moved
AMBIGUOUS = "ambiguous"  # the quote appears more than once; neighbours chose
EXACT = "exact"          # the quote appears exactly once, character for character

# Boundary detection compares words, not raw text. A split changes the
# punctuation at the seam, so "the cat sat down and then it slept" becomes
# "The cat sat down." plus "And then it slept." and a raw substring test fails
# on the added period.
_PUNCT = re.compile(r"[^\w\s]+")
# Substantiality, counted in words. A character floor high enough to reject
# "Yes." also rejects "The cat sat down.", which is a perfectly good half of a
# split sentence. The character minimum only guards pathological input.
MIN_BOUNDARY_WORDS = 4
MIN_BOUNDARY_CHARS = 12


def _words(text: str) -> str:
    """Lowercased words, punctuation stripped, padded for whole-word matching.

    The padding matters: without it "the cat sat down" is a substring of "the
    cat sat downstream today" purely because a word got extended, which is a
    different sentence rather than a moved boundary.
    """
    return " " + " ".join(_PUNCT.sub(" ", text).lower().split()) + " "


def _substantial(padded: str) -> bool:
    return (len(padded.split()) >= MIN_BOUNDARY_WORDS
            and len(padded.strip()) >= MIN_BOUNDARY_CHARS)


def _contains(outer: str, inner: str) -> bool:
    """Whole-word containment, only for a substantial inner phrase."""
    return _substantial(inner) and inner in outer


@dataclass(frozen=True)
class Match:
    index: int
    how: str
    #: True only when the quote matched verbatim AND the shared rules that
    #: produced it are unchanged. Anything else is a best effort.
    verified: bool = False

    @property
    def approximate(self) -> bool:
        return not self.verified


def _flat(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


@dataclass
class Anchor:
    exact: str
    prefix: str = ""
    suffix: str = ""
    index_hint: int = 0

    @classmethod
    def create(cls, segments: list[str], index: int) -> "Anchor":
        if not segments:
            return cls(exact="", index_hint=0)
        index = max(0, min(index, len(segments) - 1))
        before = " ".join(segments[max(0, index - 2):index])
        after = " ".join(segments[index + 1:index + 3])
        return cls(exact=segments[index],
                   prefix=before[-_CONTEXT:],
                   suffix=after[:_CONTEXT],
                   index_hint=index)

    def locate(self, segments: list[str], rules_changed: bool = False) -> "Match":
        """Find this anchor, reporting how it was found and whether to trust it.

        `rules_changed` says the shared rule data has been edited since the
        position was captured, so the stored quote may have been rewritten.
        It deliberately does NOT skip the verbatim comparison: most sentences
        in a document are untouched by a rule change, an exact hit on one of
        them is still correct, and skipping it loses the context tiebreak that
        distinguishes repeated sentences. It downgrades confidence instead.
        """
        if not segments:
            return Match(0, HINT, verified=False)
        fallback = min(max(self.index_hint, 0), len(segments) - 1)
        if not self.exact.strip():
            return Match(fallback, HINT, verified=False)

        target = _flat(self.exact)
        exact_hits = [i for i, s in enumerate(segments) if _flat(s) == target]
        if len(exact_hits) == 1:
            # Present character for character, once. Correct whatever the
            # fingerprint says, which records provenance rather than position.
            return Match(exact_hits[0], EXACT, verified=not rules_changed)
        if exact_hits:
            # Right sentence, but the quote alone cannot say which copy, so the
            # neighbours decide. A real drop in confidence that must not hide
            # inside exact.
            return Match(self._by_context(segments, exact_hits), AMBIGUOUS,
                         verified=False)

        quote = _words(self.exact)
        boundary = [i for i, s in enumerate(segments)
                    if _contains(quote, _words(s)) or _contains(_words(s), quote)]
        if boundary:
            # The words survived; only the edges moved. More precise than
            # fuzzy, not a degraded form of it.
            return Match(self._by_context(segments, boundary), BOUNDARY,
                         verified=False)

        # The sentence itself changed. Score every segment, then break ties on
        # context, because taking the first maximum picks the wrong copy when a
        # sentence repeats.
        scored = [(_overlap(target, _flat(seg)), i)
                  for i, seg in enumerate(segments)]
        best_score = max((s for s, _i in scored), default=0.0)
        if best_score < ACCEPT:
            return Match(fallback, HINT, verified=False)
        near = [i for score, i in scored if score >= best_score - TIE_BAND]
        index = near[0] if len(near) == 1 else self._by_context(segments, near)
        return Match(index, FUZZY, verified=False)

    def resolve(self, segments: list[str], rules_changed: bool = False) -> int:
        """Best matching segment index. See locate() for the confidence."""
        return self.locate(segments, rules_changed).index

    def _by_context(self, segments: list[str], candidates: list[int]) -> int:
        """Pick between repeated sentences using the surrounding text."""
        want_prefix, want_suffix = _flat(self.prefix), _flat(self.suffix)
        best_i, best_score = candidates[0], -1.0
        for i in candidates:
            before = _flat(" ".join(segments[max(0, i - 2):i]))[-_CONTEXT:]
            after = _flat(" ".join(segments[i + 1:i + 3]))[:_CONTEXT]
            score = _overlap(want_prefix, before) + _overlap(want_suffix, after)
            # Nudge towards where it used to be when context ties.
            score -= abs(i - self.index_hint) * 0.001
            if score > best_score:
                best_i, best_score = i, score
        return best_i

    def to_dict(self) -> dict:
        return {"exact": self.exact, "prefix": self.prefix,
                "suffix": self.suffix, "index_hint": self.index_hint}

    @classmethod
    def from_dict(cls, data: dict) -> "Anchor":
        return cls(exact=data.get("exact", ""), prefix=data.get("prefix", ""),
                   suffix=data.get("suffix", ""),
                   index_hint=int(data.get("index_hint") or 0))


def _overlap(a: str, b: str) -> float:
    """Share of a's words that appear in b, from 0 to 1."""
    if not a or not b:
        return 0.0
    aw, bw = set(a.split()), set(b.split())
    if not aw:
        return 0.0
    return len(aw & bw) / len(aw)
