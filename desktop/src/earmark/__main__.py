"""Entry point:  python -m earmark"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cli(args) -> int:
    """Headless mode: read something and exit when it finishes."""
    import threading

    from .app import App

    app = App()
    done = threading.Event()
    app.on_status = lambda m: print(m)
    app.on_error = lambda m: print("error: " + m, file=sys.stderr)
    app.on_segment = lambda i, t, n: print("[" + str(i + 1) + "/" + str(n) + "] " + t)
    app.reader.on_finished = done.set

    if args.file:
        app.read_file(args.file)
    elif args.text:
        app.read_text(args.text)
    elif args.claude:
        app.read_claude_session(last_n=args.last)
    elif args.clipboard:
        app.read_clipboard()
    else:
        app.read_smart()

    if app.reader.total == 0:
        app.shutdown()
        return 1
    done.wait(timeout=args.timeout)
    app.shutdown()
    return 0


def _gui() -> int:
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .app import App
    from .ui.tray import TrayApp

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("Earmark")
    qt_app.setQuitOnLastWindowClosed(False)  # the tray keeps it alive

    app = App()
    if not any(e.available for e in app.registry.all_engines()):
        QMessageBox.critical(None, "No voices",
                             "No speech engine is available on this machine.")
        return 1

    TrayApp(app, qt_app)
    return qt_app.exec()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="earmark", description="Read your screen, files and Claude sessions aloud.")
    parser.add_argument("--file", type=Path, help="read a PDF, EPUB, DOCX or text file")
    parser.add_argument("--text", help="read this text")
    parser.add_argument("--clipboard", action="store_true", help="read the clipboard")
    parser.add_argument("--claude", action="store_true",
                        help="read the most recent Claude Code session")
    parser.add_argument("--last", type=int, default=6,
                        help="with --claude, how many turns to read")
    parser.add_argument("--timeout", type=float, default=3600.0,
                        help="give up after this many seconds in CLI mode")
    parser.add_argument("--no-gui", action="store_true",
                        help="read once on the console instead of starting the tray")
    args = parser.parse_args()

    headless = args.no_gui or args.file or args.text or args.clipboard or args.claude
    return _cli(args) if headless else _gui()


if __name__ == "__main__":
    sys.exit(main())
