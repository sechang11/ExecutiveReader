"""The full text of one captured item, in a window you can read and copy from.

The player shows a title because a line of scrolling text is the worst of both:
too small to read and too distracting to ignore. The text itself has to live
somewhere, though, because recognition gets words wrong and the only way to
check one is to look at it.

So the title on the player is a door. Clicking it opens this, which holds the
whole capture, keeps it selectable so a passage can be copied out, and reads
from any point when a line is clicked.
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)


class ClipboardWindow(QWidget):
    """One capture, in full."""

    read_from = Signal(str)     # the text to read, starting where asked

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowTitle("Captured text")
        self.resize(660, 480)
        self._capture = None
        self._name = ""

        layout = QVBoxLayout(self)

        self.heading = QLabel("Nothing captured yet.")
        font = QFont(self.heading.font())
        font.setPointSizeF(font.pointSizeF() + 2)
        font.setBold(True)
        self.heading.setFont(font)
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)

        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        self.body.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        body_font = QFont(self.body.font())
        body_font.setPointSizeF(body_font.pointSizeF() + 1)
        self.body.setFont(body_font)
        layout.addWidget(self.body, 1)

        row = QHBoxLayout()
        read_all = QPushButton("Read all of it")
        read_all.clicked.connect(self._read_all)
        row.addWidget(read_all)

        read_here = QPushButton("Read from the cursor")
        read_here.setToolTip("Click anywhere in the text first, then this "
                             "reads from there to the end.")
        read_here.clicked.connect(self._read_from_cursor)
        row.addWidget(read_here)

        copy = QPushButton("Copy to clipboard")
        copy.clicked.connect(self._copy)
        row.addWidget(copy)
        row.addStretch(1)

        close = QPushButton("Close")
        close.clicked.connect(self.hide)
        row.addWidget(close)
        layout.addLayout(row)

    def show_document(self, doc) -> None:
        """Show whatever is being read, from any route.

        A captured screen area and a web page are the same thing here: a title
        and a body of text someone wants to look at while it is read.
        """
        self._capture = doc
        self._name = document_title(doc, getattr(doc, "title", ""))
        self.heading.setText(self._name)
        self.body.setPlainText(getattr(doc, "text", "") or "")
        self.show()
        self.raise_()
        self.activateWindow()

    def show_capture(self, capture) -> None:
        self._capture = capture
        when = time.strftime("%H:%M:%S", time.localtime(getattr(capture, "when", 0)))
        self._name = title_for(capture)
        self.heading.setText(self._name + "     " + when)
        self.body.setPlainText(getattr(capture, "text", "") or "")
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh(self, capture) -> None:
        """Take on more text for the capture already on show.

        Scrolling the watched area extends the capture rather than starting a
        new one, so a window left open on it would otherwise sit there showing
        a version that stopped growing the moment it was opened.

        The view follows the new text only when it was already at the bottom.
        Someone who has scrolled up is reading something, and yanking them
        back down every two seconds would make the window useless for the one
        thing it is for.
        """
        if capture is not self._capture or not self.isVisible():
            return
        bar = self.body.verticalScrollBar()
        following = bar.value() >= bar.maximum() - 4
        where = bar.value()
        self.body.setPlainText(getattr(capture, "text", "") or "")
        bar.setValue(bar.maximum() if following else where)

    def name(self) -> str:
        """What is on show, so a passage read from here keeps its name.

        Without it, everything read out of this window arrives back on the
        player called "Selected passage", which undoes the point of naming
        the thing in the first place.
        """
        return self._name

    # --- actions ---------------------------------------------------------
    def _read_all(self) -> None:
        if self._capture is not None:
            self.read_from.emit(self._capture.text)

    def _read_from_cursor(self) -> None:
        """Read from where the cursor sits to the end.

        Recognition produces long captures, and the reason to open this window
        is usually that one part of it matters. Starting from the beginning
        again would defeat the point of looking.
        """
        text = self.body.toPlainText()
        at = self.body.textCursor().position()
        self.read_from.emit(text[at:].strip() or text)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.body.toPlainText())


#: Long enough to tell two captures apart, short enough for the player.
TITLE_CHARS = 52


def document_title(doc, fallback: str = "") -> str:
    """A name for anything being read.

    A document usually brings its own title: a page has one, a file has its
    name. A screen capture has nothing, so the first line does the job, which
    is what a person would use to recognise it in a list anyway.
    """
    given = (getattr(doc, "title", "") or fallback or "").strip()
    text = (getattr(doc, "text", "") or "").strip()
    if given and given.lower() not in ("window", "screen area", "pasted text"):
        return given if len(given) <= TITLE_CHARS else given[:TITLE_CHARS] + "..."
    if not text:
        return given or "Nothing playing"
    return title_for(doc)


def title_for(capture) -> str:
    """A name for a capture, taken from the capture itself.

    Recognition has no title to offer, so the first line does the job. It is
    what a person would use to recognise the item in a list, and it costs
    nothing to compute.
    """
    text = (getattr(capture, "text", "") or "").strip()
    if not text:
        return "Empty capture"
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if len(first) <= TITLE_CHARS:
        return first
    cut = first[:TITLE_CHARS]
    # Break on a word so the title does not end mid-syllable.
    space = cut.rfind(" ")
    return (cut[:space] if space > TITLE_CHARS // 2 else cut).rstrip(" ,.;:") + "..."
