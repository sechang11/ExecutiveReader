"""Piper voices, including ones you trained yourself.

Run directly on onnxruntime rather than through the piper-tts package, which
depends on piper-phonemize and therefore embeds GPL-3.0 eSpeak NG. A Piper
voice is an .onnx plus an .onnx.json holding its own phoneme table, so the
package bought convenience rather than capability. See CAVEATS.md.

This is the path for a voice fine-tuned on your own recordings: export the
model, drop both files into the piper folder under %APPDATA%/Executive Reader/voices,
and it appears in the picker.

Licences differ per catalogue voice because they come from different source
datasets. Check the model card of any voice before shipping it commercially.
Phonemes come from our own dictionary, which imitates eSpeak's conventions but
is not eSpeak, so expect a good approximation rather than an exact match.
"""
from __future__ import annotations

import threading

import numpy as np

from ..config import voices_dir
from . import phonemes, piper_direct
from .base import Engine, EngineError, Voice
from .download import ProgressFn, download

_HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"

# name -> (repo path, display label, language, gender, approx MB)
CATALOG = {
    "en_US-amy-medium":        ("en/en_US/amy/medium/", "Amy", "en-us", "female", 63),
    "en_US-lessac-medium":     ("en/en_US/lessac/medium/", "Lessac", "en-us", "female", 63),
    "en_US-lessac-high":       ("en/en_US/lessac/high/", "Lessac (high)", "en-us", "female", 114),
    "en_US-ryan-high":         ("en/en_US/ryan/high/", "Ryan (high)", "en-us", "male", 114),
    "en_US-hfc_female-medium": ("en/en_US/hfc_female/medium/", "HFC Female", "en-us", "female", 63),
    "en_US-hfc_male-medium":   ("en/en_US/hfc_male/medium/", "HFC Male", "en-us", "male", 63),
    "en_US-libritts_r-medium": ("en/en_US/libritts_r/medium/", "LibriTTS-R", "en-us", "", 63),
    "en_GB-alba-medium":       ("en/en_GB/alba/medium/", "Alba", "en-gb", "female", 63),
    "en_GB-cori-high":         ("en/en_GB/cori/high/", "Cori (high)", "en-gb", "female", 114),
    "en_GB-northern_english_male-medium": (
        "en/en_GB/northern_english_male/medium/", "Northern English Male",
        "en-gb", "male", 63),
}

_MISSING_RUNTIME = "onnxruntime is not installed. Run: pip install onnxruntime"


