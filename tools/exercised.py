"""Which functions does the test suite actually call?

Reachability answers whether a module loads. That is a floor, and a weak one:
a module dragged in by an import counts as reached while nothing in it is ever
run. This asks the harder question by recording every call made during the
suite, using the interpreter's own profiler rather than reading the tests.

Measuring beats parsing here for the reason it did before. A scan that reads
test source counts a name in a docstring as a call, and counts its own list of
known gaps as evidence that those gaps are covered. Neither can happen when the
data comes from the interpreter.

Run: python tools/exercised.py
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "desktop" / "src"
TESTS = ROOT / "desktop" / "tests"
PACKAGE = "earmark"

_PROBE = r"""
import json, runpy, sys
from pathlib import Path

SRC = Path({src!r})
called = set()

def _profile(frame, event, arg):
    if event != "call":
        return
    code = frame.f_code
    try:
        path = Path(code.co_filename)
    except (ValueError, OSError):
        return
    try:
        rel = path.relative_to(SRC)
    except ValueError:
        return
    called.add(str(rel).replace("\\", "/") + "::" + code.co_name)

sys.argv = [{path!r}]
sys.setprofile(_profile)
try:
    runpy.run_path({path!r}, run_name="__main__")
except SystemExit:
    pass
except Exception:
    pass
finally:
    sys.setprofile(None)

sys.stderr.write("<<<CALLED>>>" + json.dumps(sorted(called)))
"""


def defined_functions() -> dict[str, str]:
    """Every def in the package, as "path::name" mapped to a display label."""
    found: dict[str, str] = {}
    for path in sorted((SRC / PACKAGE).rglob("*.py")):
        rel = str(path.relative_to(SRC)).replace("\\", "/")
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[rel + "::" + node.name] = (
                    rel.removeprefix(PACKAGE + "/").removesuffix(".py")
                    + "." + node.name)
    return found


def calls_from(test_file: Path, python: str) -> set[str]:
    code = _PROBE.format(path=str(test_file), src=str(SRC))
    result = subprocess.run([python, "-c", code], cwd=str(ROOT),
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=900)
    marker = "<<<CALLED>>>"
    tail = result.stderr or ""
    if marker not in tail:
        return set()
    return set(json.loads(tail.split(marker, 1)[1].strip()))


def main() -> int:
    python = sys.executable
    defined = defined_functions()

    called: set[str] = set()
    files = sorted(p for p in TESTS.glob("test_*.py")
                   if p.name not in ("test_reachability.py", "test_exercised.py"))
    for test_file in files:
        called |= calls_from(test_file, python)

    never = sorted(label for key, label in defined.items() if key not in called)
    public = [n for n in never if not n.rsplit(".", 1)[1].startswith("_")]

    print(str(len(defined) - len(never)) + " of " + str(len(defined))
          + " functions called by the suite (" + str(len(files)) + " test files)")
    print("  never called:      " + str(len(never)))
    print("  of those, public:  " + str(len(public)))
    if public:
        print("\npublic functions no test calls:")
        for name in public:
            print("  " + name)
    print("\nUNEXERCISED=" + ",".join(public))
    return 0


if __name__ == "__main__":
    sys.exit(main())
