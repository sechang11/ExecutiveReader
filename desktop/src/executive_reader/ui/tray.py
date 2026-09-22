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
from .clipboard_window import ClipboardWindow, document_title
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
        self.tray.setToolTip("Executive Reader")
        self.tray.setContextMenu(self._menu())
        self.tray.activated.connect(self._tray_clicked)
        self.tray.show()

        self.hotkeys = Hotkeys()
        self._bind_hotkeys()

        if app.config.mini_player:
            self.player.show()
            self.player.place_bottom_right()
        self.player.set_speed(app.config.speed)
        self.player.speed_changed.connect(self._speed_changed)
        # The area picker and the recent list live on the player because that
        # is the window that is actually on screen; the Screen tab owns the
        # machinery and hands it the previews.
        self.player.choose_area.connect(self._choose_area)
        self.player.open_row.connect(self._open_recent)
        self._reading_window = ClipboardWindow()
        self._reading_window.read_from.connect(self._read_passage)
        self.player.open_item.connect(self._open_item)
        self.player.read_screen.connect(self._read_screen_or_area)
        claude = getattr(self.library, "claude", None)
        if claude is not None:
            claude.watch_changed.connect(self._claude_watch_shown)
        screen = getattr(self.library, "screen", None)
        if screen is not None:
            screen.captures_changed.connect(self.player.set_recent)
            screen.area_changed.connect(self.player.set_area)
            screen.now_reading.connect(self.player.set_item_title)
            # One window, however it is reached: from the title on the
            # player, from a row in the list, or from the Screen tab.
            screen.use_window(self._reading_window)

    def _read_passage(self, text: str) -> None:
        """Read something chosen in the capture window, under its own name."""
        self.app.read_text(text, self._reading_window.name() or "Selected passage")

    def _open_item(self) -> None:
        """Open whatever is being read, whichever route it came from."""
        screen = getattr(self.library, "screen", None)
        doc = getattr(self.app, "_doc", None)
        text = (getattr(doc, "text", "") or "").strip()
        # When the two hold the same words, show the capture rather than the
        # document made from it: scrolling keeps adding to the capture, and
        # the document is the snapshot taken when reading started.
        live = screen.live_capture() if screen is not None else None
        if live is not None and (live.text or "").strip() == text:
            screen.open_current()
            return
        if text:
            self._reading_window.show_document(doc)
            return
        if screen is not None:
            screen.open_current()

    def _claude_watch_shown(self, on: bool) -> None:
        """The tab changed it; show the same thing in the tray."""
        self.act_claude.setChecked(bool(on))

    def _set_claude_watch(self, on: bool) -> None:
        """Follow Claude, from the tray. Kept in step with the Claude tab."""
        self.app.config.claude_watch_chosen = True
        self.app.set_claude_watch(on)
        self.app.config.save()
        claude = getattr(self.library, "claude", None)
        if claude is not None:
            claude.chk_watch.blockSignals(True)
            claude.chk_watch.setChecked(bool(self.app.config.claude_watch))
            claude.chk_watch.blockSignals(False)
        self.act_claude.setChecked(bool(self.app.config.claude_watch))

    def _read_screen_or_area(self) -> None:
        """The area when one is chosen, the whole display when not."""
        screen = getattr(self.library, "screen", None)
        if screen is not None and screen.has_area():
            screen.read_area_now()
            return
        self.app.read_screen(False)

    def _choose_area(self) -> None:
        screen = getattr(self.library, "screen", None)
        if screen is not None:
            screen.choose_area()

    def _open_recent(self, index: int) -> None:
        """Show one of the recent captures in full."""
        screen = getattr(self.library, "screen", None)
        if screen is not None:
            screen.open_row(index)

    def _speed_changed(self, speed: float) -> None:
        self.app.set_speed(speed)

    # --- menu ------------------------------------------------------------
    def _set_watch(self, mode: str) -> None:
        self.app.set_window_watch(mode)
        current = self.app.config.window_watch
        # Set without re-firing: assigning checked emits triggered on some
        # platforms, which would toggle the mode straight back off.
        for action, name in ((self.act_follow, "follow"),
                             (self.act_locked, "locked")):
            action.blockSignals(True)
            action.setChecked(current == name)
            action.blockSignals(False)

    def _menu(self) -> QMenu:
        menu = QMenu()
        menu.addAction("Read this window", self.app.read_smart)
        menu.addAction("Read selection", self.app.read_selection)
        menu.addAction("Read clipboard", self.app.read_clipboard)
        menu.addAction("Read screen (OCR)", lambda: self.app.read_screen(False))
        menu.addAction("Read screen, scrolling",
                       lambda: self.app.read_screen(True))
        menu.addSeparator()
        # Checkable, and mutually exclusive, because the two modes answer
        # different questions and having both on means nothing.
        self.act_follow = menu.addAction("Watch: follow the front window")
        self.act_follow.setCheckable(True)
        self.act_follow.triggered.connect(
            lambda on: self._set_watch("follow" if on else "off"))
        self.act_locked = menu.addAction("Watch: lock to this window")
        self.act_locked.setCheckable(True)
        self.act_locked.triggered.connect(
            lambda on: self._set_watch("locked" if on else "off"))
        menu.addSeparator()
        # The main job, reachable without opening anything.
        self.act_claude = menu.addAction("Read Claude's replies as they arrive")
        self.act_claude.setCheckable(True)
        self.act_claude.setChecked(bool(self.app.config.claude_watch))
        self.act_claude.triggered.connect(self._set_claude_watch)
        menu.addAction("Read the last six Claude replies",
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
        self.tray.setToolTip("Executive Reader — " + message)

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self.player.set_status(message)
        self.tray.showMessage("Executive Reader", message, speaker_icon(active=False), 4000)

    @Slot(str)
    def _on_state(self, state: str) -> None:
        self.player.set_state(state)
        self.tray.setIcon(speaker_icon(active=(state == PLAYING)))

    @Slot(int, str, int)
    def _on_segment(self, index: int, text: str, total: int) -> None:
        self.player.set_segment(index, text, total)
        # The Screen tab draws over the words when they came from a watched
        # area. It is the only route that knows where the text is on screen.
        screen = getattr(self.library, "screen", None)
        if screen is not None:
            screen.on_segment(text)

    @Slot(str, int)
    def _on_document(self, title: str, total: int) -> None:
        self.player.set_document(title, total)
        # Name whatever is being read, from any route, not only from a screen
        # area. The title was wired to the screen watcher alone, so reading a
        # page or the clipboard left the line blank and clicking it did
        # nothing, which is most of the ways this app gets used.
        doc = getattr(self.app, "_doc", None)
        self.player.set_item_title(document_title(doc, title))
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
        screen = getattr(self.library, "screen", None)
        if screen is not None:
            screen.shutdown()
        try:
            self.hotkeys.stop()
            self.app.shutdown()
        finally:
            self.tray.hide()
            self.qt_app.quit()
