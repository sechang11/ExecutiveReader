"""The reader: turns a Document into speech you can steer.

Playback runs on its own thread so the UI never blocks. A second thread
synthesizes the next few segments ahead of the one being spoken, which is what
keeps skipping and resuming feeling instant rather than waiting on the model.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np

from ..config import Config
from ..document import Document
from ..textproc.normalize import normalize
from ..textproc.pronounce import Dictionary
from ..textproc.segment import segment as split_segments
from ..tts.registry import Registry
from .audio import AudioSink, AudioUnavailable
from .stretch import time_stretch

IDLE, PLAYING, PAUSED = "idle", "playing", "paused"


def _noop(*_args, **_kwargs) -> None:
    pass


class Reader:
    def __init__(self, registry: Registry, config: Config,
                 dictionary: Dictionary | None = None,
                 sink: AudioSink | None = None) -> None:
        self.registry = registry
        self.config = config
        self.dictionary = dictionary or Dictionary()
        self.sink = sink or AudioSink()

        self.doc: Document | None = None
        self.segments: list[str] = []
        self.index = 0
        self.segment_seconds = 0.0
        self.state = IDLE

        # Callbacks are replaced by the app layer; defaults keep tests quiet.
        self.on_state: Callable[[str], None] = _noop
        self.on_segment: Callable[[int, str, int], None] = _noop
        self.on_finished: Callable[[], None] = _noop
        self.on_error: Callable[[str], None] = _noop

        self._lock = threading.RLock()
        self._gate = threading.Event()      # set while playback should run
        self._abort = threading.Event()     # interrupt the current segment
        self._ack = threading.Event()       # playback thread finished reacting
        self._abort_reason = ""
        self._resume_frame = 0
        #: Audio handed back to the playback loop instead of being rendered:
        #: (index, samples, rate). Used when the speed changes mid-sentence.
        self._pending: tuple | None = None
        self._speed_was = 0.0
        self._quit = False
        self._token = 0                     # bumped when voice or speed changes
        self._cache: dict[int, tuple[int, np.ndarray, int]] = {}
        self._cache_lock = threading.Lock()

        self._worker = threading.Thread(target=self._run, daemon=True,
                                        name="executive-reader-playback")
        self._worker.start()
        self._prefetcher = threading.Thread(target=self._prefetch_loop, daemon=True,
                                            name="executive-reader-prefetch")
        self._prefetcher.start()

    # --- loading ---------------------------------------------------------
    def load(self, doc: Document, start_index: int = 0, autoplay: bool = True) -> int:
        """Replace what is being read. Returns the number of segments."""
        cleaned = normalize(doc.text,
                            skip_code=self.config.skip_code_blocks,
                            urls=self.config.read_urls)
        segments = split_segments(cleaned, self.config.max_segment_chars)
        with self._lock:
            self.stop(_notify=False)
            self.doc = doc
            self.segments = segments
            self.index = max(0, min(start_index, max(0, len(segments) - 1)))
            self._resume_frame = 0
            self._invalidate()
        if autoplay and segments:
            self.play()
        else:
            self._set_state(IDLE)
        return len(segments)

    # --- transport -------------------------------------------------------
    def play(self) -> None:
        with self._lock:
            if not self.segments:
                return
            if self.index >= len(self.segments):
                self.index = 0
                self._resume_frame = 0
        self._set_state(PLAYING)
        self._gate.set()

    def pause(self) -> None:
        with self._lock:
            if self.state != PLAYING:
                return
        self._gate.clear()
        self._interrupt("pause")
        # Wait for the playback thread to record where it stopped, so
        # _resume_frame is valid the moment pause() returns.
        self._ack.wait(timeout=2.0)
        self._set_state(PAUSED)

    def toggle(self) -> None:
        if self.state == PLAYING:
            self.pause()
        else:
            self.play()

    def stop(self, _notify: bool = True) -> None:
        self._interrupt("stop")
        self._gate.clear()
        with self._lock:
            self._resume_frame = 0
        if _notify:
            self._set_state(IDLE)

    def seek(self, index: int, keep_playing: bool | None = None) -> None:
        was_playing = self.state == PLAYING if keep_playing is None else keep_playing
        with self._lock:
            if not self.segments:
                return
            self.index = max(0, min(index, len(self.segments) - 1))
            self._resume_frame = 0
        self._interrupt("seek")
        if was_playing:
            self.play()
        else:
            self.on_segment(self.index, self.current_text, len(self.segments))

    def start_at(self, index: int) -> None:
        """Move the cursor without announcing it.

        Used when a caller is about to call play() itself; seek() would
        announce the segment and playback would then announce it again.
        """
        with self._lock:
            if not self.segments:
                return
            self.index = max(0, min(index, len(self.segments) - 1))
            self._resume_frame = 0

    def next_segment(self) -> None:
        self.seek(self.index + 1)

    def prev_segment(self) -> None:
        # Restart the current sentence first, like a music player does.
        self.seek(self.index - 1 if self._resume_frame == 0 else self.index)

    # --- settings --------------------------------------------------------
    def set_speed(self, speed: float) -> None:
        """Change pace without losing your place.

        This used to throw the sentence away and start it again, which is the
        one thing a speed control must not do: you change speed because you
        are listening, and the answer was to hear the last ten seconds over.

        It cannot re-render either. Asking the voice for the same sentence at
        a new speed takes about two seconds, and two seconds of silence in the
        middle of a sentence is worse than the restart was. So the audio
        already in hand is stretched instead, which takes about fifteen
        milliseconds, and playback carries on from the same word. Sentences
        after this one are rendered at the new speed as usual, which the voice
        does better than stretching.
        """
        speed = max(0.5, min(4.0, float(speed)))
        cap = self.registry.max_speed(self.config.engine)
        playing = self.state == PLAYING
        with self._lock:
            was = self.config.speed
            self.config.speed = min(speed, cap)
            now = self.config.speed
            if abs(now - was) < 0.001 or now <= 0:
                return
            self._invalidate()
            self._speed_was = was
            if not playing:
                # Paused or stopped: the saved position was measured in audio
                # at the old pace and the next render will be at the new one.
                self._resume_frame = int(self._resume_frame * was / now)
        if playing:
            self._interrupt("speed")

    def nudge_speed(self, delta: float) -> float:
        self.set_speed(round(self.config.speed + delta, 2))
        return self.config.speed

    def set_voice(self, engine: str, voice: str) -> None:
        with self._lock:
            self.config.engine = engine
            self.config.voice = voice
            self.config.speed = min(self.config.speed,
                                    self.registry.max_speed(engine))
            self._invalidate()
            self._resume_frame = 0
        if self.state == PLAYING:
            self._interrupt("seek")

    def set_volume(self, volume: float) -> None:
        self.config.volume = max(0.0, min(1.0, float(volume)))

    def pronunciation_changed(self) -> None:
        """Throw away audio synthesized under the old pronunciation rules.

        Without this, editing how a word is said changes nothing audible until
        the prefetch queue happens to run dry, which is several sentences later
        and unpredictable. A setting that takes effect at some unknowable
        future moment is indistinguishable from one that does not work.
        """
        with self._lock:
            self._invalidate()
            self._resume_frame = 0
        if self.state == PLAYING:
            self._interrupt("seek")   # re-render this sentence with the new rules

    # --- introspection ---------------------------------------------------
    @property
    def current_text(self) -> str:
        with self._lock:
            if 0 <= self.index < len(self.segments):
                return self.segments[self.index]
        return ""

    @property
    def total(self) -> int:
        return len(self.segments)

    @property
    def progress(self) -> float:
        with self._lock:
            if not self.segments:
                return 0.0
            return self.index / len(self.segments)

    def shutdown(self, timeout: float = 3.0) -> None:
        """Stop the worker threads, then release the audio device.

        The join is not optional. Closing the stream while the playback thread
        is still inside a write frees it underneath PortAudio, which segfaults
        the interpreter on exit, after everything has reported success.
        """
        self._quit = True
        self._interrupt("stop")
        self._gate.set()
        for thread in (self._worker, self._prefetcher):
            if thread is not None and thread.is_alive():
                thread.join(timeout=timeout)
        self.sink.close()

    # --- internals -------------------------------------------------------
    def _set_state(self, state: str) -> None:
        changed = state != self.state
        self.state = state
        if changed:
            self.on_state(state)

    def _interrupt(self, reason: str) -> None:
        self._abort_reason = reason
        self._ack.clear()
        self._abort.set()

    def _invalidate(self) -> None:
        self._token += 1
        with self._cache_lock:
            self._cache.clear()

    def _prepare(self, text: str) -> str:
        return self.dictionary.apply(text)

    def _render(self, idx: int) -> tuple[np.ndarray, int]:
        token = self._token
        with self._cache_lock:
            hit = self._cache.get(idx)
        if hit is not None and hit[0] == token:
            return hit[1], hit[2]

        with self._lock:
            if not (0 <= idx < len(self.segments)):
                return np.zeros(0, dtype=np.float32), 22050
            text = self.segments[idx]
            engine, voice, speed = (self.config.engine, self.config.voice,
                                    self.config.speed)

        samples, rate = self.registry.synthesize(self._prepare(text), engine,
                                                 voice, speed)
        with self._cache_lock:
            if self._token == token:
                self._cache[idx] = (token, samples, rate)
                self._evict(idx)
        return samples, rate

    def _evict(self, around: int) -> None:
        """Drop audio outside the window the playhead is moving through.

        The window used to be centred on `around`, the segment just rendered.
        That is the wrong anchor, because the caller doing most of the
        rendering is the prefetcher and it is deliberately ahead of playback:
        rendering segment n + 3 then evicting around n + 3 throws away the
        segment being spoken and the one due next. They get synthesized again
        a moment later, so nothing sounds wrong and the cost is invisible.

        At the shipped prefetch depth of 3 that re-synthesized 11 of 14
        segments on a straight read through, and at 4 the worst segment was
        rendered three times. Depth 2, the only value that happens not to
        overlap, was what the tests used.

        Reading self.index without the lock is deliberate. This runs with
        _cache_lock held, and _invalidate takes _cache_lock while holding
        _lock, so acquiring _lock here would invert the order between two
        threads that both do it. A stale read only changes which spare
        segment is kept.
        """
        head = self.index
        keep = set(range(head - 1, head + self.config.prefetch_segments + 2))
        keep.add(around)   # never discard what this call just paid to render
        for key in [k for k in self._cache if k not in keep]:
            del self._cache[key]

    def _prefetch_loop(self) -> None:
        while not self._quit:
            time.sleep(0.15)
            if self.state != PLAYING:
                continue
            with self._lock:
                start, total = self.index + 1, len(self.segments)
                ahead = self.config.prefetch_segments
            for idx in range(start, min(start + ahead, total)):
                if self._quit or self.state != PLAYING:
                    break
                with self._cache_lock:
                    hit = self._cache.get(idx)
                if hit is not None and hit[0] == self._token:
                    continue
                try:
                    self._render(idx)
                except Exception:
                    break  # the playback thread will surface the error

    def _run(self) -> None:
        while not self._quit:
            self._gate.wait()
            if self._quit:
                return
            with self._lock:
                idx, total = self.index, len(self.segments)
                start_frame = self._resume_frame
                carried, self._pending = self._pending, None
            if total == 0:
                self._gate.clear()
                continue
            if idx >= total:
                self._gate.clear()
                self._set_state(IDLE)
                self.on_finished()
                continue

            try:
                if carried is not None and carried[0] == idx:
                    # Already in hand, stretched to the new speed. Rendering
                    # it again would be two seconds of silence for nothing.
                    samples, rate = carried[1], carried[2]
                else:
                    samples, rate = self._render(idx)
            except AudioUnavailable as exc:
                self._gate.clear()
                self._set_state(IDLE)
                self.on_error(str(exc))
                continue
            except Exception as exc:
                self.on_error(str(exc))
                with self._lock:
                    self.index += 1
                continue

            # How long this sentence takes to say. The screen highlight needs
            # it to step word by word, and only the player knows: the text
            # gives no clue, since speed and voice both change the answer.
            self.segment_seconds = (len(samples) / float(rate)) if rate else 0.0
            if start_frame == 0:
                self.on_segment(idx, self.segments[idx] if idx < total else "", total)

            self._abort.clear()
            self._abort_reason = ""
            try:
                reached = self.sink.play(samples, rate, self.config.volume,
                                         self._abort.is_set, start_frame)
            except AudioUnavailable as exc:
                self._gate.clear()
                self._set_state(IDLE)
                self.on_error(str(exc))
                continue
            except Exception as exc:
                self.on_error(str(exc))
                reached = len(samples)

            reason = self._abort_reason
            with self._lock:
                if reason == "pause":
                    self._resume_frame = reached
                elif reason == "speed":
                    was = self._speed_was or self.config.speed
                    now = self.config.speed
                    self._speed_was = 0.0
                    # Same place in the sentence, said at the new pace. A
                    # frame count is a length of audio, and the audio just
                    # got shorter or longer, so the position moves with it.
                    self._pending = (idx, time_stretch(samples, rate, now / was), rate)
                    self._resume_frame = max(0, int(reached * was / now))
                elif reason in ("stop", "seek"):
                    self._resume_frame = 0
                elif self.index == idx:
                    self.index += 1
                    self._resume_frame = 0
            self._abort.clear()
            self._abort_reason = ""
            self._ack.set()
