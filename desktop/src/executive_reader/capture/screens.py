"""Two coordinate spaces that look identical until a monitor is scaled.

Qt measures in logical pixels. On a display set to 125% it reports a 5120-wide
monitor as 4096 wide, so that text at 125% occupies the same apparent size as
text at 100% on the next screen along. Screen capture and recognition measure
in real pixels, because that is what a screenshot is made of.

On an unscaled display the two agree exactly, which is why this was missing for
so long and why it failed on one monitor and not the others. As soon as any
display is scaled they diverge twice over: the origin shifts, and every length
is off by the scale factor. A rectangle dragged in the middle of a 125% screen
was being grabbed a hundred pixels up and to the left of itself and a third too
small, so what came back was neighbouring text rather than the chosen text.

The pairing is by position and size rather than by name, because the two APIs
do not agree on what a monitor is called: Qt gives the model on the sticker and
the capture library gives "Generic PnP Monitor" for all of them. Sorted into
screen order, the lists line up, and each pair is checked: the logical size
times the scale has to equal the physical size. If any pair fails that, there
is no layout and every coordinate is passed through untouched, which is what
the app did before and is right on the single unscaled display where it works.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Rounding slack when checking a pair. Qt reports logical sizes as integers,
#: so a 1.5 scale on an odd-numbered height loses a fraction in each direction.
TOLERANCE = 2


@dataclass(frozen=True)
class Screen:
    """One display, in both spaces."""
    logical: tuple      # (left, top, width, height) as Qt sees it
    physical: tuple     # (left, top, width, height) as a screenshot sees it
    scale: float        # logical length times this is physical length


def pair(logical_screens: list, physical_monitors: list) -> list | None:
    """Match Qt's screens to the capture library's monitors.

    Both lists describe the same hardware, so sorting each into screen order
    lines them up. Returns None when they cannot be reconciled, which is the
    signal to stop converting rather than to convert badly.
    """
    if not logical_screens or not physical_monitors:
        return None
    if len(logical_screens) != len(physical_monitors):
        return None
    ours = sorted(logical_screens, key=lambda item: (item[0][0], item[0][1]))
    theirs = sorted(physical_monitors, key=lambda rect: (rect[0], rect[1]))

    out = []
    for (logical, scale), physical in zip(ours, theirs):
        # A display that reports no scale is taken as unscaled rather than
        # refused, because refusing one screen throws away the conversion for
        # all of them. It still has to pass the size check below, so a screen
        # that is scaled but will not say so is caught there anyway.
        scale = float(scale) if scale else 1.0
        if scale <= 0:
            return None
        if (abs(logical[2] * scale - physical[2]) > TOLERANCE
                or abs(logical[3] * scale - physical[3]) > TOLERANCE):
            return None
        out.append(Screen(tuple(logical), tuple(physical), scale))
    return out


def _distance(rect: tuple, x: int, y: int) -> int:
    """How far a point sits outside a rectangle. Zero when it is inside."""
    left, top, width, height = rect
    dx = max(left - x, 0, x - (left + width))
    dy = max(top - y, 0, y - (top + height))
    return dx * dx + dy * dy


def _screen_at(screens: list, x: int, y: int, physical: bool) -> Screen | None:
    """The screen a point belongs to, or the nearest one.

    Nearest rather than nothing, because a rectangle dragged to the very edge
    can end a pixel outside every monitor, and monitors of different heights
    leave gaps in the desktop that a corner can land in.
    """
    if not screens:
        return None
    return min(screens, key=lambda s: _distance(
        s.physical if physical else s.logical, x, y))


def to_physical(rect: tuple, screens: list | None) -> tuple:
    """A rectangle Qt measured, in the pixels a screenshot is made of."""
    screen = _screen_at(screens, rect[0], rect[1], physical=False)
    if screen is None:
        return tuple(rect)
    scale = screen.scale
    return (screen.physical[0] + round((rect[0] - screen.logical[0]) * scale),
            screen.physical[1] + round((rect[1] - screen.logical[1]) * scale),
            max(1, round(rect[2] * scale)),
            max(1, round(rect[3] * scale)))


def to_logical(rect: tuple, screens: list | None) -> tuple:
    """A rectangle measured on a screenshot, in the pixels Qt draws in."""
    screen = _screen_at(screens, rect[0], rect[1], physical=True)
    if screen is None:
        return tuple(rect)
    scale = screen.scale
    return (screen.logical[0] + round((rect[0] - screen.physical[0]) / scale),
            screen.logical[1] + round((rect[1] - screen.physical[1]) / scale),
            max(1, round(rect[2] / scale)),
            max(1, round(rect[3] / scale)))


# --- the live desktop ----------------------------------------------------

_cache: tuple | None = None


def qt_screens() -> list:
    """Every display as Qt describes it: logical rectangle and scale."""
    try:
        from PySide6.QtGui import QGuiApplication
    except ImportError:
        return []
    out = []
    for screen in QGuiApplication.screens():
        box = screen.geometry()
        out.append(((box.x(), box.y(), box.width(), box.height()),
                    float(screen.devicePixelRatio())))
    return out


def desktop_monitors() -> list:
    """Every display in real pixels, asked of the library that does the
    grabbing, so the answer is in the same space the grab will use."""
    try:
        import mss
    except ImportError:
        return []
    try:
        with mss.mss() as sct:
            return [(m["left"], m["top"], m["width"], m["height"])
                    for m in sct.monitors[1:]]
    except Exception:
        return []


def layout(refresh: bool = False) -> list | None:
    """The paired displays, remembered until the arrangement changes.

    Keyed on Qt's description, which is cheap to ask for and changes whenever
    a monitor is added, removed, moved or rescaled. Enumerating the physical
    monitors is the slow half and only happens when that key changes.
    """
    global _cache
    key = tuple((tuple(rect), scale) for rect, scale in qt_screens())
    if not refresh and _cache is not None and _cache[0] == key:
        return _cache[1]
    paired = pair(list(key), desktop_monitors())
    _cache = (key, paired)
    return paired


def physical(rect: tuple) -> tuple:
    """Convert a Qt rectangle for capture."""
    return to_physical(rect, layout())


def logical(rect: tuple) -> tuple:
    """Convert a captured rectangle for drawing."""
    return to_logical(rect, layout())


def scale_at(rect: tuple) -> float:
    """How much the display holding this captured rectangle is scaled."""
    screen = _screen_at(layout(), rect[0], rect[1], physical=True)
    return screen.scale if screen is not None else 1.0


def describe() -> str:
    """One line per display, for the status line and for bug reports.

    Worth showing because the failure it explains is invisible: capture reads
    the wrong part of the screen and there is nothing on screen to say so.
    """
    screens = layout()
    if not screens:
        return "Screen scaling could not be read; areas are used as given."
    return "; ".join(
        "%dx%d at %d,%d" % (s.physical[2], s.physical[3],
                            s.physical[0], s.physical[1])
        + (" (%d%%)" % round(s.scale * 100) if abs(s.scale - 1.0) > 0.01 else "")
        for s in screens)
