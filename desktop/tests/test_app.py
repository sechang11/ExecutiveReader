"""The orchestration layer: capture, read, resume, transport, bookmark.

A call trace showed the whole public surface of app.py was never invoked by any
test. Every piece underneath it was covered, which is the least useful place for
coverage to stop: this is the layer where the pieces are wired together, and
wiring is what breaks when a method is renamed or a callback is dropped.

Audio is stubbed. The point is the orchestration, not the sound.
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import install

install()

import numpy as np

import executive_reader.app as app_module
from executive_reader.app import App
from executive_reader.config import Config
from executive_reader.document import Document
from executive_reader.store.db import Store

SKIPPED = "SKIP"


class StubEngine:
    name = "stub"
    max_speed = 4.0
    available = True

    def voices(self):
        return []

    def default_voice(self):
        return "stub-voice"


class StubRegistry:
    """Silent audio, instantly. Records what it was asked to say."""

    def __init__(self) -> None:
        self.spoken: list[tuple[str, float]] = []
        self.stub = StubEngine()

    def synthesize(self, text, engine, voice, speed):
        self.spoken.append((text, speed))
        return np.zeros(240, dtype=np.float32), 24000

    def max_speed(self, engine):
        return 4.0

    def all_engines(self):
        return [self.stub]

    def describe(self):
        return ["stub: ready, 1 voice(s)"]

    def voices(self, engine=None):
        return []


class StubSink:
    def play(self, samples, rate, volume, should_abort, start_frame=0):
        return len(samples)

    def close(self):
        pass


@contextlib.contextmanager
def temp_app():
    """An App with a throwaway database and no real audio device."""
    real_registry = app_module.Registry
    app_module.Registry = StubRegistry
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = Store(Path(tmp) / "t.db")
            config = Config()
            config.auto_resume = True
            application = App(config=config, store=store)
            application.reader.sink = StubSink()
            try:
                yield application
            finally:
                application.shutdown()
    finally:
        app_module.Registry = real_registry


def _wait(predicate, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


DOC = Document(text="First sentence here. Second sentence here. "
                    "Third sentence here. Fourth sentence here.",
               title="Test Doc", uri="test://doc")


# --- reading and resuming ------------------------------------------------

def test_reading_a_document_records_it_and_speaks_it():
    with temp_app() as app:
        statuses: list[str] = []
        app.on_status = statuses.append
        total = app.read(DOC)
        assert total == 4, total
        assert _wait(lambda: app.registry.spoken), "nothing was synthesized"
        assert any("Test Doc" in s for s in statuses), statuses

        rows = app.store.history()
        assert [r.uri for r in rows] == ["test://doc"]
        assert rows[0].total == 4


def test_a_second_read_resumes_where_the_first_stopped():
    """Stops at a chosen sentence rather than racing the player.

    A stub sink returns instantly, so waiting for a mid-document index and then
    pausing reaches the end first and the test measures a clamp instead of a
    resume. Seeking makes the position the test's choice.
    """
    with temp_app() as app:
        app.read(DOC)
        assert _wait(lambda: app.reader.total == 4)
        app.reader.pause()
        app.reader.seek(2, keep_playing=False)
        app.reader.pause()
        assert app.reader.index == 2

        app.read(DOC)
        assert app.reader.index == 2, app.reader.index


def test_resuming_a_finished_document_clamps_to_its_last_sentence():
    """The end of a document is a real saved position, and it must not resolve
    past the end or silently restart from the beginning."""
    with temp_app() as app:
        finished = threading.Event()
        app.reader.on_finished = finished.set
        app.read(DOC)
        assert finished.wait(10), "playback never reached the end"

        app.read(DOC)
        assert app.reader.index == 3, app.reader.index


def test_resume_can_be_declined_per_read():
    with temp_app() as app:
        app.read(DOC)
        assert _wait(lambda: app.reader.index >= 1)
        app.reader.pause()

        app.read(DOC, resume=False)
        assert app.reader.index == 0


def test_reading_plain_text_needs_no_source():
    with temp_app() as app:
        app.read_text("A pasted paragraph to read.", "Pasted")
        assert _wait(lambda: app.registry.spoken)
        assert app.store.history()[0].title == "Pasted"


def test_empty_text_is_refused_with_a_reason():
    with temp_app() as app:
        errors: list[str] = []
        app.on_error = errors.append
        app.read_text("   ", "Nothing")
        assert errors and "Nothing to read" in errors[0], errors


# --- transport -----------------------------------------------------------

def test_transport_controls_move_the_position():
    with temp_app() as app:
        app.read(DOC)
        assert _wait(lambda: app.reader.total == 4)
        app.reader.pause()

        app.next_segment()
        assert _wait(lambda: app.reader.index >= 1)
        at = app.reader.index
        app.prev_segment()
        assert _wait(lambda: app.reader.index <= at)

        app.stop()
        assert app.reader.state == "idle"


def test_speed_nudges_are_saved_and_capped():
    with temp_app() as app:
        app.read(DOC)
        start = app.config.speed
        app.faster()
        assert app.config.speed > start
        app.slower()
        assert abs(app.config.speed - start) < 1e-9

        for _ in range(30):
            app.faster()
        assert app.config.speed <= 4.0, app.config.speed


def test_toggle_alternates_play_and_pause():
    with temp_app() as app:
        app.read(DOC)
        assert _wait(lambda: app.reader.state == "playing")
        app.toggle()
        assert _wait(lambda: app.reader.state == "paused")
        app.toggle()
        assert _wait(lambda: app.reader.state == "playing")


# --- bookmarks -----------------------------------------------------------

def test_bookmarking_stores_the_sentence_and_resumes_to_it():
    with temp_app() as app:
        app.read(DOC)
        assert _wait(lambda: app.reader.total == 4)
        app.reader.pause()
        app.reader.seek(2, keep_playing=False)

        bookmark_id = app.bookmark("check this")
        assert bookmark_id is not None
        marks = app.store.bookmarks("test://doc")
        assert len(marks) == 1 and marks[0].note == "check this"
        assert marks[0].anchor.exact == "Third sentence here."

        app.reader.seek(0, keep_playing=False)
        app.resume_bookmark(bookmark_id)
        assert _wait(lambda: app.reader.index == 2), app.reader.index


def test_bookmarking_nothing_reports_rather_than_crashing():
    with temp_app() as app:
        errors: list[str] = []
        app.on_error = errors.append
        assert app.bookmark() is None
        assert errors, "silently doing nothing is worse than saying so"


# --- capture entry points ------------------------------------------------

def _minimal_pdf(path: Path, lines: list[str]) -> None:
    """A one-page PDF with a text layer, built by hand.

    Generated rather than copied from this machine so the suite means the same
    thing elsewhere.
    """
    content = "BT /F1 12 Tf 72 720 Td 14 TL\n"
    for line in lines:
        content += "(" + line.replace("(", "").replace(")", "") + ") Tj T*\n"
    content += "ET"
    stream = content.encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += str(offset).zfill(10).encode() + b" 00000 n \n"
    out += (b"trailer\n<< /Size " + str(len(objects) + 1).encode()
            + b" /Root 1 0 R >>\nstartxref\n" + str(xref).encode()
            + b"\n%%EOF\n")
    path.write_bytes(bytes(out))


def test_a_pdf_with_a_text_layer_is_read_rather_than_ocred():
    with temp_app() as app, tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "doc.pdf"
        _minimal_pdf(path, ["The first line of the report.",
                            "The second line follows it."])
        app.read_file(path)
        assert _wait(lambda: app.registry.spoken), "nothing was read"
        said = " ".join(text for text, _speed in app.registry.spoken)
        assert "first line" in said, said
        assert app.store.history()[0].source == "file"


def test_a_short_pdf_is_not_mistaken_for_a_scanned_one():
    """The detection is "no text layer at all", not "not much text".

    A floor of 60 characters called a one-page note scanned and refused to read
    a document whose text had been extracted perfectly. Found by writing this
    test, not by anything failing in use.
    """
    from executive_reader.capture import files as file_reader
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "note.pdf"
        _minimal_pdf(path, ["A short note.", "Two lines only."])
        doc = file_reader.read_pdf(path)
        assert doc.text.strip(), "no text extracted at all"
        assert doc.meta["needs_ocr"] is False, (
            str(len(doc.text)) + " characters is a text layer")


def test_a_pdf_with_no_text_layer_is_flagged_for_ocr():
    from executive_reader.capture import files as file_reader
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scan.pdf"
        _minimal_pdf(path, [])
        doc = file_reader.read_pdf(path)
        assert doc.meta["needs_ocr"] is True, repr(doc.text)


def test_an_unreadable_file_reports_rather_than_failing_silently():
    with temp_app() as app, tempfile.TemporaryDirectory() as tmp:
        errors: list[str] = []
        app.on_error = errors.append
        path = Path(tmp) / "thing.xyz"
        path.write_text("hello", encoding="utf-8")
        app.read_file(path)
        assert errors, "an unsupported file must say so"


def test_a_missing_clipboard_reports_rather_than_reading_nothing():
    with temp_app() as app:
        from executive_reader.capture import clipboard
        real = clipboard.capture
        clipboard.capture = lambda: None
        errors: list[str] = []
        app.on_error = errors.append
        try:
            app.read_clipboard()
        finally:
            clipboard.capture = real
        assert errors and "clipboard" in errors[0].lower(), errors


def test_claude_sessions_are_listed_and_readable():
    from executive_reader.capture import claude_transcript as ct
    sessions = ct.sessions(limit=3)
    if not sessions:
        return SKIPPED
    latest = ct.latest_session()
    assert latest is not None and latest.is_file()
    turns = ct.read_turns(latest)
    assert isinstance(turns, list)
    doc = ct.as_document(latest, include_thinking=False, last_n=2)
    assert doc is None or doc.text.strip()


# --- watchers ------------------------------------------------------------

def test_watchers_start_and_stop_without_leaking_threads():
    before = threading.active_count()
    with temp_app() as app:
        app.set_clipboard_watch(True)
        assert app.config.clipboard_watch is True
        app.set_clipboard_watch(False)
        assert app.config.clipboard_watch is False
    assert _wait(lambda: threading.active_count() <= before + 2), \
        "threads outlived the app"


def test_sleep_timer_is_set_and_cleared():
    with temp_app() as app:
        statuses: list[str] = []
        app.on_status = statuses.append
        app.set_sleep_timer(30)
        assert app.config.sleep_timer_minutes == 30
        assert any("30 minutes" in s for s in statuses), statuses
        app.set_sleep_timer(0)
        assert app.config.sleep_timer_minutes == 0


def test_reloading_the_dictionary_picks_up_a_new_rule():
    from executive_reader.textproc.pronounce import Rule
    with temp_app() as app:
        app.store.upsert_rule(Rule(pattern="zzq", replacement="zed zed queue"))
        app.reload_dictionary()
        assert "zed zed queue" in app.dictionary.apply("the zzq value")


def test_editing_a_pronunciation_discards_audio_made_under_the_old_rule():
    """Otherwise the change lands several sentences later, at a moment nobody
    can predict, which is indistinguishable from it not working."""
    from executive_reader.textproc.pronounce import Rule
    with temp_app() as app:
        app.read(Document(text="The zzq is here. Another sentence. A third one. "
                               "And a fourth.", title="Rules", uri="test://rules"))
        assert _wait(lambda: app.registry.spoken)
        app.reader.pause()
        cached_before = len(app.reader._cache)
        assert cached_before > 0, "nothing was prefetched to invalidate"

        app.store.upsert_rule(Rule(pattern="zzq", replacement="zed zed queue"))
        app.reload_dictionary()
        assert app.reader._cache == {}, "stale audio survived the rule change"

        app.reader.play()
        assert _wait(lambda: any("zed zed queue" in text
                                 for text, _speed in app.registry.spoken)),             [t for t, _s in app.registry.spoken]


if __name__ == "__main__":
    passed = failed = skipped = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            if fn() == SKIPPED:
                print("SKIP", name)
                skipped += 1
            else:
                print("PASS", name)
                passed += 1
        except Exception as exc:
            print("FAIL", name, "->", type(exc).__name__, exc)
            failed += 1
    print("\n" + str(passed) + " passed, " + str(failed) + " failed, "
          + str(skipped) + " skipped")
    sys.exit(1 if failed else 0)
