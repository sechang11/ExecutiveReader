"""Things drawn on top of the screen: the area picker, and the marker.

Both used to be a single window stretched across the whole desktop. That
cannot work when displays are scaled differently, and it failed in two ways at
once on a desk with a 125% monitor between two at 100%.

A window gets one scale factor, belonging to the screen Qt considers it to be
on. Stretched across all three it was rendered at the primary's 100%, so its
own pixels were real pixels -- while every coordinate handed to it had been
carefully converted into Qt's, which on the scaled monitor are different
numbers. The marker landed about a hundred pixels from the word.

It also came up short. Qt lays screens out in its own coordinates, where the
125% monitor is 4096 wide; the desktop it has to cover there is 5120. The
window was a thousand real pixels narrower than the desktop, so the right-hand
edge of that monitor could not be dimmed, drawn on, or selected at all.

So there is one pane per screen now. Each sits entirely within its own
display, which means Qt renders it at that display's scale and its coordinates
are unambiguous. Anything that crosses between them is held in real pixels,
the one space every screen agrees on, and converted per pane when it is drawn.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QRect, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..capture import screens


def _paired() -> list:
    """Each Qt screen beside its real-pixel rectangle and scale.

    Paired on the geometry Qt reports, because that is what identifies a
    display in both descriptions; the names do not match.
    """
    known = {tuple(s.logical): s for s in (screens.layout() or [])}
    out = []
    for qt_screen in QGuiApplication.screens():
        box = qt_screen.geometry()
        key = (box.x(), box.y(), box.width(), box.height())
        found = known.get(key)
        out.append((qt_screen, found.physical if found else key,
                    found.scale if found else 1.0))
    return out


def _to_pane(rect: tuple, physical: tuple, scale: float) -> QRect:
    """A real-pixel rectangle in one pane's own coordinates."""
    return QRect(round((rect[0] - physical[0]) / scale),
                 round((rect[1] - physical[1]) / scale),
                 max(1, round(rect[2] / scale)),
                 max(1, round(rect[3] / scale)))


def _touches(rect: tuple, physical: tuple) -> bool:
    """Whether a real-pixel rectangle appears on this display at all."""
    return not (rect[0] + rect[2] <= physical[0]
                or rect[0] >= physical[0] + physical[2]
                or rect[1] + rect[3] <= physical[1]
                or rect[1] >= physical[1] + physical[3])


def cursor_now() -> tuple:
    """Where the mouse is, in real pixels.

    Asked of Windows rather than taken from a Qt event. A pane's own
    coordinates would do for the pane the drag started on, but a drag that
    crosses onto a differently scaled screen would be measured in two units
    at once, and the answer is wanted in the units the screen grab uses.
    """
    try:
        import win32api
        return tuple(win32api.GetCursorPos())
    except Exception:
        here = QCursor.pos()
        return screens.physical((here.x(), here.y(), 1, 1))[:2]


class _Pane(QWidget):
    """One screen's worth of overlay. Never spans two."""

    def __init__(self, qt_screen, physical: tuple, scale: float,
                 clickable: bool) -> None:
        super().__init__(None)
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        if not clickable:
            flags |= Qt.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        if not clickable:
            self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.physical = physical
        self.scale = scale
        self.draw = None
        handle = self.windowHandle()
        if handle is not None:
            handle.setScreen(qt_screen)
        self.setGeometry(qt_screen.geometry())

    def paintEvent(self, _event) -> None:
        if self.draw is not None:
            self.draw(self)


class _Panes(QObject):
    """A pane on every screen, rebuilt when the arrangement changes."""

    clickable = False

    def __init__(self) -> None:
        super().__init__(None)
        self._panes: list = []
        self._key: tuple = ()

    def panes(self) -> list:
        paired = _paired()
        key = tuple((tuple(p), s) for _q, p, s in paired)
        if key != self._key or len(self._panes) != len(paired):
            self._drop()
            for qt_screen, physical, scale in paired:
                self._panes.append(
                    _Pane(qt_screen, physical, scale, self.clickable))
            self._key = key
        return self._panes

    def _drop(self) -> None:
        for pane in self._panes:
            pane.hide()
            pane.deleteLater()
        self._panes = []

    def close(self) -> None:
        self._drop()
        self._key = ()


