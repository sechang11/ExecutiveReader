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

The hard question here is not how to read a conversation, it is which one.
A machine with a dozen Claude sessions on it has a dozen transcripts being
appended to, most of them by agents nobody is watching. So the default is the
conversation you most recently typed in, and the list lets you stay on one.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QGroupBox,
                               QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout,
                               QWidget)

from ..capture import claude_transcript as ct

#: Enough to pick one out without turning the tab into a file browser.
RECENT_SESSIONS = 25

#: Qt's per-item user data slot.
_PATH = 32


def when(stamp: float) -> str:
    """How long ago, in the units a person would use."""
    if not stamp:
        return "no one has typed in it"
    minutes = max(0, (time.time() - stamp) / 60.0)
    if minutes < 1:
        return "just now"
    if minutes < 90:
        return "%d minutes ago" % minutes
    hours = minutes / 60.0
    if hours < 36:
        return "%d hours ago" % hours
    return "%d days ago" % (hours / 24.0)


def describe(conversation) -> str:
    """One line for the list: what it is called, where, and how recent."""
    if conversation is None:
        return "nothing yet"
    tail = "   ·   " + when(conversation.last_typed)
    if not conversation.by_hand:
        tail = "   ·   an agent, not you"
    return conversation.title + "   ·   " + conversation.project + tail


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

        self.chk_auto = QCheckBox("Follow whichever conversation I am typing in")
        self.chk_auto.setChecked(not (app.config.claude_session or "").strip())
        self.chk_auto.setToolTip(
            "Moves with you as you switch conversations. Untick it, or pick "
            "one from the list, to stay on a single conversation.")
        self.chk_auto.stateChanged.connect(self._auto_toggled)
        inner.addWidget(self.chk_auto)

        self.lbl_following = QLabel("Following: nothing yet")
        inner.addWidget(self.lbl_following)

        row = QHBoxLayout()
        for label, count in (("Read the last reply", 1),
                             ("Read the last six", 6),
                             ("Read the whole conversation", None)):
            button = QPushButton(label)
            button.setMinimumHeight(40)
            button.setToolTip("Reads the conversation named above.")
            button.clicked.connect(lambda _c=False, n=count: self._read_recent(n))
            row.addWidget(button)
        inner.addLayout(row)
        layout.addWidget(box)

        layout.addWidget(QLabel(
            "Your conversations, most recently typed in first. "
            "Click one to stay on it."))
        self.sessions = QListWidget()
        self.sessions.setSelectionMode(QAbstractItemView.SingleSelection)
        self.sessions.itemClicked.connect(self._pin)
        layout.addWidget(self.sessions, 1)

        app.on_claude_session = self._session_changed
        # The list ages as you look at it, and conversations come and go.
        self._ticker = QTimer(self)
        self._ticker.setInterval(15000)
        self._ticker.timeout.connect(self.refresh)
        self._ticker.start()
        self.refresh()

    # --- state -----------------------------------------------------------
    def refresh(self) -> None:
        root = ct.projects_dir(self.app.config.claude_projects_dir)
        found = ct.conversations(root, limit=RECENT_SESSIONS)
        target = self.app.claude_target()
        self.sessions.clear()
        for conversation in found:
            item = QListWidgetItem(describe(conversation))
            item.setData(_PATH, str(conversation.path))
            if target is not None and str(conversation.path) == str(target):
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            if not conversation.by_hand:
                item.setToolTip("Nobody has typed in this one, so it is an "
                                "agent working on its own. It is never "
                                "followed unless you pick it.")
            self.sessions.addItem(item)
        self._show_target(target, found)
        if not found:
            self.status.emit(
                "No Claude Code conversations found under " + str(root) + ".")

    def _show_target(self, target, found: list) -> None:
        match = next((c for c in found
                      if target is not None and str(c.path) == str(target)), None)
        how = ("following whatever you type in"
               if self.chk_auto.isChecked() else "staying on this one")
        self.lbl_following.setText(
            "Reading: " + (describe(match) if match else "nothing yet")
            + "      (" + how + ")")

    def _session_changed(self, path) -> None:
        """The watcher moved to another conversation, on its own thread."""
        QTimer.singleShot(0, self.refresh)

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

    def _auto_toggled(self) -> None:
        if self.chk_auto.isChecked():
            self.app.set_claude_session(None)
            self.status.emit("Following whichever conversation you type in.")
        elif not (self.app.config.claude_session or "").strip():
            # Nothing pinned yet, so hold the one being read right now.
            self.app.set_claude_session(self.app.claude_target())
        self.refresh()

    def _pin(self, item) -> None:
        path = item.data(_PATH)
        if not path:
            return
        self.chk_auto.blockSignals(True)
        self.chk_auto.setChecked(False)
        self.chk_auto.blockSignals(False)
        self.app.set_claude_session(path)
        self.status.emit("Staying on " + item.text().split("   ·   ")[0] + ".")
        self.refresh()

    def _read_recent(self, last_n) -> None:
        self.app.read_claude_session(last_n=last_n)
