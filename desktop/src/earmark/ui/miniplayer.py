"""Compact always-on-top player.

Shows the sentence being spoken, which doubles as the reading position when
the source window is behind other things. Frameless and draggable so it can
sit out of the way.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

from .icons import glyph_icon

_STYLE = """
QFrame#card {
    background: #1e2126;
    border: 1px solid #33383f;
    border-radius: 10px;
}
QLabel#title { color: #9aa0a6; font-size: 11px; }
QLabel#sentence { color: #e8eaed; font-size: 13px; }
QLabel#speed { color: #4f8cff; font-size: 11px; font-weight: bold; }
QPushButton {
    background: transparent; border: none; border-radius: 6px; padding: 4px;
}
QPushButton:hover { background: #2b3038; }
QProgressBar {
    background: #2b3038; border: none; border-radius: 2px; max-height: 4px;
}
QProgressBar::chunk { background: #4f8cff; border-radius: 2px; }
"""


class MiniPlayer(QWidget):
    play_pause = Signal()
    stop = Signal()
    next_segment = Signal()
    prev_segment = Signal()
    open_library = Signal()

    def __init__(self) -> None:
        super().__init__(None,
                         Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(380)
        self._drag_from: QPoint | None = None

        card = QFrame(self)
        card.setObjectName("card")
        card.setStyleSheet(_STYLE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self.title = QLabel("Nothing playing")
        self.title.setObjectName("title")
        self.title.setTextFormat(Qt.PlainText)
        layout.addWidget(self.title)

        self.sentence = QLabel("")
        self.sentence.setObjectName("sentence")
        self.sentence.setWordWrap(True)
        self.sentence.setTextFormat(Qt.PlainText)
        self.sentence.setMinimumHeight(38)
        font = QFont()
        font.setPointSize(10)
        self.sentence.setFont(font)
        layout.addWidget(self.sentence)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)
        layout.addWidget(self.progress)

        row = QHBoxLayout()
        row.setSpacing(2)
        self._btn_prev = self._button("prev", "Previous sentence", self.prev_segment)
        self._btn_play = self._button("play", "Play or pause", self.play_pause)
        self._btn_next = self._button("next", "Next sentence", self.next_segment)
        self._btn_stop = self._button("stop", "Stop", self.stop)
        for btn in (self._btn_prev, self._btn_play, self._btn_next, self._btn_stop):
            row.addWidget(btn)
        row.addStretch(1)

        self.speed = QLabel("1x")
        self.speed.setObjectName("speed")
        row.addWidget(self.speed)

        library = QPushButton("Library")
        library.setStyleSheet("color:#9aa0a6; font-size:11px;")
        library.setCursor(Qt.PointingHandCursor)
        library.clicked.connect(self.open_library.emit)
        row.addWidget(library)
        layout.addLayout(row)

    def _button(self, glyph: str, tip: str, signal) -> QPushButton:
        btn = QPushButton()
        btn.setIcon(glyph_icon(glyph))
        btn.setToolTip(tip)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedSize(30, 26)
        btn.clicked.connect(signal.emit)
        return btn

    # --- updates ---------------------------------------------------------
    def set_document(self, title: str, total: int) -> None:
        self.title.setText(title + "  ·  " + str(total) + " sentences")
        self.progress.setValue(0)

    def set_segment(self, index: int, text: str, total: int) -> None:
        self.sentence.setText(text)
        self.progress.setValue(int((index + 1) / total * 1000) if total else 0)
        base = self.title.text().split("  ·  ")[0]
        self.title.setText(base + "  ·  " + str(index + 1) + " of " + str(total))

    def set_state(self, state: str) -> None:
        self._btn_play.setIcon(glyph_icon("pause" if state == "playing" else "play"))

    def set_speed(self, speed: float) -> None:
        self.speed.setText(("%.2f" % speed).rstrip("0").rstrip(".") + "x")

    def set_status(self, message: str) -> None:
        self.sentence.setText(message)

    # --- dragging --------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_from)
            event.accept()

    def mouseReleaseEvent(self, _event) -> None:
        self._drag_from = None

    def place_bottom_right(self, margin: int = 24) -> None:
        screen = self.screen() or self.windowHandle().screen()
        area = screen.availableGeometry()
        self.adjustSize()
        self.move(area.right() - self.width() - margin,
                  area.bottom() - self.height() - margin)
