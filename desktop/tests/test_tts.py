"""Voice selection, fallback, and the speed mapping.

The whole tts package was loaded by the suite and asserted against by nothing,
which a reachability scan cannot tell apart from tested. The gap mattered: a
Piper voice the user trained themselves appeared in the picker, was chosen, and
was then silently replaced by a Windows system voice.

No test here needs a model file or an audio device. Where an engine is needed it
is a stub, and where the real Piper engine is used its voice folder is
redirected into a temporary directory so the machine's own voices are never
read or written.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import install

install()

from executive_reader.tts import piper_engine
from executive_reader.tts.base import Engine, EngineError, Voice
from executive_reader.tts.registry import Registry
from executive_reader.tts.sapi import SapiEngine


class StubEngine(Engine):
    """An engine with exactly the voices you say it has."""

    max_speed = 4.0

    def __init__(self, name: str, installed: list[str],
                 offered: list[str] | None = None, available: bool = True) -> None:
        self.name = name
        self._installed = list(installed)
        self._offered = list(offered if offered is not None else installed)
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    def voices(self) -> list[Voice]:
        return [Voice(id=v, name=v, engine=self.name,
                      installed=v in self._installed) for v in self._offered]

    def default_voice(self) -> str:
        return self._installed[0] if self._installed else "nothing-installed"

    def synthesize(self, text, voice, speed):
        raise AssertionError("no test here should reach synthesis")


def _registry(**engines: StubEngine) -> Registry:
    """A Registry backed entirely by stubs, including its SAPI attribute."""
    reg = Registry()
    reg._engines = dict(engines)
    reg.sapi = engines.get("sapi", StubEngine("sapi", [], available=False))
    return reg


# --- the invariant that was violated -----------------------------------------

def test_resolve_never_returns_a_voice_that_is_not_installed():
    """The one property that matters: whatever comes back must be usable.

    Returning an engine plus a voice id it does not have pushes the failure
    down into synthesis, where it surfaces as an error part-way through a
    document rather than as a voice the picker should not have offered.
    """
    reg = _registry(
        sapi=StubEngine("sapi", ["David", "Zira"]),
        # Offers a catalogue but has downloaded none of it, which is what a
        # fresh install looks like.
        kokoro=StubEngine("kokoro", [], offered=["af_heart", "af_bella"],
                          available=False),
        piper=StubEngine("piper", ["my_own_voice"], available=True),
        # Claims to work, has downloaded nothing. This is the shape that broke
        # the invariant, and it stayed unreachable only by coincidence.
        broken=StubEngine("broken", [], offered=["something"], available=True),
    )
    asked = [("kokoro", "af_heart"), ("kokoro", ""), ("piper", "my_own_voice"),
             ("piper", "en_US-amy-medium"), ("sapi", "Zira"),
             ("sapi", "A Voice That Was Uninstalled"), ("nonsense", "whatever"),
             ("broken", "something"), ("broken", ""), ("", "")]
    for engine, voice in asked:
        chosen, vid = reg.resolve(engine, voice)
        installed = {v.id for v in chosen.voices() if v.installed}
        assert vid in installed, (
            "resolve(" + repr(engine) + ", " + repr(voice) + ") returned "
            + chosen.name + ":" + repr(vid) + ", which is not installed")


def test_a_missing_neural_model_degrades_to_a_system_voice():
    reg = _registry(
        sapi=StubEngine("sapi", ["David"]),
        kokoro=StubEngine("kokoro", [], offered=["af_heart"], available=False),
    )
    chosen, vid = reg.resolve("kokoro", "af_heart")
    assert chosen.name == "sapi", chosen.name
    assert vid == "David", vid


def test_no_usable_engine_says_so_rather_than_returning_nothing():
    reg = _registry(sapi=StubEngine("sapi", [], available=False))
    try:
        reg.resolve("sapi", "David")
    except EngineError:
        return
    raise AssertionError("resolve should refuse when nothing can speak")


def test_an_engine_that_cannot_list_its_voices_degrades_instead_of_failing():
    """Enumeration is allowed to fail. Playback is not.

    The voice list came from a bare comprehension over `eng.voices()`, so an
    engine whose enumeration raised took the error straight out of resolve and
    turned a recoverable "pick another voice" into no audio at all.
    """
    class Exploding(StubEngine):
        def voices(self):
            raise EngineError("the speech COM server is having a day")

    reg = _registry(sapi=StubEngine("sapi", ["David"]),
                    kokoro=Exploding("kokoro", ["af_heart"], available=True))
    chosen, vid = reg.resolve("kokoro", "af_heart")
    assert chosen.name == "sapi", chosen.name
    assert vid == "David", vid


# --- a voice you trained yourself --------------------------------------------

def _piper_with(tmp: Path, voice_ids: list[str]) -> piper_engine.PiperEngine:
    """A real PiperEngine whose voice folder is `tmp` and nothing else."""
    folder = tmp / "piper"
    folder.mkdir(parents=True, exist_ok=True)
    for vid in voice_ids:
        (folder / (vid + ".onnx")).write_bytes(b"not a real model")
        (folder / (vid + ".onnx.json")).write_text("{}", encoding="utf-8")
    piper_engine.voices_dir = lambda: tmp
    piper_engine.phonemes.available = lambda: True
    return piper_engine.PiperEngine()


def test_a_voice_you_trained_is_offered_and_can_actually_be_used():
    """The picker listed it; availability ignored it; resolve fell to SAPI.

    `available` and `default_voice` both answered by walking CATALOG, and a
    voice fine-tuned on your own recordings has no catalogue entry. So the
    engine called itself unavailable while advertising a working voice, the
    user selected it, and Microsoft David spoke instead, with nothing anywhere
    saying why. This is the fine-tuning path the README documents.
    """
    saved_dir, saved_phon = piper_engine.voices_dir, piper_engine.phonemes.available
    try:
        with tempfile.TemporaryDirectory() as tmp:
            eng = _piper_with(Path(tmp), ["my_own_voice"])
            offered = {v.id for v in eng.voices() if v.installed}
            assert offered == {"my_own_voice"}, offered
            assert eng.available, "an engine with a usable voice called itself unavailable"
            assert eng.default_voice() == "my_own_voice", eng.default_voice()

            reg = Registry()
            reg._engines["piper"] = eng
            reg.piper = eng
            chosen, vid = reg.resolve("piper", "my_own_voice")
            assert chosen.name == "piper", (
                "picked " + chosen.name + ":" + str(vid)
                + " instead of the voice the user trained")
            assert vid == "my_own_voice", vid
    finally:
        piper_engine.voices_dir = saved_dir
        piper_engine.phonemes.available = saved_phon


def test_an_empty_voice_folder_leaves_piper_unavailable():
    """The fix must not make the engine claim to work with nothing installed."""
    saved_dir, saved_phon = piper_engine.voices_dir, piper_engine.phonemes.available
    try:
        with tempfile.TemporaryDirectory() as tmp:
            eng = _piper_with(Path(tmp), [])
            assert not eng.available, "no voices, yet it reported itself available"
            assert not [v for v in eng.voices() if v.installed]
    finally:
        piper_engine.voices_dir = saved_dir
        piper_engine.phonemes.available = saved_phon


def test_a_model_without_its_config_is_not_a_voice():
    """Piper needs both files. Half a voice must not be offered as a whole one."""
    saved_dir, saved_phon = piper_engine.voices_dir, piper_engine.phonemes.available
    try:
        with tempfile.TemporaryDirectory() as tmp:
            eng = _piper_with(Path(tmp), [])
            (Path(tmp) / "piper" / "half_a_voice.onnx").write_bytes(b"model only")
            assert eng.custom_voice_ids() == [], eng.custom_voice_ids()
            assert not eng.available
    finally:
        piper_engine.voices_dir = saved_dir
        piper_engine.phonemes.available = saved_phon


# --- the speeds the app actually offers --------------------------------------

def test_the_offered_speeds_land_close_to_what_the_button_says():
    """SAPI has integer rate steps, so 2x is not reachable exactly.

    The buttons promise 1.5x, 2x, 2.5x and 3x. What matters is that each lands
    near enough that the label is not a lie, and that changing the measured
    step constant cannot quietly widen the gap.
    """
    for asked in (1.5, 2.0, 2.5, 3.0):
        got = SapiEngine.effective_speed(asked)
        drift = abs(got - asked) / asked
        assert drift < 0.06, ("asked for " + str(asked) + "x, delivered "
                              + str(round(got, 3)) + "x")


def test_the_speed_scale_is_ordered_and_clamped():
    speeds = [SapiEngine.effective_speed(s) for s in (0.5, 1.0, 1.5, 2.0, 3.0)]
    assert speeds == sorted(speeds), speeds
    assert abs(SapiEngine.effective_speed(1.0) - 1.0) < 0.001
    # Above the engine's ceiling the answer must stop rising, not keep scaling.
    assert SapiEngine.effective_speed(10.0) == SapiEngine.effective_speed(4.0)
    # Nonsense input must not produce a negative or zero rate.
    assert SapiEngine.effective_speed(0) > 0
    assert SapiEngine.effective_speed(-3) > 0


def test_an_unknown_engine_still_reports_a_usable_speed_ceiling():
    assert Registry().max_speed("nonsense") >= 1.0


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