class RegionPicker(_Panes):
    """Dim every screen and let one rectangle be dragged out of them."""

    clickable = True

    chosen = Signal(tuple)      # (left, top, width, height) in real pixels
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._from: tuple | None = None
        self._to: tuple | None = None

    def show(self) -> None:
        for pane in self.panes():
            pane.draw = self._paint
            pane.mousePressEvent = self._pressed
            pane.mouseMoveEvent = self._moved
            pane.mouseReleaseEvent = self._released
            pane.keyPressEvent = self._key_pressed
            pane.setCursor(Qt.CrossCursor)
            pane.setMouseTracking(True)
            pane.show()
            pane.raise_()

    def activateWindow(self) -> None:
        if self._panes:
            self._panes[0].activateWindow()

    # --- the drag ---------------------------------------------------------
    def selection(self) -> tuple | None:
        if self._from is None or self._to is None:
            return None
        left, right = sorted((self._from[0], self._to[0]))
        top, bottom = sorted((self._from[1], self._to[1]))
        return (left, top, right - left, bottom - top)

    def _pressed(self, _event) -> None:
        self._from = cursor_now()
        self._to = self._from
        self._repaint()

    def _moved(self, _event) -> None:
        if self._from is not None:
            self._to = cursor_now()
            self._repaint()

    def _released(self, _event) -> None:
        box = self.selection()
        self.close()
        # Too small to be a deliberate selection; treat it as a cancel rather
        # than starting a watcher on twelve pixels.
        if box is None or box[2] < 20 or box[3] < 20:
            self.cancelled.emit()
            return
        self.chosen.emit(box)

    def _key_pressed(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
            self.cancelled.emit()

    def _repaint(self) -> None:
        for pane in self._panes:
            pane.update()

    def _paint(self, pane) -> None:
        painter = QPainter(pane)
        painter.fillRect(pane.rect(), QColor(0, 0, 0, 110))
        box = self.selection()
        if box is None:
            painter.setPen(QColor(235, 235, 235))
            painter.drawText(pane.rect(), Qt.AlignCenter,
                             "Drag the area to read.   Escape to cancel.")
            return
        here = _to_pane(box, pane.physical, pane.scale)
        # Punch the selection out of the dimming so the content shows.
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.fillRect(here, Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setPen(QPen(QColor(90, 170, 255), 2))
        painter.drawRect(here)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(here.left() + 4, max(14, here.top() - 6),
                         str(box[2]) + " x " + str(box[3]) + " pixels")


class Highlight(_Panes):
    """The watched area, the words being spoken, and what recognition found.

    Three layers, kept apart because they answer different questions. The
    outline says where the area is, and has to survive every sentence and
    every gap between them. The marker says which word is being said. The
    recognition boxes say where the recogniser believes the words are, so that
    "the marker is in the wrong place" can be told from "the area is in the
    wrong place" by looking instead of guessing.
    """

    def __init__(self) -> None:
        super().__init__()
        self._frame: tuple | None = None
        self._boxes: list = []
        self._found: list = []

    # --- the three layers -------------------------------------------------
    def set_frame(self, rect: tuple | None) -> None:
        self._frame = tuple(rect) if rect else None
        self._apply()

    def show_words(self, words, frame: tuple | None = None) -> None:
        self._boxes = [(w.left, w.top, w.width, w.height) for w in words]
        if frame is not None:
            self._frame = tuple(frame)
        self._apply()

    def show_found(self, words) -> None:
        self._found = [(w.left, w.top, w.width, w.height) for w in words]
        self._apply()

    def clear(self) -> None:
        """Take the marker off. The outline is not a marker and stays."""
        self._boxes = []
        self._apply()

    def clear_all(self) -> None:
        self._frame = None
        self._boxes = []
        self._found = []
        self._apply()

    # --- drawing ----------------------------------------------------------
    def _apply(self) -> None:
        if not (self._frame or self._boxes or self._found):
            for pane in self._panes:
                pane.hide()
            return
        for pane in self.panes():
            pane.draw = self._paint
            pane.show()
            pane.raise_()
            pane.update()

    def _paint(self, pane) -> None:
        painter = QPainter(pane)
        if self._frame and _touches(self._frame, pane.physical):
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(90, 170, 255, 200), 2))
            painter.drawRect(_to_pane(self._frame, pane.physical, pane.scale))
        if self._found:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(255, 80, 80, 160), 1))
            for box in self._found:
                if _touches(box, pane.physical):
                    painter.drawRect(_to_pane(box, pane.physical, pane.scale))
        # Drawn under the words rather than over them, so the text stays
        # readable while it is marked.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 214, 0, 90))
        for box in self._boxes:
            if _touches(box, pane.physical):
                painter.drawRoundedRect(
                    _to_pane(box, pane.physical, pane.scale).adjusted(-2, -1, 2, 1),
                    3, 3)
