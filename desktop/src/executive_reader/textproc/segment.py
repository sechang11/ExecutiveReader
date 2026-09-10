"""Split text into speakable segments.

Segments are roughly sentences. Short segments matter more than perfect
linguistics: they set how fast playback starts, how precise skip-back is, and
how tightly the highlight tracks the voice.
"""
from __future__ import annotations

import re

from . import shared_rules

# Abbreviations whose trailing period must not end a sentence. The live list
# comes from shared/abbreviations.json so the Chrome extension splits sentences
# exactly the same way; this set is the fallback when that file is not around.
_ABBREV_FALLBACK = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "rev", "hon",
    "inc", "ltd", "llc", "co", "corp", "dept", "est", "fig", "vol", "no",
    "vs", "etc", "eg", "ie", "al", "approx", "min", "max", "avg", "ref",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec", "mon", "tue", "wed", "thu", "fri", "sat", "sun",
    "u.s", "u.k", "e.g", "i.e", "a.m", "p.m", "ph.d", "b.sc", "m.sc",
}
_ABBREV = shared_rules.abbreviations(_ABBREV_FALLBACK)

#: A lone letter before the period is an initial, not a sentence end.
#: "J. R. R. Tolkien wrote it." is one sentence, and used to be three. The rule
#: has to outrank the looks-like-a-new-sentence rescue further down, because the
#: next initial is capitalised too and would otherwise read as a fresh sentence
#: every time. The extension already had this; the two halves disagreed on every
#: name written with initials.
_INITIAL = re.compile(r"(?:^|\s)[A-Za-z]$")

_SENT_END = re.compile(r"([.!?\u2026]+)([\"'\u201d\u2019\)\]]*)(\s+)")
_WORD_TAIL = re.compile(r"([A-Za-z][A-Za-z.]*)$")
_CLAUSE_SPLIT = re.compile(r"(?<=[,;:\u2014])\s+")


def _ends_abbrev(chunk: str) -> bool:
    m = _WORD_TAIL.search(chunk.rstrip(". "))
    if not m:
        return False
    return m.group(1).lower().strip(".") in _ABBREV


def _split_sentences(text: str) -> list[str]:
    out: list[str] = []
    start = 0
    for m in _SENT_END.finditer(text):
        end = m.end(2)
        candidate = text[start:end]
        head = text[start:m.start(1)]
        # "3.14" or "Dr." should not terminate a sentence.
        if head and head[-1].isdigit() and m.group(1) == "." and end < len(text) \
                and text[end:end + 2].strip()[:1].isdigit():
            continue
        if _INITIAL.search(head):
            continue
        if _ends_abbrev(head):
            continue
        nxt = text[m.end():m.end() + 1]
        if nxt and not (nxt.isupper() or nxt.isdigit() or nxt in "\"'\u201c\u2018([-\u2014"):
            continue
        piece = candidate.strip()
        if piece:
            out.append(piece)
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def _cap(segment: str, limit: int) -> list[str]:
    """Break an over-long sentence at clause boundaries, then at word boundaries."""
    if len(segment) <= limit:
        return [segment]
    pieces: list[str] = []
    buf = ""
    for clause in _CLAUSE_SPLIT.split(segment):
        if not clause:
            continue
        if buf and len(buf) + len(clause) + 1 > limit:
            pieces.append(buf.strip())
            buf = clause
        else:
            buf = f"{buf} {clause}".strip()
    if buf:
        pieces.append(buf.strip())

    final: list[str] = []
    for p in pieces:
        while len(p) > limit:
            cut = p.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            final.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            final.append(p)
    return final


def segment(text: str, max_chars: int = 320) -> list[str]:
    """Return speakable segments in reading order."""
    segments: list[str] = []
    # Blank lines are hard breaks: never merge across a paragraph boundary.
    for para in re.split(r"\n\s*\n+", text):
        para = para.strip()
        if not para:
            continue
        for line in para.split("\n"):
            line = line.strip()
            if not line:
                continue
            for sent in _split_sentences(line):
                segments.extend(_cap(sent, max_chars))
    return [s for s in segments if any(c.isalnum() for c in s)]
