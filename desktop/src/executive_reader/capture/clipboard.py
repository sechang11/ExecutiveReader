"""Clipboard capture: the paste path with the pasting removed."""
from __future__ import annotations

import threading
import time
from typing import Callable

from ..document import Document


def read_text() -> str:
    try:
        import win32clipboard
        import win32con
    except ImportError:
        return ""
    for _attempt in range(5):
        try:
            win32clipboard.OpenClipboard()
        except Exception:
            # Another process holds the clipboard; it is normally brief.
            time.sleep(0.05)
            continue
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) or ""
            return ""
        except Exception:
            return ""
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
    return ""


def set_text(value: str) -> bool:
    try:
        import win32clipboard
        import win32con
    except ImportError:
        return False
    try:
        win32clipboard.OpenClipboard()
    except Exception:
        return False
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, value)
        return True
    except Exception:
        return False
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def capture() -> Document | None:
    text = read_text().strip()
    if len(text) < 2:
        return None
    return Document(text=text, title="Clipboard", source="clipboard")


def copy_selection(restore: bool = True, settle: float = 0.12) -> Document | None:
    """Press Ctrl+C and read the result.

    The last-resort way to get a selection out of an app that exposes no
    accessible text. The previous clipboard contents are put back afterwards,
    so using this does not cost the user whatever they had copied.
    """
    try:
        import win32api
        import win32con
    except ImportError:
        return None

    previous = read_text() if restore else ""
    marker = "\x00executive-reader\x00"
    set_text(marker)

    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord("C"), 0, 0, 0)
    win32api.keybd_event(ord("C"), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)

    text = ""
    deadline = time.time() + 1.0
    while time.time() < deadline:
        time.sleep(settle)
        current = read_text()
        if current and current != marker:
            text = current
            break

    if restore:
        set_text(previous)
    text = text.strip()
    if len(text) < 2:
        return None
    return Document(text=text, title="Selection", source="selection")


class Watcher:
    """Call a function whenever the clipboard gains new text.

    Off by default in config: when it is on, everything you copy gets read.
    """

    def __init__(self, on_text: Callable[[Document], None],
                 interval: float = 0.6, min_chars: int = 8) -> None:
        self.on_text = on_text
        self.interval = interval
        self.min_chars = min_chars
        self._last = read_text()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="executive-reader-clipboard")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            current = read_text()
            if current and current != self._last:
                self._last = current
                if len(current.strip()) >= self.min_chars:
                    try:
                        self.on_text(Document(text=current.strip(),
                                              title="Clipboard",
                                              source="clipboard"))
                    except Exception:
                        pass
            self._stop.wait(self.interval)
