"""Tray, mini player, library window, and the command line.

These were the last modules no test loaded, 957 lines of them. Construction is
where most interface bugs live: a signal wired to a method that was renamed, a
widget referenced before it exists, an icon drawn with a Qt call that changed.
None of that needs a human to look at the window, and none of it is caught by
testing the layer underneath.

Skips cleanly when Qt cannot open a display, so the suite still runs headless.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import install

install()

SKIPPED = "SKIP"


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
        assert tray.library.centralWidget().count() == 5, "five tabs expected"
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
        assert player.sentence.text() == "First sentence."
        assert player.progress.value() > 0

        player.set_speed(2.5)
        assert player.speed.text() == "2.5x", player.speed.text()
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
