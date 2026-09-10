"""Python half of the sentence-segmentation conformance check.

Reads cases as JSON on stdin, writes results as JSON on stdout. All the
comparing happens in conformance_segment.mjs, so this file has no opinion about
what agreement means.

Cases go through the whole pipeline, normalise then segment, because that is
what a listener hears. Isolating segment() looked cleaner and was misleading:
this half rejoins hard-wrapped prose during normalise, so feeding raw text to
segment() alone reports it breaking sentences it would never break in use.

Attribution is still possible, because conformance.mjs compares the normalising
stages on their own. A difference that appears here and not there belongs to
segmentation.
"""
import json
import sys
from pathlib import Path

# Windows defaults these to the locale encoding, which mangles the ellipsis and
# curly-quote cases into something neither half ever sees.
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "src"))

from executive_reader.config import Config  # noqa: E402
from executive_reader.textproc.normalize import normalize  # noqa: E402
from executive_reader.textproc.segment import segment  # noqa: E402

cfg = Config()
cases = json.load(sys.stdin)
json.dump([segment(normalize(text, skip_code=cfg.skip_code_blocks,
                             urls=cfg.read_urls), cfg.max_segment_chars)
           for text in cases], sys.stdout)
