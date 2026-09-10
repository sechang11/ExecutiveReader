"""User-defined pronunciation overrides.

Every engine mangles names, acronyms and jargon. This is the cheapest quality
lever in the whole app: one table the user edits, applied before synthesis.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import shared_rules

# Fallback for when shared/pronunciation.json is unavailable. The shared file
# is the source of truth; the Chrome extension reads a synced copy of it, so
# both halves say "engine ex" for nginx without anyone syncing by hand.
DEFAULTS: list[tuple[str, str]] = [
    ("nginx", "engine ex"), ("kubernetes", "koober netties"), ("kubectl", "koob control"),
    ("PostgreSQL", "postgres"), ("psql", "postgres"), ("SQL", "sequel"),
    ("PyTorch", "pie torch"), ("numpy", "num pie"), ("scipy", "sigh pie"),
    ("matplotlib", "mat plot lib"), ("Jupyter", "jupiter"), ("PyPI", "pie pea eye"),
    ("regex", "redge ex"), ("regexp", "redge ex"), ("async", "ay sink"),
    ("char", "care"), ("enum", "ee num"), ("tuple", "tupple"), ("cache", "cash"),
    ("Linux", "linnux"), ("sudo", "soo doo"), ("chmod", "ch mod"), ("ssh", "S S H"),
    ("GUI", "gooey"), ("JSON", "jay son"), ("YAML", "yammle"), ("JWT", "jot"),
    ("OAuth", "oh auth"), ("nan", "N A N"), ("i18n", "internationalization"),
    ("repo", "repo"), ("env", "environment"), ("config", "config"),
    ("LLM", "L L M"), ("API", "A P I"), ("CLI", "C L I"), ("UI", "U I"),
    ("URL", "U R L"), ("HTTP", "H T T P"), ("CSS", "C S S"), ("SVG", "S V G"),
]


@dataclass
class Rule:
    pattern: str
    replacement: str
    is_regex: bool = False
    enabled: bool = True


def default_rules() -> list[Rule]:
    """The shipped overrides, from shared/pronunciation.json where present."""
    return [Rule(pattern=entry["match"], replacement=entry["say"],
                 is_regex=entry["regex"])
            for entry in shared_rules.pronunciations(DEFAULTS)]


class Dictionary:
    def __init__(self, rules: list[Rule] | None = None) -> None:
        self._compiled: list[tuple[re.Pattern, str]] = []
        self.set_rules(rules if rules is not None else default_rules())

    def set_rules(self, rules: list[Rule]) -> None:
        self._compiled = []
        for rule in rules:
            if not rule.enabled or not rule.pattern:
                continue
            try:
                if rule.is_regex:
                    rx = re.compile(rule.pattern, re.I)
                else:
                    # Word-bounded so "cache" does not fire inside "cached".
                    rx = re.compile(rf"(?<!\w){re.escape(rule.pattern)}(?!\w)", re.I)
            except re.error:
                continue
            self._compiled.append((rx, rule.replacement))

    def apply(self, text: str) -> str:
        for rx, repl in self._compiled:
            text = rx.sub(lambda _m, r=repl: r, text)
        return text
