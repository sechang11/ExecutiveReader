"""Common shape for every voice engine.

An engine turns a short piece of text into mono float32 samples. Speed is
applied at synthesis time, not by resampling afterwards, so 2.5x keeps its
natural pitch instead of sounding like a chipmunk.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    engine: str
    lang: str = "en"
    gender: str = ""
    installed: bool = True
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.engine}:{self.id}"


class EngineError(RuntimeError):
    pass


class Engine(abc.ABC):
    name: str = "base"
    #: Rough upper bound on natural-sounding speed for this engine.
    max_speed: float = 4.0

    @property
    @abc.abstractmethod
    def available(self) -> bool:
        """False when the backing library or model is not installed yet."""

    @abc.abstractmethod
    def voices(self) -> list[Voice]:
        ...

    @abc.abstractmethod
    def synthesize(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
        """Return (mono float32 samples in [-1, 1], sample rate)."""

    def default_voice(self) -> str:
        vs = [v for v in self.voices() if v.installed]
        return vs[0].id if vs else ""

    def warm_up(self) -> None:
        """Optional: load models so the first sentence is not slow."""


def pcm16_to_float(raw: bytes) -> np.ndarray:
    if not raw:
        return np.zeros(0, dtype=np.float32)
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    return audio / 32768.0


def silence(seconds: float, rate: int) -> np.ndarray:
    return np.zeros(int(seconds * rate), dtype=np.float32)
