"""Reading Claude, from the transcript rather than from the screen.

This is the route the application should have been built around, and it was
in the box the whole time: Claude Code writes every session to a JSONL file as
it goes, so the text is exact, the reply is a separate field from the
reasoning, and tool traffic can be dropped without having to recognise what it
looks like.

Compare what it replaced. Reading the screen means choosing a rectangle,
recognising pixels, guessing where a sentence ended because the line wrapped,
losing the words when the view scrolls, and being unable to tell the reply
apart from the interface around it. Every one of those is a problem this file
does not have, because none of them are problems about text. They were
problems about pixels.

So this is the first tab, switched on, and the screen area is what it always
should have been: the way to read something that will not give up its text.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QGroupBox,
                               QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout,
                               QWidget)

from ..capture import claude_transcript as ct

#: Enough to pick one out without turning the tab into a file browser.
RECENT_SESSIONS = 12


def describe(path) -> str:
    """A conversation, named the way a person would recognise it.

    The file name is a UUID and the folder is the project path with the
    separators beaten out of it, so neither is worth showing as it stands.
    """
    if path is None:
        return "nothing yet"
    project = path.parent.name.replace("-", "/").strip("/")
    while "//" in project:
        project = project.replace("//", "/")
    short = project.rsplit("/", 1)[-1] or project
    return short + "  ·  " + path.stem[:8]


class ClaudeTab(QWidget):
    status = Signal(str)
    #: So the tray entry and this checkbox can never disagree. Two controls
    #: bound to one setting and not told about each other is how the old
    #: Settings copy quietly wrote back a stale answer.
    watch_changed = Signal(bool)

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        layout = QVBoxLayout(self)

        blurb = QLabel(
            "Claude Code writes every conversation to a file as it goes, and "
            "this reads that file. The text is exact rather than recognised, "
            "the reply is already separate from the reasoning and the tool "
            "output, and nothing depends on what is on screen: scrolling, "
            "window size and which monitor you are on stop mattering.")
        blurb.setWordWrap(True)
        blurb.setStyleSheet("color:#888;")
        layout.addWidget(blurb)

        box = QGroupBox("Follow along")
        inner = QVBoxLayout(box)

        self.chk_watch = QCheckBox("Read Claude's replies out loud as they arrive")
        self.chk_watch.setToolTip(
            "Reads each reply once it has been written. Starts from now, so "
            "switching it on does not read the conversation so far.")
        self.chk_watch.setChecked(bool(app.config.claude_watch))
        self.chk_watch.stateChanged.connect(self._watch_toggled)
        inner.addWidget(self.chk_watch)

        self.chk_thinking = QCheckBox("Also read the thinking, not just the reply")
        self.chk_thinking.setChecked(bool(app.config.claude_read_thinking))
        self.chk_thinking.setToolTip(
            "The reasoning Claude shows above an answer. Off by default "
            "because it is usually the part you are happy to skip.")
        self.chk_thinking.stateChanged.connect(self._thinking_toggled)
        inner.addWidget(self.chk_thinking)

        self.lbl_following = QLabel("Following: nothing yet")
        self.lbl_following.setToolTip(
            "Whichever conversation is being written to. Start a new one and "
            "this follows it across by itself.")
        inner.addWidget(self.lbl_following)

        row = QHBoxLayout()
        last = QPushButton("Read the last reply")
        last.setMinimumHeight(40)
        last.clicked.connect(lambda: self._read_recent(1))
        row.addWidget(last)

        several = QPushButton("Read the last six")
        several.setMinimumHeight(40)
        several.clicked.connect(lambda: self._read_recent(6))
        row.addWidget(several)

        whole = QPushButton("Read the whole conversation")
        whole.setMinimumHeight(40)
        whole.clicked.connect(lambda: self._read_recent(None))
        row.addWidget(whole)
        inner.addLayout(row)
        layout.addWidget(box)

        layout.addWidget(QLabel(
            "Recent conversations, newest first. Click one to read it."))
        self.sessions = QListWidget()
        self.sessions.setSelectionMode(QAbstractItemView.SingleSelection)
        self.sessions.itemClicked.connect(self._read_session)
        layout.addWidget(self.sessions, 1)

        app.on_claude_session = self._session_changed
        self.refresh()

    # --- state -----------------------------------------------------------
    def refresh(self) -> None:
        root = ct.projects_dir(self.app.config.claude_projects_dir)
        found = ct.sessions(root, limit=RECENT_SESSIONS)
        self.sessions.clear()
        for path in found:
            item = QListWidgetItem(describe(path))
            item.setData(32, str(path))
            self.sessions.addItem(item)
        self.lbl_following.setText(
            "Following: " + describe(found[0] if found else None))
        if not found:
            self.status.emit(
                "No Claude Code conversations found under " + str(root) + ".")

    def _session_changed(self, path) -> None:
        """The watcher moved to another conversation, on its own thread."""
        QTimer.singleShot(0, lambda: self._show_session(path))

    def _show_session(self, path) -> None:
        self.lbl_following.setText("Following: " + describe(path))
        self.refresh_soon()

    def refresh_soon(self) -> None:
        QTimer.singleShot(250, self.refresh)

    # --- actions ---------------------------------------------------------
    def _watch_toggled(self) -> None:
        on = self.chk_watch.isChecked()
        # Remember that this was answered, so the default never overrides it.
        self.app.config.claude_watch_chosen = True
        self.app.set_claude_watch(on)
        self.app.config.save()
        self.status.emit("Reading Claude's replies as they arrive."
                         if on else "No longer following Claude.")
        self.watch_changed.emit(bool(self.app.config.claude_watch))
        self.refresh()

    def _thinking_toggled(self) -> None:
        self.app.config.claude_read_thinking = self.chk_thinking.isChecked()
        self.app.config.save()
        # The watcher holds the old answer inside its tail, so restart it.
        if self.app.config.claude_watch:
            self.app.set_claude_watch(True)

    def _read_recent(self, last_n) -> None:
        self.app.read_claude_session(last_n=last_n)

    def _read_session(self, item) -> None:
        from pathlib import Path
        path = item.data(32)
        if path:
            self.app.read_claude_session(Path(path))
