"""Run the cross-language conformance check as part of the Python suite.

The two halves of this project reimplement the same shared rules in different
languages. Describing our implementations to each other missed a real bug once
already, so agreement is checked by running both over the same inputs rather
than by asserting it.

Skips cleanly when Node is not installed, because the Python side must stay
runnable on its own.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import REPO_ROOT

SKIPPED = "SKIP"


def _harness(name: str = "conformance.mjs") -> Path | None:
    if REPO_ROOT is None:
        return None
    script = REPO_ROOT / "tools" / name
    return script if script.is_file() else None


def test_python_and_javascript_agree_exactly():
    node = shutil.which("node")
    script = _harness()
    if node is None or script is None:
        print("   (no Node or no harness; skipping)")
        return SKIPPED

    result = subprocess.run(
        [node, str(script)], cwd=str(REPO_ROOT), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, "harness failed:\n" + output

    numbers = {}
    for line in output.splitlines():
        for key in ("identical", "equivalent", "differing"):
            if line.strip().startswith(key + ":"):
                digits = "".join(c for c in line.split(":", 1)[1] if c.isdigit())
                if digits:
                    numbers[key] = int(digits)

    # "264 comparisons across 66 inputs"
    header = next((line for line in output.splitlines()
                   if "comparisons across" in line), "")
    counts = [int(w) for w in header.split() if w.isdigit()]
    assert len(counts) == 2, "could not parse the harness header:\n" + output
    comparisons, inputs = counts

    assert "differing" in numbers, "could not parse harness output:\n" + output
    assert numbers["differing"] == 0, \
        "the two implementations disagree:\n" + output
    # Equivalent means same speech, different whitespace. The shared output
    # contract exists to drive this to zero; anything above it is drift.
    assert numbers.get("equivalent", 0) == 0, \
        "output contract not being honoured on one side:\n" + output

    # Every comparison the harness ran must have matched. Asserting against the
    # harness's own total rather than a hand-picked floor means adding stages or
    # inputs cannot silently leave some of them unchecked, and the number does
    # not go stale when the other half grows the suite.
    assert numbers.get("identical") == comparisons, (
        "not every comparison matched: " + str(numbers.get("identical"))
        + " of " + str(comparisons) + "\n" + output)
    assert inputs > 0 and comparisons >= inputs, \
        "the harness compared nothing:\n" + output



def test_both_halves_resolve_a_bookmark_the_same_way():
    """The third shared pipeline, and the one that had no guard.

    Text and phonemes were both checked across the two languages. Anchors were
    not, although docs/anchor-vocabulary.md calls the five labels a contract
    that neither half may change alone. The first run of that harness found
    eight disagreements, so the gap was not theoretical.

    Known divergences are enumerated in the harness itself. This asserts only
    that none is unrecorded and that every case was actually compared.
    """
    node = shutil.which("node")
    script = _harness("conformance_anchor.mjs")
    if node is None or script is None:
        print("   (no Node or no harness; skipping)")
        return SKIPPED

    result = subprocess.run(
        [node, str(script)], cwd=str(REPO_ROOT), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, "anchor harness failed:" + chr(10) + output

    numbers = {}
    for line in output.splitlines():
        for key in ("identical", "known divergence", "unexpected"):
            if line.strip().startswith(key + ":"):
                digits = "".join(c for c in line.split(":", 1)[1] if c.isdigit())
                if digits:
                    numbers[key] = int(digits)

    header = next((line for line in output.splitlines()
                   if line.strip().endswith("cases")), "")
    counts = [int(w) for w in header.split() if w.isdigit()]
    assert len(counts) == 1, ("could not parse the harness header:"
                              + chr(10) + output)
    cases = counts[0]

    assert "unexpected" in numbers, ("could not parse harness output:"
                                     + chr(10) + output)
    assert numbers["unexpected"] == 0, (
        "the two halves disagree in a way nobody recorded:" + chr(10) + output)

    # Every case must land in exactly one bucket. Asserting against the
    # harness's own total rather than a hand-picked floor means adding cases
    # cannot silently leave some of them unchecked.
    seen = numbers.get("identical", 0) + numbers.get("known divergence", 0)
    assert seen == cases, ("only " + str(seen) + " of " + str(cases)
                           + " cases were accounted for:" + chr(10) + output)
    assert cases > 0, "the harness compared nothing:" + chr(10) + output


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
