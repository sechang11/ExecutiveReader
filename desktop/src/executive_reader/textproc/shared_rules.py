"""Load the rule data that both halves of the project share.

`shared/*.json` is the source of truth for language data: which tokens end in a
period without ending a sentence, and how particular words should be said. The
Chrome extension reads a synced copy of the same files, so the two sides cannot
drift apart.

Engine code stays on each side. Only data crosses.

Everything here falls back to a built-in default when the files are absent, so
the desktop app still runs if it is ever packaged without the repository around
it.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


def shared_dir() -> Path | None:
    """Locate shared/ by walking up from this file.

    Layout is repo/shared and repo/desktop/src/executive_reader/textproc/, so the search
    starts four levels up and allows for the app being moved.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "shared"
        if (candidate / "abbreviations.json").is_file():
            return candidate
    return None


@lru_cache(maxsize=8)
def load(name: str) -> dict:
    """Read shared/<name>.json. Returns an empty dict when unavailable."""
    folder = shared_dir()
    if folder is None:
        return {}
    path = folder / (name + ".json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def abbreviations(fallback: set[str]) -> set[str]:
    """Every token that may end in a period without ending a sentence.

    Categories are unioned and lowercased; trailing periods are stripped so
    both "Ph.D" and "Ph.D." resolve to the same key.
    """
    data = load("abbreviations")
    if not data:
        return set(fallback)
    out: set[str] = set()
    for key, value in data.items():
        if key.startswith("_") or key == "notes" or not isinstance(value, list):
            continue
        for token in value:
            if isinstance(token, str) and token.strip():
                out.add(token.strip().rstrip(".").lower())
    # Never come back with less than we started with.
    return out | set(fallback)


def pronunciations(fallback: list[tuple[str, str]]) -> list[dict]:
    """Pronunciation overrides as {match, say, regex} records."""
    data = load("pronunciation")
    if not data:
        return [{"match": m, "say": s, "regex": False} for m, s in fallback]
    # A user entry must REPLACE the shipped one for the same word, not merely
    # be skipped as a duplicate. Skipping silently discards the override, so
    # the setting appears to save and does nothing. Ordering it first is not
    # enough either: rules apply in sequence, so a shipped rule further down
    # can match what the user's rule just produced and undo it.
    merged: dict[str, dict] = {}
    order: list[str] = []
    for group in ("builtin", "user"):
        for entry in data.get(group, []) or []:
            if not isinstance(entry, dict):
                continue
            match = str(entry.get("match") or "").strip()
            if not match:
                continue
            key = match.lower()
            if key not in merged:
                order.append(key)
            merged[key] = {"match": match,
                           "say": str(entry.get("say") or ""),
                           "regex": bool(entry.get("regex"))}
    rules = [merged[key] for key in order]
    if not rules:
        return [{"match": m, "say": s, "regex": False} for m, s in fallback]
    return rules


def expansions(fallback: dict[str, str]) -> list[dict]:
    """Say-as rules: what a token means when spoken.

    Lives in normalization.json rather than pronunciation.json because these
    change meaning, not sound, and both sides apply them before the
    pronunciation dictionary runs.
    """
    def _fallback() -> list[dict]:
        return [{"match": k, "say": v, "match_case": False}
                for k, v in fallback.items()]

    data = load("normalization").get("expansions")
    if not isinstance(data, list) or not data:
        return _fallback()
    out = []
    for entry in data:
        if isinstance(entry, dict) and entry.get("match"):
            out.append({"match": str(entry["match"]),
                        "say": str(entry.get("say") or ""),
                        # Off by default. Set on an entry that would otherwise
                        # eat an ordinary word, such as US for United States.
                        "match_case": bool(entry.get("matchCase"))})
    return out or _fallback()


def symbols() -> list[dict]:
    """Symbol readings, each gated by a named condition.

    The shared file carries no regular expressions on purpose, because Python
    and JavaScript disagree on lookbehind and group syntax. Each rule names a
    condition instead and each side builds its own matcher. Conditions are
    always, isolated, digit-before and digit-after.
    """
    data = load("normalization").get("symbols")
    if not isinstance(data, list):
        return []
    out = []
    for entry in data:
        if not isinstance(entry, dict) or not entry.get("symbol"):
            continue
        out.append({"symbol": str(entry["symbol"]),
                    "say": str(entry.get("say") or ""),
                    "when": str(entry.get("when") or "always")})
    return out


def currency() -> list[dict]:
    """Currency symbols, which move from prefix in text to suffix in speech."""
    data = load("normalization").get("currency")
    if not isinstance(data, list):
        return []
    out = []
    for entry in data:
        if not isinstance(entry, dict) or not entry.get("symbol"):
            continue
        singular = str(entry.get("singular") or "")
        out.append({"symbol": str(entry["symbol"]), "singular": singular,
                    "plural": str(entry.get("plural") or singular)})
    return out


def fingerprint() -> str:
    """Content hash of the shared rule data, or "" when unavailable.

    Stored alongside a reading position so a later edit to the shared rules is
    detectable rather than silent: the rules decide how a document is split
    into sentences, so changing them rewrites the very text a saved position
    was captured from. Generated by tools/sync-shared.mjs.
    """
    data = load("fingerprint")
    combined = data.get("combined")
    return str(combined) if isinstance(combined, str) else ""


def url_mode(default: str = "domain") -> str:
    urls = load("normalization").get("urls")
    if isinstance(urls, dict):
        mode = urls.get("mode")
        if mode in ("full", "domain", "skip"):
            return str(mode)
    return default


def describe() -> str:
    folder = shared_dir()
    if folder is None:
        return "shared/ not found; using built-in rule data"
    return "shared rules from " + str(folder)
