"""Wiring: capture sources, the reader, storage and shortcuts in one object.

Deliberately free of Qt so the whole application can be driven and tested
headlessly. The UI layer subscribes to the callbacks exposed here.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .capture import claude_transcript as ct
from .capture import clipboard, files, ladder, ocr
from .config import Config
from .document import Document
from .player.engine import IDLE, PLAYING, Reader
from .store.anchors import AMBIGUOUS, BOUNDARY, EXACT, FUZZY, Anchor, Match
from .store.db import Store
from .textproc.pronounce import Dictionary
from .tts.registry import Registry


def _noop(*_a, **_k) -> None:
    pass


def position_note(match: Match | None, rules_changed: bool) -> str:
    """What to tell the reader about where a resume landed.

    Worded per recovery path rather than per stamp. An exact quote match means
    the sentence is present verbatim in the current text, so the position is
    right whatever the stamp says, and calling that approximate overstates the
    doubt. Only the paths that actually guessed say so.

    The five situations are defined in docs/anchor-vocabulary.md, a contract
    both halves implement. The situation crosses the boundary; the prose stays
    local, because a tray notification and a side panel have different tone and
    length budgets.
    """
    if match is None or match.how == EXACT:
        return ""
    if match.how == AMBIGUOUS:
        return " This sentence appears more than once, so this is the closest match."
    if match.how == BOUNDARY:
        return (" The sentence is longer or shorter than when you saved this,"
                " so this is the closest match.")
    if match.how == FUZZY:
        cause = ("The reading rules changed" if rules_changed
                 else "The page changed")
        return " " + cause + " since you saved this, so this is the nearest sentence."
    return (" Could not find where you stopped, so reading starts from the"
            " saved position.")


class App:
    def __init__(self, config: Config | None = None,
                 store: Store | None = None) -> None:
        self.config = config or Config.load()
        self.store = store or Store()
        self.registry = Registry()
        self.dictionary = Dictionary(self.store.rules())
        self.reader = Reader(self.registry, self.config, self.dictionary)

        # UI hooks.
        self.on_status: Callable[[str], None] = _noop
        self.on_document: Callable[[Document, int], None] = _noop
        self.on_state: Callable[[str], None] = _noop
        self.on_segment: Callable[[int, str, int], None] = _noop
        self.on_error: Callable[[str], None] = _noop

        self.reader.on_state = self._state_changed
        self.reader.on_segment = self._segment_changed
        self.reader.on_finished = self._finished
        self.reader.on_error = lambda msg: self.on_error(msg)

        self._doc: Document | None = None
        self._segment_started = 0.0
        self._save_lock = threading.Lock()

        self._clip_watcher: clipboard.Watcher | None = None
        self._claude_tail: ct.Tail | None = None
        self._claude_thread: threading.Thread | None = None
        self._claude_stop = threading.Event()
        self._sleep_timer: threading.Timer | None = None

        if self.config.clipboard_watch:
            self.set_clipboard_watch(True)
        if self.config.claude_watch:
            self.set_claude_watch(True)

    # --- reader events ---------------------------------------------------
    def _state_changed(self, state: str) -> None:
        if state != PLAYING:
            self._flush_position()
        self.on_state(state)

    def _segment_changed(self, index: int, text: str, total: int) -> None:
        now = time.monotonic()
        elapsed = now - self._segment_started if self._segment_started else 0.0
        self._segment_started = now
        self._persist(index, min(elapsed, 120.0), finished=False)
        self.on_segment(index, text, total)

    def _finished(self) -> None:
        self._persist(self.reader.total, 0.0, finished=True)
        self.on_status("Finished reading.")
        self.on_state(IDLE)

    def _flush_position(self) -> None:
        if self._doc is not None:
            self._persist(self.reader.index, 0.0, finished=False)

    def _persist(self, index: int, seconds: float, finished: bool) -> None:
        doc = self._doc
        if doc is None:
            return
        anchor = Anchor.create(self.reader.segments, index)
        with self._save_lock:
            try:
                self.store.save_position(doc.uri, index, anchor, seconds, finished)
            except Exception:
                pass

    # --- reading ---------------------------------------------------------
    def read(self, doc: Document, resume: bool | None = None) -> int:
        """Start reading a document, resuming where it was left off."""
        self._doc = doc
        segments_guess = 0
        self.store.touch(doc.uri, doc.title, doc.source, doc.snippet, 0)
        self._apply_site_voice(doc)

        resume = self.config.auto_resume if resume is None else resume
        start = 0
        anchor = None
        rules_changed = False
        if resume:
            start, anchor, rules_changed = self.store.position(doc.uri)

        total = self.reader.load(doc, start_index=0, autoplay=False)
        segments_guess = total

        match = None
        if anchor is not None and self.reader.segments:
            match = anchor.locate(self.reader.segments, rules_changed)
            start = match.index
        start = max(0, min(start, max(0, total - 1)))

        self.store.touch(doc.uri, doc.title, doc.source, doc.snippet, total)
        self._segment_started = time.monotonic()
        self.on_document(doc, total)
        if total:
            self.reader.start_at(start)
            self.reader.play()
            where = (" from sentence " + str(start + 1)) if start else ""
            note = position_note(match, rules_changed) if start else ""
            self.on_status("Reading " + doc.title + where + "." + note)
        else:
            self.on_status("Nothing readable in " + doc.title + ".")
        return segments_guess

    def _apply_site_voice(self, doc: Document) -> None:
        """Per-site voice and speed, when one was saved for this host."""
        host = ""
        url = doc.meta.get("url") or doc.uri
        try:
            parsed = urlparse(url if "://" in url else "//" + url)
            host = (parsed.netloc or "").lower().removeprefix("www.")
        except ValueError:
            host = ""
        if not host:
            return
        pref = self.store.site_pref(host)
        if pref is None:
            return
        engine, voice, speed = pref
        if engine:
            self.reader.set_voice(engine, voice)
        if speed:
            self.reader.set_speed(speed)

    # --- capture entry points -------------------------------------------
    def read_smart(self) -> None:
        self.on_status("Looking for text...")
        result = ladder.smart(allow_ocr=self.config.ocr_enabled)
        if not result:
            hint = ladder.chrome_hint()
            self.on_error(" ".join(result.notes + ([hint] if hint else [])))
            return
        for note in result.notes:
            self.on_status(note)
        self.read(result.doc)

    def read_clipboard(self) -> None:
        doc = clipboard.capture()
        if doc is None:
            self.on_error("The clipboard has no text.")
            return
        self.read(doc)

    def read_selection(self) -> None:
        doc = clipboard.copy_selection()
        if doc is None:
            self.on_error("Nothing is selected.")
            return
        self.read(doc)

    def read_screen(self, scrolled: bool = False) -> None:
        if not ocr.available():
            self.on_error("Screen OCR needs mss, Pillow and winocr.")
            return
        self.on_status("Reading the screen...")
        try:
            doc = ocr.scroll_and_stitch() if scrolled else ocr.capture()
        except ocr.OCRUnavailable as exc:
            self.on_error(str(exc))
            return
        if doc is None:
            self.on_error("No text was recognised on screen.")
            return
        self.read(doc)

    def read_file(self, path: str | Path) -> None:
        try:
            doc = files.read(path)
        except files.UnsupportedFile as exc:
            self.on_error(str(exc))
            return
        except Exception as exc:
            self.on_error("Could not read that file: " + str(exc))
            return
        if doc.meta.get("needs_ocr"):
            self.on_error("That PDF has no text layer, so it is probably "
                          "scanned. Screen OCR is the only way to read it.")
            return
        self.read(doc)

    def read_text(self, text: str, title: str = "Pasted text") -> None:
        if not text.strip():
            self.on_error("Nothing to read.")
            return
        self.read(Document(text=text, title=title, source="text"))

    # --- claude ----------------------------------------------------------
    def read_claude_session(self, path: Path | None = None,
                            last_n: int | None = None) -> None:
        target = path or ct.latest_session()
        if target is None:
            self.on_error("No Claude Code transcripts were found.")
            return
        doc = ct.as_document(target, self.config.claude_read_thinking, last_n)
        if doc is None:
            self.on_error("That Claude session has nothing to read.")
            return
        self.read(doc)

    def set_claude_watch(self, enabled: bool) -> None:
        """Read new Claude replies aloud as they are written to the transcript."""
        self.config.claude_watch = enabled
        if not enabled:
            self._claude_stop.set()
            self._claude_thread = None
            self._claude_tail = None
            return
        target = ct.latest_session(ct.projects_dir(self.config.claude_projects_dir))
        if target is None:
            self.on_error("No Claude Code transcripts were found.")
            self.config.claude_watch = False
            return
        self._claude_tail = ct.Tail(target, self.config.claude_read_thinking)
        self._claude_stop.clear()
        self._claude_thread = threading.Thread(target=self._claude_loop, daemon=True,
                                               name="executive-reader-claude")
        self._claude_thread.start()
        self.on_status("Watching " + target.name[:8] + " for new replies.")

    def _claude_loop(self) -> None:
        while not self._claude_stop.is_set():
            tail = self._claude_tail
            if tail is None:
                return
            try:
                turns = tail.poll()
            except Exception:
                turns = []
            for turn in turns:
                body = turn.speakable(self.config.claude_read_thinking)
                if body.strip():
                    self.read(Document(text=body, title="Claude reply",
                                       source="claude"), resume=False)
            self._claude_stop.wait(1.0)

    # --- clipboard watch -------------------------------------------------
    def set_clipboard_watch(self, enabled: bool) -> None:
        self.config.clipboard_watch = enabled
        if enabled:
            if self._clip_watcher is None:
                self._clip_watcher = clipboard.Watcher(
                    lambda doc: self.read(doc, resume=False))
            self._clip_watcher.start()
            self.on_status("Reading everything you copy.")
        elif self._clip_watcher is not None:
            self._clip_watcher.stop()
            self._clip_watcher = None

    # --- transport -------------------------------------------------------
    def toggle(self) -> None:
        self.reader.toggle()

    def stop(self) -> None:
        self.reader.stop()

    def next_segment(self) -> None:
        self.reader.next_segment()

    def prev_segment(self) -> None:
        self.reader.prev_segment()

    def faster(self) -> None:
        speed = self.reader.nudge_speed(0.25)
        self.on_status("Speed " + ("%.2f" % speed).rstrip("0").rstrip(".") + "x")
        self.config.save()

    def slower(self) -> None:
        speed = self.reader.nudge_speed(-0.25)
        self.on_status("Speed " + ("%.2f" % speed).rstrip("0").rstrip(".") + "x")
        self.config.save()

    def set_voice(self, engine: str, voice: str) -> None:
        self.reader.set_voice(engine, voice)
        self.config.save()
        self.on_status("Voice: " + (voice or engine))

    def bookmark(self, note: str = "") -> int | None:
        if self._doc is None or not self.reader.segments:
            self.on_error("Nothing is being read.")
            return None
        index = self.reader.index
        anchor = Anchor.create(self.reader.segments, index)
        bid = self.store.add_bookmark(self._doc.uri, self._doc.title, index,
                                      anchor, note)
        self.on_status("Bookmarked sentence " + str(index + 1) + ".")
        return bid

    def resume_bookmark(self, bookmark_id: int) -> None:
        for mark in self.store.bookmarks():
            if mark.id != bookmark_id:
                continue
            if self._doc is not None and self._doc.uri == mark.uri:
                found = mark.anchor.locate(self.reader.segments,
                                           mark.rules_changed)
                self.reader.seek(found.index)
                # Same wording as resuming, because it is the same situation.
                self.on_status("Jumped to the bookmark."
                               + position_note(found, mark.rules_changed))
                return
            self.on_error("Open " + mark.title + " first, then resume it.")
            return

    def set_sleep_timer(self, minutes: int) -> None:
        if self._sleep_timer is not None:
            self._sleep_timer.cancel()
            self._sleep_timer = None
        self.config.sleep_timer_minutes = max(0, int(minutes))
        if minutes <= 0:
            self.on_status("Sleep timer off.")
            return
        self._sleep_timer = threading.Timer(minutes * 60.0, self._sleep_fired)
        self._sleep_timer.daemon = True
        self._sleep_timer.start()
        self.on_status("Stopping in " + str(minutes) + " minutes.")

    def _sleep_fired(self) -> None:
        self.reader.pause()
        self.on_status("Sleep timer reached; paused.")

    def reload_dictionary(self) -> None:
        """Pick up edited pronunciation rules, including for audio already made."""
        self.dictionary.set_rules(self.store.rules())
        self.reader.pronunciation_changed()

    def shutdown(self) -> None:
        self._flush_position()
        self._claude_stop.set()
        if self._clip_watcher is not None:
            self._clip_watcher.stop()
        if self._sleep_timer is not None:
            self._sleep_timer.cancel()
        self.config.save()
        self.reader.shutdown()
        self.store.close()
