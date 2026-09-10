"""English grapheme-to-phoneme, permissively licensed.

CMUdict for the words it knows, letter-to-sound rules for everything else. This
is the default path precisely because it ships nothing copyleft: eSpeak covers
more languages and more words but is GPL-3.0, so it is an add-on the user
installs rather than something we distribute. See CAVEATS.md.

Output targets Kokoro's phoneme alphabet and deliberately imitates eSpeak's
conventions rather than correct notation: stress marks the vowel, not the
syllable onset, and long vowels carry length under stress. Kokoro was trained on
eSpeak-shaped input and rewards familiarity rather than correctness. A version
that notated stress properly sounded worse.

This is a port of the extension's g2p-en.js, deliberately identical so both
halves pronounce a word the same way. Divergence here would be audible and
unexplainable: the same reader saying a name two ways depending on which half
spoke.
"""
from __future__ import annotations

import gzip
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

# ARPAbet to Kokoro IPA. Verified against the model alphabet by verify().
ARPA = {
    "AA": "ɑ", "AE": "æ", "AH": "ʌ", "AO": "ɔ", "AW": "aʊ", "AY": "aɪ",
    "B": "b", "CH": "ʧ", "D": "d", "DH": "ð",
    "EH": "ɛ", "ER": "ɚ", "EY": "eɪ",
    "F": "f", "G": "ɡ", "HH": "h",
    "IH": "ɪ", "IY": "i", "JH": "ʤ",
    "K": "k", "L": "l", "M": "m", "N": "n", "NG": "ŋ",
    "OW": "oʊ", "OY": "ɔɪ",
    "P": "p", "R": "ɹ", "S": "s", "SH": "ʃ",
    "T": "t", "TH": "θ",
    "UH": "ʊ", "UW": "u",
    "V": "v", "W": "w", "Y": "j", "Z": "z", "ZH": "ʒ",
}

# Vowels eSpeak lengthens under stress, matched for the same reason.
LENGTHENED = {"AA": "ɑː", "AO": "ɔː", "IY": "iː", "UW": "uː"}

# --- homograph cues ------------------------------------------------------
# The failure data cannot fix. A dictionary has no context, so "he read the
# book" comes out as "reed" and adding entries never helps. These cues are a
# cheap approximation of part-of-speech tagging, covering what occurs in prose.

DETERMINERS = {
    "the", "a", "an", "this", "that", "these", "those", "his", "her", "its",
    "their", "my", "your", "our", "no", "any", "some", "each", "every",
    "another", "one", "two", "three", "more", "most", "such",
}
VERB_CUES = {
    "to", "will", "would", "can", "could", "shall", "should", "may", "might",
    "must", "i", "we", "you", "they", "he", "she", "it", "who", "and", "or",
    "not", "don't", "please", "help", "let",
}
PAST_CUES = {"have", "has", "had", "having", "already", "yesterday", "once",
             "never", "just"}

# Pairs whose two readings are not a stress shift at all.
SPECIAL = {
    "read": {"past": ["R", "EH1", "D"], "present": ["R", "IY1", "D"]},
    "lead": {"noun": ["L", "EH1", "D"], "verb": ["L", "IY1", "D"]},
    "live": {"adj": ["L", "AY1", "V"], "verb": ["L", "IH1", "V"]},
    "wind": {"noun": ["W", "IH1", "N", "D"], "verb": ["W", "AY1", "N", "D"]},
    "close": {"noun": ["K", "L", "OW1", "S"], "verb": ["K", "L", "OW1", "Z"]},
    "use": {"noun": ["Y", "UW1", "S"], "verb": ["Y", "UW1", "Z"]},
    "minute": {"noun": ["M", "IH1", "N", "AH0", "T"],
               "adj": ["M", "AY0", "N", "UW1", "T"]},
    "tear": {"noun": ["T", "IH1", "R"], "verb": ["T", "EH1", "R"]},
    "bow": {"noun": ["B", "OW1"], "verb": ["B", "AW1"]},
}

