"""Tray icon, menu, and the bridge from worker threads onto the UI thread.

Reader callbacks arrive on background threads. Touching Qt widgets from those
threads crashes, so everything crosses over as a signal.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..hotkeys import Hotkeys
from ..player.engine import PLAYING
from .icons import speaker_icon
from .library import Library
from .miniplayer import MiniPlayer


class Bridge(QObject):
    """Marshals reader events onto the Qt thread."""
    status = Signal(str)
    error = Signal(str)
    state = Signal(str)
    segment = Signal(int, str, int)
    document = Signal(str, int)


class TrayApp(QObject):
    def __init__(self, app, qt_app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.qt_app = qt_app

        self.bridge = Bridge()
        app.on_status = self.bridge.status.emit
        app.on_error = self.bridge.error.emit
        app.on_state = self.bridge.state.emit
        app.on_segment = self.bridge.segment.emit
        app.on_document = lambda doc, total: self.bridge.document.emit(doc.title, total)

        self.player = MiniPlayer()
        self.library = Library(app)

        self.bridge.status.connect(self._on_status)
        self.bridge.error.connect(self._on_error)
        self.bridge.state.connect(self._on_state)
        self.bridge.segment.connect(self._on_segment)
        self.bridge.document.connect(self._on_document)

        self.player.play_pause.connect(app.toggle)
        self.player.stop.connect(app.stop)
        self.player.next_segment.connect(app.next_segment)
        self.player.prev_segment.connect(app.prev_segment)
        self.player.open_library.connect(self.show_library)
        self.library.read_uri.connect(self._reopen)

        self.tray = QSystemTrayIcon(speaker_icon(active=False))
        self.tray.setToolTip("Earmark")
        self.tray.setContextMenu(self._menu())
        self.tray.activated.connect(self._tray_clicked)
        self.tray.show()

        self.hotkeys = Hotkeys()
        self._bind_hotkeys()

        if app.config.mini_player:
            self.player.show()
            self.player.place_bottom_right()
        self.player.set_speed(app.config.speed)

    # --- menu ------------------------------------------------------------
    def _menu(self) -> QMenu:
        menu = QMenu()
        menu.addAction("Read this window", self.app.read_smart)
        menu.addAction("Read selection", self.app.read_selection)
        menu.addAction("Read clipboard", self.app.read_clipboard)
        menu.addAction("Read screen (OCR)", lambda: self.app.read_screen(False))
        menu.addAction("Read screen, scrolling",
                       lambda: self.app.read_screen(True))
        menu.addSeparator()
        menu.addAction("Read latest Claude session",
                       lambda: self.app.read_claude_session(last_n=6))
        menu.addSeparator()
        menu.addAction("Play / pause", self.app.toggle)
        menu.addAction("Stop", self.app.stop)
        menu.addAction("Bookmark here", lambda: self.app.bookmark())
        menu.addSeparator()
        menu.addAction("Show player", self._show_player)
        menu.addAction("Library and settings", self.show_library)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        return menu

    def _tray_clicked(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self._show_player()
        elif reason == QSystemTrayIcon.DoubleClick:
            self.show_library()

    def _show_player(self) -> None:
        self.player.show()
        self.player.place_bottom_right()
        self.player.raise_()

    def show_library(self) -> None:
        self.library.show()
        self.library.raise_()
        self.library.activateWindow()
        self.library.refresh()

    # --- hotkeys ---------------------------------------------------------
    def _bind_hotkeys(self) -> None:
        keys = self.app.config.hotkeys
        actions = {
            "read_smart": self.app.read_smart,
            "read_clip": self.app.read_clipboard,
            "read_ocr": lambda: self.app.read_screen(False),
            "play_pause": self.app.toggle,
            "stop": self.app.stop,
            "next_sent": self.app.next_segment,
            "prev_sent": self.app.prev_segment,
            "faster": self.app.faster,
            "slower": self.app.slower,
            "bookmark": lambda: self.app.bookmark(),
        }
        for name, action in actions.items():
            spec = keys.get(name)
            if spec:
                self.hotkeys.bind(name, spec, action)
        self.hotkeys.start()

        if self.hotkeys.failed:
            taken = ", ".join(name + " (" + spec + ")"
                              for name, spec in self.hotkeys.failed)
            note = "Another app already owns: " + taken
            self.library.set_hotkey_note(note)
            self.bridge.status.emit(note)
        else:
            self.library.set_hotkey_note(
                "All shortcuts registered. Edit them in the config file.")

    # --- events ----------------------------------------------------------
    @Slot(str)
    def _on_status(self, message: str) -> None:
        self.player.set_status(message)
        self.tray.setToolTip("Earmark — " + message)

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self.player.set_status(message)
        self.tray.showMessage("Earmark", message, speaker_icon(active=False), 4000)

    @Slot(str)
    def _on_state(self, state: str) -> None:
        self.player.set_state(state)
        self.tray.setIcon(speaker_icon(active=(state == PLAYING)))

    @Slot(int, str, int)
    def _on_segment(self, index: int, text: str, total: int) -> None:
        self.player.set_segment(index, text, total)

    @Slot(str, int)
    def _on_document(self, title: str, total: int) -> None:
        self.player.set_document(title, total)
        self.player.set_speed(self.app.config.speed)
        if not self.player.isVisible() and self.app.config.mini_player:
            self._show_player()

    def _reopen(self, uri: str) -> None:
        """Reopen something from history by re-reading its original source."""
        if not uri:
            return
        if uri.startswith("file:///"):
            from urllib.parse import unquote, urlparse
            self.app.read_file(unquote(urlparse(uri).path).lstrip("/"))
        elif uri.startswith("claude://"):
            from ..capture import claude_transcript as ct
            path = ct.projects_dir(self.app.config.claude_projects_dir)
            match = next((p for p in ct.sessions(path, limit=999)
                          if p.stem == uri.removeprefix("claude://")), None)
            if match is not None:
                self.app.read_claude_session(match)
            else:
                self.bridge.error.emit("That transcript is no longer on disk.")
        else:
            self.bridge.error.emit(
                "Open it in its app, then press the read shortcut.")

    def quit(self) -> None:
        try:
            self.hotkeys.stop()
            self.app.shutdown()
        finally:
            self.tray.hide()
            self.qt_app.quit()
