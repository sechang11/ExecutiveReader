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

_CRLF = re.compile(r"\r\n?")
# PDF tables of contents use dot leaders, often as spaced dots: "Title . . . 12".
# Left alone they arrive at the voice as a long run of full stops.
_TOC_LINE = re.compile(r"^([^\n]*?)[ \t]*\.(?:[ \t]*\.){3,}[ \t]*\d{1,4}[ \t]*$", re.M)
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



@lru_cache(maxsize=1)
def _owned_symbols() -> tuple:
    """Every character the shared symbols and currency lists claim.

    Those lists are the authority on what gets spoken, so nothing earlier in
    the pipeline may destroy one of their characters before they see it.
    """
    out = []
    for entry in list(shared_rules.symbols()) + list(shared_rules.currency()):
        ch = entry.get("symbol") or ""
        if ch:
            out.append(ch)
    return tuple(out)


#: Characters the collapse stage deliberately produces, which NFKC would then
#: undo. U+2026 is the whole reason the ellipsis rule exists, and NFKC expands
#: it straight back into three periods, which the repeat rule then turns into
#: one: the authored trailing-off restored and destroyed inside one function.
#:
#: The stage-by-stage harness could not see this. apply_collapse returned the
#: ellipsis correctly and all 510 comparisons passed. It showed up only by
#: timing the finished sentence through the real voice and finding the number
#: had not moved, which is the mirror of the four-dot difference that only a
#: stage comparison could see. Both checks are needed and neither substitutes.
_COLLAPSE_PRODUCES = ("…",)


@lru_cache(maxsize=1)
def _nfkc_shield() -> tuple:
    """Owned characters that NFKC would rewrite, each with a placeholder.

    NFKC turns U+2122 into the two letters TM, so the symbols stage never saw
    the character and "Widget(tm)" was read as "Widget T M" rather than
    "Widget trademark". The conformance corpus contains that exact string and
    passed, because the harness compares the symbols stage in isolation and
    never runs the whole normalizer.

    One character is affected today. The set is computed from the shared lists
    rather than written down, so adding a symbol cannot quietly bring the bug
    back.
    """
    pairs = []
    for index, ch in enumerate(_owned_symbols() + _COLLAPSE_PRODUCES):
        if unicodedata.normalize("NFKC", ch) != ch:
            pairs.append((ch, chr(0xE000 + index)))
    return tuple(pairs)


def _nfkc_preserving_symbols(text: str) -> str:
    shield = _nfkc_shield()
    for ch, placeholder in shield:
        text = text.replace(ch, placeholder)
    text = unicodedata.normalize("NFKC", text)
    for ch, placeholder in shield:
        text = text.replace(placeholder, ch)
    return text


# --- the shared collapse stage ------------------------------------------
#
# shared/normalization.json has carried a `collapse` section since the
# beginning and neither half read it. Both did some of it inline and
# inconsistently instead, which is how the two came to rewrite typographic
# punctuation differently: this half straightened curly quotes and the other
# did not, so the same page produced different text.
#
# The cost was not conformance. The phonemizer looks words up in CMUdict by
# literal text, so a curly apostrophe turns "don't" into two unknown tokens and
# the neural voices say "dawn tee". That is most contractions on most
# professionally typeset pages.
#
# `_collapse_rules` in the shared file defines every key, and `_order` fixes
# the order because two of them interact. Both are read here rather than
# restated, so a change to the contract does not need this file edited.

_COLLAPSE_FALLBACK = {
    "zero_width": True, "soft_hyphens": True, "smart_quotes_to_plain": True,
    "nbsp_to_space": True, "dashes_to_plain": True, "em_dash_to_comma": True,
    "footnote_markers": True, "dot_leaders": True,
    "repeated_punctuation": True, "emoji": "skip",
}
_ORDER_FALLBACK = ["zero_width", "soft_hyphens", "smart_quotes_to_plain",
                   "nbsp_to_space", "dashes_to_plain", "em_dash_to_comma",
                   "footnote_markers", "dot_leaders", "repeated_punctuation",
                   "emoji"]

_ZW = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_SOFT_HYPHEN = dict.fromkeys(map(ord, "­"), None)
_SMART_QUOTES = str.maketrans({
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
})
# Guillemets are deliberately absent: they are not English quoting and mapping
# them would be a guess.
_NBSP = str.maketrans({" ": " ", " ": " ", " ": " "})
_PLAIN_DASHES = str.maketrans({"–": "-", "‒": "-", "―": "-"})
# The spaces either side are absorbed on purpose. Replacing the character alone
# turns "left - quickly" into "left , quickly", with the comma floating off the
# word it belongs to.
_EM_DASH = re.compile(r"[ \t]*—[ \t]*")
_SUPERSCRIPT_DIGITS = dict.fromkeys(
    [0x00b9, 0x00b2, 0x00b3] + list(range(0x2070, 0x207a)), None)