# --- letter-to-sound -----------------------------------------------------
# The honest weak point: proper nouns, product names and coinages land here and
# get an approximation. Ordered longest-first so digraphs win.
LTS = [
    ("tion", "ʃən"), ("sion", "ʒən"), ("ough", "ʌf"), ("augh", "æf"),
    ("tch", "ʧ"), ("dge", "ʤ"), ("igh", "aɪ"), ("ing", "ɪŋ"),
    ("ch", "ʧ"), ("sh", "ʃ"), ("th", "θ"), ("ph", "f"), ("wh", "w"),
    ("ck", "k"), ("qu", "kw"), ("ng", "ŋ"), ("gh", "ɡ"),
    ("ee", "iː"), ("ea", "iː"), ("oo", "uː"), ("ou", "aʊ"), ("ow", "aʊ"),
    ("ai", "eɪ"), ("ay", "eɪ"), ("oi", "ɔɪ"), ("oy", "ɔɪ"), ("au", "ɔː"),
    ("a", "æ"), ("e", "ɛ"), ("i", "ɪ"), ("o", "ɑ"), ("u", "ʌ"), ("y", "i"),
    ("b", "b"), ("c", "k"), ("d", "d"), ("f", "f"), ("g", "ɡ"), ("h", "h"),
    ("j", "ʤ"), ("k", "k"), ("l", "l"), ("m", "m"), ("n", "n"), ("p", "p"),
    ("r", "ɹ"), ("s", "s"), ("t", "t"), ("v", "v"), ("w", "w"), ("x", "ks"),
    ("z", "z"),
]

# Letters that survive neither decomposition nor an ASCII lookup. Stripping
# combining marks turns "é" into "e", but "ł" carries its stroke inside the
# codepoint. Left alone it falls out of a letter match and splits the word:
# "Skłodowska" arrives as two fragments, each given a confident wrong reading.
FOLD = {
    "ł": "l", "Ł": "l", "ø": "o", "Ø": "o", "đ": "d", "Đ": "d",
    "ð": "th", "Ð": "th", "þ": "th", "Þ": "th", "ß": "ss",
    "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe", "ı": "i",
}

_PUNCT_TOKENS = '.,;:!?—…"()'
# Letters and combining marks, plus the apostrophe. Written without \p{L}
# because Python's re has no Unicode property escapes: [^\W\d_] is letters,
# and Mn/Mc marks are matched by \w so they are admitted explicitly.
_TOKEN = re.compile(r"[^\W\d_][\w'̀-ͯ]*|['][^\W\d_][\w']*"
                    r"|[" + re.escape(_PUNCT_TOKENS) + r"]")
_COMBINING = re.compile(r"[̀-ͯ҃-҉֑-ֽ]")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?…])")
_STRESS_DIGIT = re.compile(r"\d$")


#: Where the dictionary may live, canonical first.
#:
#: shared/ holds the original. The extension keeps a synced copy because Chrome
#: can only package files inside an extension's own directory, and that copy is
#: the last resort here rather than the first: reading it would mean reading a
#: duplicate that can go stale while believing it was the source.
_DICT_LOCATIONS = (("shared", "cmudict"),
                   ("vendor", "cmudict"),
                   ("extension", "vendor", "cmudict"))


