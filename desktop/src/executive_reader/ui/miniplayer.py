"""Compact always-on-top player.

Shows the sentence being spoken, which doubles as the reading position when
the source window is behind other things. Frameless and draggable so it can
sit out of the way.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                               QListWidget, QProgressBar,
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


#: Offered on the player. Anything finer belongs in settings; these are the
#: steps people actually listen at.
SPEEDS = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0]


def _label(value: float) -> str:
    text = ("%.2f" % value).rstrip("0").rstrip(".")
    return text + "x"


class MiniPlayer(QWidget):
    play_pause = Signal()
    stop = Signal()
    next_segment = Signal()
    prev_segment = Signal()
    open_library = Signal()
    speed_changed = Signal(float)
    choose_area = Signal()
    replay = Signal(int)
    open_item = Signal()
    read_screen = Signal()

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

        # A title rather than the sentence being spoken. Scrolling text here
        # was the worst of both: too small to read and too busy to ignore.
        # Clicking it opens the whole capture, which is where the text
        # belongs, because checking a misread word means looking at it.
        self.sentence = QLabel("")
        self.sentence.setObjectName("sentence")
        self.sentence.setWordWrap(True)
        self.sentence.setTextFormat(Qt.PlainText)
        self.sentence.setMinimumHeight(38)
        self.sentence.setCursor(Qt.PointingHandCursor)
        self.sentence.setToolTip("Click to see the whole thing.")
        self.sentence.mousePressEvent = self._title_clicked
        font = QFont()
        font.setPointSize(10)
        self.sentence.setFont(font)
        layout.addWidget(self.sentence)

        # The recent captures, on the window that is actually on screen. The
        # library has the full list; this is the handful worth reaching for
        # without opening anything.
        self.recent = QListWidget()
        self.recent.setObjectName("recent")
        self.recent.setMaximumHeight(96)
        self.recent.setUniformItemSizes(True)
        self.recent.itemClicked.connect(
            lambda item: self.replay.emit(self.recent.row(item)))
        self.recent.hide()
        layout.addWidget(self.recent)

        buttons = QHBoxLayout()
        self.btn_area = QPushButton("Choose an area to read")
        self.btn_area.setCursor(Qt.PointingHandCursor)
        self.btn_area.setToolTip(
            "Drag a box around anything on screen. Whatever text appears "
            "inside it gets read, and each reading is kept in the list above.")
        self.btn_area.clicked.connect(self.choose_area.emit)
        buttons.addWidget(self.btn_area)

        self.btn_screen = QPushButton("Read the screen now")
        self.btn_screen.setCursor(Qt.PointingHandCursor)
        self.btn_screen.setToolTip(
            "Read whatever is on screen right now, once, by recognising the "
            "picture. No area needed.")
        self.btn_screen.clicked.connect(self.read_screen.emit)
        buttons.addWidget(self.btn_screen)
        layout.addLayout(buttons)

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

        # A control rather than a readout. The speeds are the ones people
        # actually use for listening rather than a continuous slider nobody
        # can hit a round number on.
        self.speed = QComboBox()
        self.speed.setObjectName("speed")
        self.speed.setCursor(Qt.PointingHandCursor)
        self.speed.setFixedWidth(64)
        for value in SPEEDS:
            self.speed.addItem(_label(value), value)
        self.speed.setCurrentIndex(SPEEDS.index(1.0))
        self.speed.currentIndexChanged.connect(self._speed_picked)
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
        # The sentence is deliberately not shown here any more. It is the
        # progress bar and the counter that say where the reading has got to;
        # the line above stays a title you can click.
        self.progress.setValue(int((index + 1) / total * 1000) if total else 0)
        base = self.title.text().split("  ·  ")[0]
        self.title.setText(base + "  ·  " + str(index + 1) + " of " + str(total))

    def set_state(self, state: str) -> None:
        self._btn_play.setIcon(glyph_icon("pause" if state == "playing" else "play"))

    def _speed_picked(self, index: int) -> None:
        value = self.speed.itemData(index)
        if value is not None:
            self.speed_changed.emit(float(value))

    def set_speed(self, speed: float) -> None:
        """Show the speed without re-emitting it.

        The app also changes speed from the shortcuts and from a saved
        setting, and letting that assignment fire currentIndexChanged would
        bounce the value straight back into the reader.
        """
        nearest = min(SPEEDS, key=lambda value: abs(value - speed))
        self.speed.blockSignals(True)
        if abs(nearest - speed) < 0.01:
            self.speed.setCurrentIndex(SPEEDS.index(nearest))
        else:
            # A speed the list does not offer, set from a shortcut nudge.
            self.speed.setEditable(False)
            self.speed.setCurrentIndex(SPEEDS.index(nearest))
        self.speed.blockSignals(False)

    def _title_clicked(self, _event) -> None:
        self.open_item.emit()

    def set_item_title(self, title: str) -> None:
        """Name the thing being read, instead of showing it word by word."""
        self.sentence.setText(title)

    #: Ten is what fits without the player becoming a window of its own.
    RECENT_ROWS = 10

    def set_recent(self, previews: list) -> None:
        """Show the most recent captures, newest first."""
        self.recent.clear()
        for line in previews[:self.RECENT_ROWS]:
            self.recent.addItem(line)
        self.recent.setVisible(bool(previews))

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
