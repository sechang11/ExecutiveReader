"""Python half of the anchor conformance check.

Reads cases as JSON on stdin, writes results as JSON on stdout. All the
comparing happens in conformance_anchor.mjs, so this file has no opinion about
what agreement means.

The two halves store an anchor differently on purpose: this one keeps a
character window either side, the extension keeps whole neighbouring sentences.
That is not the contract. The contract is the answer, so each side builds its
own anchor from the same document and the harness compares where the bookmark
lands and what it is called.
"""
import json
import sys
from pathlib import Path

# Windows defaults these to the locale encoding, which mangles the curly quotes
# and non-breaking spaces the normalisation cases depend on.
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "src"))

from executive_reader.store.anchors import Anchor  # noqa: E402

results = []
for case in json.load(sys.stdin):
    anchor = Anchor.create(case["before"], case["index"])
    found = anchor.locate(case["after"], bool(case.get("rulesChanged")))
    results.append({"index": found.index, "how": found.how,
                    "verified": bool(found.verified)})
json.dump(results, sys.stdout)
