"""Clean captured text before it reaches a voice.

Most of the perceived quality gap between text-to-speech tools lives here
rather than in the model. Raw page text is full of things that sound wrong
when spoken literally: navigation crumbs, markdown syntax, bare URLs,
footnote markers and runs of punctuation.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from . import shared_rules, symbols

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)
_QUOTES = str.maketrans({
    "\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'",
    "\u2013": "-", "\u2014": ", ", "\u00a0": " ",
})

_CRLF = re.compile(r"\r\n?")
# PDF tables of contents use dot leaders, often as spaced dots: "Title . . . 12".
# Left alone they arrive at the voice as a long run of full stops.
_TOC_LINE = re.compile(r"^([^\n]*?)[ \t]*\.(?:[ \t]*\.){3,}[ \t]*\d{1,4}[ \t]*$", re.M)
_DOT_LEADER = re.compile(r"[ \t]*\.(?:[ \t]*\.){3,}[ \t]*")
_BARE_NUMBER_LINE = re.compile(r"^[ \t]*\d{1,4}[ \t]*$", re.M)

# PDFs and emails hard-wrap prose mid-sentence. Rejoin those lines, or every
# wrap becomes a false sentence boundary and the voice stops in the wrong place.
_HYPHEN_WRAP = re.compile(r"([a-z])-\n([a-z])")
_SOFT_WRAP = re.compile(r"([a-z,;:])\n(?=[a-z(])")
# A short line with no terminator followed by a capital really is a heading.
_HEADING_LINE = re.compile(r"^([^\n]{1,70}[A-Za-z0-9\"')\]])\n(?=[A-Z])", re.M)

_FENCE = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_MD_HEAD = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_MD_BULLET = re.compile(r"^\s*[-*+\u2022]\s+", re.M)
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((?:[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_MD_IMG = re.compile(r"!\[([^\]\n]*)\]\([^)]*\)")
_MD_EMPH = re.compile(r"(\*{1,3}|_{1,3})(\S.*?\S|\S)\1")
_URL = re.compile(r"\bhttps?://([^\s/]+)(/\S*)?|\bwww\.([^\s/]+)(/\S*)?")
_FOOTNOTE = re.compile(r"\[\s*\d{1,3}\s*\]")
_PUNCT_RUN = re.compile(r"([!?.,;:\-])\1{2,}")
_MULTISPACE = re.compile(r"[ \t]{2,}")
_SPACE_PUNCT = re.compile(r"[ \t]+([,.;:!?])")
_BLANK_LINE = re.compile(r"^[ \t]+$", re.M)
_BLANKLINES = re.compile(r"\n{3,}")
_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]{6,}\|?\s*$", re.M)

# Say-as expansion comes from shared/normalization.json so the extension does
# the same thing. This dict is only the fallback for when that file is absent.
_SHARED_FALLBACK = {
    "e.g.": "for example", "i.e.": "that is", "etc.": "et cetera",
    "vs.": "versus", "approx.": "approximately", "et al.": "and others",
    "cf.": "compare", "N.B.": "note well", "w/o": "without", "w/": "with",
    "b/c": "because", "n/a": "not applicable", "->": "to", "=>": "to",
    ">=": "greater than or equal to", "<=": "less than or equal to",
}

# A letter or digit, underscore excluded, matching the shared boundary rule.
_ALNUM = r"[^\W_]"


@lru_cache(maxsize=1)
def _expansion_rules() -> list[tuple[str, re.Pattern, str]]:
    """Compile the shared say-as list under its three matching rules.

    Returns (literal, pattern, replacement) triples, longest literal first so
    "w/o" is tried before "w/" and wins. The literal is carried along so the
    ordering contract can be asserted directly rather than inferred from the
    compiled pattern, whose length changes with escaping.

    Matches are boundary-rejected: one adjacent to a letter or digit does not
    fire, which keeps "->" out of node->next and "w/" out of xw/y while still
    catching "A -> B". Case-insensitive unless an entry opts out.
    """
    entries = sorted(shared_rules.expansions(_SHARED_FALLBACK),
                     key=lambda e: len(e["match"]), reverse=True)
    rules = []
    for entry in entries:
        literal = entry["match"]
        if not literal:
            continue
        pattern = ("(?<!" + _ALNUM + ")" + re.escape(literal)
                   + "(?!" + _ALNUM + ")")
        flags = 0 if entry.get("match_case") else re.I
        rules.append((literal, re.compile(pattern, flags), entry["say"]))
    return rules


def apply_expansions(text: str) -> str:
    """The say-as stage on its own: i.e. becomes that is, w/ becomes with.

    Public so the cross-language conformance harness can compare this stage
    directly instead of reaching into the compiled rule tuples, whose shape is
    an implementation detail and free to change.
    """
    for _literal, pattern, replacement in _expansion_rules():
        text = pattern.sub(replacement, text)
    return text


def reload_rules() -> None:
    """Pick up edits to shared/normalization.json without a restart."""
    _expansion_rules.cache_clear()
    shared_rules.load.cache_clear()
    symbols.reload()


def _urls(text: str, mode: str) -> str:
    def repl(m: re.Match) -> str:
        if mode == "skip":
            return " link "
        host = m.group(1) or m.group(3) or ""
        host = host.lower().removeprefix("www.")
        if mode == "domain":
            return f" link to {host} "
        return m.group(0)
    return _URL.sub(repl, text)


def normalize(text: str, *, skip_code: bool = False, urls: str = "domain") -> str:
    """Return text shaped for speech. Order matters: structure, then content."""
    if not text:
        return ""

    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_ZERO_WIDTH).translate(_QUOTES)
    text = _CRLF.sub("\n", text)

    # Contents entries become just their title; the page number is a reference,
    # not something to say. Any other leaders collapse to a pause.
    text = _TOC_LINE.sub(r"\1.", text)
    text = _DOT_LEADER.sub(". ", text)
    text = _BARE_NUMBER_LINE.sub("", text)

    text = _FENCE.sub(" \n\n Code block. \n\n " if skip_code else " \n\n ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _TABLE_RULE.sub("", text)
    text = _MD_IMG.sub(lambda m: f" image, {m.group(1)} " if m.group(1) else " image ", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_HEAD.sub("", text)
    text = _MD_BULLET.sub("", text)
    text = _MD_EMPH.sub(r"\2", text)

    text = _urls(text, urls)
    text = _FOOTNOTE.sub("", text)

    text = apply_expansions(text)
    text = symbols.apply(text)

    text = _PUNCT_RUN.sub(r"\1", text)
    text = _MULTISPACE.sub(" ", text)
    # Stripped footnotes and code fences leave orphaned space and
    # whitespace-only lines; tidy both before the splitter sees them.
    text = _SPACE_PUNCT.sub(r"\1", text)
    # Closing the gaps can re-form a run (". . ." becomes "..."), so collapse
    # repeats once more now that spacing is settled.
    text = _PUNCT_RUN.sub(r"\1", text)
    text = _BLANK_LINE.sub("", text)
    text = _BLANKLINES.sub("\n\n", text)

    # Undo hard wrapping first, then give real headings a terminator so they do
    # not run into the paragraph below them as one breathless sentence.
    text = _HYPHEN_WRAP.sub(r"\1\2", text)
    text = _SOFT_WRAP.sub(r"\1 ", text)
    text = _HEADING_LINE.sub(r"\1.\n", text)
    return text.strip()
