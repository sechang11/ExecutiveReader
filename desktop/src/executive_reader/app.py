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
from .capture import clipboard, files, ladder, ocr, uia, watch
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

        self._window_watcher: watch.WindowWatcher | None = None
        self._watch_lock = threading.Lock()
        self._watch_pending: list[str] = []
        self._watch_title = ""
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
        # Anything the watcher queued while this was speaking goes now.
        self._flush_watched()

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

    def _stop_claude_watch(self, timeout: float = 2.0) -> None:
        """Stop the watcher and wait for it to actually be gone.

        Two faults lived in not waiting.

        Turning the setting off and straight back on, inside the one second the
        loop spends waiting, left the old thread alive: it woke, found the stop
        flag cleared again and a fresh tail in place, and carried on. Two
        threads then polled one Tail, which shares a file offset, so a reply
        arrived twice or not at all.

        The second is the shape that used to segfault the player on exit.
        shutdown() set the flag and went straight on to closing the database and
        the audio device, so a watcher part-way through reading a reply could
        touch both after they were gone.
        """
        self._claude_stop.set()
        thread = self._claude_thread
        self._claude_thread = None
        self._claude_tail = None
        if (thread is not None and thread.is_alive()
                and thread is not threading.current_thread()):
            thread.join(timeout=timeout)

    def set_claude_watch(self, enabled: bool) -> None:
        """Read new Claude replies aloud as they are written to the transcript."""
        self.config.claude_watch = enabled
        # Always from a clean state, including when this is called while already
        # watching, which otherwise started a second thread beside the first.
        self._stop_claude_watch()
        if not enabled:
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
            # One document for the whole batch, not one per turn.
            #
            # A poll returns everything written since the last one, and reading
            # is not a queue: Reader.load() stops whatever is speaking and
            # replaces it. So two replies landing in the same second meant the
            # first was cut off part-way through a sentence and the second
            # started, with nothing to say a reply had been skipped.
            #
            # Joining on a blank line keeps them separate to the segmenter,
            # which treats a paragraph break as a hard boundary, so each reply
            # still starts its own sentence.
            bodies = [turn.speakable(self.config.claude_read_thinking)
                      for turn in turns]
            bodies = [body.strip() for body in bodies if body.strip()]
            if bodies:
                self.read(Document(text=(chr(10) + chr(10)).join(bodies),
                                   title="Claude reply", source="claude"),
                          resume=False)
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

    # --- window watch ----------------------------------------------------
    def set_window_watch(self, mode: str) -> None:
        """Read new text as it appears in a window. "off", "follow" or "locked".

        `follow` tracks whichever window is in front. `locked` stays on the
        window that is in front when it is switched on, so a long answer keeps
        being read while you work somewhere else. Following cannot do that: the
        moment you click away it would start reading what you clicked on.

        Neither reads what is already on screen. Everything present when the
        watcher starts counts as seen, which is also what keeps sidebars,
        toolbars and navigation out of it without a list of things to ignore.
        """
        if self._window_watcher is not None:
            self._window_watcher.stop()
            self._window_watcher = None
        self.config.window_watch = mode if mode in (watch.FOLLOW, watch.LOCKED) else "off"
        if self.config.window_watch == "off":
            self.on_status("Stopped watching.")
            return

        target = ""
        if self.config.window_watch == watch.LOCKED:
            try:
                target = uia.window_info().title
            except Exception:
                target = ""
            if not target:
                self.on_error("Could not tell which window to lock onto.")
                self.config.window_watch = "off"
                return

        watcher = watch.WindowWatcher(
            on_text=self._watched_text,
            mode=self.config.window_watch,
            target=target,
            interval=self.config.window_watch_interval)
        watcher.on_error = self.on_error
        watcher.start()
        self._window_watcher = watcher
        if self.config.window_watch == watch.LOCKED:
            self.on_status("Reading new text in " + target[:40] + ".")
        else:
            self.on_status("Reading new text in whichever window is in front.")

    def _watched_text(self, text: str, title: str) -> None:
        """Queue a new passage instead of interrupting the one being read.

        Reading is not a queue: load() stops whatever is speaking and replaces
        it. A window produces text continuously, so handing each passage
        straight to the reader means every one cuts off the last and only the
        final few words of each are ever heard. The transcript watcher had the
        same defect for bursts; here it would be the normal case rather than a
        burst.
        """
        with self._watch_lock:
            self._watch_pending.append(text)
            self._watch_title = title or self._watch_title
        self._flush_watched()

    def _flush_watched(self) -> None:
        if self.reader.state == PLAYING:
            return
        with self._watch_lock:
            if not self._watch_pending:
                return
            body = (chr(10) + chr(10)).join(self._watch_pending)
            title = self._watch_title
            self._watch_pending.clear()
        self.read(Document(text=body, title=title or "Window", source="watch"),
                  resume=False)

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
        self._stop_claude_watch()
        if self._window_watcher is not None:
            self._window_watcher.stop()
        if self._clip_watcher is not None:
            self._clip_watcher.stop()
        if self._sleep_timer is not None:
            self._sleep_timer.cancel()
        self.config.save()
        self.reader.shutdown()
        self.store.close()
