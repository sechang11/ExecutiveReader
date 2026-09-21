"""Run Kokoro directly on onnxruntime, without the kokoro-onnx package.

The package is convenient but its tokenizer imports eSpeak NG at module level,
so merely importing it loads GPL-3.0 code into the process and puts it in our
dependency list. Passing it pre-computed phonemes does not help: the import
happens either way.

The model itself is Apache 2.0 and the interface is small. Three inputs, one
output. So this drops the dependency rather than working around it, which is
what makes the permissive path actually permissive. See CAVEATS.md.

Everything here is arithmetic and array handling; the only third-party pieces
are numpy and onnxruntime, both permissive.
"""
from __future__ import annotations

import json
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

SAMPLE_RATE = 24000
#: The model accepts 510 phoneme tokens; two are the boundary padding.
MAX_TOKENS = 508

# Affricates are single symbols in the alphabet. The two-character forms are
# not rejected, they tokenize as two different sounds, so normalising is a
# correctness fix rather than tidying: "tʃ" would become t followed by ʃ.
LIGATURES = (("tʃ", "ʧ"), ("dʒ", "ʤ"), ("ts", "ʦ"),
             ("dz", "ʣ"), ("tɕ", "ʨ"), ("dʑ", "ʥ"))


class KokoroUnavailable(RuntimeError):
    pass


#: Where the phoneme alphabet may live, canonical first.
#:
#: It still sits only in the extension's package, which is the same shape the
#: dictionary had before it moved to shared/: a desktop-only build cannot be
#: assembled without reaching into the other product's directory. shared/ is
#: listed first so that move needs no change here when it happens.
_VOCAB_LOCATIONS = (("shared", "kokoro"),
                    ("vendor", "kokoro"),
                    ("extension", "vendor", "kokoro"))


