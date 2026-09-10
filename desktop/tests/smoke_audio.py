"""End-to-end check against the real sound device.

Not part of the automated suite: it needs speakers and it makes noise.
Run it directly to confirm the whole chain works on this machine.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import install

install()

from earmark.config import Config
from earmark.document import Document
from earmark.player.engine import Reader
from earmark.tts.registry import Registry

TEXT = ("Aloud is working. This is the second sentence, read at normal speed. "
        "And this third one proves that skipping ahead lands cleanly.")


def main() -> int:
    cfg = Config()
    cfg.volume = 0.6
    registry = Registry()
    print("engines:")
    for line in registry.describe():
        print("  " + line)

    reader = Reader(registry, cfg)
    done = threading.Event()
    reader.on_segment = lambda i, t, n: print("  [" + str(i + 1) + "/" + str(n) + "] " + t)
    reader.on_finished = done.set
    reader.on_error = lambda msg: print("  error: " + msg)

    doc = Document(text=TEXT, title="Smoke test", uri="test://smoke")
    total = reader.load(doc)
    print("segments: " + str(total) + ", speaking now...")

    if not done.wait(60):
        print("FAILED: playback did not finish in time")
        reader.shutdown()
        return 1

    print("\nnow at 2.5x:")
    done.clear()
    reader.set_speed(2.5)
    started = time.time()
    reader.load(doc)
    if not done.wait(60):
        print("FAILED: fast playback did not finish")
        reader.shutdown()
        return 1
    print("finished in " + ("%.1f" % (time.time() - started)) + "s")

    reader.shutdown()
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
