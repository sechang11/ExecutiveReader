"""Watch a rectangle of the screen and read what appears in it.

The window watcher reads text an application exposes. This reads pixels, so it
works on anything at all: a game, a video, a remote desktop, a PDF in a viewer
that refuses to cooperate. The cost is the usual one for recognition. It sees
only what is visible and it sometimes gets a word wrong.

Two things make it usable rather than a novelty.

Recognition returns a bounding box per word, so the words being spoken can be
drawn on screen. That is the only route in the whole app that can highlight
what it is reading, because it is the only one that knows where the text is.

And a capture is kept rather than spoken and forgotten. Each one is a row you
can come back to, which matters because recognition is the rung most likely to
produce something you want to re-read rather than something you want repeated.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import ocr

#: How often to look at the rectangle. Recognition is far more expensive than
#: an accessibility walk, so this is slower than the window watcher on purpose.
DEFAULT_INTERVAL = 2.0

#: Below this, a capture is a stray word or a misread edge and not worth
#: interrupting anything for.
MIN_CHARS = 20

#: How many lines have to be shared before a capture counts as carrying on
#: from the one before it rather than being a different thing to read.
_MIN_OVERLAP_LINES = 2

#: Recognition failing once is a flicker, a screen lock or a window moving
#: over the area. Failing this many times running is something to say out
#: loud, because the symptom is silence and silence looks like working.
_FAILURES_BEFORE_SAYING = 3


@dataclass
class Word:
    text: str
    left: int
    top: int
    width: int
    height: int


@dataclass
class Capture:
    """One recognition of the rectangle, kept so it can be re-read."""
    text: str
    when: float = field(default_factory=time.time)
    words: list = field(default_factory=list)
    #: True when this carries on from the capture before it, which happens
    #: whenever the page is scrolled while it is being read.
    continues: bool = False

    @property
    def preview(self) -> str:
        flat = " ".join(self.text.split())
        return flat[:90] + ("..." if len(flat) > 90 else "")


#: A line with at least this many tokens is judged on its shape. Below it,
#: "a b" is more likely to be real text than a misread.
_SHAPE_MIN_TOKENS = 3

#: Above this share of single-character tokens the line is not language. Icons,
#: bullets, checkboxes and list markers all come back from recognition as runs
#: of lone letters, which is where "O O O O O O" comes from.
_SINGLE_TOKEN_SHARE = 0.6

#: A line needs this share of letters and digits to be worth saying. Below it
#: the line is box-drawing, punctuation or recognition noise.
_ALNUM_SHARE = 0.4


def is_gibberish(line: str) -> bool:
    """True when a recognised line is not language.

    Recognition returns something for every mark on screen, so icons, bullets,
    borders and checkboxes arrive as lines of lone letters and punctuation.
    Spoken, they are a stream of single letters, which is the loudest possible
    way to be wrong.

    Shape is what separates them, not a word list: real prose has multi-letter
    words and is mostly letters, and none of that depends on the language or
    on which icon set an application happens to use.
    """
    stripped = line.strip()
    if not stripped:
        return True
    letters = sum(1 for ch in stripped if ch.isalnum())
    if letters / len(stripped) < _ALNUM_SHARE:
        return True
    tokens = stripped.split()
    if len(tokens) >= _SHAPE_MIN_TOKENS:
        singles = sum(1 for token in tokens if len(token.strip(".,;:!?-")) <= 1)
        if singles / len(tokens) > _SINGLE_TOKEN_SHARE:
            return True
    return False


def clean(text: str) -> str:
    """Drop the lines of a recognition that are not language."""
    return chr(10).join(line for line in (text or "").splitlines()
                        if not is_gibberish(line))


def read_region(rect: tuple) -> Capture:
    """Recognise one rectangle. Returns text and where each word sits.

    Coordinates come back in screen space, already offset by the rectangle's
    own position, so an overlay can draw them without knowing about the
    rectangle at all.
    """
    left, top, _width, _height = rect
    image = ocr._grab(rect)
    result = _recognize(image)
    words: list[Word] = []
    for line in result.get("lines") or []:
        for word in (line.get("words") if isinstance(line, dict) else None) or []:
            box = word.get("bounding_rect") or {}
            words.append(Word(
                text=word.get("text", ""),
                left=int(left + box.get("x", 0)),
                top=int(top + box.get("y", 0)),
                width=int(box.get("width", 0)),
                height=int(box.get("height", 0))))
    text = "\n".join(
        (line.get("text") if isinstance(line, dict) else "") or ""
        for line in result.get("lines") or []).strip()
    body = clean(text or (result.get("text") or "").strip())
    # Words that survive the clean, so the highlight cannot land on a line the
    # reader never says.
    kept = {w.strip().lower() for line in body.splitlines() for w in line.split()}
    words = [w for w in words
             if "".join(c for c in w.text.lower() if c.isalnum())
             in {"".join(c for c in k if c.isalnum()) for k in kept}]
    return Capture(text=body, words=words)


def _recognize(image) -> dict:
    try:
        import winocr
    except ImportError as exc:
        raise ocr.OCRUnavailable(
            "winocr is not installed. Run: pip install winocr") from exc
    try:
        result = winocr.recognize_pil_sync(image, "en")
    except Exception as exc:
        raise ocr.OCRUnavailable("Windows OCR failed: " + str(exc)) from exc
    return result if isinstance(result, dict) else {"text": getattr(result, "text", "")}


def _key(text: str) -> str:
    """What counts as the same capture. Recognition is not deterministic at the
    edges, so comparing raw text reports a change every few seconds on a static
    screen; comparing the letters alone does not."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def continuation(previous: str, current: str) -> str | None:
    """The part of `current` that carries on from `previous`, if it does.

    Scrolling a page the reader is already working through produces a capture
    that overlaps the last one: the lines that were at the bottom are now at
    the top. Treated as a fresh capture that becomes a new row saying mostly
    what the previous row already said, and the reading restarts from text you
    have already heard.

    Recognised by the overlap itself. If any run of lines at the end of the
    previous capture appears at the start of this one, the rest is new and
    belongs on the end of what is already there. Returns None when the two
    share nothing, which is a genuinely different thing to read.

    Compared on letters alone, for the same reason the change check is:
    recognition is not repeatable at the edges. The identical line of pixels
    read twice comes back as "Il" and "II", or with a comma that appears and
    disappears. Compared as written, an overlap like that goes unnoticed and
    the scroll becomes a new row repeating what was just read.
    """
    old_lines = [line for line in (previous or "").splitlines() if _key(line)]
    new_lines = [line for line in (current or "").splitlines() if _key(line)]
    if not old_lines or not new_lines:
        return None
    old_keys = [_key(line) for line in old_lines]
    new_keys = [_key(line) for line in new_lines]
    limit = min(len(old_lines), len(new_lines))
    # Longest overlap first: a short one is more likely to be a coincidence.
    for size in range(limit, 0, -1):
        if old_keys[-size:] == new_keys[:size]:
            # One shared line is not evidence of a scroll. Applications keep
            # fixed furniture inside a watched area -- a heading, a footer, a
            # prompt box -- and it matches whatever else is on screen. Taking
            # that as a continuation appends a different conversation to the
            # clipboard of the last one instead of starting a new one, which
            # is exactly what it looked like when switching conversations
            # produced no new row. Total containment still counts: everything
            # that was there is still there.
            if size < _MIN_OVERLAP_LINES and size < limit:
                return None
            rest = new_lines[size:]
            return chr(10).join(rest) if rest else ""
    return None


