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

    @property
    def preview(self) -> str:
        flat = " ".join(self.text.split())
        return flat[:90] + ("..." if len(flat) > 90 else "")


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
    return Capture(text=text or (result.get("text") or "").strip(), words=words)


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
                shot = read_region(self.rect)
                key = _key(shot.text)
                if key and key != self._last_key and len(shot.text) >= self.min_chars:
                    self._last_key = key
                    self.on_capture(shot)
            except ocr.OCRUnavailable as exc:
                self.on_error(str(exc))
                return
            except Exception:
                pass
            self._stop.wait(self.interval)


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
