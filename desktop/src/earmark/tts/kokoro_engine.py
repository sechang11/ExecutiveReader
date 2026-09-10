"""Kokoro-82M, the recommended neural voice.

The model and its voice packs are Apache 2.0. Two files, roughly 330 MB
together, downloaded once. Speed is a synthesis parameter rather than a
playback rate, so fast reading stays natural instead of pitch-shifted.

This engine deliberately does not use the kokoro-onnx package. Kokoro takes
phonemes rather than text, and that package produces them with eSpeak NG, which
is GPL-3.0 and is imported at module level, so merely depending on it puts
copyleft code in the dependency list. Instead the phonemes come from our own
permissively licensed dictionary and the model runs directly on onnxruntime.

The cost of that choice is English only: the permissive dictionary covers
English, so the usable voice catalogue is smaller until a user installs eSpeak
themselves as an add-on. See CAVEATS.md for the licence reasoning and what the
add-on unlocks.
"""
from __future__ import annotations

import importlib.util
import threading

import numpy as np

from ..config import voices_dir
from . import kokoro_direct, phonemes
from .base import Engine, EngineError, Voice
from .download import ProgressFn, download

_RELEASE = ("https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
            "model-files-v1.0/")
MODEL_URL = _RELEASE + "kokoro-v1.0.onnx"
VOICES_URL = _RELEASE + "voices-v1.0.bin"

# Shown before the voice pack is downloaded so the picker is not empty on a
# fresh install. Once the pack is loaded the real list comes from the file.
_KNOWN = [
    ("af_heart", "Heart", "en-us", "female"), ("af_bella", "Bella", "en-us", "female"),
    ("af_nicole", "Nicole", "en-us", "female"), ("af_sarah", "Sarah", "en-us", "female"),
    ("af_sky", "Sky", "en-us", "female"), ("af_nova", "Nova", "en-us", "female"),
    ("am_adam", "Adam", "en-us", "male"), ("am_michael", "Michael", "en-us", "male"),
    ("am_echo", "Echo", "en-us", "male"), ("am_liam", "Liam", "en-us", "male"),
    ("am_onyx", "Onyx", "en-us", "male"), ("am_puck", "Puck", "en-us", "male"),
    ("bf_emma", "Emma", "en-gb", "female"), ("bf_isabella", "Isabella", "en-gb", "female"),
    ("bm_george", "George", "en-gb", "male"), ("bm_fable", "Fable", "en-gb", "male"),
]

_LANG_FOR_PREFIX = {
    "a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr",
    "h": "hi", "i": "it", "j": "ja", "p": "pt-br", "z": "cmn",
}
#: Prefixes the permissive English dictionary can pronounce on its own.
_ENGLISH_PREFIXES = ("a", "b")

_MISSING_RUNTIME = "onnxruntime is not installed. Run: pip install onnxruntime"
_MISSING_DICT = ("The pronunciation dictionary is missing. It lives in "
                 "vendor/cmudict and ships with the project.")
_MISSING_MODEL = "Kokoro model files are missing. Download them from Settings."