class RegionWatcher:
    """Recognise a rectangle on a timer and report genuinely new text."""

    def __init__(self, rect: tuple,
                 on_capture: Callable[[Capture], None],
                 interval: float = DEFAULT_INTERVAL,
                 min_chars: int = MIN_CHARS) -> None:
        self.rect = rect
        self.on_capture = on_capture
        self.interval = max(0.5, float(interval))
        self.min_chars = min_chars
        self.on_error: Callable[[str], None] = lambda _m: None

        self._last_key = ""
        self._last_text = ""
        self._failures = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="executive-reader-region")
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if (thread is not None and thread.is_alive()
                and thread is not threading.current_thread()):
            thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._look()
                self._failures = 0
            except ocr.OCRUnavailable as exc:
                self.on_error(str(exc))
                return
            except Exception as exc:
                # Swallowed silently, this looked like an area that had
                # simply stopped noticing anything, which is the same thing
                # a working watcher looks like on a screen that is not
                # changing. Said out loud it is a fault to fix.
                self._failures += 1
                if self._failures == _FAILURES_BEFORE_SAYING:
                    self.on_error("Cannot read that area right now: "
                                  + (str(exc) or exc.__class__.__name__))
            self._stop.wait(self.interval)

    def _look(self) -> None:
        """One recognition, reported only if it is genuinely new.

        Was written inline with a `continue` that skipped the wait at the
        bottom of the loop, so noticing a scroll cost an extra recognition
        immediately afterwards.
        """
        shot = read_region(self.rect)
        key = _key(shot.text)
        if not key or key == self._last_key or len(shot.text) < self.min_chars:
            return
        self._last_key = key
        # A scroll continues the last capture rather than starting a new one,
        # so the rows stay one row per thing read.
        tail = continuation(self._last_text, shot.text)
        self._last_text = shot.text
        if tail is None:
            self.on_capture(shot)
            return
        if tail.strip():
            shot.continues = True
            shot.text = tail
            self.on_capture(shot)


def words_for(capture: Capture, sentence: str) -> list:
    """The word boxes that make up `sentence`, for drawing a highlight.

    Matched on letters alone and in order, because the sentence has been
    through normalisation by the time it is spoken: punctuation changed,
    abbreviations expanded, whitespace collapsed. Comparing the words as
    written would match almost nothing.
    """
    wanted = [w for w in ("".join(c if c.isalnum() else " " for c in sentence.lower())).split() if w]
    if not wanted:
        return []
    out, at = [], 0
    for word in capture.words:
        plain = "".join(c for c in word.text.lower() if c.isalnum())
        if not plain:
            continue
        if at < len(wanted) and plain == wanted[at]:
            out.append(word)
            at += 1
            if at >= len(wanted):
                break
        elif out and plain != wanted[min(at, len(wanted) - 1)]:
            # A run that started but did not continue was a coincidence.
            if at < len(wanted) // 2:
                out, at = [], 0
    return out if at >= max(1, len(wanted) // 2) else []
