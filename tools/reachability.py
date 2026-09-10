"""Which application modules does the test suite actually load?

Measured by running each test file and recording what ended up imported, rather
than by following import statements. Static analysis misses dynamic imports and
reports well-tested modules as untested, which is worse than reporting nothing:
it invites someone to close a gap that was never open, and to trust the rest of
the list.

Loaded is not the same as tested. A module that no test loads is certainly
untested; one that loads may only have been dragged in by an import. So treat
the output as a floor on the gap, not a coverage figure.

Run: python tools/reachability.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "desktop" / "src"
TESTS = ROOT / "desktop" / "tests"
PACKAGE = "earmark"

_PROBE = """
import json, runpy, sys
sys.argv = [{path!r}]
try:
    runpy.run_path({path!r}, run_name="__main__")
except SystemExit:
    pass
except Exception:
    pass
loaded = sorted(m for m in sys.modules if m == {pkg!r} or m.startswith({pkg!r} + "."))
sys.stderr.write("<<<LOADED>>>" + json.dumps(loaded))
"""


def modules_on_disk() -> set[str]:
    found = set()
    for path in (SRC / PACKAGE).rglob("*.py"):
        parts = path.relative_to(SRC).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            found.add(".".join(parts))
    return found


def loaded_by(test_file: Path, python: str) -> set[str]:
    code = _PROBE.format(path=str(test_file), pkg=PACKAGE)
    result = subprocess.run([python, "-c", code], cwd=str(ROOT),
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=600)
    marker = "<<<LOADED>>>"
    tail = (result.stderr or "")
    if marker not in tail:
        return set()
    return set(json.loads(tail.split(marker, 1)[1].strip()))


def main() -> int:
    python = sys.executable
    on_disk = modules_on_disk()
    reached: set[str] = set()

    # Skip the guard test: it invokes this tool, and scanning it would recurse.
    # It loads nothing the other files do not.
    files = sorted(p for p in TESTS.glob("test_*.py")
                   if p.name != "test_reachability.py")
    for test_file in files:
        reached |= loaded_by(test_file, python)

    unreached = sorted(m for m in on_disk if m not in reached)
    print(str(len(on_disk) - len(unreached)) + " of " + str(len(on_disk))
          + " modules loaded by the suite (" + str(len(files)) + " test files)")
    if unreached:
        print("\nnever loaded by any test:")
        for name in unreached:
            path = SRC / (name.replace(".", "/") + ".py")
            lines = len(path.read_text(encoding="utf-8").splitlines()) if path.is_file() else 0
            print("  " + name.ljust(34) + str(lines).rjust(5) + " lines")
        total = sum(
            len((SRC / (n.replace(".", "/") + ".py")).read_text(encoding="utf-8").splitlines())
            for n in unreached
            if (SRC / (n.replace(".", "/") + ".py")).is_file())
        print("\n  " + str(total) + " lines unreachable from the suite")
    # Machine-readable, so the guard test asserts on this rather than parsing
    # prose that will be reworded eventually.
    print("\nUNREACHED=" + ",".join(unreached))
    return 0


if __name__ == "__main__":
    sys.exit(main())
