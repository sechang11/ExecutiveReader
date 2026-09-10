"""Grapheme-to-phoneme, tokenization, and the licence boundary."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import REPO_ROOT, install

install()

from earmark.tts import kokoro_direct as kd
from earmark.tts import phonemes as ph

SKIPPED = "SKIP"


# --- the licence boundary ------------------------------------------------

def test_no_copyleft_speech_packages_are_installed():
    """The whole reason this module exists.

    Kokoro needs phonemes, and every off-the-shelf way to produce them uses
    eSpeak NG, which is GPL-3.0. espeakng-loader ships the library binary and
    19 MB of data inside site-packages, so depending on it would put copyleft
    code in anything distributed. If one of these reappears in the environment,
    the permissive path has quietly stopped being permissive.

    eSpeak installed on the machine by the user is fine and is the supported
    add-on. This checks our dependencies, not the user's computer.
    """
    import importlib.util
    for name in ("kokoro_onnx", "phonemizer", "espeakng_loader"):
        assert importlib.util.find_spec(name) is None, (
            name + " is installed; it pulls in GPL-3.0 eSpeak NG. See CAVEATS.md")


def test_speaking_loads_no_copyleft_module():
    ph.phonemize("The quick brown fox jumps over the lazy dog.")
    loaded = [m for m in sys.modules
              if "espeak" in m or m in ("kokoro_onnx", "phonemizer")]
    assert loaded == [], loaded


# --- pronunciation -------------------------------------------------------

def test_dictionary_words_get_their_real_pronunciation():
    ipa, misses = ph.phonemize("The quick brown fox.")
    assert misses == [], misses
    assert ipa == "ðə kwˈɪk bɹˈaʊn fˈɑːks.", ipa


def test_stress_marks_the_vowel_not_the_syllable_onset():
    """Deliberately not correct notation. Kokoro was trained on eSpeak-shaped
    input and rewards familiarity over correctness; marking the syllable
    properly sounded worse."""
    ipa, _ = ph.phonemize("quick")
    assert ipa == "kwˈɪk", ipa
    assert not ipa.startswith("ˈ"), "the mark belongs on the vowel"


def test_stressed_long_vowels_carry_length():
    for word, expected in (("father", "ɑː"), ("thought", "ɔː"),
                           ("fleece", "iː"), ("goose", "uː")):
        ipa, _ = ph.phonemize(word)
        assert expected in ipa, (word, ipa)


def test_unstressed_ah_reduces_to_schwa():
    ipa, _ = ph.phonemize("banana")
    assert ipa.startswith("bə"), ipa
    assert "ə" in ipa


def test_punctuation_is_kept_and_never_preceded_by_a_space():
    ipa, _ = ph.phonemize("Wait, what? Yes!")
    assert "," in ipa and "?" in ipa and "!" in ipa
    assert " ," not in ipa and " ?" not in ipa and " !" not in ipa


# --- homographs ----------------------------------------------------------

def test_homographs_use_the_preceding_word_as_a_cue():
    noun, _ = ph.phonemize("a record")
    verb, _ = ph.phonemize("to record")
    assert noun != verb, "the two readings must differ"


def test_read_needs_an_explicit_cue_in_both_directions():
    past, _ = ph.phonemize("I have read it already.")
    present, _ = ph.phonemize("Please read this.")
    assert "ɹˈɛd" in past, past
    assert "ɹˈiːd" in present, present


def test_a_homograph_without_a_cue_defers_to_the_dictionary():
    """No cue is not a licence to guess; the dictionary entry stands."""
    assert ph.disambiguate("record", None) is None
    assert ph.disambiguate("record", "zebra") is None


def test_noun_and_verb_readings_are_not_a_stress_shift():
    """English reduces unstressed vowels, so re-stressing the noun's first
    vowel gives "ruh-CORD" rather than "REC-ord". Both readings must be real
    dictionary entries rather than one derived from the other."""
    pair = ph._homographs().get("record")
    assert pair, "record should be in the homograph table"
    assert pair["noun"][1] != pair["verb"][1], (pair["noun"], pair["verb"])


# --- words the dictionary lacks -----------------------------------------

def test_unknown_words_fall_back_and_are_reported():
    ipa, misses = ph.phonemize("blorptastic")
    assert misses == ["blorptastic"], misses
    assert ipa, "a fallback pronunciation is better than silence"


def test_letter_to_sound_prefers_longer_graphemes():
    assert ph.sound("nation").endswith("ʃən")
    assert "ʧ" in ph.sound("watch")
    assert "aɪ" in ph.sound("night")


def test_trailing_silent_e_is_dropped_but_short_words_keep_it():
    assert not ph.sound("name").endswith("ɛ")
    assert ph.sound("be"), "too short for the rule; must still say something"


def test_folding_keeps_a_word_whole():
    """Stripping marks handles café; ł carries its stroke inside the codepoint
    and would otherwise split the word into two confidently wrong fragments."""
    assert ph.fold("café") == "cafe"
    assert ph.fold("Skłodowska") == "sklodowska"
    assert ph.fold("Straße") == "strasse"
    _ipa, misses = ph.phonemize("Skłodowska")
    assert misses == ["Skłodowska"], "one word, not two fragments"


def test_apostrophes_do_not_split_words():
    _ipa, misses = ph.phonemize("don't")
    assert misses == [], misses


# --- the model alphabet --------------------------------------------------

def test_every_symbol_we_emit_exists_in_the_model_alphabet():
    """Unknown symbols are dropped silently, so the word loses a sound and
    nothing reports it. This is the check that keeps that impossible."""
    vocab = kd.vocabulary()
    assert len(vocab) > 100, len(vocab)
    assert ph.verify(vocab) == [], ph.verify(vocab)


def test_tokenizer_counts_what_it_drops():
    ids, dropped = kd.tokenize("kæt☃")
    assert ids, "the real phonemes still tokenize"
    assert dropped == ["☃"], dropped


def test_affricate_ligatures_are_normalized():
    """The alphabet has ʧ as one symbol and no "tʃ". Left alone the two-char
    form tokenizes happily as t then ʃ, which is a different sound."""
    assert kd.normalize_phonemes("tʃ") == "ʧ"
    assert kd.normalize_phonemes("dʒ") == "ʤ"
    single, _ = kd.tokenize("ʧ")
    split, _ = kd.tokenize("tʃ")
    assert single == split, "both spellings must reach the same token"
    assert len(single) == 1


def test_style_row_is_chosen_by_token_count_and_clamped():
    import numpy as np
    bank = np.arange(60, dtype="float32").reshape(10, 6)
    assert kd.KokoroModel.style_for(bank, 3).tolist() == [12, 13, 14, 15, 16, 17]
    assert kd.KokoroModel.style_for(bank, 99).tolist() == [54, 55, 56, 57, 58, 59]
    assert kd.KokoroModel.style_for(bank, 0).tolist() == [0, 1, 2, 3, 4, 5]


def test_long_input_is_chunked_to_the_model_limit():
    pieces = kd.chunk(list(range(1200)))
    assert [len(p) for p in pieces] == [508, 508, 184]
    assert kd.chunk([]) == []


# --- cross-language agreement -------------------------------------------

def test_both_halves_pronounce_identically():
    """A divergence here is audible and unexplainable: the same reader saying
    a name two ways depending on which half spoke."""
    node = shutil.which("node")
    harness = (REPO_ROOT / "tools" / "conformance_g2p.mjs") if REPO_ROOT else None
    if node is None or harness is None or not harness.is_file():
        print("   (no Node or no harness; skipping)")
        return SKIPPED

    result = subprocess.run([node, str(harness)], cwd=str(REPO_ROOT),
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=300)
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, output
    assert "differing:  0" in output, output



def test_the_app_survives_being_packaged_without_the_vendored_data():
    """The dictionary currently lives inside the extension's directory, so a
    desktop-only package could ship without it. That must degrade to system
    voices rather than crash, and must say which piece is missing: a user told
    "not installed" after downloading 330 MB of model will download it again.
    """
    from earmark.tts import kokoro_direct as direct
    from earmark.tts.registry import Registry

    real_ph, real_kd = ph.vendor_dir, direct.vendor_dir
    ph.vendor_dir = lambda: None
    direct.vendor_dir = lambda: None
    ph._dictionary.cache_clear()
    ph._homographs.cache_clear()
    direct.vocabulary.cache_clear()
    try:
        registry = Registry()
        assert registry.kokoro.blocked_by == "the pronunciation dictionary"
        assert "pronunciation dictionary" in " ".join(registry.describe())

        # Asking for a neural voice still speaks, through a system one.
        engine, _voice = registry.resolve("kokoro", "af_heart")
        assert engine.name == "sapi"
        samples, rate = registry.synthesize("Still speaks.", "kokoro",
                                            "af_heart", 1.0)
        assert len(samples) > 0 and rate > 0
    finally:
        ph.vendor_dir, direct.vendor_dir = real_ph, real_kd
        ph._dictionary.cache_clear()
        ph._homographs.cache_clear()
        direct.vocabulary.cache_clear()


def test_missing_pieces_are_reported_distinctly():
    """Three different absences with three different fixes."""
    from earmark.tts.registry import Registry
    kokoro = Registry().kokoro
    # The model is genuinely not downloaded on this machine.
    assert kokoro.blocked_by == "the voice model download", kokoro.blocked_by



def test_shared_data_is_read_from_the_canonical_copy():
    """Not from the extension's synced duplicate.

    The extension keeps a copy because Chrome can only package files inside an
    extension directory. Reading that copy would mean reading a duplicate that
    can go stale while believing it was the source, and a stale dictionary is
    silent: words simply come out pronounced by an older rule.
    """
    from earmark.tts import kokoro_direct as direct
    from _paths import REPO_ROOT

    assert REPO_ROOT is not None
    shared = REPO_ROOT / "shared"
    for label, resolved in (("dictionary", ph.vendor_dir()),
                            ("phoneme alphabet", direct.vendor_dir())):
        assert resolved is not None, "no " + label + " found at all"
        # Both directories are named the same in either location, so asserting
        # the name passes wherever it was read from. The full path is the only
        # assertion that distinguishes canonical from copy.
        assert resolved.parent == shared, (
            label + " read from " + str(resolved) + ", not the canonical copy")


def test_the_synced_copies_match_the_canonical_ones():
    """A copy that has drifted is worse than no copy.

    A missing copy fails loudly the first time something loads it. A stale one
    is consistent: both halves agree, both harnesses pass, and the agreement is
    on old data, so every check we have would confirm it.

    Discovers what is in shared/ rather than naming directories, so a third one
    is covered the day it appears instead of the day someone remembers.
    """
    import hashlib

    from _paths import REPO_ROOT
    shared = REPO_ROOT / "shared"
    mirror = REPO_ROOT / "extension" / "vendor"

    compared = 0
    for source in sorted(shared.rglob("*")):
        if not source.is_file():
            continue
        duplicate = mirror / source.relative_to(shared)
        if not duplicate.is_file():
            continue          # not everything shared is mirrored into vendor/
        assert (hashlib.sha256(source.read_bytes()).digest()
                == hashlib.sha256(duplicate.read_bytes()).digest()), (
            str(source.relative_to(shared))
            + " has drifted; run: node tools/sync-shared.mjs")
        compared += 1

    # A comparison of nothing passes trivially, which is the failure this
    # whole family of tests exists to prevent.
    assert compared >= 3, "only compared " + str(compared) + " mirrored files"


def test_the_phoneme_alphabet_is_complete():
    """A truncated alphabet is silent. Tokenization drops what it does not
    recognise, so words lose sounds and nothing reports an error."""
    from earmark.tts import kokoro_direct as direct
    assert len(direct.vocabulary()) == 115, len(direct.vocabulary())


def test_the_shipped_dictionary_is_usable_not_merely_present():
    """Present-and-corrupt is a real outcome for a copied binary, and an empty
    dictionary degrades silently to letter-to-sound guesses for every word."""
    import gzip

    from _paths import REPO_ROOT
    packed = (REPO_ROOT / "shared" / "cmudict" / "cmudict.txt.gz").read_bytes()
    text = gzip.decompress(packed).decode("utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) > 120000, len(lines)
    assert len(ph._dictionary()) > 120000, len(ph._dictionary())



# --- Piper, including voices you trained ---------------------------------

def _fake_piper_config():
    return {
        "audio": {"sample_rate": 16000},
        "inference": {"noise_scale": 0.6, "noise_w": 0.9},
        "phoneme_id_map": {"^": [1], "$": [2], "_": [0],
                           "k": [10], "æ": [11], "t": [12]},
    }


def test_piper_brackets_and_interleaves_its_phonemes():
    """Part of the encoding, not decoration: a model trained with a pad token
    between every phoneme produces noise without them."""
    from earmark.tts import piper_direct
    ids, missing = piper_direct.phoneme_ids("kæt", _fake_piper_config())
    assert ids == [1, 10, 0, 11, 0, 12, 0, 2], ids
    assert missing == []


def test_piper_reports_symbols_its_voice_cannot_say():
    from earmark.tts import piper_direct
    _ids, missing = piper_direct.phoneme_ids("kæt☃", _fake_piper_config())
    assert missing == ["☃"], missing


def test_piper_speed_is_a_duration_not_a_rate():
    """Length scale is the reciprocal of speed, which is why fast reading keeps
    its pitch instead of sounding resampled."""
    from earmark.tts import piper_direct
    config = _fake_piper_config()
    assert abs(piper_direct.scales(config, 2.0)[1] - 0.5) < 1e-6
    assert abs(piper_direct.scales(config, 0.5)[1] - 2.0) < 1e-6
    # Noise settings come from the voice, not from us.
    assert abs(piper_direct.scales(config, 1.0)[0] - 0.6) < 1e-6


def test_piper_uses_each_voices_own_sample_rate():
    from earmark.tts import piper_direct
    assert piper_direct.sample_rate(_fake_piper_config()) == 16000
    assert piper_direct.sample_rate({}) == piper_direct.DEFAULT_RATE


def test_a_voice_without_a_phoneme_table_is_refused_clearly():
    from earmark.tts import piper_direct
    try:
        piper_direct.phoneme_ids("kat", {"audio": {"sample_rate": 22050}})
    except piper_direct.PiperUnavailable as exc:
        assert "phoneme_id_map" in str(exc), str(exc)
        return
    raise AssertionError("expected PiperUnavailable")


def test_piper_needs_no_copyleft_package():
    """piper-tts depends on piper-phonemize, which embeds GPL-3.0 eSpeak NG."""
    import importlib.util
    for name in ("piper", "piper_phonemize"):
        assert importlib.util.find_spec(name) is None, name


def test_training_your_own_voice_remains_possible():
    """The drop-in path must survive the licence change: a fine-tuned model is
    an .onnx and an .onnx.json, and both run here without piper-tts."""
    from earmark.tts.piper_engine import PiperEngine
    engine = PiperEngine()
    assert engine.library_present, "onnxruntime and the dictionary are enough"
    assert hasattr(engine, "install_custom")


if __name__ == "__main__":
    passed = failed = skipped = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            if fn() == SKIPPED:
                print("SKIP", name)
                skipped += 1
            else:
                print("PASS", name)
                passed += 1
        except Exception as exc:
            print("FAIL", name, "->", type(exc).__name__, exc)
            failed += 1
    print("\n" + str(passed) + " passed, " + str(failed) + " failed, "
          + str(skipped) + " skipped")
    sys.exit(1 if failed else 0)