def vendor_dir() -> Path | None:
    """Find the pronunciation dictionary by walking up from this file.

    Both halves read one dictionary on purpose: two would drift and the same
    word would be said two ways, which is a defect nobody can diagnose from
    the outside.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        for parts in _DICT_LOCATIONS:
            candidate = parent.joinpath(*parts)
            if (candidate / "cmudict.txt.gz").is_file():
                return candidate
    return None


@lru_cache(maxsize=1)
def _dictionary() -> dict[str, list[str]]:
    folder = vendor_dir()
    if folder is None:
        return {}
    try:
        raw = gzip.decompress((folder / "cmudict.txt.gz").read_bytes())
    except OSError:
        return {}
    out: dict[str, list[str]] = {}
    for line in raw.decode("utf-8", "replace").split("\n"):
        space = line.find(" ")
        if space < 1:
            continue
        out[line[:space]] = line[space + 1:].split(" ")
    return out


@lru_cache(maxsize=1)
def _homographs() -> dict[str, dict[str, list[str]]]:
    folder = vendor_dir()
    if folder is None:
        return {}
    path = folder / "homographs.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def available() -> bool:
    return vendor_dir() is not None


def fold(word: str) -> str:
    """Reduce a word to the ASCII letters the dictionary understands."""
    decomposed = unicodedata.normalize("NFD", word)
    stripped = _COMBINING.sub("", decomposed)
    return "".join(FOLD.get(ch, ch) for ch in stripped).lower()


def disambiguate(word: str, prev: str | None) -> list[str] | None:
    """Choose between readings using the preceding word, or None to defer."""
    special = SPECIAL.get(word)
    if special is not None:
        if word == "read":
            # The dictionary's own first entry is the past tense, so leaving it
            # alone gets "please read" wrong. Both readings need a cue, and a
            # genuinely ambiguous context defers rather than guesses.
            if prev in PAST_CUES:
                return special["past"]
            if prev in VERB_CUES:
                return special["present"]
            return None
        if word == "live":
            return special["adj"] if prev in DETERMINERS else special["verb"]
        if prev and prev in DETERMINERS:
            return special.get("noun")
        if prev and prev in VERB_CUES:
            return special.get("verb")
        return None

    pair = _homographs().get(word)
    if not pair or not prev:
        return None
    if prev in DETERMINERS:
        return pair.get("noun")
    if prev in VERB_CUES:
        return pair.get("verb")
    # No cue either way: the dictionary's own entry beats a guess.
    return None


def to_ipa(phones: list[str]) -> str:
    out = []
    for phone in phones:
        bare = _STRESS_DIGIT.sub("", phone)
        match = _STRESS_DIGIT.search(phone)
        stress = match.group(0) if match else None
        if stress == "1":
            out.append("ˈ")
        elif stress == "2":
            out.append("ˌ")

        if bare == "AH" and stress == "0":
            out.append("ə")           # unstressed AH is a schwa
        elif stress != "0" and bare in LENGTHENED:
            out.append(LENGTHENED[bare])
        else:
            out.append(ARPA.get(bare, ""))
    return "".join(out)


def sound(word: str) -> str:
    """Letter-to-sound fallback for a word the dictionary lacks."""
    out = []
    i = 0
    length = len(word)
    while i < length:
        # Trailing silent e: "name" is not "nameh".
        if i == length - 1 and word[i] == "e" and length > 3:
            break
        for grapheme, phoneme in LTS:
            if word.startswith(grapheme, i):
                out.append(phoneme)
                i += len(grapheme)
                break
        else:
            i += 1
    return ("ˈ" + "".join(out)) if out else ""


def phonemize(text: str) -> tuple[str, list[str]]:
    """Return (phonemes, words the dictionary did not have)."""
    misses: list[str] = []
    out: list[str] = []
    prev: str | None = None

    for token in _TOKEN.findall(text):
        if len(token) == 1 and token in _PUNCT_TOKENS:
            out.append(token)
            continue
        key = fold(token)
        if not key:
            continue                  # a script we cannot fold to letters
        forced = disambiguate(key, prev)
        phones = forced if forced is not None else _dictionary().get(key)
        if phones:
            out.append(to_ipa(phones))
        else:
            misses.append(token)
            out.append(sound(key))
        prev = key

    return _SPACE_BEFORE_PUNCT.sub(r"\1", " ".join(out)), misses


def verify(vocab) -> list[str]:
    """Symbols we can emit that the model's alphabet lacks.

    Anything missing is dropped silently at tokenization, so the word loses a
    sound and nothing reports it.
    """
    bad = set()
    everything = (list(ARPA.values()) + list(LENGTHENED.values())
                  + [p for _g, p in LTS] + ["ə", "ˈ", "ˌ"])
    for value in everything:
        for ch in value:
            if ch not in vocab:
                bad.add(ch)
    return sorted(bad)
