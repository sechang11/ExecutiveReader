"""Watch a window and read only what appears in it.

The difference from pressing the read shortcut is that this speaks the *new*
text rather than all of it, which turns out to solve a problem that filtering
could not. A sidebar, a toolbar and a navigation strip are static: they are
already there when watching starts, so they are never new, so they are never
read. No list of things to ignore, and nothing to maintain per application.

Two modes, because they serve different situations.

`follow` reads whichever window is in front. It is the one to leave on while
working in a single app.

`locked` stays on the window it was started on, whatever you do afterwards.
That is the one for listening to a long answer in one window while working in
another, which the following mode cannot do by definition: the moment you click
away it would start reading whatever you clicked on.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from . import uia

#: How often to look. Fast enough to feel responsive, slow enough that a whole
#: accessibility tree walk is not running continuously.
DEFAULT_INTERVAL = 1.5

#: Lines shorter than this are labels, buttons and counters rather than
#: sentences. A watcher that reads them announces every changing timestamp and
#: unread badge in the window.
MIN_LINE = 24

FOLLOW = "follow"
LOCKED = "locked"


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


class WindowWatcher:
    """Poll a window and hand new lines to a callback, in order."""

    def __init__(self, on_text: Callable[[str, str], None],
                 mode: str = FOLLOW, target: str = "",
                 interval: float = DEFAULT_INTERVAL,
                 min_line: int = MIN_LINE) -> None:
        self.on_text = on_text
        self.mode = mode
        self.target = target
        self.interval = max(0.3, float(interval))
        self.min_line = min_line
        self.on_error: Callable[[str], None] = lambda _message: None

        self._seen: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._title = ""

    # --- lifecycle -------------------------------------------------------
    def start(self) -> None:
        self.stop()
        self._stop.clear()
        # Prime before speaking anything. Everything already on screen counts
        # as seen, so switching this on does not read the whole window back at
        # you; it starts from the next thing that appears.
        try:
            text, title = self._read()
            self._title = title
            for line in _lines(text):
                self._seen.add(line.lower())
        except Exception as exc:  # pragma: no cover - depends on the desktop
            self.on_error(str(exc))
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="executive-reader-watch")
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop and wait, so nothing is mid-read when the caller tears down.

        Same reason the Claude transcript watcher joins: returning while the
        thread is still inside a read lets it touch a closed database.
        """
        self._stop.set()
        thread = self._thread
        self._thread = None
        if (thread is not None and thread.is_alive()
                and thread is not threading.current_thread()):
            thread.join(timeout=timeout)
        self._seen.clear()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def watching(self) -> str:
        """What it is locked to, for showing the user."""
        return self._title

    # --- internals -------------------------------------------------------
    def _read(self) -> tuple[str, str]:
        if self.mode == LOCKED and self.target:
            doc = uia.capture_window(self.target)
        else:
            doc = uia.capture()
        if doc is None:
            return "", self._title
        return doc.text, (doc.title or self._title)

    def _fresh(self, text: str) -> str:
        """Lines not spoken before, in the order they appear.

        Matching on the line rather than on a position in the document is what
        makes this survive the window scrolling, re-rendering, or growing in
        the middle. A line that genuinely repeats is read once, which is the
        price of not re-reading a whole conversation every time a word lands
        somewhere above it.
        """
        out = []
        for line in _lines(text):
            key = line.lower()
            if key in self._seen:
                continue
            self._seen.add(key)
            if len(line) >= self.min_line:
                out.append(line)
        return "\n\n".join(out)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                text, title = self._read()
                if title:
                    self._title = title
                fresh = self._fresh(text)
                if fresh.strip():
                    self.on_text(fresh, self._title)
            except uia.UIAUnavailable as exc:
                self.on_error(str(exc))
                return
            except Exception:
                # A window closing mid-walk is ordinary. Keep watching.
                pass
            self._stop.wait(self.interval)