# Four or more, so a genuine ellipsis is left to repeated_punctuation below.
_DOT_LEADER_RUN = re.compile(r"\.(?:[ \t]*\.){3,}")
#: Exactly three periods is an authored trailing-off and becomes U+2026.
#: Exactly two is a typo and becomes one period. Four or more never reach here,
#: because dot_leaders has already turned them into a space, which is why it has
#: to run first. The three-then-two ordering matters too: taking two first would
#: leave "..." as ".." rather than an ellipsis.
#:
#: This one is not tidying. Measured on five sentences on Microsoft David,
#: collapsing an authored "..." to a period adds about half a second of pause
#: every time, turning a trailing-off into a firmer stop than was written. That
#: voice cannot tell "..." from U+2026 at all, so nothing is won by choosing the
#: character; the gain is from no longer collapsing. U+2026 is the target
#: because it is a real token in the Kokoro vocabulary and free on the rest.
_ELLIPSIS_RUN = re.compile(r"\.{3}")
_DOUBLE_PERIOD = re.compile(r"\.{2}")
_BANG_QUESTION_RUN = re.compile(r"([!?])\1+")
# Python's re has no \p{Extended_Pictographic}, which is what the other half
# uses, so this is the pictographic planes plus the variation selector. It is
# narrower than the property: symbols the shared symbols list already handles,
# such as (c) and (R) and (TM), are outside it, which the rule requires anyway.
_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U0001f1e6-\U0001f1ff☀-➿️]+")


def _collapse_flags() -> dict:
    return shared_rules.collapse(_COLLAPSE_FALLBACK)


def apply_collapse(text: str) -> str:
    """Cleanup that must happen before anything matches literal text.

    Runs first, ahead of expansions, symbols and the dictionary, because every
    one of those compares against literal characters and so does CMUdict.
    """
    flags = _collapse_flags()
    for name in (shared_rules.collapse_order(_ORDER_FALLBACK) or _ORDER_FALLBACK):
        setting = flags.get(name)
        if not setting:
            continue
        if name == "zero_width":
            text = text.translate(_ZW)
        elif name == "soft_hyphens":
            text = text.translate(_SOFT_HYPHEN)
        elif name == "smart_quotes_to_plain":
            text = text.translate(_SMART_QUOTES)
        elif name == "nbsp_to_space":
            text = text.translate(_NBSP)
        elif name == "dashes_to_plain":
            text = text.translate(_PLAIN_DASHES)
        elif name == "em_dash_to_comma":
            text = _EM_DASH.sub(", ", text)
        elif name == "footnote_markers":
            # Superscripts, and bracketed markers now that the shared rule
            # covers them. Both halves argued the bracketed case in opposite
            # directions from what each engine "obviously" does, and both were
            # wrong until it was measured: on Microsoft David the marker costs
            # 0.205s of audible interruption mid-sentence, and on Kokoro it
            # produces no token at all. Removing it is better on one engine and
            # free on the other.
            #
            # This lived further down normalize() here, past every stage the
            # harnesses compare, which is precisely why the two halves could
            # each write the disagreement down and still never see it.
            text = text.translate(_SUPERSCRIPT_DIGITS)
            text = _FOOTNOTE.sub("", text)
        elif name == "dot_leaders":
            text = _DOT_LEADER_RUN.sub(" ", text)
        elif name == "repeated_punctuation":
            text = _ELLIPSIS_RUN.sub("…", text)
            text = _DOUBLE_PERIOD.sub(".", text)
            text = _BANG_QUESTION_RUN.sub(r"\1", text)
        elif name == "emoji" and setting == "skip":
            keep = set(_owned_symbols())
            # The shared lists are the authority on what gets spoken, so a
            # character they claim is never an emoji however it is
            # classified. Copyright, registered and trademark are all
            # Extended_Pictographic, and deleting them takes the words the
            # symbols list exists to produce with them.
            text = _EMOJI.sub(
                lambda m: "".join(c for c in m.group(0) if c in keep), text)
    return text


def normalize(text: str, *, skip_code: bool = False, urls: str = "domain") -> str:
    """Return text shaped for speech. Order matters: structure, then content."""
    if not text:
        return ""

    text = _CRLF.sub("\n", text)
    # A contents entry is a whole-line shape: title, leader, page number, end of
    # line. It has to be recognised before the shared dot_leaders rule turns the
    # leader into a space, because after that there is nothing left to tell a
    # contents line from a sentence with a number in it, and the page number
    # gets read aloud. This removes a number rather than transforming a leader,
    # so it is not the shared rule wearing a different name.
    text = _TOC_LINE.sub(r"\1.", text)
    text = _BARE_NUMBER_LINE.sub("", text)

    # Before NFKC, which turns a superscript digit into an ordinary one and so
    # would convert a footnote marker into content instead of removing it.
    text = apply_collapse(text)
    text = _nfkc_preserving_symbols(text)

    text = _FENCE.sub(" \n\n Code block. \n\n " if skip_code else " \n\n ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _TABLE_RULE.sub("", text)
    text = _MD_IMG.sub(lambda m: f" image, {m.group(1)} " if m.group(1) else " image ", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_HEAD.sub("", text)
    text = _MD_BULLET.sub("", text)
    text = _MD_EMPH.sub(r"\2", text)

    text = _urls(text, urls)

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
