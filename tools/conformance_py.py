"""Python half of the cross-language conformance check.

Reads cases as JSON on stdin, writes results as JSON on stdout. Deliberately
dumb: all the comparing happens in conformance.mjs, so this file has no opinion
about what agreement means.
"""
import json
import sys
from pathlib import Path

# Windows defaults these to the locale encoding (cp1252), which silently mangles
# every non-ASCII symbol we care about: £ arrives as Â£, © as Â©, ™ as â„¢. That
# looked like a real disagreement between the two implementations on the first
# run. It was not; it was this pipe.
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "src"))

from executive_reader.textproc import normalize as normalize_mod  # noqa: E402
from executive_reader.textproc import symbols  # noqa: E402


# Called directly, with no getattr fallback on purpose. This harness once
# reached into a private _expansion_rules() because no public entry point
# existed; now that one does, a fallback would only let a future removal pass
# silently and compare the wrong thing. An AttributeError here is the correct
# outcome.
cases = json.load(sys.stdin)
out = {
    "expansions": [normalize_mod.apply_expansions(c) for c in cases],
    "currency": [symbols.apply_currency(c) for c in cases],
    "symbols": [symbols.apply_symbols(c) for c in cases],
    "both": [symbols.apply(c) for c in cases],
}
json.dump(out, sys.stdout)