def vendor_dir() -> Path | None:
    """Find the model's phoneme alphabet by walking up from this file."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        for parts in _VOCAB_LOCATIONS:
            candidate = parent.joinpath(*parts)
            if (candidate / "vocab.json").is_file():
                return candidate
    return None


@lru_cache(maxsize=1)
def vocabulary() -> dict[str, int]:
    """The model's phoneme alphabet, shared with the extension."""
    folder = vendor_dir()
    if folder is None:
        return {}
    try:
        data = json.loads((folder / "vocab.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def normalize_phonemes(ipa: str) -> str:
    for two_char, single in LIGATURES:
        ipa = ipa.replace(two_char, single)
    return ipa


def tokenize(ipa: str, vocab: dict[str, int] | None = None) -> tuple[list[int], list[str]]:
    """Phonemes to model ids, plus the symbols the alphabet did not contain.

    Unknown symbols are dropped rather than rejected, which is silent: the word
    simply loses a sound. So they are counted and handed back for the caller to
    surface rather than swallowed here.
    """
    vocab = vocabulary() if vocab is None else vocab
    ids: list[int] = []
    dropped: list[str] = []
    for ch in normalize_phonemes(ipa):
        token = vocab.get(ch)
        if token is None:
            if not ch.isspace():
                dropped.append(ch)
            continue
        ids.append(int(token))
    return ids[:MAX_TOKENS], dropped


def chunk(ids: list[int], limit: int = MAX_TOKENS) -> list[list[int]]:
    """Split an over-long phoneme run into model-sized pieces."""
    if len(ids) <= limit:
        return [ids] if ids else []
    return [ids[i:i + limit] for i in range(0, len(ids), limit)]


class KokoroModel:
    """Lazily loaded ONNX session plus the voice style bank."""

    def __init__(self, model_path: Path, voices_path: Path) -> None:
        self.model_path = Path(model_path)
        self.voices_path = Path(voices_path)
        self._session = None
        self._voices = None
        self._input_names: set[str] = set()
        self._lock = threading.Lock()

    # --- loading ---------------------------------------------------------
    def _load(self):
        if self._session is not None:
            return self._session
        try:
            import onnxruntime
        except ImportError as exc:
            raise KokoroUnavailable(
                "onnxruntime is not installed. Run: pip install onnxruntime"
            ) from exc
        if not self.model_path.is_file():
            raise KokoroUnavailable("Kokoro model file is missing.")
        if not self.voices_path.is_file():
            raise KokoroUnavailable("Kokoro voice pack is missing.")

        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3          # errors only
        self._session = onnxruntime.InferenceSession(
            str(self.model_path), sess_options=options,
            providers=onnxruntime.get_available_providers())
        self._input_names = {i.name for i in self._session.get_inputs()}
        return self._session

    def _voice_bank(self):
        if self._voices is None:
            if not self.voices_path.is_file():
                raise KokoroUnavailable("Kokoro voice pack is missing.")
            self._voices = np.load(str(self.voices_path))
        return self._voices

    def voice_names(self) -> list[str]:
        try:
            return sorted(self._voice_bank().files)
        except (KokoroUnavailable, AttributeError, OSError):
            return []

    # --- synthesis -------------------------------------------------------
    @staticmethod
    def style_for(voice: np.ndarray, token_count: int) -> np.ndarray:
        """One style row per phoneme count, so n tokens use row n - 1."""
        if voice.ndim == 1:
            return voice
        row = min(max(token_count, 1), len(voice)) - 1
        return voice[row]

    #: What the published Kokoro ONNX builds call the two required inputs.
    #: Listed most common first; the loaded model decides which is used.
    _TOKEN_INPUTS = ("tokens", "input_ids")
    _STYLE_INPUTS = ("style", "ref_s")

    def _pick_input(self, candidates: tuple, what: str) -> str:
        for name in candidates:
            if name in self._input_names:
                return name
        raise KokoroUnavailable(
            "This Kokoro model names its " + what + " input something "
            "unexpected: " + ", ".join(sorted(self._input_names))
            + ". Expected one of " + ", ".join(candidates) + ".")

    def _token_input(self) -> str:
        return self._pick_input(self._TOKEN_INPUTS, "token")

    def _style_input(self) -> str:
        return self._pick_input(self._STYLE_INPUTS, "style")

    def synthesize(self, phonemes: str, voice: str,
                   speed: float = 1.0) -> tuple[np.ndarray, int, list[str]]:
        """Return (samples, sample rate, dropped symbols)."""
        ids, dropped = tokenize(phonemes)
        if not ids:
            return np.zeros(0, dtype=np.float32), SAMPLE_RATE, dropped

        with self._lock:
            session = self._load()
            bank = self._voice_bank()
            try:
                style_bank = bank[voice]
            except (KeyError, IndexError) as exc:
                raise KokoroUnavailable("Unknown Kokoro voice: " + voice) from exc

            pieces = []
            for part in chunk(ids):
                style = self.style_for(np.asarray(style_bank), len(part))
                tokens = np.array([[0, *part, 0]], dtype=np.int64)
                # Ask the model what its inputs are called rather than assuming.
                # Published Kokoro ONNX builds differ here: this one names them
                # `tokens` and `style`, others use `input_ids` and `ref_s`. The
                # name was hardcoded to `input_ids`, so every synthesis raised
                # "Required inputs (['tokens']) are missing" — and nothing
                # noticed, because the engine cannot run at all until a 310 MB
                # model is downloaded, and no test or harness downloads one.
                feeds = {
                    self._token_input(): tokens,
                    self._style_input(): np.asarray(
                        style, dtype=np.float32).reshape(1, -1),
                }
                # Speed is a synthesis input, so fast reading keeps its pitch.
                # Resampling finished audio is what makes it sound chipmunky.
                if "speed" in self._input_names:
                    feeds["speed"] = np.array([speed], dtype=np.float32)
                audio = session.run(None, feeds)[0]
                pieces.append(np.asarray(audio, dtype=np.float32).reshape(-1))

        samples = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
        return samples, SAMPLE_RATE, dropped
