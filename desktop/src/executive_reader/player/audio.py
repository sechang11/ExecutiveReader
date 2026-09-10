"""Audio output.

Writes in small blocks and checks an abort flag between each one, so stopping
is immediate rather than waiting out the buffered tail of a sentence. The
frame counter it returns is what makes pause resume mid-sentence instead of
restarting the sentence.
"""
from __future__ import annotations

import threading
from typing import Callable

import numpy as np

_BLOCK = 1024


class AudioUnavailable(RuntimeError):
    pass


class AudioSink:
    def __init__(self, blocksize: int = _BLOCK) -> None:
        self._sd = None
        self._stream = None
        self._rate: int | None = None
        self._blocksize = blocksize
        self._lock = threading.Lock()

    def _backend(self):
        if self._sd is None:
            try:
                import sounddevice as sd
            except (ImportError, OSError) as exc:
                raise AudioUnavailable("sounddevice is not available: " + str(exc)) from exc
            self._sd = sd
        return self._sd

    def _ensure(self, rate: int):
        sd = self._backend()
        if self._stream is not None and self._rate == rate:
            if not self._stream.active:
                self._stream.start()
            return self._stream
        self.close()
        self._stream = sd.OutputStream(
            samplerate=rate, channels=1, dtype="float32",
            blocksize=self._blocksize, latency="low",
        )
        self._stream.start()
        self._rate = rate
        return self._stream

    def play(self, samples: np.ndarray, rate: int, volume: float,
             should_abort: Callable[[], bool], start_frame: int = 0) -> int:
        """Play samples from start_frame. Returns the frame reached.

        A return value below len(samples) means playback was aborted there,
        which is exactly the offset a later resume should start from.
        """
        if samples is None or len(samples) == 0:
            return 0
        with self._lock:
            stream = self._ensure(rate)
            i = max(0, min(int(start_frame), len(samples)))
            gain = max(0.0, min(1.0, float(volume)))
            while i < len(samples):
                if should_abort():
                    self._drop_buffered()
                    return i
                chunk = samples[i:i + self._blocksize]
                stream.write(np.ascontiguousarray(chunk * gain, dtype=np.float32))
                i += len(chunk)
            return len(samples)

    def _drop_buffered(self) -> None:
        """Discard audio already handed to the device so stop feels instant."""
        if self._stream is None:
            return
        try:
            self._stream.abort()
            self._stream.start()
        except Exception:
            self.close()

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.abort()
                self._stream.close()
            except Exception:
                pass
        self._stream = None
        self._rate = None
