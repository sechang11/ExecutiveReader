"""No application module may be unreachable from the suite.

A module nothing loads is one nobody would notice breaking. This started at 8
of 40 unreachable, 1,172 lines, found only because the question was asked
directly. The point of the guard is that the next one is found on the day it
appears rather than the day someone thinks to look again.

Loaded is not tested. A module can be dragged in by an import and never
asserted against, which is how the file readers sat unverified while being
loaded by every run. So this is a floor, not a coverage figure, and it is
written to be read that way.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import REPO_ROOT, install

install()

SKIPPED = "SKIP"


def _run_scan():
    tool = (REPO_ROOT / "tools" / "reachability.py") if REPO_ROOT else None
    if tool is None or not tool.is_file():
        return None
    result = subprocess.run([sys.executable, str(tool)], cwd=str(REPO_ROOT),
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=600)
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    return result.stdout or ""


def test_every_module_is_loaded_by_some_test():
    output = _run_scan()
    if output is None:
        print("   (scanner not found; skipping)")
        return SKIPPED

    line = next((ln for ln in output.splitlines() if ln.startswith("UNREACHED=")),
                None)
    assert line is not None, "scanner produced no machine-readable result:\n" + output
    unreached = [name for name in line.split("=", 1)[1].split(",") if name]
    assert unreached == [], (
        "no test loads these, so nobody would notice them breaking: "
        + ", ".join(unreached))


def test_the_scan_examined_the_whole_package():
    """A scanner that found no modules reports none unreachable and passes."""
    output = _run_scan()
    if output is None:
        return SKIPPED
    header = output.splitlines()[0]
    # "N of M modules loaded by the suite (K test files)"
    numbers = [int(w) for w in header.replace("(", " ").split() if w.isdigit()]
    assert len(numbers) >= 3, header
    loaded, total, test_files = numbers[0], numbers[1], numbers[2]
    assert total >= 35, "only " + str(total) + " modules found on disk"
    assert loaded == total, header
    assert test_files >= 5, "only " + str(test_files) + " test files scanned"


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
