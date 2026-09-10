"""Run a Piper voice on onnxruntime, without the piper-tts package.

Same reasoning as kokoro_direct. The convenience package depends on
piper-phonemize, which embeds eSpeak NG and is GPL-3.0, so depending on it
would put copyleft over anything distributed. The voice models themselves are
ordinary ONNX and each one ships a JSON config that already contains the
phoneme-to-id table, so the package buys convenience rather than capability.

This is what keeps "train your own voice" alive. A Piper model fine-tuned on
your own recordings is a .onnx and a .onnx.json; drop them in and they run
here, with phonemes from our own permissive dictionary.

The honest caveat: Piper voices are trained on eSpeak's phoneme output, and our
dictionary imitates eSpeak's conventions but is not eSpeak. Expect a good
approximation on ordinary English rather than an exact match.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np

#: Piper's control symbols, present in every voice's phoneme_id_map.
BOS = "^"
EOS = "$"
PAD = "_"

#: Defaults from Piper's training configuration, used when a voice omits them.
DEFAULT_NOISE = 0.667
DEFAULT_NOISE_W = 0.8
DEFAULT_RATE = 22050


class PiperUnavailable(RuntimeError):
    pass


def load_config(config_path: Path) -> dict:
    try:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PiperUnavailable("Unreadable voice config: " + str(config_path)) from exc
    if not isinstance(data, dict):
        raise PiperUnavailable("Voice config is not an object: " + str(config_path))
    return data


def sample_rate(config: dict) -> int:
    audio = config.get("audio")
    if isinstance(audio, dict) and audio.get("sample_rate"):
        try:
            return int(audio["sample_rate"])
        except (TypeError, ValueError):
            pass
    return DEFAULT_RATE


def phoneme_ids(phonemes: str, config: dict) -> tuple[list[int], list[str]]:
    """Map phonemes to model ids, and report the ones this voice lacks.

    Piper interleaves a pad token between every phoneme and brackets the whole
    sequence, which is part of the encoding rather than decoration: a model
    trained that way produces noise without it.

    Unknown symbols are skipped, which is silent, so they are returned for the
    caller to surface rather than swallowed.
    """
    table = config.get("phoneme_id_map")
    if not isinstance(table, dict) or not table:
        raise PiperUnavailable("Voice config has no phoneme_id_map")

    def ids_for(symbol: str) -> list[int] | None:
        value = table.get(symbol)
        if value is None:
            return None
        if isinstance(value, int):
            return [value]
        return [int(v) for v in value]

    start, end, pad = ids_for(BOS), ids_for(EOS), ids_for(PAD)
    out: list[int] = list(start or [])
    missing: list[str] = []
    for char in phonemes:
        mapped = ids_for(char)
        if mapped is None:
            if not char.isspace():
                missing.append(char)
            continue
        out.extend(mapped)
        if pad:
            out.extend(pad)
    out.extend(end or [])
    return out, missing


def scales(config: dict, speed: float) -> np.ndarray:
    """Piper's three synthesis knobs: noise, length, noise width.

    Length is duration rather than rate, so it is the reciprocal of speed. It
    is a synthesis input, which is why fast reading keeps its pitch instead of
    sounding resampled.
    """
    inference = config.get("inference")
    inference = inference if isinstance(inference, dict) else {}
    noise = float(inference.get("noise_scale", DEFAULT_NOISE))
    noise_w = float(inference.get("noise_w", DEFAULT_NOISE_W))
    length = 1.0 / max(0.1, float(speed))
    return np.array([noise, length, noise_w], dtype=np.float32)


class PiperVoiceModel:
    """Lazily loaded ONNX session for one voice."""

    def __init__(self, model_path: Path, config_path: Path) -> None:
        self.model_path = Path(model_path)
        self.config_path = Path(config_path)
        self._session = None
        self._config: dict | None = None
        self._lock = threading.Lock()

    @property
    def config(self) -> dict:
        if self._config is None:
            self._config = load_config(self.config_path)
        return self._config

    def _load(self):
        if self._session is not None:
            return self._session
        try:
            import onnxruntime
        except ImportError as exc:
            raise PiperUnavailable(
                "onnxruntime is not installed. Run: pip install onnxruntime"
            ) from exc
        if not self.model_path.is_file():
            raise PiperUnavailable("Voice model is missing: " + str(self.model_path))
        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        self._session = onnxruntime.InferenceSession(
            str(self.model_path), sess_options=options,
            providers=onnxruntime.get_available_providers())
        return self._session

    def synthesize(self, phonemes: str,
                   speed: float = 1.0) -> tuple[np.ndarray, int, list[str]]:
        config = self.config
        ids, missing = phoneme_ids(phonemes, config)
        rate = sample_rate(config)
        if len(ids) <= 2:                      # nothing but the brackets
            return np.zeros(0, dtype=np.float32), rate, missing

        with self._lock:
            session = self._load()
            feeds = {
                "input": np.array([ids], dtype=np.int64),
                "input_lengths": np.array([len(ids)], dtype=np.int64),
                "scales": scales(config, speed),
            }
            names = {i.name for i in session.get_inputs()}
            if "sid" in names:
                speaker = config.get("speaker_id_map") or {}
                first = 0
                if isinstance(speaker, dict) and speaker:
                    first = int(next(iter(speaker.values())))
                feeds["sid"] = np.array([first], dtype=np.int64)
            feeds = {k: v for k, v in feeds.items() if k in names}
            audio = session.run(None, feeds)[0]

        return np.asarray(audio, dtype=np.float32).reshape(-1), rate, missing
