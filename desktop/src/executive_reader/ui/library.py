"""History, bookmarks, voices, pronunciation and settings."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QDoubleSpinBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QProgressDialog, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem,
                               QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from ..tts.piper_engine import CATALOG as PIPER_CATALOG
from .claude_tab import ClaudeTab
from .screen_tab import ScreenTab
from ..textproc import shared_rules
from ..textproc.pronounce import Rule
from ..tts.base import EngineError
from ..tts.download import human


def _ago(stamp: float) -> str:
    if not stamp:
        return ""
    seconds = max(0, time.time() - stamp)
    for limit, unit, size in ((60, "s", 1), (3600, "m", 60),
                              (86400, "h", 3600), (604800, "d", 86400)):
        if seconds < limit:
            return str(int(seconds // size)) + unit + " ago"
    return time.strftime("%d %b", time.localtime(stamp))


class Library(QMainWindow):
    read_uri = Signal(str)

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.setWindowTitle("Executive Reader")
        self.resize(860, 620)

        tabs = QTabWidget()
        # Claude first, because reading Claude is what this is for and it is
        # also the route that needs nothing set up. It was a checkbox on the
        # sixth tab while the screen recogniser, which is the last resort,
        # had the first one and all the buttons. Someone following the
        # interface was led straight to the worst way of doing the main job.
        self.claude = ClaudeTab(app)
        self.claude.status.connect(lambda message: app.on_status(message))
        tabs.addTab(self.claude, "Claude")
        # Second: how to make it read anything else.
        self.screen = ScreenTab(app)
        self.screen.status.connect(lambda message: app.on_status(message))
        tabs.addTab(self.screen, "Anything else")
        tabs.addTab(self._history_tab(), "History")
        tabs.addTab(self._bookmarks_tab(), "Bookmarks")
        tabs.addTab(self._voices_tab(), "Voices")
        tabs.addTab(self._words_tab(), "Pronunciation")
        tabs.addTab(self._settings_tab(), "Settings")
        tabs.currentChanged.connect(lambda _i: self.refresh())
        self.setCentralWidget(tabs)
        self.refresh()

    # --- history ---------------------------------------------------------
    def _history_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        row = QHBoxLayout()
        self.unfinished_only = QCheckBox("Only unfinished")
        self.unfinished_only.stateChanged.connect(lambda _s: self.refresh())
        row.addWidget(self.unfinished_only)
        row.addStretch(1)
        clear = QPushButton("Clear history")
        clear.clicked.connect(self._clear_history)
        row.addWidget(clear)
        layout.addLayout(row)

        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(
            ["Title", "Source", "Progress", "Listened", "Last read"])
        self._tune(self.history_table)
        self.history_table.cellDoubleClicked.connect(self._resume_history)
        layout.addWidget(self.history_table)

        hint = QLabel("Double-click a row to reopen it where you stopped.")
        hint.setStyleSheet("color:#888;")
        layout.addWidget(hint)
        return page

    def _tune(self, table: QTableWidget) -> None:
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

    def _refresh_history(self) -> None:
        rows = self.app.store.history(unfinished_only=self.unfinished_only.isChecked())
        self.history_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            minutes = row.seconds / 60.0
            cells = [row.title, row.source,
                     str(int(row.progress * 100)) + "%",
                     ("%.0f min" % minutes) if minutes >= 1 else "<1 min",
                     _ago(row.last_read)]
            for col, value in enumerate(cells):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.UserRole, row.uri)
                    item.setToolTip(row.snippet)
                self.history_table.setItem(i, col, item)

    def _resume_history(self, row: int, _col: int) -> None:
        item = self.history_table.item(row, 0)
        if item is not None:
            self.read_uri.emit(str(item.data(Qt.UserRole) or ""))

    def _clear_history(self) -> None:
        confirm = QMessageBox.question(
            self, "Clear history",
            "Delete every history entry? Bookmarks are kept.")
        if confirm == QMessageBox.Yes:
            self.app.store.clear_history()
            self.refresh()

    # --- bookmarks -------------------------------------------------------
    def _bookmarks_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.bookmark_table = QTableWidget(0, 4)
        self.bookmark_table.setHorizontalHeaderLabels(
            ["Sentence", "Document", "Note", "Saved"])
        self._tune(self.bookmark_table)
        layout.addWidget(self.bookmark_table)

        row = QHBoxLayout()
        row.addStretch(1)
        delete = QPushButton("Delete selected")
        delete.clicked.connect(self._delete_bookmark)
        row.addWidget(delete)
        layout.addLayout(row)
        return page

    def _refresh_bookmarks(self) -> None:
        marks = self.app.store.bookmarks()
        self.bookmark_table.setRowCount(len(marks))
        for i, mark in enumerate(marks):
            cells = [mark.anchor.exact[:90], mark.title, mark.note,
                     _ago(mark.created)]
            for col, value in enumerate(cells):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.UserRole, mark.id)
                self.bookmark_table.setItem(i, col, item)

    def _delete_bookmark(self) -> None:
        row = self.bookmark_table.currentRow()
        if row < 0:
            return
        item = self.bookmark_table.item(row, 0)
        if item is not None:
            self.app.store.delete_bookmark(int(item.data(Qt.UserRole)))
            self.refresh()

    # --- voices ----------------------------------------------------------
    def _voices_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self.engine_status = QLabel("")
        self.engine_status.setStyleSheet("color:#888;")
        layout.addWidget(self.engine_status)

        picker = QHBoxLayout()
        picker.addWidget(QLabel("Voice"))
        self.voice_box = QComboBox()
        self.voice_box.setMinimumWidth(360)
        picker.addWidget(self.voice_box, 1)
        use = QPushButton("Use")
        use.clicked.connect(self._use_voice)
        picker.addWidget(use)
        preview = QPushButton("Preview")
        preview.clicked.connect(self._preview_voice)
        picker.addWidget(preview)
        layout.addLayout(picker)

        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Speed"))
        self.speed_box = QDoubleSpinBox()
        self.speed_box.setRange(0.5, 4.0)
        self.speed_box.setSingleStep(0.25)
        self.speed_box.setValue(self.app.config.speed)
        self.speed_box.valueChanged.connect(self.app.reader.set_speed)
        speed_row.addWidget(self.speed_box)
        speed_row.addStretch(1)
        layout.addLayout(speed_row)

        download = QHBoxLayout()
        self.kokoro_button = QPushButton("Download Kokoro voices (~330 MB)")
        self.kokoro_button.clicked.connect(self._install_kokoro)
        download.addWidget(self.kokoro_button)
        download.addStretch(1)
        layout.addLayout(download)

        # Piper is a second opinion rather than an upgrade. Its voices are
        # trained one per speaker, so a particular one can suit a listener
        # better than Kokoro even though Kokoro is the stronger model overall,
        # and each is a small download rather than one large one.
        piper_row = QHBoxLayout()
        piper_row.addWidget(QLabel("Piper voice:"))
        self.piper_box = QComboBox()
        for vid, (_folder, label, lang, gender, size_mb) in PIPER_CATALOG.items():
            shown = label + "  " + lang + (" " + gender if gender else "")
            shown += "   " + str(size_mb) + " MB"
            self.piper_box.addItem(shown, vid)
        piper_row.addWidget(self.piper_box, 1)
        self.piper_button = QPushButton("Download this one")
        self.piper_button.clicked.connect(self._install_piper)
        piper_row.addWidget(self.piper_button)
        layout.addLayout(piper_row)

        from ..tts import espeak_addon
        note = QLabel(
            espeak_addon.detect().summary
            + "\n\nKokoro's model and voice packs are Apache 2.0. Piper voice "
            "licences vary per voice; check the model card before selling "
            "anything built on one.\n\n"
            "To use a voice you trained yourself, drop its .onnx and .onnx.json "
            "into the voices/piper folder and it appears in this list.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#888;")
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _refresh_voices(self) -> None:
        # Say which rule data is in use as well as which engines are. When
        # shared/ cannot be found the app falls back to built-in rules and
        # still works, so the only symptom is a word pronounced by a rule
        # someone changed weeks ago. shared_rules.describe() was written to
        # answer exactly that and nothing was calling it.
        status = list(self.app.registry.describe())
        status.append(shared_rules.describe())
        self.engine_status.setText("   ".join(status))
        current = self.voice_box.currentData()
        self.voice_box.clear()
        for voice in self.app.registry.voices():
            label = voice.engine + " · " + voice.name
            if voice.gender:
                label += " (" + voice.gender + ")"
            if voice.note:
                label += "  — " + voice.note
            self.voice_box.addItem(label, (voice.engine, voice.id))
        if current is not None:
            index = self.voice_box.findData(current)
            if index >= 0:
                self.voice_box.setCurrentIndex(index)
        self.kokoro_button.setEnabled(not self.app.registry.kokoro.installed)
        if self.app.registry.kokoro.installed:
            self.kokoro_button.setText("Kokoro voices installed")

    def _use_voice(self) -> None:
        data = self.voice_box.currentData()
        if data:
            self.app.set_voice(data[0], data[1])
            self.refresh()

    def _preview_voice(self) -> None:
        data = self.voice_box.currentData()
        if not data:
            return
        engine, voice = data
        try:
            # Through the app, which puts the previous voice back afterwards.
            # Reaching into the reader writes the voice into the config, so a
            # voice you listened to and rejected became your default.
            self.app.preview_voice(engine, voice)
        except EngineError as exc:
            QMessageBox.warning(self, "Voice unavailable", str(exc))

    def _install_piper(self) -> None:
        """Fetch one Piper voice: a model file and its settings."""
        voice_id = self.piper_box.currentData()
        if not voice_id:
            return
        engine = self.app.registry.piper
        dialog = QProgressDialog("Downloading " + voice_id + "...", "Cancel", 0, 100, self)
        dialog.setWindowModality(Qt.WindowModal)
        dialog.show()

        def progress(done: int, total: int) -> None:
            if total:
                dialog.setValue(int(done / total * 100))
            dialog.setLabelText("Downloading " + voice_id + chr(10)
                                + human(done) + " of " + human(total))
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()

        try:
            engine.install(voice_id, progress)
        except Exception as exc:
            dialog.close()
            QMessageBox.critical(self, "Download failed", str(exc))
            return
        dialog.close()
        self.refresh()
        QMessageBox.information(
            self, "Voice ready",
            voice_id + " is installed. Pick it in the list above to use it.")

    def _install_kokoro(self) -> None:
        kokoro = self.app.registry.kokoro
        if not kokoro.library_present:
            QMessageBox.warning(self, "Missing package",
                                "Install it first:\n\npip install kokoro-onnx")
            return
        dialog = QProgressDialog("Downloading Kokoro voices...", "Cancel", 0, 100, self)
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setAutoClose(False)
        dialog.show()

        def progress(done: int, total: int) -> None:
            if total:
                dialog.setValue(int(done / total * 100))
            dialog.setLabelText("Downloading Kokoro voices...\n" +
                                human(done) + " of " + human(total))
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()

        try:
            kokoro.install(progress)
        except Exception as exc:
            dialog.close()
            QMessageBox.critical(self, "Download failed", str(exc))
            return
        dialog.close()
        QMessageBox.information(self, "Ready",
                                "Kokoro voices installed. Pick one above.")
        self.refresh()

    # --- pronunciation ---------------------------------------------------
    def _words_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(
            "Override how particular words are said. This is the cheapest way "
            "to fix names, acronyms and jargon."))

        self.rules_table = QTableWidget(0, 2)
        self.rules_table.setHorizontalHeaderLabels(["Written", "Spoken as"])
        self._tune(self.rules_table)
        self.rules_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.rules_table)

        row = QHBoxLayout()
        self.rule_pattern = QLineEdit()
        self.rule_pattern.setPlaceholderText("Written, e.g. nginx")
        self.rule_value = QLineEdit()
        self.rule_value.setPlaceholderText("Spoken as, e.g. engine ex")
        row.addWidget(self.rule_pattern)
        row.addWidget(self.rule_value, 1)
        add = QPushButton("Add")
        add.clicked.connect(self._add_rule)
        row.addWidget(add)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self._remove_rule)
        row.addWidget(remove)
        layout.addLayout(row)
        return page

    def _refresh_rules(self) -> None:
        rules = sorted(self.app.store.rules(), key=lambda r: r.pattern.lower())
        self.rules_table.setRowCount(len(rules))
        for i, rule in enumerate(rules):
            self.rules_table.setItem(i, 0, QTableWidgetItem(rule.pattern))
            self.rules_table.setItem(i, 1, QTableWidgetItem(rule.replacement))

    def _add_rule(self) -> None:
        pattern = self.rule_pattern.text().strip()
        if not pattern:
            return
        self.app.store.upsert_rule(Rule(pattern=pattern,
                                        replacement=self.rule_value.text().strip()))
        self.app.reload_dictionary()
        self.rule_pattern.clear()
        self.rule_value.clear()
        self.refresh()

    def _remove_rule(self) -> None:
        row = self.rules_table.currentRow()
        if row < 0:
            return
        item = self.rules_table.item(row, 0)
        if item is not None:
            self.app.store.delete_rule(item.text())
            self.app.reload_dictionary()
            self.refresh()

    # --- settings --------------------------------------------------------
    def _settings_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        cfg = self.app.config

        self.chk_resume = QCheckBox("Resume where I stopped")
        self.chk_resume.setChecked(cfg.auto_resume)
        layout.addWidget(self.chk_resume)

        self.chk_code = QCheckBox("Do not even mention code blocks")
        self.chk_code.setToolTip(
            "Left unticked, a code block is announced with its language and "
            "length -- \"PowerShell code block, two lines\" -- and the code "
            "itself is not read out. Tick this to leave it out in silence.\n\n"
            "It used to be the other way round, so the default removed code "
            "without a word and this was the only way to be told it was "
            "there.")
        self.chk_code.setChecked(cfg.skip_code_blocks)
        layout.addWidget(self.chk_code)

        self.chk_ocr = QCheckBox("Allow screen OCR as a fallback")
        self.chk_ocr.setChecked(cfg.ocr_enabled)
        layout.addWidget(self.chk_ocr)

        self.chk_clip = QCheckBox("Read everything I copy")
        self.chk_clip.setChecked(cfg.clipboard_watch)
        layout.addWidget(self.chk_clip)

        # The Claude switches live on the Claude tab. They were here as well,
        # and two checkboxes bound to one setting is a way to lose it:
        # turning it on over there and then saving anything here wrote back
        # whatever this copy happened to be showing.
        claude_note = QLabel(
            "Reading Claude's replies, and whether to include the thinking, "
            "are on the Claude tab.")
        claude_note.setWordWrap(True)
        claude_note.setStyleSheet("color:#888;")
        layout.addWidget(claude_note)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("Read links as"))
        self.url_mode = QComboBox()
        self.url_mode.addItems(["domain", "skip", "full"])
        self.url_mode.setCurrentText(cfg.read_urls)
        url_row.addWidget(self.url_mode)
        url_row.addStretch(1)
        layout.addLayout(url_row)

        sleep_row = QHBoxLayout()
        sleep_row.addWidget(QLabel("Sleep timer (minutes, 0 = off)"))
        self.sleep_spin = QSpinBox()
        self.sleep_spin.setRange(0, 240)
        self.sleep_spin.setValue(cfg.sleep_timer_minutes)
        sleep_row.addWidget(self.sleep_spin)
        sleep_row.addStretch(1)
        layout.addLayout(sleep_row)

        open_row = QHBoxLayout()
        open_file = QPushButton("Read a file...")
        open_file.clicked.connect(self._open_file)
        open_row.addWidget(open_file)
        open_row.addStretch(1)
        layout.addLayout(open_row)

        layout.addWidget(QLabel("Paste text to read:"))
        self.paste_box = QTextEdit()
        self.paste_box.setPlaceholderText("Paste anything here, then press Read.")
        self.paste_box.setMaximumHeight(110)
        layout.addWidget(self.paste_box)
        read_pasted = QPushButton("Read pasted text")
        read_pasted.clicked.connect(self._read_pasted)
        layout.addWidget(read_pasted)

        save = QPushButton("Save settings")
        save.clicked.connect(self._save_settings)
        layout.addWidget(save)

        self.hotkey_note = QLabel("")
        self.hotkey_note.setWordWrap(True)
        self.hotkey_note.setStyleSheet("color:#888;")
        layout.addWidget(self.hotkey_note)
        layout.addStretch(1)
        return page

    def _open_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Read a file", "",
            "Readable files (*.pdf *.epub *.docx *.txt *.md);;All files (*)")
        if path:
            self.app.read_file(path)

    def _read_pasted(self) -> None:
        text = self.paste_box.toPlainText()
        if text.strip():
            self.app.read_text(text)

    def _save_settings(self) -> None:
        cfg = self.app.config
        cfg.auto_resume = self.chk_resume.isChecked()
        cfg.skip_code_blocks = self.chk_code.isChecked()
        cfg.ocr_enabled = self.chk_ocr.isChecked()
        cfg.read_urls = self.url_mode.currentText()
        cfg.save()
        self.app.set_clipboard_watch(self.chk_clip.isChecked())
        self.app.set_sleep_timer(self.sleep_spin.value())
        QMessageBox.information(self, "Saved", "Settings saved.")

    def set_hotkey_note(self, text: str) -> None:
        self.hotkey_note.setText(text)

    # --- shared ----------------------------------------------------------
    def refresh(self) -> None:
        self._refresh_history()
        self._refresh_bookmarks()
        self._refresh_voices()
        self._refresh_rules()
        self.speed_box.blockSignals(True)
        self.speed_box.setValue(self.app.config.speed)
        self.speed_box.blockSignals(False)
