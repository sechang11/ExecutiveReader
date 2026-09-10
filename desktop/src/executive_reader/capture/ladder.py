"""Decide where the text comes from.

Each rung is tried in turn and the first one that yields readable text wins.
The order matters: everything above OCR reads real text, including the parts
scrolled out of view, so OCR is only reached when there is genuinely nothing
else to read.

    1. Selection      what you highlighted
    2. File           the PDF, EPUB or document the window has open
    3. Window text    the whole page or document via accessibility
    4. Clipboard      whatever you last copied
    5. Screen OCR     pixels, as a last resort
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..document import Document
from . import clipboard, files, ocr, uia

_LOCAL_PDF = re.compile(r"^(file:///|[a-zA-Z]:[\\/]).*\.(pdf|epub|docx|txt|md)$", re.I)


class CaptureResult:
    def __init__(self, doc: Document | None, rung: str, notes: list[str]) -> None:
        self.doc = doc
        self.rung = rung
        self.notes = notes

    def __bool__(self) -> bool:
        return self.doc is not None


def _local_path(url: str) -> Path | None:
    """Turn a file:// URL or a bare Windows path into a real path."""
    if not url:
        return None
    candidate = url.strip().strip('"')
    if candidate.lower().startswith("file:///"):
        parsed = urlparse(candidate)
        candidate = unquote(parsed.path).lstrip("/")
    try:
        path = Path(candidate)
    except (OSError, ValueError):
        return None
    return path if path.exists() and path.is_file() else None


def from_file(path, notes: list[str] | None = None) -> Document | None:
    """Parse a document, appending to `notes` whenever the answer is no.

    Every rung below this one explains itself when it declines. This one used
    to decline in silence, so a PDF that failed to parse looked exactly like a
    window with no text, and the ladder walked past it to the clipboard and
    read something the user copied an hour ago.

    The scanned-PDF case was worse than silent. The advice was written onto
    `doc.meta["hint"]` and the document was then dropped for having no text, so
    the one message that tells a user what to do about a scanned PDF could not
    be delivered by this path at all. Nothing anywhere read that key.
    """
    try:
        doc = files.read(path)
    except files.UnsupportedFile as exc:
        _note(notes, str(exc))
        return None
    except Exception as exc:
        _note(notes, "Could not read " + Path(path).name + ": " + str(exc))
        return None
    if doc.meta.get("needs_ocr"):
        # Decline rather than read it. A scanned page sometimes carries a
        # dozen stray characters from a watermark or a form field, which is
        # enough to look like text and be spoken instead of the page. Falling
        # through is what the OCR rung is for.
        _note(notes, "This PDF has no text layer, so it is probably scanned. "
                     "Screen OCR is the only way to read it.")
        return None
    if not doc.text.strip():
        _note(notes, Path(path).name + " has no readable text.")
        return None
    return doc


def _note(notes: list[str] | None, message: str) -> None:
    if notes is not None and message not in notes:
        notes.append(message)


def smart(*, allow_ocr: bool = True, allow_clipboard: bool = True,
          timeout: float = 8.0) -> CaptureResult:
    """Run the ladder against the focused window."""
    notes: list[str] = []

    try:
        selection = uia.capture_selection()
        if selection is not None:
            return CaptureResult(selection, "selection", notes)
    except uia.UIAUnavailable as exc:
        notes.append(str(exc))
    except Exception:
        notes.append("Selection read failed.")

    info = uia.WindowInfo()
    try:
        info = uia.window_info()
    except Exception:
        pass

    # A browser pointed at a local document: parse the file, do not scrape it.
    for candidate in (info.url, info.title):
        if candidate and _LOCAL_PDF.match(candidate.strip()):
            path = _local_path(candidate)
            if path is not None:
                doc = from_file(path, notes)
                if doc is not None:
                    return CaptureResult(doc, "file", notes)

    try:
        window = uia.capture(timeout=timeout)
        if window is not None:
            return CaptureResult(window, "window", notes)
        notes.append("This window exposes no accessible text.")
    except uia.UIAUnavailable as exc:
        notes.append(str(exc))
    except Exception:
        notes.append("Window read failed.")

    if allow_clipboard:
        clip = clipboard.capture()
        if clip is not None:
            notes.append("Fell back to the clipboard.")
            return CaptureResult(clip, "clipboard", notes)

    if allow_ocr and ocr.available():
        try:
            shot = ocr.capture()
            if shot is not None:
                notes.append("Read the visible screen with OCR; scroll and "
                             "run it again for the rest.")
                return CaptureResult(shot, "ocr", notes)
        except ocr.OCRUnavailable as exc:
            notes.append(str(exc))

    return CaptureResult(None, "none", notes or ["Nothing readable was found."])


def chrome_hint() -> str:
    """Setup advice when Chrome is open but hiding its page text."""
    try:
        if uia.chrome_accessibility_state() == "off":
            return ("Chrome is not exposing page text. Start it with "
                    "--force-renderer-accessibility to read web pages, or use "
                    "the clipboard and OCR paths instead.")
    except Exception:
        pass
    return ""
