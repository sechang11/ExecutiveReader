"""Anchors and persistence."""
from __future__ import annotations

import contextlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import install

install()

from executive_reader.store.anchors import Anchor
from executive_reader.store.db import Store
from executive_reader.textproc.pronounce import Rule


@contextlib.contextmanager
def temp_store():
    """A Store in a throwaway directory, always closed.

    Without the guaranteed close, an assertion failure leaves the SQLite
    connection open, Windows refuses to remove the directory, and the cleanup
    error replaces the real assertion message with a path error. A failure you
    cannot read is barely better than no failure at all.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = Store(Path(tmp) / "t.db")
        try:
            yield store
        finally:
            store.close()


SEGMENTS = [
    "Introduction to the topic.",
    "The first point is about clarity.",
    "The second point is about contrast.",
    "The third point is about spacing.",
    "In conclusion, design is iterative.",
]


def test_anchor_finds_exact_position():
    anchor = Anchor.create(SEGMENTS, 2)
    assert anchor.exact == SEGMENTS[2]
    assert anchor.resolve(SEGMENTS) == 2


def test_anchor_survives_inserted_paragraphs():
    anchor = Anchor.create(SEGMENTS, 3)
    shifted = SEGMENTS[:1] + ["A newly added sentence.", "And another one."] + SEGMENTS[1:]
    # The stored index would now be wrong; the quoted text must win.
    assert shifted[anchor.index_hint] != anchor.exact
    assert anchor.resolve(shifted) == 5
    assert shifted[5] == SEGMENTS[3]


def test_anchor_survives_deleted_paragraphs():
    anchor = Anchor.create(SEGMENTS, 4)
    trimmed = [s for i, s in enumerate(SEGMENTS) if i not in (1, 2)]
    assert anchor.resolve(trimmed) == 2
    assert trimmed[2] == SEGMENTS[4]


def test_anchor_disambiguates_repeated_sentences():
    repeated = ["Header.", "Same line.", "Middle bit.", "Same line.", "Tail."]
    anchor = Anchor.create(repeated, 3)
    assert anchor.resolve(repeated) == 3, "context should pick the later copy"


def test_anchor_falls_back_when_text_is_gone():
    anchor = Anchor.create(SEGMENTS, 2)
    unrelated = ["Totally different.", "Nothing alike here.", "Or here."]
    resolved = anchor.resolve(unrelated)
    assert 0 <= resolved < len(unrelated)


def test_history_roundtrip_and_resume():
    with temp_store() as store:
        store.touch("test://doc", "A Document", "file", "snippet here", 5)
        anchor = Anchor.create(SEGMENTS, 3)
        store.save_position("test://doc", 3, anchor, seconds=12.5)

        index, restored, rules_changed = store.position("test://doc")
        assert index == 3
        assert restored is not None and restored.exact == SEGMENTS[3]
        # Saved and read back under the same rules, so nothing has moved.
        assert rules_changed is False

        rows = store.history()
        assert len(rows) == 1 and rows[0].title == "A Document"
        assert abs(rows[0].progress - 0.6) < 1e-9
        assert rows[0].seconds == 12.5


def test_unfinished_filter_and_forget():
    with temp_store() as store:
        store.touch("a://1", "One", "file", "", 10)
        store.touch("a://2", "Two", "file", "", 10)
        store.save_position("a://1", 4)
        store.save_position("a://2", 10, finished=True)

        unfinished = store.history(unfinished_only=True)
        assert [r.uri for r in unfinished] == ["a://1"]

        store.forget("a://1")
        assert [r.uri for r in store.history()] == ["a://2"]


def test_bookmarks():
    with temp_store() as store:
        store.touch("test://doc", "A Document", "file", "", 5)
        bid = store.add_bookmark("test://doc", "A Document", 2,
                                 Anchor.create(SEGMENTS, 2), note="check this")
        marks = store.bookmarks("test://doc")
        assert len(marks) == 1
        assert marks[0].note == "check this"
        assert marks[0].anchor.resolve(SEGMENTS) == 2

        store.delete_bookmark(bid)
        assert store.bookmarks("test://doc") == []


def test_pronunciation_rules_seed_and_update():
    with temp_store() as store:
        seeded = store.rules()
        assert len(seeded) > 10, "defaults should be installed"

        store.upsert_rule(Rule(pattern="Kashix", replacement="cash icks"))
        assert any(r.pattern == "Kashix" for r in store.rules())

        store.upsert_rule(Rule(pattern="Kashix", replacement="kay shix"))
        match = [r for r in store.rules() if r.pattern == "Kashix"]
        assert len(match) == 1 and match[0].replacement == "kay shix"

        store.delete_rule("Kashix")
        assert not any(r.pattern == "Kashix" for r in store.rules())


def test_site_preferences():
    with temp_store() as store:
        assert store.site_pref("example.com") is None
        store.set_site_pref("example.com", "kokoro", "af_heart", 1.75)
        assert store.site_pref("example.com") == ("kokoro", "af_heart", 1.75)



def test_anchor_survives_a_normalization_rule_change():
    """The hazard that actually applies here.

    Anchors store sentence text, not character offsets, so both the stored
    quote and the segments it is matched against are normalized text and
    cannot disagree about position. What can change is the normalization
    itself: shared rule edits rewrite the sentences of an already-bookmarked
    document. Resolution must degrade to the right sentence, not to sentence
    zero. This is not hypothetical, the say-as list gained w/ and -> mid-project.
    """
    before = [
        "Setup instructions follow.",
        "Serve the coffee w/ cream, not w/o it.",
        "Then map node->next across the list.",
        "Finally, restart the service.",
    ]
    after = [
        "Setup instructions follow.",
        "Serve the coffee with cream, not without it.",
        "Then map node->next across the list.",
        "Finally, restart the service.",
    ]
    anchor = Anchor.create(before, 1)
    assert anchor.exact not in after, "the sentence really did change"
    assert anchor.resolve(after) == 1, "should still land on the same sentence"

    # A sentence the rule change left alone must still resolve exactly.
    untouched = Anchor.create(before, 2)
    assert untouched.resolve(after) == 2


def test_anchor_prefers_the_right_sentence_over_a_similar_one():
    segments = [
        "Serve the coffee with cream today.",
        "Serve the coffee with cream tomorrow.",
    ]
    anchor = Anchor.create(["Serve the coffee w/ cream tomorrow."], 0)
    anchor.index_hint = 1
    assert anchor.resolve(segments) == 1, "the hint must break a near tie"



def test_rules_stamp_detects_a_shared_data_change():
    """A stored position records which rules produced it."""
    from executive_reader.textproc import shared_rules
    with temp_store() as store:
        store.touch("test://doc", "Doc", "file", "", 5)
        store.save_position("test://doc", 3, Anchor.create(SEGMENTS, 3))

        _i, _a, changed = store.position("test://doc")
        assert changed is False, "same rules, nothing should look changed"

        original = shared_rules.fingerprint
        shared_rules.fingerprint = lambda: "sha256:something-else"
        try:
            _i, _a, changed = store.position("test://doc")
            assert changed is True, "an edited ruleset must be visible"
        finally:
            shared_rules.fingerprint = original


def test_unstamped_positions_are_not_treated_as_changed():
    """Rows saved before stamping existed must not all claim to be stale."""
    from executive_reader.textproc import shared_rules
    with temp_store() as store:
        store.touch("test://doc", "Doc", "file", "", 5)
        original = shared_rules.fingerprint
        shared_rules.fingerprint = lambda: ""
        try:
            store.save_position("test://doc", 2, Anchor.create(SEGMENTS, 2))
        finally:
            shared_rules.fingerprint = original
        _i, _a, changed = store.position("test://doc")
        assert changed is False, "unknown is not the same as changed"


def test_exact_match_is_kept_when_rules_changed():
    """A rules change must not discard verbatim matching.

    Most sentences survive a rules edit untouched, an exact hit on one is still
    correct, and dropping to fuzzy loses the context tiebreak that separates
    repeated sentences. Confidence is downgraded instead.
    """
    from executive_reader.store.anchors import AMBIGUOUS, EXACT
    segments = ["Intro.", "Same line.", "Middle.", "Same line.", "Tail."]

    # A sentence that appears once: exact, and verified while the stamps agree.
    unique = Anchor.create(segments, 2)
    steady = unique.locate(segments, rules_changed=False)
    assert (steady.index, steady.how, steady.verified) == (2, EXACT, True)

    shifted = unique.locate(segments, rules_changed=True)
    assert shifted.index == 2, "the quote still matches word for word"
    assert shifted.how == EXACT, "a rules edit must not discard verbatim matching"
    assert shifted.verified is False, "but provenance is no longer guaranteed"

    # A sentence that appears twice is ambiguous, never exact and never
    # verified, however the stamps compare. Right sentence, chosen copy.
    duplicate = Anchor.create(segments, 3)
    for changed in (False, True):
        found = duplicate.locate(segments, rules_changed=changed)
        assert found.index == 3, "neighbours still pick the later copy"
        assert found.how == AMBIGUOUS, found.how
        assert found.verified is False


def test_fuzzy_matching_breaks_ties_on_context():
    """Taking the first best score picks the wrong copy when a rewritten
    sentence appears twice."""
    from executive_reader.store.anchors import FUZZY
    old = ["Alpha.", "Serve it w/ cream.", "Beta.", "Serve it w/ cream.", "Gamma."]
    new = ["Alpha.", "Serve it with cream.", "Beta.", "Serve it with cream.",
           "Gamma."]
    found = Anchor.create(old, 3).locate(new, rules_changed=True)
    assert found.index == 3, found
    assert found.how == FUZZY and found.verified is False



def test_resume_message_is_worded_per_recovery_path():
    """An exact quote match is right whatever the stamp says, so it must not
    claim to be approximate. Only the paths that guessed say so."""
    from executive_reader.app import position_note
    from executive_reader.store.anchors import (AMBIGUOUS, BOUNDARY, EXACT,
                                   FUZZY, HINT, Match)

    assert position_note(Match(3, EXACT, verified=True), False) == ""
    assert position_note(Match(3, EXACT, verified=False), True) == "",         "a verbatim match is not approximate just because the rules moved"
    assert position_note(None, True) == ""

    assert "more than once" in position_note(Match(3, AMBIGUOUS), False)
    assert "longer or shorter" in position_note(Match(3, BOUNDARY), False)
    assert "page changed" in position_note(Match(3, FUZZY), False)
    assert "reading rules changed" in position_note(Match(3, FUZZY), True),         "blame the rules when the rules are what moved"
    assert "Could not find" in position_note(Match(0, HINT), False)


def test_every_recovery_path_has_distinct_wording():
    """Four situations, four accounts. The extension words the same four, so a
    silent overlap here would have the two halves describe one situation
    differently to the same person."""
    from executive_reader.app import position_note
    from executive_reader.store.anchors import (AMBIGUOUS, BOUNDARY, FUZZY,
                                   HINT, Match)

    notes = [position_note(Match(1, how), changed)
             for how in (AMBIGUOUS, BOUNDARY, FUZZY, HINT)
             for changed in (False, True)]
    assert all(n.strip() for n in notes), "no guessing path may stay silent"
    assert len(set(notes)) == 5, notes



def test_absent_stamp_is_never_reported_as_changed_rules():
    """Unknown provenance is not changed provenance.

    A position saved before stamping existed has no record of which rules made
    it. Reporting that as "the reading rules changed" states a fact nobody
    established. It must read as a changed page instead, which is the honest
    account of a sentence that no longer matches.
    """
    from executive_reader.app import position_note
    from executive_reader.store.anchors import FUZZY, Match
    from executive_reader.textproc import shared_rules

    with temp_store() as store:
        store.touch("test://doc", "Doc", "file", "", 5)
        original = shared_rules.fingerprint
        shared_rules.fingerprint = lambda: ""      # save with no stamp
        try:
            store.save_position("test://doc", 3, Anchor.create(SEGMENTS, 3))
        finally:
            shared_rules.fingerprint = original

        _index, _anchor, rules_changed = store.position("test://doc")
        assert rules_changed is False, "absent must not read as changed"

        note = position_note(Match(3, FUZZY), rules_changed)
        assert "reading rules changed" not in note, note
        assert "page changed" in note, note




def test_five_situations_classify_per_the_contract():
    """docs/anchor-vocabulary.md, tried most certain first."""
    from executive_reader.store.anchors import (AMBIGUOUS, BOUNDARY, EXACT, FUZZY,
                                       HINT, Anchor)
    cases = [
        (EXACT, ["A.", "The cat sat down and then it slept.", "B."],
                ["A.", "The cat sat down and then it slept.", "B."], 1),
        (AMBIGUOUS, ["Same line.", "Mid.", "Same line."],
                    ["Same line.", "Mid.", "Same line."], 2),
        (BOUNDARY, ["The cat sat down and then it slept."],
                   ["The cat sat down.", "And then it slept."], 0),
        (BOUNDARY, ["The cat sat down."],
                   ["The cat sat down. And then it slept."], 0),
        (FUZZY, ["Serve the coffee with cream today."],
                ["Serve the coffee with milk today."], 0),
        (HINT, ["The cat sat down."],
               ["Totally unrelated words here.", "Nothing alike."], 0),
    ]
    for expected, old, new, index in cases:
        found = Anchor.create(old, index).locate(new)
        assert found.how == expected, (expected, found.how, old, new)


def test_boundary_needs_whole_words_not_raw_substrings():
    """"the cat sat down" inside "the cat sat downstream" is a different
    sentence, not a moved boundary. Without whole-word padding it reports a
    confident boundary match."""
    from executive_reader.store.anchors import BOUNDARY, Anchor
    found = Anchor.create(["The cat sat down"], 0).locate(
        ["The cat sat downstream today and left"])
    assert found.how != BOUNDARY, "a lengthened word is not a moved boundary"


def test_boundary_requires_a_substantial_quote():
    """A short sentence must not match half the document. Counted in words,
    because any character floor high enough to reject "Yes." also rejects
    "The cat sat down.", which is a real half of a split sentence."""
    from executive_reader.store.anchors import BOUNDARY, Anchor
    trivial = Anchor.create(["Yes."], 0).locate(
        ["Yes it did happen that way.", "No."])
    assert trivial.how != BOUNDARY, "too short to anchor anything"

    real_half = Anchor.create(["The cat sat down and then it slept."], 0).locate(
        ["The cat sat down.", "And then it slept."])
    assert real_half.how == BOUNDARY, "a four-word half must still count"


def test_split_detection_survives_punctuation_at_the_seam():
    """Splitting adds a period the saved quote never had, so comparing raw
    text finds no splits at all."""
    from executive_reader.store.anchors import BOUNDARY, Anchor
    found = Anchor.create(["the cat sat down and then it slept"], 0).locate(
        ["The cat sat down.", "And then it slept."])
    assert found.how == BOUNDARY, found.how



def test_identical_surroundings_fall_through_to_the_recorded_index():
    """Contract clause: when repeated copies sit in identical surroundings the
    neighbours cannot decide either, so the tiebreak falls to the recorded
    index and nothing was actually chosen."""
    from executive_reader.store.anchors import AMBIGUOUS, Anchor
    table = ["Row.", "Total: 5", "Row.", "Total: 5", "Row.", "Total: 5", "Row."]
    anchor = Anchor.create(table, 3)
    found = anchor.locate(table)
    assert found.how == AMBIGUOUS, found.how
    assert found.index == anchor.index_hint, "must fall back, not guess"


def test_ambiguous_message_never_claims_context_resolved_it():
    """Same clause, user-facing half. The label and the position are honest;
    claiming the surroundings picked the copy would not be."""
    from executive_reader.app import position_note
    from executive_reader.store.anchors import AMBIGUOUS, Match
    note = position_note(Match(3, AMBIGUOUS), False).lower()
    assert "more than once" in note
    for claim in ("context", "neighbour", "surrounding", "chose", "chosen"):
        assert claim not in note, "overstates what happened: " + claim



# Twenty-five distinct words, so overlap scores land 0.04 apart and two
# candidates can be placed either side of the tie band deliberately.
_WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo "
          "lima mike november oscar papa quebec romeo sierra tango uniform "
          "victor whiskey xray yankee").split()


def test_the_tie_band_is_narrow_enough_to_trust_a_clear_winner():
    """Pins the band, not just the tie-breaking rule.

    A sweep found this constant unpinned: every existing case had its two
    candidates scoring identically, so any band from 0.05 upwards behaved the
    same and the number could be changed freely. Separating them needs two
    candidates whose scores differ by more than the band but less than double
    it, which with 25-word sentences means a gap of 0.08.

    The one that scores clearly higher wins outright. Widening the band to 0.1
    would pull the weaker candidate in and let its neighbours overrule the
    score, which resolves to a different sentence.
    """
    from executive_reader.store.anchors import FUZZY, Anchor

    target = " ".join(_WORDS) + "."
    higher = " ".join(_WORDS[:22]) + " zulu zulu zulu."          # 0.88
    lower = " ".join(_WORDS[:20]) + " zulu zulu zulu zulu zulu."  # 0.80
    opening, first, second, closing = ("Opening filler.",
                                       "Distinct preamble marker.",
                                       "Second preamble marker.",
                                       "Closing trailer marker.")

    # Saved where its neighbours match the position the *weaker* candidate now
    # occupies, so score and context disagree on purpose.
    anchor = Anchor.create([opening, first, second, target, closing], 3)
    found = anchor.locate([opening, higher, second, lower, closing])

    assert found.how == FUZZY, found.how
    assert found.index == 1, (
        "the clear winner on score must not be overruled by context; "
        "resolved to " + str(found.index))


def test_the_tie_band_is_wide_enough_to_consult_context_when_scores_are_close():
    """The lower bound, which looked unpinnable and is not.

    Two identical candidates tie at every width including zero, so a fixture of
    equal sentences pins nothing. Near-but-not-equal does: one word apart is a
    gap of 0.04, narrower than the band, so both stay in contention and their
    neighbours decide. Shrinking the band to 0.02 or 0.0 lets the higher score
    win alone and resolves somewhere else.
    """
    from executive_reader.store.anchors import FUZZY, Anchor

    target = " ".join(_WORDS) + "."
    higher = " ".join(_WORDS[:22]) + " zulu zulu zulu."        # 0.88
    close = " ".join(_WORDS[:21]) + " zulu zulu zulu zulu."    # 0.84
    opening, first, second, closing = ("Opening filler.",
                                       "Distinct preamble marker.",
                                       "Second preamble marker.",
                                       "Closing trailer marker.")

    anchor = Anchor.create([opening, first, second, target, closing], 3)
    found = anchor.locate([opening, higher, second, close, closing])

    # Both candidates differ from the target by substitution, never by a trim.
    # A candidate contained in the target resolves as boundary and never
    # reaches the band at all, so the constant would be routed past entirely.
    assert found.how == FUZZY, found.how
    assert found.index == 3, (
        "a 0.04 gap is inside the band, so the neighbours decide; "
        "resolved to " + str(found.index))


def test_equal_candidates_tie_at_any_band_width():
    """Recorded so nobody mistakes this for coverage of the band.

    Identical sentences score identically and are therefore in contention at
    every width, including zero. This asserts the tie-break works; it says
    nothing about how wide the band is.
    """
    from executive_reader.store.anchors import Anchor

    repeated = ["Alpha.", "Serve it w/ cream.", "Beta.", "Serve it w/ cream.",
                "Gamma."]
    rewritten = ["Alpha.", "Serve it with cream.", "Beta.",
                 "Serve it with cream.", "Gamma."]
    found = Anchor.create(repeated, 3).locate(rewritten)
    assert found.index == 3, found.index


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
