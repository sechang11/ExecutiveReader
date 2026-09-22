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

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout,
                               QWidget)

from ..capture import region as region_mod
from ..capture import screens
from .clipboard_window import ClipboardWindow, title_for
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
    captures_changed = Signal(list)
    now_reading = Signal(str)
    #: The chosen rectangle, or None. The player asks so that its own read
    #: button can mean the area when there is one.
    area_changed = Signal(object)

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self._watcher: region_mod.RegionWatcher | None = None
        self._picker: RegionPicker | None = None
        self._highlight = Highlight()
        self._captures: list = []
        self._rect: tuple | None = None
        self._live_capture: region_mod.Capture | None = None
        self._window = ClipboardWindow()
        self._word_timer = QTimer(self)
        self._word_timer.setSingleShot(True)
        self._word_timer.timeout.connect(self._next_word)
        self._word_queue: list = []
        self._window.read_from.connect(
            lambda text: self.app.read_text(text, 'Screen area'))

        layout = QVBoxLayout(self)
        layout.addWidget(self._controls())
        layout.addWidget(self._area_box())
        layout.addWidget(QLabel("Everything read from the area, newest first. "
                                "Click a row to see all of it. Double-click "
                                "to hear it again."))
        self.rows = QListWidget()
        self.rows.setSelectionMode(QAbstractItemView.SingleSelection)
        # Seeing and hearing are different wants, so they are different
        # gestures. Recognition misreads words, and the only way to find out
        # which is to look at the text rather than listen to it again.
        self.rows.itemClicked.connect(self._open_row)
        self.rows.itemActivated.connect(self._read_row)
        layout.addWidget(self.rows, 1)

    # --- construction ----------------------------------------------------
    def _controls(self) -> QGroupBox:
        box = QGroupBox("Read")
        grid = QGridLayout(box)
        # Shortcuts are read from the settings rather than written here, so a
        # button can never print one the app does not actually listen for.
        keys = dict(getattr(self.app.config, "hotkeys", {}) or {})
        buttons = [
            ("Read this page",
             "Everything in the window in front of you, article and all.\n"
             "Start here. It is the one to use most of the time.",
             "read_smart", self.app.read_smart),
            ("Read highlighted text",
             "Only what you have selected with the mouse.\n"
             "Use it to hear one paragraph instead of a whole page.",
             "", self.app.read_selection),
            ("Read what I copied",
             "Whatever you last copied with Ctrl+C.\n"
             "Use it when a window will not give up its text.",
             "read_clip", self.app.read_clipboard),
            ("Read the pixels on screen",
             "Recognises the picture instead of asking for text.\n"
             "Slower, and it can misread. For images, video and\n"
             "viewers that refuse every other route.",
             "read_ocr", lambda: self.app.read_screen(False)),
            ("Play or pause", "", "play_pause", self.app.toggle),
            ("Stop", "", "stop", self.app.stop),
        ]
        for i, (label, why, key, slot) in enumerate(buttons):
            shortcut = _pretty(keys.get(key, "")) if key else ""
            button = QPushButton(label + ("\n" + shortcut if shortcut else ""))
            button.setMinimumHeight(56)
            if why:
                button.setToolTip(why)
            button.clicked.connect(slot)
            grid.addWidget(button, i // 3, i % 3)

        # The four ways of reading differ only in where the words come from,
        # which the old names never said.
        note = QLabel(
            "The four reading buttons differ by where the words come from: "
            "the whole window, just what you highlighted, your clipboard, or "
            "the pixels themselves. Hover one to see when to use it.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#888;")
        grid.addWidget(note, 2, 0, 1, 3)
        return box

    def _area_box(self) -> QGroupBox:
        box = QGroupBox("Screen area")
        outer = QVBoxLayout(box)
        row = QHBoxLayout()

        self.btn_pick = QPushButton("Choose an area to watch")
        self.btn_pick.setToolTip(
            "Drag a new rectangle. Choosing again is also how the area is "
            "resized: the old one is replaced by what you drag.")
        self.btn_pick.setMinimumHeight(40)
        self.btn_pick.clicked.connect(self.choose_area)
        # Hovering shows the rectangle. It is the only way to see where the
        # area is now that the outline no longer sits on screen, and it costs
        # nothing to look.
        self.btn_pick.enterEvent = self._peek_area
        self.btn_pick.leaveEvent = self._unpeek_area
        row.addWidget(self.btn_pick)

        self.btn_now = QPushButton("Read the area now")
        self.btn_now.setMinimumHeight(40)
        self.btn_now.setEnabled(False)
        self.btn_now.setToolTip(
            "Read the chosen area once, without waiting for it to change. "
            "None of the buttons above do this: they read a window, a "
            "selection, the clipboard or the whole screen.")
        self.btn_now.clicked.connect(self.read_area_now)
        row.addWidget(self.btn_now)

        self.btn_clear = QPushButton("Forget the area")
        self.btn_clear.setMinimumHeight(40)
        self.btn_clear.setEnabled(False)
        self.btn_clear.setToolTip("Stop watching and forget the rectangle.")
        self.btn_clear.clicked.connect(self.clear_area)
        row.addWidget(self.btn_clear)

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

    def _peek_area(self, _event) -> None:
        if self._rect:
            self._highlight.show_words([], self._rect)

    def _unpeek_area(self, _event) -> None:
        if not self._word_queue:
            self._highlight.clear()

    def _area_chosen(self, rect: tuple) -> None:
        self._rect = rect
        # Reported in real pixels, which is what gets captured. On a scaled
        # display that is not the number of pixels the rectangle looked like,
        # so the scale is named rather than left to look like a mistake.
        scale = screens.scale_at(rect)
        note = ("" if abs(scale - 1.0) < 0.01
                else "  That display is scaled to %d%%." % round(scale * 100))
        self.lbl_area.setText(
            "Watching %d by %d pixels at %d, %d.%s"
            % (rect[2], rect[3], rect[0], rect[1], note))
        self.area_changed.emit(rect)
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
        self.btn_now.setEnabled(True)
        self.btn_clear.setEnabled(True)
        self.status.emit("Watching that area. It reads new text by itself; "
                         "use Read the area now to hear it again.")

    def stop_watching(self, quiet: bool = False) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        self.btn_stop.setEnabled(False)
        self._highlight.clear()
        if not quiet:
            self.status.emit("Stopped watching the area.")

    def read_area_now(self) -> None:
        """Read the area once, on demand.

        Worth its own button because none of the reading buttons above cover
        it: they read a window, a selection, the clipboard or the whole
        screen, and the area is none of those. Without this the only way to
        hear it was to wait for the text inside it to change.
        """
        if not self._rect:
            self.status.emit("Choose an area first.")
            return
        try:
            capture = region_mod.read_region(self._rect)
        except Exception as exc:
            self.status.emit(str(exc))
            return
        if not capture.text.strip():
            self.status.emit("No text recognised in that area.")
            return
        self._add_capture(capture)

    def clear_area(self) -> None:
        """Forget the rectangle entirely.

        Separate from stopping, because stopping keeps the area so it can be
        started again; this is the way out when the rectangle is in the wrong
        place and you want it gone rather than adjusted.
        """
        self.stop_watching(quiet=True)
        self._rect = None
        self._live_capture = None
        self.btn_now.setEnabled(False)
        self.btn_clear.setEnabled(False)
        self.lbl_area.setText("No area chosen.")
        self.area_changed.emit(None)
        self.status.emit("Area forgotten.")

    # --- captures --------------------------------------------------------
    def _label(self, capture) -> str:
        """How a capture is named in a list: the time, and what it says."""
        return (time.strftime("%H:%M:%S", time.localtime(capture.when))
                + "   " + title_for(capture))

    def _captured(self, capture) -> None:
        """A recognition arrived. Called from the watcher thread."""
        # Qt widgets may only be touched from the GUI thread, so hop across.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._add_capture(capture))

    def _add_capture(self, capture) -> None:
        # A scroll extends the row already there instead of adding a near-copy
        # of it. Without this, scrolling a page being read produces a new row
        # that mostly repeats the last one, and the reading starts again from
        # text already heard.
        if getattr(capture, "continues", False) and self._captures:
            head = self._captures[0]
            head.text = (head.text + chr(10) + capture.text).strip()
            head.words = list(head.words) + list(capture.words)
            if self.rows.count():
                self.rows.item(0).setText(self._label(head))
            # A window open on this one has just gained text; show it.
            self._window.refresh(head)
            self._announce()
            if self._live_capture is head:
                # Already reading this one: queue the rest rather than
                # restarting it from the top.
                self.app.read_text(capture.text, "Screen area")
            return
        self._captures.insert(0, capture)
        del self._captures[MAX_ROWS:]
        item = QListWidgetItem(self._label(capture))
        item.setToolTip(capture.preview)
        self.rows.insertItem(0, item)
        while self.rows.count() > MAX_ROWS:
            self.rows.takeItem(self.rows.count() - 1)
        self._announce()

        if self.chk_lock.isChecked() and self.app.reader.state == "playing":
            self.status.emit("New text found; locked, so it is waiting for you.")
            return
        self._speak(capture)

    def replay(self, index: int) -> None:
        """Read one of the recent captures, chosen from the player."""
        if 0 <= index < len(self._captures):
            self._speak(self._captures[index])

    def _announce(self) -> None:
        self.captures_changed.emit(
            [time.strftime("%H:%M", time.localtime(c.when)) + "  " + title_for(c)
             for c in self._captures])

    def _read_row(self, item) -> None:
        index = self.rows.row(item)
        if 0 <= index < len(self._captures):
            self._speak(self._captures[index])

    def _open_row(self, item) -> None:
        self.open_row(self.rows.row(item))

    def open_row(self, index: int) -> None:
        """Show one of the captures in full, chosen from either list."""
        if 0 <= index < len(self._captures):
            self._window.show_capture(self._captures[index])

    def _speak(self, capture) -> None:
        self._live_capture = capture
        self.now_reading.emit(title_for(capture))
        self.app.read_text(capture.text, "Screen area")

    def has_area(self) -> bool:
        """Whether a rectangle has been chosen."""
        return self._rect is not None

    def live_capture(self):
        """The capture being read, or None. Live: a scroll extends it."""
        return self._live_capture

    def use_window(self, window) -> None:
        """Share the app's one capture window instead of keeping a second.

        Two windows open on two different captures is a worse answer than one
        window open on the one that was asked for, and which of them appeared
        would depend on where the click landed.
        """
        old, self._window = self._window, window
        if old is not None and old is not window:
            old.close()
            old.deleteLater()

    def open_current(self) -> None:
        """Show the whole of whatever is being read."""
        if self._live_capture is None:
            self.status.emit("Nothing captured yet.")
            return
        self._window.show_capture(self._live_capture)

    # --- highlight -------------------------------------------------------
    def _highlight_toggled(self) -> None:
        if not self.chk_highlight.isChecked():
            self._highlight.clear()

    def on_segment(self, sentence: str) -> None:
        """Mark the sentence being spoken, one word at a time.

        Lighting the whole sentence says where the reader is to within a few
        seconds, which is not much help on a paragraph. Stepping word by word
        follows the voice.

        The timing is shared out by length rather than measured, because
        nothing reports when a particular word is spoken: the player knows how
        long the sentence takes, and a longer word takes proportionally
        longer to say. It drifts within a sentence and is corrected at every
        sentence boundary, which is close enough to follow with your eyes.
        """
        self._word_timer.stop()
        self._word_queue = []
        if not self.chk_highlight.isChecked() or self._live_capture is None:
            return
        words = region_mod.words_for(self._live_capture, sentence)
        if not words:
            self._highlight.clear()
            return

        seconds = float(getattr(self.app.reader, "segment_seconds", 0.0) or 0.0)
        if seconds <= 0:
            self._highlight.show_words(words)
            return
        total = sum(max(1, len(w.text)) for w in words)
        self._word_queue = [
            (word, max(40, int(seconds * 1000 * max(1, len(word.text)) / total)))
            for word in words]
        self._next_word()

    def _next_word(self) -> None:
        if not self._word_queue:
            return
        word, milliseconds = self._word_queue.pop(0)
        self._highlight.show_words([word])
        if self._word_queue:
            self._word_timer.start(milliseconds)

    def shutdown(self) -> None:
        self.stop_watching(quiet=True)
        self._highlight.close()
        self._window.close()
