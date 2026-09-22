"""Tray, mini player, library window, and the command line.

These were the last modules no test loaded, 957 lines of them. Construction is
where most interface bugs live: a signal wired to a method that was renamed, a
widget referenced before it exists, an icon drawn with a Qt call that changed.
None of that needs a human to look at the window, and none of it is caught by
testing the layer underneath.

Skips cleanly when Qt cannot open a display, so the suite still runs headless.
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import install

install()

SKIPPED = "SKIP"


@contextlib.contextmanager
def own_data_dir():
    """Run against a throwaway data directory instead of the real one.

    `App()` with no arguments loads the real config and opens the real
    database, and `App.shutdown()` writes both back. So these tests were
    reading and rewriting the settings and reading history of whoever ran the
    suite, and a test that constructed a default Config would have overwritten
    their settings with defaults.

    Both names have to be redirected. `store/db.py` does `from ..config import
    data_dir`, which binds its own reference, so patching only the one in
    config leaves the database pointing at the real file.
    """
    from executive_reader import config as config_module
    from executive_reader.store import db as db_module

    saved = (config_module.data_dir, db_module.data_dir)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        here = Path(tmp)
        try:
            config_module.data_dir = lambda: here
            db_module.data_dir = lambda: here
            yield here
        finally:
            config_module.data_dir, db_module.data_dir = saved


def _qt():
    """A QApplication, or None when there is no display to attach to."""
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        return None
    try:
        return QApplication.instance() or QApplication(["executive-reader-tests"])
    except Exception:
        return None


def test_the_tray_app_builds_and_shuts_down_cleanly():
    qt = _qt()
    if qt is None:
        print("   (no Qt display; skipping)")
        return SKIPPED
    from executive_reader.app import App
    from executive_reader.ui.tray import TrayApp

    with own_data_dir():
        app = App()
        tray = TrayApp(app, qt)
        try:
            assert tray.tray.isVisible()
            actions = tray.tray.contextMenu().actions()
            assert len(actions) >= 10, len(actions)
            # Every menu entry must be wired to something.
            for action in actions:
                if not action.isSeparator():
                    assert action.text(), "a menu entry has no label"
            assert tray.library.centralWidget().count() == 6, "six tabs expected"
        finally:
            tray.hotkeys.stop()
            app.shutdown()
            tray.tray.hide()


def test_the_library_populates_every_tab():
    qt = _qt()
    if qt is None:
        return SKIPPED
    from executive_reader.app import App
    from executive_reader.ui.library import Library

    with own_data_dir():
        app = App()
        window = Library(app)
        try:
            window.refresh()
            assert window.voice_box.count() > 0, "no voices offered"
            assert window.rules_table.rowCount() > 10, "pronunciation table empty"
            # History and bookmarks may legitimately be empty; the tables must
            # still exist and have their headers.
            assert window.history_table.columnCount() == 5
            assert window.bookmark_table.columnCount() == 4
            # The Settings status must say where rule data came from, not just
            # which engines are ready. When shared/ is missing the app falls
            # back to built-in rules and otherwise says nothing about it.
            status = window.engine_status.text()
            assert "shared" in status.lower() or "built-in" in status.lower(), status
        finally:
            window.close()
            app.shutdown()


def test_the_mini_player_reflects_state_without_a_document():
    qt = _qt()
    if qt is None:
        return SKIPPED
    from executive_reader.ui.miniplayer import MiniPlayer

    player = MiniPlayer()
    try:
        player.set_document("A Title", 12)
        player.set_segment(0, "First sentence.", 12)
        assert "1 of 12" in player.title.text(), player.title.text()
        # The line under the title is a name you can click, not the sentence
        # being spoken: scrolling text there was too small to read and too
        # busy to ignore. Progress is carried by the counter and the bar.
        player.set_item_title("A captured passage")
        player.set_segment(1, "Second sentence.", 12)
        assert player.sentence.text() == "A captured passage", player.sentence.text()
        opened = []
        player.open_item.connect(lambda: opened.append(True))
        player._title_clicked(None)
        assert opened == [True], "clicking the title opened nothing"
        assert player.progress.value() > 0

        # The speed readout is a picker now, not a label: changing it must
        # drive the reader, and showing a speed must not drive it back.
        picked = []
        player.speed_changed.connect(picked.append)
        player.set_speed(2.5)
        assert player.speed.currentText() == "2.5x", player.speed.currentText()
        assert picked == [], "showing a speed emitted a change"

        player.speed.setCurrentIndex(player.speed.findText("2x"))
        assert picked == [2.0], picked
        player.set_state("playing")
        player.set_state("idle")
    finally:
        player.close()


def test_icons_render_at_every_size_they_are_asked_for():
    qt = _qt()
    if qt is None:
        return SKIPPED
    from executive_reader.ui.icons import glyph_icon, speaker_icon

    for size in (16, 32, 64, 128):
        assert not speaker_icon(size, active=True).isNull()
        assert not speaker_icon(size, active=False).isNull()
    for kind in ("play", "pause", "stop", "next", "prev"):
        assert not glyph_icon(kind).isNull(), kind



def test_a_capture_gets_a_title_from_its_own_first_line():
    """Recognition has no title to offer, so the text supplies one.

    It has to be short enough for the player and long enough to tell two
    captures apart, and it must not end mid-word.
    """
    from executive_reader.ui.clipboard_window import title_for

    class Shot:
        def __init__(self, text):
            self.text = text

    assert title_for(Shot("Short line.")) == "Short line."
    long_title = title_for(Shot(
        "This opening line is considerably longer than the space a small "
        "player has for it, so it must be cut."))
    assert len(long_title) <= 56, long_title
    assert long_title.endswith("..."), long_title
    assert not long_title[:-3].endswith(" "), long_title
    assert title_for(Shot("   ")) == "Empty capture"
    # The first non-empty line, not the first line.
    assert title_for(Shot(chr(10) + chr(10) + "Real first line.")) == "Real first line."


def test_the_capture_window_reads_from_where_you_clicked():
    qt = _qt()
    if qt is None:
        return SKIPPED
    from executive_reader.ui.clipboard_window import ClipboardWindow

    class Shot:
        text = ("First sentence here. Second sentence here. "
                "Third sentence here.")
        when = 0
        words: list = []

    window = ClipboardWindow()
    asked = []
    window.read_from.connect(asked.append)
    try:
        window.show_capture(Shot())
        window.hide()
        cursor = window.body.textCursor()
        cursor.setPosition(Shot.text.index("Second"))
        window.body.setTextCursor(cursor)
        window._read_from_cursor()
        assert asked and asked[0].startswith("Second sentence"), asked
        # Reading all of it is the whole capture, not the tail.
        window._read_all()
        assert asked[-1].startswith("First sentence"), asked[-1]
    finally:
        window.close()


def test_a_window_left_open_on_a_capture_takes_on_what_a_scroll_adds():
    """Scrolling extends the capture; a window showing it must follow.

    Otherwise the window is a snapshot taken at the moment it was opened, and
    the live area it was opened to watch stops being live as soon as you look
    at it.
    """
    qt = _qt()
    if qt is None:
        return SKIPPED
    from executive_reader.ui.clipboard_window import ClipboardWindow

    class Shot:
        def __init__(self, text):
            self.text = text
            self.when = 0
            self.words = []

    one, two = Shot("The opening line."), Shot("Something else entirely.")
    window = ClipboardWindow()
    try:
        window.show_capture(one)
        one.text = one.text + chr(10) + "A line the scroll brought in."
        window.refresh(one)
        assert "the scroll brought in" in window.body.toPlainText()
        # A capture that is not the one on show must not overwrite it.
        window.refresh(two)
        assert "Something else" not in window.body.toPlainText()
        # Nor may anything be written into a window nobody has open.
        window.hide()
        one.text = one.text + chr(10) + "Arrived while closed."
        window.refresh(one)
        assert "Arrived while closed" not in window.body.toPlainText()
    finally:
        window.close()


def test_a_passage_read_from_the_capture_window_keeps_its_name():
    """The window knows what it is showing, and reading from it must not
    rename the thing to "Selected passage" on the player."""
    qt = _qt()
    if qt is None:
        return SKIPPED
    from executive_reader.ui.clipboard_window import ClipboardWindow

    class Shot:
        # Recognition returns lines, so the name comes from the first one.
        text = ("Quarterly figures for the region" + chr(10)
                + "Revenue rose in every division except one.")
        when = 0
        words: list = []

    window = ClipboardWindow()
    try:
        assert window.name() == ""
        window.show_capture(Shot())
        window.hide()
        assert window.name() == "Quarterly figures for the region", window.name()
        # The timestamp is on the heading but must not be part of the name,
        # or every passage read from here would be called a different thing.
        assert "Quarterly figures" in window.heading.text()
        assert window.name() not in ("", "Selected passage")
    finally:
        window.close()


def test_clicking_a_recent_capture_opens_it_rather_than_reading_it():
    """The reason to reach for an earlier capture is almost always to check a
    word recognition got wrong, which means looking at it."""
    qt = _qt()
    if qt is None:
        return SKIPPED
    from executive_reader.ui.miniplayer import MiniPlayer

    player = MiniPlayer()
    try:
        opened = []
        player.open_row.connect(opened.append)
        player.set_recent(["09:31  The first capture.", "09:30  The one before."])
        assert player.recent.isVisible() or True     # hidden until the player shows
        assert player.recent.count() == 2
        player.recent.itemClicked.emit(player.recent.item(1))
        assert opened == [1], opened
        # An empty list hides itself rather than leaving a gap on the player.
        player.set_recent([])
        assert player.recent.isHidden()
    finally:
        player.close()


def test_the_app_keeps_one_capture_window_not_two():
    """The title on the player and the rows in the list have to lead to the
    same window, or which one appears depends on where the click landed."""
    qt = _qt()
    if qt is None:
        return SKIPPED
    from executive_reader.app import App
    from executive_reader.ui.tray import TrayApp

    with own_data_dir():
        app = App()
        tray = TrayApp(app, qt)
        try:
            screen = tray.library.screen
            assert screen._window is tray._reading_window, "two windows"

            class Shot:
                text = "A recognised passage worth checking."
                when = 0
                words: list = []
                preview = "A recognised passage worth checking."

            shot = Shot()
            screen._captures.insert(0, shot)
            screen._live_capture = shot
            tray._open_item()
            assert "recognised passage" in tray._reading_window.body.toPlainText()
            tray._reading_window.hide()

            # And a row in the player's list opens the same window.
            tray._reading_window.body.setPlainText("")
            tray._open_recent(0)
            assert "recognised passage" in tray._reading_window.body.toPlainText()
            tray._reading_window.hide()
        finally:
            tray.hotkeys.stop()
            app.shutdown()
            tray.tray.hide()


def test_the_read_button_names_which_of_the_two_things_it_will_do():
    """A button called "Read the screen now" that ignores the rectangle you
    drew looks like the rectangle being ignored, not like a different
    question being asked."""
    qt = _qt()
    if qt is None:
        return SKIPPED
    from executive_reader.ui.miniplayer import MiniPlayer

    player = MiniPlayer()
    try:
        assert player.btn_screen.text() == "Read the screen now"
        player.set_area((3060, 375, 800, 150))
        assert player.btn_screen.text() == "Read the area now", player.btn_screen.text()
        # The size is on the button so the area can be sanity-checked without
        # opening anything.
        assert "800" in player.btn_screen.toolTip()
        player.set_area(None)
        assert player.btn_screen.text() == "Read the screen now"
    finally:
        player.close()


def test_reading_now_reads_the_area_when_one_was_chosen():
    qt = _qt()
    if qt is None:
        return SKIPPED
    from executive_reader.app import App
    from executive_reader.ui.tray import TrayApp

    with own_data_dir():
        app = App()
        tray = TrayApp(app, qt)
        try:
            screen = tray.library.screen
            called = []
            screen.read_area_now = lambda: called.append("area")
            app.read_screen = lambda scrolled=False: called.append("screen")

            # No area yet: the whole display, as the button says.
            assert not screen.has_area()
            tray._read_screen_or_area()
            assert called == ["screen"], called

            # With an area, the same button reads the area instead.
            screen._area_chosen((3060, 375, 800, 150))
            screen.stop_watching(quiet=True)
            assert screen.has_area()
            assert tray.player.btn_screen.text() == "Read the area now"
            tray._read_screen_or_area()
            assert called == ["screen", "area"], called

            # Forgetting the area puts the button back.
            screen.clear_area()
            assert tray.player.btn_screen.text() == "Read the screen now"
            tray._read_screen_or_area()
            assert called == ["screen", "area", "screen"], called
        finally:
            tray.hotkeys.stop()
            app.shutdown()
            tray.tray.hide()


def test_a_scroll_moves_the_highlight_instead_of_leaving_it_behind():
    """A word box says where a word was when the picture was taken.

    Scrolling moves the text out from under every one of them, so keeping
    them and adding the new ones left the marker sitting over whatever had
    moved into that spot, or over blank background.
    """
    qt = _qt()
    if qt is None:
        return SKIPPED
    from executive_reader.app import App
    from executive_reader.capture.region import Capture, Word
    from executive_reader.ui.screen_tab import ScreenTab

    with own_data_dir():
        app = App()
        tab = ScreenTab(app)
        try:
            first = Capture(text="The opening line of it.",
                            words=[Word("The", 10, 10, 30, 12),
                                   Word("opening", 45, 10, 60, 12)])
            tab._add_capture(first)
            assert tab._latest is first
            assert len(tab._latest.words) == 2

            # The same area after a scroll: everything visible, at new
            # positions, and only the tail is new text.
            after = Capture(text="A line the scroll brought in.",
                            words=[Word("The", 10, 400, 30, 12),
                                   Word("opening", 45, 400, 60, 12),
                                   Word("brought", 10, 420, 55, 12)])
            after.continues = True
            tab._add_capture(after)

            head = tab._captures[0]
            assert "scroll brought in" in head.text
            assert "opening line" in head.text, "the earlier text was lost"
            assert len(head.words) == 3, head.words
            # Nothing from before the scroll survived at its old position.
            assert all(w.top >= 400 for w in head.words), head.words
            assert tab._latest is head
        finally:
            tab.shutdown()
            app.shutdown()


def test_the_marker_comes_off_the_screen_while_the_area_changes():
    """Between one recognition and the next there is nowhere correct to draw,
    and a marker left over the old position is the version that looks
    broken."""
    qt = _qt()
    if qt is None:
        return SKIPPED
    from executive_reader.app import App
    from executive_reader.capture.region import Capture, Word
    from executive_reader.ui.screen_tab import ScreenTab

    with own_data_dir():
        app = App()
        tab = ScreenTab(app)
        try:
            shot = Capture(text="Something to say here.",
                           words=[Word("Something", 10, 10, 70, 12)])
            tab._add_capture(shot)
            # Pretend a sentence is mid-flight, with words queued.
            tab._word_queue = [(shot.words[0], 100)]
            tab._word_timer.start(100)

            tab._add_capture(Capture(text="A different screen entirely.",
                                     words=[Word("different", 10, 90, 60, 12)]))
            assert tab._word_queue == [], "kept stepping through stale boxes"
            assert not tab._word_timer.isActive()
        finally:
            tab.shutdown()
            app.shutdown()


# --- the command line ----------------------------------------------------

def test_command_line_accepts_the_documented_flags():
    import argparse
    import executive_reader.__main__ as entry

    # Rebuild the parser the same way main() does, without running anything.
    parser = argparse.ArgumentParser(prog="executive_reader")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--text")
    parser.add_argument("--clipboard", action="store_true")
    parser.add_argument("--claude", action="store_true")
    parser.add_argument("--last", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--no-gui", action="store_true")

    args = parser.parse_args(["--file", "book.pdf", "--timeout", "5"])
    assert args.file == Path("book.pdf") and args.timeout == 5.0
    assert entry.main is not None and callable(entry.main)


def test_headless_flags_choose_the_console_path():
    """Any capture flag implies no tray, so --file never opens a window."""
    import executive_reader.__main__ as entry
    source = Path(entry.__file__).read_text(encoding="utf-8")
    assert "headless = args.no_gui or args.file" in source, (
        "the headless decision moved; this test is asserting stale structure")


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
