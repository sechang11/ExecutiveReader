"""One place to ask for a voice, whichever engine backs it.

Voices are addressed as "engine:id", for example "kokoro:af_heart" or
"sapi:Microsoft Zira Desktop - English (United States)".
"""
from __future__ import annotations

import numpy as np

from .base import Engine, EngineError, Voice
from .kokoro_engine import KokoroEngine
from .piper_engine import PiperEngine
from .sapi import SapiEngine


class Registry:
    def __init__(self) -> None:
        self.sapi = SapiEngine()
        self.kokoro = KokoroEngine()
        self.piper = PiperEngine()
        self._engines: dict[str, Engine] = {
            e.name: e for e in (self.sapi, self.kokoro, self.piper)
        }

    def engine(self, name: str) -> Engine:
        try:
            return self._engines[name]
        except KeyError:
            raise EngineError("Unknown engine: " + str(name)) from None

    def all_engines(self) -> list[Engine]:
        return list(self._engines.values())

    def voices(self, engine: str | None = None) -> list[Voice]:
        if engine:
            return self.engine(engine).voices()
        out: list[Voice] = []
        for eng in self._engines.values():
            try:
                out.extend(eng.voices())
            except EngineError:
                continue
        return out

    def resolve(self, engine: str, voice: str) -> tuple[Engine, str]:
        """Pick the best usable engine and voice, falling back rather than failing.

        A missing neural model should degrade to a system voice, not stop
        playback, so the app still speaks on a fresh install.

        Whatever comes back must be installed on the engine that comes back
        with it. Handing over a voice id the engine does not have moves the
        failure down into synthesis, where it appears part-way through a
        document rather than as a voice the picker should not have offered.
        """
        eng = self._engines.get(engine)
        if eng is not None and eng.available:
            known = self._installed(eng)
            if voice in known:
                return eng, voice
            fallback = eng.default_voice()
            # `or not known` was accepted here too, which returned an engine
            # with nothing downloaded alongside a voice id it did not have.
            # It stayed harmless only because every engine's `available`
            # happened to imply an installed voice, which is a fact about
            # three other modules rather than about this one.
            if fallback in known:
                return eng, fallback
        if self.sapi.available:
            if voice in self._installed(self.sapi):
                return self.sapi, voice
            return self.sapi, self.sapi.default_voice()
        raise EngineError("No usable voice engine is installed.")

    @staticmethod
    def _installed(eng: Engine) -> set[str]:
        """Voice ids the engine says it can speak with right now.

        An engine that cannot enumerate must degrade like one with nothing
        installed. Letting that error escape resolve() turns a recoverable
        "pick another voice" into no audio at all.
        """
        try:
            return {v.id for v in eng.voices() if v.installed}
        except EngineError:
            return set()

    def synthesize(self, text: str, engine: str, voice: str,
                   speed: float) -> tuple[np.ndarray, int]:
        eng, vid = self.resolve(engine, voice)
        return eng.synthesize(text, vid, speed)

    def max_speed(self, engine: str) -> float:
        eng = self._engines.get(engine)
        return eng.max_speed if eng else 3.0

    def describe(self) -> list[str]:
        lines = []
        for eng in self._engines.values():
            if eng.available:
                ready = "ready"
            else:
                # Say what is actually missing where the engine can tell us.
                # "Not installed" sends someone to re-download a model they
                # already have when the real gap is somewhere else.
                missing = getattr(eng, "blocked_by", "")
                ready = ("needs " + missing) if missing else "not installed"
            try:
                count = sum(1 for v in eng.voices() if v.installed)
            except EngineError:
                count = 0
            lines.append(eng.name + ": " + ready + ", " + str(count) + " voice(s)")
        return lines
