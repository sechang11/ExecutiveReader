"""Windows SAPI voices.

The zero-setup tier. Every Windows machine already has these, so the app can
speak before anything is downloaded. Quality depends on which voices are
installed; Windows 11 ships the Microsoft Natural voices, which are decent.

Audio is captured into a memory stream rather than sent straight to the
speakers, so SAPI shares the same player, speed control and stop behaviour as
the neural engines.
"""
from __future__ import annotations

import math
import threading

import numpy as np

from .base import Engine, EngineError, Voice, pcm16_to_float

_SAFT_22KHZ_16BIT_MONO = 22
_RATE = 22050


class SapiEngine(Engine):
    name = "sapi"
    max_speed = 3.0  # beyond this SAPI clips words rather than speeding up

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._voices: list[Voice] | None = None

    @property
    def available(self) -> bool:
        try:
            import win32com.client  # noqa: F401
        except ImportError:
            return False
        return True

    def _dispatch(self):
        import pythoncom
        import win32com.client
        # Each worker thread needs COM initialised before Dispatch.
        pythoncom.CoInitialize()
        return win32com.client.Dispatch("SAPI.SpVoice")

    def voices(self) -> list[Voice]:
        if self._voices is not None:
            return self._voices
        if not self.available:
            self._voices = []
            return self._voices
        found: list[Voice] = []
        try:
            speaker = self._dispatch()
            for token in speaker.GetVoices():
                desc = token.GetDescription()
                lang = "en"
                gender = ""
                try:
                    gender = (token.GetAttribute("Gender") or "").lower()
                except Exception:
                    pass
                found.append(Voice(id=desc, name=desc, engine=self.name,
                                   lang=lang, gender=gender, installed=True))
        except Exception as exc:  # pragma: no cover - depends on the machine
            raise EngineError(f"SAPI unavailable: {exc}") from exc
        self._voices = found
        return found

    #: Measured on Windows 11 by timing rendered audio at every rate from -10
    #: to 10: each step multiplies speaking rate by this much, so rate 10 lands
    #: at 3.04x. The scale is geometric, not linear.
    _RATE_STEP = 1.1176

    @classmethod
    def _rate_for(cls, speed: float) -> int:
        """Map a speed multiplier onto SAPI's -10..10 rate scale."""
        if speed <= 0:
            return 0
        return max(-10, min(10, int(round(math.log(speed, cls._RATE_STEP)))))

    @classmethod
    def effective_speed(cls, speed: float) -> float:
        """The speed actually delivered. SAPI only has integer rate steps, so a
        request of 1.5x lands on the nearest reachable value instead."""
        return cls._RATE_STEP ** cls._rate_for(speed)

    def synthesize(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
        if not text.strip():
            return np.zeros(0, dtype=np.float32), _RATE
        import win32com.client
        with self._lock:
            speaker = self._dispatch()
            if voice:
                for token in speaker.GetVoices():
                    if token.GetDescription() == voice:
                        speaker.Voice = token
                        break
            speaker.Rate = self._rate_for(speed)

            stream = win32com.client.Dispatch("SAPI.SpMemoryStream")
            fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
            fmt.Type = _SAFT_22KHZ_16BIT_MONO
            stream.Format = fmt
            speaker.AudioOutputStream = stream
            speaker.Speak(text)

            data = stream.GetData()
        raw = bytes(data) if not isinstance(data, bytes) else data
        return pcm16_to_float(raw), _RATE