class KokoroEngine(Engine):
    name = "kokoro"
    max_speed = 4.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model: kokoro_direct.KokoroModel | None = None
        self._voice_ids: list[str] | None = None
        self._reported_drops: set[str] = set()

    # --- installation ----------------------------------------------------
    @property
    def model_path(self):
        return voices_dir() / "kokoro-v1.0.onnx"

    @property
    def voices_path(self):
        return voices_dir() / "voices-v1.0.bin"

    @property
    def installed(self) -> bool:
        return self.model_path.exists() and self.voices_path.exists()

    @property
    def library_present(self) -> bool:
        """Runtime and pronunciation data present, without importing either."""
        return (importlib.util.find_spec("onnxruntime") is not None
                and phonemes.available()
                and bool(kokoro_direct.vendor_dir()))

    @property
    def available(self) -> bool:
        return self.library_present and self.installed

    @property
    def blocked_by(self) -> str:
        """What is missing, specifically. Empty when the engine is usable.

        Worth distinguishing because the failures look identical from outside
        and have opposite fixes. A user who has downloaded 330 MB of model and
        is told "not installed" will download it again. The dictionary is the
        one that goes missing when the app is packaged without the vendored
        data beside it.
        """
        if importlib.util.find_spec("onnxruntime") is None:
            return "the onnxruntime package"
        if not phonemes.available() or not kokoro_direct.vendor_dir():
            return "the pronunciation dictionary"
        if not self.installed:
            return "the voice model download"
        return ""

    def install(self, on_progress: ProgressFn | None = None) -> None:
        """Download both model files. Safe to re-run; existing files are kept."""
        if importlib.util.find_spec("onnxruntime") is None:
            raise EngineError(_MISSING_RUNTIME)
        if not phonemes.available():
            raise EngineError(_MISSING_DICT)
        download(MODEL_URL, self.model_path, on_progress)
        download(VOICES_URL, self.voices_path, on_progress)

    # --- synthesis -------------------------------------------------------
    def _load(self) -> kokoro_direct.KokoroModel:
        if self._model is not None:
            return self._model
        if importlib.util.find_spec("onnxruntime") is None:
            raise EngineError(_MISSING_RUNTIME)
        if not phonemes.available():
            raise EngineError(_MISSING_DICT)
        if not self.installed:
            raise EngineError(_MISSING_MODEL)
        self._model = kokoro_direct.KokoroModel(self.model_path, self.voices_path)
        return self._model

    def warm_up(self) -> None:
        with self._lock:
            self._load()

    def speaks(self, voice_id: str) -> bool:
        """Whether this voice's language can be pronounced without eSpeak."""
        return voice_id[:1] in _ENGLISH_PREFIXES

    def voices(self) -> list[Voice]:
        ids = self._voice_ids
        if ids is None and self._model is not None:
            found = self._model.voice_names()
            if found:
                ids = found
                self._voice_ids = ids

        if ids:
            labels = {vid: label for vid, label, _lang, _gender in _KNOWN}
            out = []
            for vid in ids:
                gender = ("female" if vid[1:2] == "f"
                          else "male" if vid[1:2] == "m" else "")
                speakable = self.speaks(vid)
                out.append(Voice(
                    id=vid, name=labels.get(vid, vid), engine=self.name,
                    lang=_LANG_FOR_PREFIX.get(vid[:1], "en"), gender=gender,
                    installed=speakable,
                    note="" if speakable else "needs the eSpeak add-on"))
            return out

        note = "" if self.installed else "download required"
        return [Voice(id=vid, name=label, engine=self.name, lang=lang,
                      gender=gender, installed=self.installed, note=note)
                for vid, label, lang, gender in _KNOWN]

    def default_voice(self) -> str:
        return "af_heart"

    def synthesize(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
        if not text.strip():
            return np.zeros(0, dtype=np.float32), kokoro_direct.SAMPLE_RATE
        voice = voice or self.default_voice()
        if not self.speaks(voice):
            raise EngineError(
                "The " + _LANG_FOR_PREFIX.get(voice[:1], "this") + " voices need "
                "the eSpeak add-on. English voices work without it.")
        speed = max(0.5, min(self.max_speed, float(speed)))

        ipa, _misses = phonemes.phonemize(text)
        model = self._load()
        samples, rate, dropped = model.synthesize(ipa, voice, speed)
        # A dropped symbol costs the word a sound and nothing else reports it.
        for symbol in dropped:
            if symbol not in self._reported_drops:
                self._reported_drops.add(symbol)
        return samples, rate

    @property
    def unsupported_symbols(self) -> list[str]:
        """Phonemes produced that the model's alphabet lacks, if any ever are.

        Should stay empty: verify() checks the mapping against the alphabet.
        Non-empty means a pronunciation rule emits something the model silently
        discards, which is audible as a missing sound and reported nowhere else.
        """
        return sorted(self._reported_drops)
