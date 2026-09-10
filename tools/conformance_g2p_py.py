"""Python half of the phoneme conformance check.

Reads cases as JSON on stdin, writes results as JSON on stdout. All the
comparing happens in conformance_g2p.mjs, so this file has no opinion about
what agreement means.
"""
import json
import sys
from pathlib import Path

# Windows defaults these to the locale encoding, which mangles every non-ASCII
# symbol in the alphabet and would read as a real disagreement.
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "src"))

from earmark.tts import phonemes  # noqa: E402

cases = json.load(sys.stdin)
results = [phonemes.phonemize(c) for c in cases]
json.dump({"ipa": [ipa for ipa, _misses in results],
           "misses": [misses for _ipa, misses in results]}, sys.stdout)
