"""Screen OCR: the last resort.

Only reached when nothing else exposes text, which in practice means images,
canvas, video frames, games and locked-down viewers. It reads pixels, so it
sees only what is on screen. That is the limitation the other rungs of the
ladder exist to avoid.

scroll_and_stitch works around it for scrollable content by capturing,
scrolling, capturing again and dropping the overlap.
"""
from __future__ import annotations

import time

from ..document import Document

_MIN_CHARS = 12


class OCRUnavailable(RuntimeError):
    pass


def choose_monitor(monitors: list, point: tuple | None = None) -> dict:
    """Which display to read when no area was chosen.

    The one the mouse is on, because that is the one being looked at. It is
    also the right answer when the button being pressed is on the floating
    player, since the player sits on the screen in use.

    This used to take the first display the capture library listed, which on
    a single screen is the only screen and on three is whichever Windows
    happened to enumerate first. On this desk that was not even the primary
    one, so reading the screen read a monitor nobody was looking at.
    """
    real = [m for m in monitors[1:]] or monitors
    if point is not None:
        x, y = point
        for m in real:
            if (m["left"] <= x < m["left"] + m["width"]
                    and m["top"] <= y < m["top"] + m["height"]):
                return m
    for m in real:
        if m.get("is_primary"):
            return m
    return real[0]


def _cursor() -> tuple | None:
    """Where the mouse is, in the pixels a screenshot is made of."""
    try:
        from PySide6.QtGui import QCursor

        from . import screens
    except ImportError:
        return None
    try:
        at = QCursor.pos()
        return screens.physical((at.x(), at.y(), 1, 1))[:2]
    except Exception:
        return None


def _grab(region: tuple[int, int, int, int] | None = None):
    """Screenshot a left/top/width/height box, or the display in use."""
    try:
        import mss
        from PIL import Image
    except ImportError as exc:
        raise OCRUnavailable("mss and Pillow are required for screen OCR.") from exc

    with mss.mss() as sct:
        if region:
            left, top, width, height = region
            box = {"left": left, "top": top, "width": width, "height": height}
        else:
            box = choose_monitor(sct.monitors, _cursor())
        shot = sct.grab(box)
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def _recognize(image) -> str:
    """Run the built-in Windows OCR engine. Free, fast, no model download."""
    try:
        import winocr
    except ImportError as exc:
        raise OCRUnavailable(
            "winocr is not installed. Run: pip install winocr") from exc
    try:
        result = winocr.recognize_pil_sync(image, "en")
    except Exception as exc:
        raise OCRUnavailable("Windows OCR failed: " + str(exc)) from exc
    if isinstance(result, dict):
        return (result.get("text") or "").strip()
    return (getattr(result, "text", "") or "").strip()


def available() -> bool:
    try:
        import mss  # noqa: F401
        import winocr  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def capture(region: tuple[int, int, int, int] | None = None) -> Document | None:
    """Read whatever text is visible on screen right now."""
    text = _recognize(_grab(region)).strip()
    if len(text) < _MIN_CHARS:
        return None
    return Document(text=text, title="Screen", source="ocr",
                    meta={"region": region})


def _dedupe(previous: str, current: str) -> str:
    """Drop lines of current that already appeared at the tail of previous.

    Consecutive screenshots overlap, so without this every scroll step would
    repeat several lines.
    """
    if not previous:
        return current
    prev_lines = [ln.strip() for ln in previous.splitlines() if ln.strip()]
    cur_lines = [ln.strip() for ln in current.splitlines() if ln.strip()]
    if not prev_lines or not cur_lines:
        return current
    tail = prev_lines[-12:]
    # Find the longest run of leading current-lines already present in the tail.
    cut = 0
    for i in range(min(len(cur_lines), len(tail)), 0, -1):
        if cur_lines[:i] == tail[-i:]:
            cut = i
            break
    if cut == 0:
        seen = set(tail)
        while cut < len(cur_lines) and cur_lines[cut] in seen:
            cut += 1
    return "\n".join(cur_lines[cut:])


def scroll_and_stitch(pages: int = 8, pause: float = 0.45,
                      clicks: int = 6,
                      region: tuple[int, int, int, int] | None = None) -> Document | None:
    """Capture, scroll, repeat, then join the results.

    For image-only content that does not fit on one screen. Slower and less
    reliable than every other capture route, so it is never the default.
    """
    try:
        import win32api
        import win32con
    except ImportError as exc:
        raise OCRUnavailable("pywin32 is required for scrolling capture.") from exc

    collected: list[str] = []
    previous = ""
    for step in range(max(1, pages)):
        chunk = _recognize(_grab(region)).strip()
        fresh = _dedupe(previous, chunk)
        if step > 0 and not fresh.strip():
            break  # nothing new appeared; we have reached the end
        if fresh.strip():
            collected.append(fresh)
        previous = chunk
        for _ in range(clicks):
            win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, -120, 0)
            time.sleep(0.02)
        time.sleep(pause)

    text = "\n".join(collected).strip()
    if len(text) < _MIN_CHARS:
        return None
    return Document(text=text, title="Screen (scrolled)", source="ocr",
                    meta={"region": region, "steps": len(collected)})