class PiperEngine(Engine):
    name = "piper"
    max_speed = 4.0
    #: Past this the model returns less speed than it is asked for and
    #: slurs what it does return, so the player takes the remainder out
    #: of the audio instead. Measured, not guessed: 1.5 comes back as
    #: 1.51, while 2.0 comes back as 1.78 and 3.0 as 2.09.
    native_speed_limit = 1.5

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded: dict[str, piper_direct.PiperVoiceModel] = {}
        self._unmapped: set[str] = set()

    # --- installation ----------------------------------------------------
    def model_path(self, voice_id: str):
        return voices_dir() / "piper" / (voice_id + ".onnx")

    def config_path(self, voice_id: str):
        return voices_dir() / "piper" / (voice_id + ".onnx.json")

    def is_installed(self, voice_id: str) -> bool:
        return self.model_path(voice_id).exists() and self.config_path(voice_id).exists()

    @property
    def library_present(self) -> bool:
        """Runtime and pronunciation data, without importing either."""
        import importlib.util
        return (importlib.util.find_spec("onnxruntime") is not None
                and phonemes.available())

    def custom_voice_ids(self) -> list[str]:
        """Voices dropped into the folder by hand, including ones you trained.

        A voice you fine-tuned yourself has no catalogue entry, so anything
        that asks "is a voice installed" by walking CATALOG cannot see it.
        """
        folder = voices_dir() / "piper"
        if not folder.exists():
            return []
        return [model.stem for model in sorted(folder.glob("*.onnx"))
                if model.stem not in CATALOG
                and self.config_path(model.stem).exists()]

    @property
    def available(self) -> bool:
        # Custom voices count. They were listed by voices() and ignored here,
        # so a user whose only Piper voice was one they trained saw it in the
        # picker, selected it, and got Microsoft David: the engine reported
        # itself unavailable, and resolve() fell through to SAPI without
        # saying anything. That is the fine-tuning path the README documents.
        return self.library_present and (
            any(self.is_installed(v) for v in CATALOG)
            or bool(self.custom_voice_ids()))

    def install(self, voice_id: str, on_progress: ProgressFn | None = None) -> None:
        if voice_id not in CATALOG:
            raise EngineError("Unknown Piper voice: " + voice_id)
        folder = CATALOG[voice_id][0]
        download(_HF + folder + voice_id + ".onnx", self.model_path(voice_id), on_progress)
        download(_HF + folder + voice_id + ".onnx.json", self.config_path(voice_id))

    def install_custom(self, onnx_file, json_file, voice_id: str) -> None:
        """Register a voice you trained or downloaded yourself.

        This is the drop-in point for a Piper model fine-tuned on your own
        recordings: copy the exported .onnx and its .onnx.json here and it
        appears in the picker like any other voice.
        """
        dest_model = self.model_path(voice_id)
        dest_model.parent.mkdir(parents=True, exist_ok=True)
        dest_model.write_bytes(open(onnx_file, "rb").read())
        self.config_path(voice_id).write_bytes(open(json_file, "rb").read())

    # --- synthesis -------------------------------------------------------
    def voices(self) -> list[Voice]:
        out = []
        for vid, (_folder, label, lang, gender, size_mb) in CATALOG.items():
            here = self.is_installed(vid)
            out.append(Voice(id=vid, name=label, engine=self.name, lang=lang,
                             gender=gender, installed=here,
                             note="" if here else str(size_mb) + " MB download"))
        # Anything dropped into the piper folder by hand, including your own.
        for vid in self.custom_voice_ids():
            out.append(Voice(id=vid, name=vid + " (custom)", engine=self.name,
                             installed=True, note="custom"))
        return out

    def default_voice(self) -> str:
        for vid in CATALOG:
            if self.is_installed(vid):
                return vid
        # Same blind spot as available(): prefer a real voice the user has
        # over a catalogue name they have not downloaded.
        custom = self.custom_voice_ids()
        if custom:
            return custom[0]
        return next(iter(CATALOG))

    def _load(self, voice_id: str) -> piper_direct.PiperVoiceModel:
        existing = self._loaded.get(voice_id)
        if existing is not None:
            return existing
        if not self.library_present:
            raise EngineError(_MISSING_RUNTIME)
        if not self.is_installed(voice_id):
            raise EngineError("Piper voice not downloaded: " + voice_id)
        model = piper_direct.PiperVoiceModel(self.model_path(voice_id),
                                             self.config_path(voice_id))
        self._loaded[voice_id] = model
        return model

    def sample_rate(self, voice_id: str) -> int:
        """The voice's own rate, from its config rather than assumed."""
        try:
            return piper_direct.sample_rate(
                piper_direct.load_config(self.config_path(voice_id)))
        except piper_direct.PiperUnavailable:
            return piper_direct.DEFAULT_RATE

    def synthesize(self, text: str, voice_id: str, speed: float) -> tuple[np.ndarray, int]:
        if not text.strip():
            return np.zeros(0, dtype=np.float32), piper_direct.DEFAULT_RATE
        voice_id = voice_id or self.default_voice()
        speed = max(0.5, min(self.max_speed, float(speed)))

        ipa, _misses = phonemes.phonemize(text)
        try:
            model = self._load(voice_id)
            samples, rate, missing = model.synthesize(ipa, speed)
        except piper_direct.PiperUnavailable as exc:
            raise EngineError(str(exc)) from exc

        # A symbol this voice's table lacks is skipped, which costs the word a
        # sound and reports nothing on its own.
        for symbol in missing:
            self._unmapped.add(symbol)
        return samples, rate

    @property
    def unmapped_symbols(self) -> list[str]:
        """Phonemes no loaded voice could map. Should stay empty."""
        return sorted(self._unmapped)
