"""Reader state-machine tests. No real audio device is touched."""
from __future__ import annotations

import collections
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import install

install()

import numpy as np

from executive_reader.config import Config
from executive_reader.document import Document
from executive_reader.player.engine import IDLE, PAUSED, PLAYING, Reader


class StubSink:
    """Pretends to play audio in real time so timing-based tests stay honest."""

    # Fast enough to keep tests quick, slow enough that a pause lands in the
    # middle of a segment rather than on a boundary.
    def __init__(self, speed_factor: float = 5.0) -> None:
        self.speed_factor = speed_factor
        self.played: list[int] = []

    def play(self, samples, rate, volume, should_abort, start_frame=0):
        self.played.append(len(samples))
        total = len(samples)
        i = start_frame
        step = max(1, rate // 100)
        while i < total:
            if should_abort():
                return i
            time.sleep(step / rate / self.speed_factor)
            i += step
        return total

    def close(self):
        pass


class StubRegistry:
    """One second of silence per segment, regardless of text."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []

    def synthesize(self, text, engine, voice, speed):
        self.calls.append((text, speed))
        return np.zeros(24000, dtype=np.float32), 24000

    def max_speed(self, engine):
        return 4.0


def make_reader():
    cfg = Config()
    cfg.prefetch_segments = 2
    reg = StubRegistry()
    sink = StubSink()
    return Reader(reg, cfg, sink=sink), reg, sink


DOC = Document(text="One two three. Four five six. Seven eight nine. Ten eleven twelve.",
               title="Counting", uri="test://counting")


def test_load_segments_and_autoplay_reaches_end():
    reader, _reg, _sink = make_reader()
    seen: list[int] = []
    done = threading.Event()
    reader.on_segment = lambda i, t, n: seen.append(i)
    reader.on_finished = done.set

    total = reader.load(DOC)
    assert total == 4, total
    assert done.wait(15), "playback never finished"
    assert seen == [0, 1, 2, 3], seen
    assert reader.state == IDLE
    reader.shutdown()


def test_pause_resumes_mid_sentence():
    reader, _reg, _sink = make_reader()
    started = threading.Event()
    reader.on_segment = lambda i, t, n: started.set()

    reader.load(DOC)
    assert started.wait(5)
    time.sleep(0.1)
    reader.pause()
    assert reader.state == PAUSED
    frame = reader._resume_frame
    assert frame > 0, "pause should record a mid-sentence offset"

    time.sleep(0.2)
    assert reader._resume_frame == frame, "paused reader kept playing"
    reader.play()
    time.sleep(0.1)
    assert reader.state == PLAYING
    reader.shutdown()


def test_seek_and_skip():
    reader, _reg, _sink = make_reader()
    seen: list[int] = []
    reader.on_segment = lambda i, t, n: seen.append(i)

    reader.load(DOC, autoplay=False)
    assert reader.state == IDLE
    reader.seek(2)
    assert reader.index == 2
    reader.play()
    time.sleep(0.3)
    reader.next_segment()
    time.sleep(0.3)
    assert reader.index >= 3, reader.index
    reader.stop()
    assert reader.state == IDLE
    reader.shutdown()


def test_speed_change_invalidates_cache_and_reaches_engine():
    reader, reg, _sink = make_reader()
    reader.load(DOC, autoplay=False)
    reader.set_speed(2.5)
    reader.play()
    time.sleep(0.4)
    speeds = {speed for _text, speed in reg.calls}
    assert 2.5 in speeds, speeds
    reader.shutdown()


def test_speed_is_capped_to_engine_maximum():
    reader, _reg, _sink = make_reader()
    reader.set_speed(99.0)
    assert reader.config.speed == 4.0
    reader.set_speed(0.01)
    assert reader.config.speed == 0.5
    reader.shutdown()


def test_resume_from_saved_index():
    reader, _reg, _sink = make_reader()
    seen: list[int] = []
    reader.on_segment = lambda i, t, n: seen.append(i)
    reader.load(DOC, start_index=2)
    time.sleep(0.4)
    assert seen and seen[0] == 2, seen
    reader.shutdown()



def test_the_speed_ceiling_is_the_apps_own_not_just_the_engines():
    """Pins the product limit rather than the engine's.

    A threshold sweep found this constant unpinned: every test used a stub
    reporting the same maximum, so the app's own ceiling could be raised to any
    value and nothing noticed. An engine claiming more must still be capped.
    """
    reader, registry, _sink = make_reader()
    registry.max_speed = lambda engine: 8.0
    try:
        reader.set_speed(99.0)
        assert reader.config.speed == 4.0, reader.config.speed
    finally:
        reader.shutdown()


def test_the_speed_floor_holds_against_zero_and_negatives():
    """Zero speed is a division by zero in every engine that takes duration."""
    reader, _registry, _sink = make_reader()
    try:
        for value in (0.0, -3.0, 0.001):
            reader.set_speed(value)
            assert reader.config.speed == 0.5, (value, reader.config.speed)
    finally:
        reader.shutdown()


def test_sapi_rates_stay_inside_the_range_the_api_accepts():
    """SAPI defines -10 to 10. Sending more is not faster, it is invalid."""
    from executive_reader.tts.sapi import SapiEngine
    for speed in (0.001, 0.5, 1.0, 2.5, 3.0, 99.0):
        rate = SapiEngine._rate_for(speed)
        assert -10 <= rate <= 10, (speed, rate)
    assert SapiEngine._rate_for(99.0) == 10
    assert SapiEngine._rate_for(0.001) == -10



def test_a_straight_read_synthesizes_each_segment_once():
    """Prefetch must not evict what playback is about to need.

    The eviction window was centred on the segment just rendered rather than on
    the playhead. The prefetcher works ahead by design, so it discarded the
    sentence being spoken and the one due next, and both were synthesized again
    moments later. Nothing sounds wrong. It costs twice the CPU, which on a
    neural voice is the difference between keeping up and stalling.

    This builds its own Config rather than using make_reader(), which pins
    prefetch to 2. That is the one depth where the old window happened not to
    overlap, so the fixture was set to the single value that hid the defect.
    """
    cfg = Config()
    assert cfg.prefetch_segments >= 3, (
        "shipped prefetch depth dropped to " + str(cfg.prefetch_segments)
        + "; this test no longer covers the case that regressed")
    reg, sink = StubRegistry(), StubSink(speed_factor=60.0)
    reader = Reader(reg, cfg, sink=sink)
    doc = Document(text=" ".join("Sentence number " + str(i) + " here."
                                 for i in range(14)),
                   title="Counting", uri="test://prefetch")
    done = threading.Event()
    reader.on_finished = done.set
    try:
        total = reader.load(doc)
        assert done.wait(timeout=40), "playback never finished"
    finally:
        reader.shutdown()

    counts = collections.Counter(text for text, _speed in reg.calls)
    repeated = sorted(t for t, c in counts.items() if c > 1)
    assert not repeated, (str(len(repeated)) + " of " + str(total)
                          + " segments were synthesized more than once")

if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print("PASS", name)
            passed += 1
        except Exception as exc:
            print("FAIL", name, "->", type(exc).__name__, exc)
            failed += 1
    print("\n" + str(passed) + " passed, " + str(failed) + " failed")
    sys.exit(1 if failed else 0)
