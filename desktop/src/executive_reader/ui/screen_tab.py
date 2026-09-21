"""The Screen tab: buttons instead of remembered shortcuts, and a watched area.

Two complaints produced this. The first is that the app was only usable if you
knew Ctrl+Alt+R, Ctrl+Alt+V and Ctrl+Alt+O, which nobody does on the second
day. Every action is a button here, with its shortcut printed on it so the
shortcut is learned by using the button rather than by reading a table.

The second is the screen area. Drag a rectangle the way the screenshot tool
works, and whatever text appears inside it gets recognised and read. Each
recognition is kept as a row rather than spoken and discarded, because
recognition is the route most likely to produce something worth re-reading.

New text interrupts by default, which is what makes it feel live: whatever the
rectangle is pointed at is what you hear. Lock stops that when you want to
finish listening to what is already going.
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout,
                               QWidget)

from ..capture import region as region_mod
from .overlay import Highlight, RegionPicker

#: Most recent first, and capped: this is a live feed, not an archive. The
#: history tab is where reading that was asked for on purpose ends up.
MAX_ROWS = 40


def _pretty(combo: str) -> str:
    """ctrl+alt+r as Ctrl + Alt + R, which is how a keyboard is described."""
    if not combo:
        return ""
    return " + ".join(part.strip().title() for part in combo.split("+") if part.strip())


class ScreenTab(QWidget):
    status = Signal(str)

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self._watcher: region_mod.RegionWatcher | None = None
        self._picker: RegionPicker | None = None
        self._highlight = Highlight()
        self._captures: list = []
        self._rect: tuple | None = None
        self._live_capture: region_mod.Capture | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(self._controls())
        layout.addWidget(self._area_box())
        layout.addWidget(QLabel("Everything read from the area, newest first. "
                                "Click a row to hear it again."))
        self.rows = QListWidget()
        self.rows.setSelectionMode(QAbstractItemView.SingleSelection)
        self.rows.itemActivated.connect(self._read_row)
        self.rows.itemClicked.connect(self._read_row)
        layout.addWidget(self.rows, 1)

    # --- construction ----------------------------------------------------
    def _controls(self) -> QGroupBox:
        box = QGroupBox("Read")
        grid = QGridLayout(box)
        # Shortcuts are read from the settings rather than written here, so a
        # button can never print one the app does not actually listen for.
        keys = dict(getattr(self.app.config, "hotkeys", {}) or {})
        buttons = [
            ("Read this window", "read_smart", self.app.read_smart),
            ("Read what I selected", "", self.app.read_selection),
            ("Read the clipboard", "read_clip", self.app.read_clipboard),
            ("Read the screen now", "read_ocr", lambda: self.app.read_screen(False)),
            ("Play or pause", "play_pause", self.app.toggle),
            ("Stop", "stop", self.app.stop),
        ]
        for i, (label, key, slot) in enumerate(buttons):
            shortcut = _pretty(keys.get(key, "")) if key else ""
            button = QPushButton(label + ("\n" + shortcut if shortcut else ""))
            button.setMinimumHeight(48)
            button.clicked.connect(slot)
            grid.addWidget(button, i // 3, i % 3)
        return box

    def _area_box(self) -> QGroupBox:
        box = QGroupBox("Screen area")
        outer = QVBoxLayout(box)
        row = QHBoxLayout()

        self.btn_pick = QPushButton("Choose an area to watch")
        self.btn_pick.setMinimumHeight(40)
        self.btn_pick.clicked.connect(self.choose_area)
        row.addWidget(self.btn_pick)

        self.btn_stop = QPushButton("Stop watching")
        self.btn_stop.setMinimumHeight(40)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_watching)
        row.addWidget(self.btn_stop)
        outer.addLayout(row)

        self.chk_lock = QCheckBox(
            "Lock: finish what is being read instead of switching to new text")
        outer.addWidget(self.chk_lock)

        self.chk_highlight = QCheckBox("Highlight the words being read")
        self.chk_highlight.setChecked(True)
        self.chk_highlight.stateChanged.connect(self._highlight_toggled)
        outer.addWidget(self.chk_highlight)

        self.lbl_area = QLabel("No area chosen.")
        self.lbl_area.setStyleSheet("color:#888;")
        outer.addWidget(self.lbl_area)
        return box

    # --- area ------------------------------------------------------------
    def choose_area(self) -> None:
        self._picker = RegionPicker()
        self._picker.chosen.connect(self._area_chosen)
        self._picker.cancelled.connect(lambda: self.status.emit("Cancelled."))
        self._picker.show()
        self._picker.activateWindow()

    def _area_chosen(self, rect: tuple) -> None:
        self._rect = rect
        self.lbl_area.setText(
            "Watching %d by %d pixels at %d, %d." % (rect[2], rect[3], rect[0], rect[1]))
        self.start_watching()

    def start_watching(self) -> None:
        if not self._rect:
            return
        self.stop_watching(quiet=True)
        watcher = region_mod.RegionWatcher(self._rect, self._captured)
        watcher.on_error = lambda message: self.status.emit(message)
        watcher.start()
        self._watcher = watcher
        self.btn_stop.setEnabled(True)
        self.status.emit("Watching that area for text.")
        if self.chk_highlight.isChecked():
            self._highlight.show_words([], self._rect)

    def stop_watching(self, quiet: bool = False) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        self.btn_stop.setEnabled(False)
        self._highlight.clear()
        if not quiet:
            self.status.emit("Stopped watching the area.")

    # --- captures --------------------------------------------------------
    def _captured(self, capture) -> None:
        """A recognition arrived. Called from the watcher thread."""
        # Qt widgets may only be touched from the GUI thread, so hop across.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._add_capture(capture))

    def _add_capture(self, capture) -> None:
        self._captures.insert(0, capture)
        del self._captures[MAX_ROWS:]
        item = QListWidgetItem(time.strftime("%H:%M:%S", time.localtime(capture.when))
                               + "   " + capture.preview)
        self.rows.insertItem(0, item)
        while self.rows.count() > MAX_ROWS:
            self.rows.takeItem(self.rows.count() - 1)

        if self.chk_lock.isChecked() and self.app.reader.state == "playing":
            self.status.emit("New text found; locked, so it is waiting for you.")
            return
        self._speak(capture)

    def _read_row(self, item) -> None:
        index = self.rows.row(item)
        if 0 <= index < len(self._captures):
            self._speak(self._captures[index])

    def _speak(self, capture) -> None:
        self._live_capture = capture
        self.app.read_text(capture.text, "Screen area")

    # --- highlight -------------------------------------------------------
    def _highlight_toggled(self) -> None:
        if not self.chk_highlight.isChecked():
            self._highlight.clear()
        elif self._watcher is not None:
            self._highlight.show_words([], self._rect)

    def on_segment(self, sentence: str) -> None:
        """Mark the sentence being spoken, when it came from the screen area."""
        if not self.chk_highlight.isChecked() or self._live_capture is None:
            return
        words = region_mod.words_for(self._live_capture, sentence)
        self._highlight.show_words(words, self._rect)

    def shutdown(self) -> None:
        self.stop_watching(quiet=True)
        self._highlight.close()
