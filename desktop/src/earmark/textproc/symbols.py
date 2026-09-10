"""Turn symbols and currency into words, using the shared rule data.

The shared file states a condition by name rather than shipping a regular
expression, because Python and JavaScript disagree on lookbehind and group
syntax. This module is the Python half of that contract: it turns each named
condition into a matcher. The extension builds its own from the same names.

Conditions:

    always        every occurrence
    isolated      whitespace or a string edge on both sides, so R&D and C++
                  and key=value survive untouched
    digit-before  nearest preceding non-space character is a digit, as in 50%
    digit-after   nearest following non-space character is a digit, as in #3
"""
from __future__ import annotations

import re
from functools import lru_cache

from . import shared_rules

_NUMBER = r"\d[\d,]*(?:\.\d+)?"


def _matcher(symbol: str, when: str) -> re.Pattern | None:
    escaped = re.escape(symbol)
    if when == "always":
        return re.compile(escaped)
    if when == "isolated":
        return re.compile(r"(?<!\S)" + escaped + r"(?!\S)")
    if when == "digit-before":
        return re.compile(r"(?<=\d)[ \t]*" + escaped)
    if when == "digit-after":
        return re.compile(escaped + r"[ \t]*(?=\d)")
    return None


@lru_cache(maxsize=1)
def _symbol_rules() -> list[tuple[re.Pattern, str]]:
    rules = []
    for entry in shared_rules.symbols():
        pattern = _matcher(entry["symbol"], entry["when"])
        if pattern is None or not entry["say"]:
            continue
        rules.append((pattern, " " + entry["say"] + " "))
    return rules


@lru_cache(maxsize=1)
def _currency_rules() -> list[tuple[re.Pattern, str, str]]:
    rules = []
    for entry in shared_rules.currency():
        pattern = re.compile(re.escape(entry["symbol"]) + r"[ \t]*(" + _NUMBER + r")")
        rules.append((pattern, entry["singular"], entry["plural"]))
    return rules


def _is_one(amount: str) -> bool:
    try:
        return float(amount.replace(",", "")) == 1.0
    except ValueError:
        return False


def apply_currency(text: str) -> str:
    """$50 becomes 50 dollars, because the symbol leads in text but trails in
    speech. That reordering is why currency cannot be a plain symbol rule."""
    for pattern, singular, plural in _currency_rules():
        text = pattern.sub(
            lambda m: m.group(1) + " " + (singular if _is_one(m.group(1)) else plural),
            text)
    return text


def apply_symbols(text: str) -> str:
    """Replacements are padded on both sides, then tidied.

    The tidy belongs to this stage rather than to the caller, matching where
    the JavaScript side puts it, so the two agree when the symbol stage is
    compared on its own and not only at the end of the whole pipeline.
    """
    for pattern, replacement in _symbol_rules():
        text = pattern.sub(replacement, text)
    return tidy(text)


_SPACE_RUN = re.compile(r"[ \t]{2,}")


def tidy(text: str) -> str:
    """The shared output contract for the end of the symbol stage.

    Collapse runs of spaces and tabs to one, trim the ends, leave newlines
    alone. Both implementations pad replacements differently, so without this
    they produce the same speech from different strings and a cross-language
    check can only assert equivalence. It also keeps offset arithmetic honest:
    a stray double space shifts every position after it.
    """
    return _SPACE_RUN.sub(" ", text).strip(" \t")


def apply(text: str) -> str:
    """Currency first: it consumes the digits a symbol rule might otherwise claim."""
    return tidy(apply_symbols(apply_currency(text)))


def reload() -> None:
    _symbol_rules.cache_clear()
    _currency_rules.cache_clear()
