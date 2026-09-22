"""Two things drawn on top of the whole screen.

`RegionPicker` is the drag-a-rectangle step, deliberately shaped like the
screenshot tool everyone already knows: the screen dims, you drag, Escape
cancels.

`Highlight` draws over the words currently being spoken. It is the only place
in the app that can do this, because recognition is the only route that knows
where the words are; every other route gets text with no coordinates attached.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..capture import screens


def _virtual_screen() -> QRect:
    """The whole desktop, including second monitors."""
    rect = QRect()
    for screen in QGuiApplication.screens():
        rect = rect.united(screen.geometry())
    return rect


class RegionPicker(QWidget):
    """Dim the screen and let one rectangle be dragged out of it."""

    chosen = Signal(tuple)      # (left, top, width, height) in real pixels
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(_virtual_screen())
        self._origin = None
        self._current = None

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))
        if self._origin and self._current:
            box = QRect(self._origin, self._current).normalized()
            # Punch the selection out of the dimming so the content shows.
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(box, Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setPen(QPen(QColor(90, 170, 255), 2))
            painter.drawRect(box)
            label = str(box.width()) + " x " + str(box.height())
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(box.left() + 4, max(14, box.top() - 6), label)
        else:
            painter.setPen(QColor(235, 235, 235))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Drag the area to read.   Escape to cancel.")

    def mousePressEvent(self, event) -> None:
        self._origin = event.position().toPoint()
        self._current = self._origin
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, _event) -> None:
        if self._origin is None or self._current is None:
            return
        box = QRect(self._origin, self._current).normalized()
        offset = _virtual_screen().topLeft()
        self.close()
        # Too small to be a deliberate selection; treat it as a cancel rather
        # than starting a watcher on twelve pixels. Judged in the space the
        # drag happened in, because that is the size the hand made.
        if box.width() < 20 or box.height() < 20:
            self.cancelled.emit()
            return
        # Handed on in real pixels. Everything downstream of here is capture:
        # the grab, recognition and the word boxes it returns all measure the
        # screen as a screenshot does, and on a scaled display that is not
        # what Qt just measured.
        self.chosen.emit(screens.physical(
            (box.left() + offset.x(), box.top() + offset.y(),
             box.width(), box.height())))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
            self.cancelled.emit()


class Highlight(QWidget):
    """A click-through overlay marking the words being spoken."""

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setGeometry(_virtual_screen())
        self._boxes: list = []
        self._frame: tuple | None = None

    def show_words(self, words, frame: tuple | None = None) -> None:
        """Mark these word boxes. An empty list clears the overlay.

        Word boxes arrive in real pixels, because recognition found them on a
        screenshot. Painting happens in Qt's pixels. On a scaled display those
        differ, and drawing one as if it were the other put the marker some
        way from the word it was meant to be under.
        """
        offset = _virtual_screen().topLeft()
        self._boxes = []
        for word in words:
            left, top, width, height = screens.logical(
                (word.left, word.top, word.width, word.height))
            self._boxes.append(QRect(left - offset.x(), top - offset.y(),
                                     width, height))
        self._frame = screens.logical(frame) if frame else None
        if self._boxes or self._frame:
            self.show()
            self.raise_()
        else:
            self.hide()
        self.update()

    def clear(self) -> None:
        self.show_words([], None)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        if self._frame:
            offset = _virtual_screen().topLeft()
            left, top, width, height = self._frame
            painter.setPen(QPen(QColor(90, 170, 255, 180), 2))
            painter.drawRect(left - offset.x(), top - offset.y(), width, height)
        # Drawn under the words rather than over them, so the text stays
        # readable while it is marked.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 214, 0, 90))
        for box in self._boxes:
            painter.drawRoundedRect(box.adjusted(-2, -1, 2, 1), 3, 3)
