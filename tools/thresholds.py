"""Every numeric threshold in the source, and whether a test pins it.

Two different defects hide in a magic number, and neither shows up in normal
use:

1. The number answers the wrong question. A "is there much text" floor used to
   decide "is there any text at all" rejects short but perfectly good input,
   and the app still works, so nobody reports it.
2. The number is unpinned. Tests exercise the rule around it and pass at any
   value, so the constant can be changed freely and silently.

The second is detectable mechanically: perturb the literal, run the tests, and
see whether anything notices. This lists the candidates and, with --mutate,
does exactly that.

Not every number found is tunable. Windows message codes, HTTP status codes and
SAPI enum values are defined elsewhere and a test pinning them asserts nothing
about this project. Read the list first; mutation mode is worth running on the
handful that encode a judgment, not on all of them.

Run: python tools/thresholds.py [--mutate]
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "desktop" / "src" / "earmark"
TESTS = ROOT / "desktop" / "tests"

#: Values too common to be thresholds; flagging them is noise.
IGNORED = {0, 1, -1, 2, 100, 1000}


class Finder(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.found: list[tuple[int, str, float]] = []

    def _record(self, node, kind: str) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, (int, float)):
                if sub.value in IGNORED or isinstance(sub.value, bool):
                    continue
                self.found.append((sub.lineno, kind, sub.value))

    def visit_Compare(self, node):
        self._record(node, "compare")
        self.generic_visit(node)

    def visit_Call(self, node):
        name = getattr(node.func, "id", "")
        if name in ("min", "max"):
            self._record(node, name)
        self.generic_visit(node)

    def visit_Assign(self, node):
        """Named constants, which is where a threshold goes once it is tidied.

        The tool missed these at first, and the omission had a perverse shape:
        extracting a magic number into a well-named module constant, which is
        the recommended fix, removed it from the audit. The tidier the code got,
        the less this tool could see.
        """
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                self._record(node.value, "constant")
        self.generic_visit(node)


def find_all() -> dict[str, list[tuple[int, str, float]]]:
    out: dict[str, list[tuple[int, str, float]]] = {}
    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        finder = Finder(path)
        finder.visit(tree)
        if finder.found:
            out[str(path.relative_to(SRC))] = finder.found
    return out


def _clean_env() -> dict:
    """Run children without writing or reading cached bytecode.

    Mutating a file, testing, and restoring it can happen inside one filesystem
    timestamp tick. Python then considers the .pyc it wrote for the *mutated*
    source still valid for the restored one, and the next run silently executes
    the wrong code. That turns this tool's verdicts into caching artifacts, in
    either direction, which is worse than not running it: it reports confident
    results about code that was never executed.
    """
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_suite(python: str) -> bool:
    """True when every test file passes."""
    env = _clean_env()
    for test in sorted(TESTS.glob("test_*.py")):
        result = subprocess.run([python, str(test)], cwd=str(ROOT),
                                capture_output=True, text=True, env=env,
                                encoding="utf-8", errors="replace", timeout=900)
        if result.returncode != 0:
            return False
    return True


#: A constant known to be pinned, used to prove the harness can detect anything.
CANARY_FILE = "capture/files.py"
CANARY_TEXT = "MIN_TEXT_CHARS = 16"
CANARY_BROKEN = "MIN_TEXT_CHARS = 9999"


def canary_detects_a_break(python: str) -> bool:
    """Break something known-pinned and require the suite to notice.

    Without this the tool cannot distinguish "every constant is pinned" from
    "the harness never fails". Both print the same reassuring result, and the
    second is the more likely of the two once anything goes wrong with how the
    tests are invoked.
    """
    path = SRC / Path(CANARY_FILE)
    original = path.read_text(encoding="utf-8")
    if CANARY_TEXT not in original:
        return False
    path.write_text(original.replace(CANARY_TEXT, CANARY_BROKEN, 1),
                    encoding="utf-8")
    try:
        return not run_suite(python)
    finally:
        path.write_text(original, encoding="utf-8")


def mutate(python: str, only: str = "") -> int:
    unpinned: list[str] = []
    found = find_all()
    if only:
        found = {k: v for k, v in found.items() if only.lower() in k.lower()}
    total = sum(len(v) for v in found.values())
    for cache in SRC.rglob("__pycache__"):
        for item in cache.glob("*"):
            item.unlink(missing_ok=True)
    print("checking the harness can detect a break...")
    if not canary_detects_a_break(python):
        print("  CANARY SURVIVED: the suite passes with a known-pinned "
              "constant broken, so every verdict below would be meaningless. "
              "Nothing was run.")
        return 1
    print("  canary caught it\n")
    print("perturbing " + str(total) + " thresholds; this takes a while\n")

    for rel, entries in found.items():
        path = SRC / rel
        original = path.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)
        for lineno, kind, value in entries:
            line = lines[lineno - 1]
            literal = repr(value)
            if literal not in line:
                continue
            bumped = value * 2 if value else 1
            if isinstance(value, int):
                bumped = int(bumped)
            patched = list(lines)
            patched[lineno - 1] = line.replace(literal, repr(bumped), 1)
            path.write_text("".join(patched), encoding="utf-8")
            try:
                survived = run_suite(python)
            finally:
                path.write_text(original, encoding="utf-8")
            label = rel + ":" + str(lineno) + "  " + kind + " " + literal
            if survived:
                unpinned.append(label)
                print("  UNPINNED  " + label + " -> " + repr(bumped))
            else:
                print("  pinned    " + label)

    print("\n" + str(len(unpinned)) + " of " + str(total) + " thresholds unpinned")
    return 0


def main() -> int:
    if "--mutate" in sys.argv:
        only = ""
        if "--only" in sys.argv:
            index = sys.argv.index("--only")
            if index + 1 < len(sys.argv):
                only = sys.argv[index + 1]
        return mutate(sys.executable, only)
    found = find_all()
    total = sum(len(v) for v in found.values())
    print(str(total) + " numeric thresholds in " + str(len(found)) + " files\n")
    for rel, entries in found.items():
        print(rel)
        for lineno, kind, value in entries:
            print("  line " + str(lineno).rjust(4) + "  " + kind.ljust(8)
                  + repr(value))
    return 0


if __name__ == "__main__":
    sys.exit(main())
