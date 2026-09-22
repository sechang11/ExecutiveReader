"""Normalisation, segmentation and pronunciation."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import install

install()

from executive_reader.textproc.normalize import normalize
from executive_reader.textproc.pronounce import Dictionary, Rule
from executive_reader.textproc.segment import segment


def test_abbreviations_do_not_split_sentences():
    out = segment("Dr. Smith met Mr. Jones. They talked.")
    assert out == ["Dr. Smith met Mr. Jones.", "They talked."], out


def test_decimals_do_not_split_sentences():
    out = segment("Pi is 3.14159 exactly. Or close.")
    assert out == ["Pi is 3.14159 exactly.", "Or close."], out


def test_long_sentences_are_capped_for_low_latency():
    long_one = "This clause runs on, " * 40 + "and finally ends."
    out = segment(long_one, max_chars=120)
    assert len(out) > 1
    assert all(len(s) <= 130 for s in out), [len(s) for s in out]


def test_paragraph_breaks_are_respected():
    out = segment("First para\n\nSecond para")
    assert out == ["First para", "Second para"], out


def test_dot_leaders_and_page_numbers_are_removed():
    toc = "Contents\nStart with a feature . . . . . . . . . . 8\nDetail comes later . . . . 12"
    out = normalize(toc)
    assert "....." not in out
    assert " 8" not in out and " 12" not in out
    assert "Start with a feature." in out, out


def test_hard_wrapped_prose_is_rejoined():
    wrapped = "the computed font size of nested\nelements is often not what you\nexpect."
    out = segment(normalize(wrapped))
    assert len(out) == 1, out
    assert "nested elements" in out[0]


def test_hyphenated_line_breaks_are_healed():
    out = normalize("when sizing an ele-\nment carefully")
    assert "element carefully" in out, out


def test_headings_get_a_terminator():
    out = segment(normalize("Avoid em units\nWhen you build a scale, be careful."))
    assert out[0] == "Avoid em units.", out


def test_code_blocks_can_be_skipped():
    src = "Before.\n\n```\nprint(1)\n```\n\nAfter."
    kept = normalize(src, skip_code=False)
    skipped = normalize(src, skip_code=True)
    assert "print" not in kept and "print" not in skipped
    assert "Code block" in skipped and "Code block" not in kept


def test_urls_read_as_domains_by_default():
    out = normalize("See https://docs.example.com/a/b/c for details.")
    assert "link to docs.example.com" in out, out
    assert "/a/b/c" not in out


def test_urls_can_be_skipped_entirely():
    out = normalize("See https://docs.example.com/a for details.", urls="skip")
    assert "docs.example.com" not in out and "link" in out


def test_markdown_syntax_is_stripped_but_text_survives():
    out = normalize("## Heading\n\nSome **bold** and [a link](http://x.com) here.")
    assert "#" not in out and "**" not in out
    assert "bold" in out and "a link" in out


def test_footnote_markers_leave_no_orphan_space():
    out = normalize("As shown by Smith [1]. Next.")
    assert "[1]" not in out
    assert " ." not in out, repr(out)


def test_pronunciation_is_word_bounded():
    d = Dictionary([Rule(pattern="cache", replacement="cash")])
    assert d.apply("clear the cache now") == "clear the cash now"
    # Must not fire inside a longer word.
    assert d.apply("it was cached") == "it was cached"


def test_pronunciation_defaults_cover_developer_words():
    out = Dictionary().apply("Deploy nginx behind SQL.")
    assert "engine ex" in out and "sequel" in out, out


def test_empty_and_whitespace_input_is_safe():
    assert normalize("") == ""
    assert normalize("   \n\n  ") == ""
    assert segment("") == []
    assert segment("   ") == []



def test_shared_rules_are_the_source_of_truth():
    from executive_reader.textproc import shared_rules
    assert shared_rules.shared_dir() is not None, 'shared/ should be found from the repo'
    abbrevs = shared_rules.abbreviations({'zzz'})
    assert len(abbrevs) > 100, len(abbrevs)
    assert {'dr', 'ph.d', 'u.s', 'sept'} <= abbrevs
    assert 'zzz' in abbrevs, 'fallback entries must never be dropped'


def test_shared_pronunciations_load():
    from executive_reader.textproc import shared_rules
    rules = shared_rules.pronunciations([("x", "y")])
    assert len(rules) > 30, len(rules)
    by_match = {r["match"].lower(): r for r in rules}
    assert by_match["nginx"]["say"] == "engine ex"
    # Builtins must behave identically in Python re and JavaScript RegExp, so
    # none of them may be a pattern. The flag stays for user-added entries.
    assert not any(r["regex"] for r in rules), \
        "builtin pronunciations must be literal, not regex"
    assert all("regex" in r for r in rules), "the flag must still be parsed"


def test_say_as_expansion_lives_outside_the_pronunciation_file():
    """The two shared files divide by meaning versus sound; keep them apart."""
    from executive_reader.textproc import shared_rules
    spoken = {r["match"].lower() for r in shared_rules.pronunciations([("x", "y")])}
    assert "i.e." not in spoken and "e.g." not in spoken, \
        "say-as belongs in normalization.json, not pronunciation.json"


def test_shared_loader_falls_back_when_files_missing(monkeypatch=None):
    from executive_reader.textproc import shared_rules
    original = shared_rules.shared_dir
    shared_rules.load.cache_clear()
    shared_rules.shared_dir = lambda: None
    try:
        assert shared_rules.abbreviations({'mr', 'dr'}) == {'mr', 'dr'}
        rules = shared_rules.pronunciations([('SQL', 'sequel')])
        assert rules == [{'match': 'SQL', 'say': 'sequel', 'regex': False}]
        assert shared_rules.url_mode('domain') == 'domain'
    finally:
        shared_rules.shared_dir = original
        shared_rules.load.cache_clear()


def test_extension_copy_matches_shared_source():
    import json

    from _paths import REPO_ROOT

    assert REPO_ROOT is not None, "could not locate the repository root"
    checked = 0
    for name in ("abbreviations", "pronunciation", "normalization"):
        source_file = REPO_ROOT / "shared" / (name + ".json")
        copy_file = REPO_ROOT / "extension" / "src" / "shared" / (name + ".json")
        assert source_file.exists(), "missing shared/" + name + ".json"
        if not copy_file.exists():
            continue
        source = json.loads(source_file.read_text(encoding="utf-8"))
        copied = json.loads(copy_file.read_text(encoding="utf-8"))
        assert source == copied, (
            name + " is out of sync; run: node tools/sync-shared.mjs")
        checked += 1
    # Guard against the whole check quietly becoming a no-op after a move.
    assert checked >= 2, "expected to compare at least two shared files"



def test_isolated_symbols_convert_but_code_survives():
    out = normalize("Ben & Jerry make R&D fun with C++ and a && b and key=value.")
    assert "Ben and Jerry" in out, out
    for survivor in ("R&D", "C++", "&&", "key=value"):
        assert survivor in out, survivor + " should survive: " + out


def test_digit_conditioned_symbols():
    out = normalize("Issue #42 rose 20% to ~5 units at 30 deg.")
    assert "number 42" in out and "20 percent" in out and "about 5" in out, out
    # A hash with no digit after it is a heading marker or a C#, not a number.
    assert "number" not in normalize("Learn C# and #hashtag today.")


def test_currency_moves_from_prefix_to_suffix():
    assert "50 dollars" in normalize("It cost $50 today.")
    assert "1 dollar" in normalize("It cost $1 today."), "singular form"
    assert "20 pounds" in normalize("It cost £20 today.")


def test_email_and_handles_survive_the_at_rule():
    out = normalize("Mail me@example.com or find @someone, but ping me @ noon.")
    assert "me@example.com" in out and "@someone" in out, out
    assert "at noon" in out, out


def test_expansions_come_from_shared_data():
    from executive_reader.textproc import shared_rules
    rules = shared_rules.expansions({"zzz": "fallback"})
    matches = {r["match"].lower() for r in rules}
    assert {"i.e.", "e.g.", "etc."} <= matches, matches
    assert "that is" in normalize("Use it, i.e. carefully.")



def test_expansion_boundary_rule_protects_code():
    """A match adjacent to a letter or digit must not fire."""
    out = normalize("node->next and xw/y and a<=b stay intact.")
    for survivor in ("node->next", "xw/y", "a<=b"):
        assert survivor in out, survivor + " was eaten: " + out


def test_expansion_still_fires_when_delimited():
    assert "A to B" in normalize("A -> B means implication.")
    assert "x less than or equal to y" in normalize("Compare x <= y here.")
    assert "that is" in normalize("Use it, i.e. carefully.")


def test_longest_match_wins_regardless_of_file_order():
    """w/o must beat w/, even if the shared file lists them the other way."""
    out = normalize("Coffee w/o sugar but w/ cream.")
    assert out == "Coffee without sugar but with cream.", out


def test_expansion_rules_are_sorted_longest_first():
    """Order by the literal being matched, not by the compiled pattern, whose
    length shifts with regex escaping."""
    from executive_reader.textproc.normalize import _expansion_rules
    literals = [literal for literal, _pattern, _say in _expansion_rules()]
    lengths = [len(x) for x in literals]
    assert lengths == sorted(lengths, reverse=True), literals
    assert literals.index("w/o") < literals.index("w/"), literals


def test_match_case_flag_is_honoured():
    from executive_reader.textproc import shared_rules
    entries = shared_rules.expansions({"x": "y"})
    assert all("match_case" in e for e in entries), "flag must be parsed"
    # Nothing needs it yet, but the schema must express it.
    assert not any(e["match_case"] for e in entries)


def test_unknown_symbol_condition_is_ignored_not_guessed():
    """An older build must not start expanding at random on a newer file."""
    from executive_reader.textproc.symbols import _matcher
    assert _matcher("$", "digit-after") is not None
    assert _matcher("$", "some-future-condition") is None



def test_output_contract_collapses_spaces_but_keeps_newlines():
    """The shared _output_contract, which lets the cross-language check assert
    equality rather than only equivalence."""
    from executive_reader.textproc.symbols import tidy
    assert tidy("a    b") == "a b"
    assert tidy("  padded  ") == "padded"
    assert tidy("a \t\t b") == "a b"
    assert tidy("one\n\ntwo") == "one\n\ntwo", "newlines must survive"
    assert tidy("line \n next") == "line \n next", "only runs collapse"


def test_symbol_stage_tidies_on_its_own():
    """tidy belongs to the symbol stage, not to the caller, so the stage agrees
    with the JavaScript side when compared in isolation."""
    from executive_reader.textproc.symbols import apply, apply_symbols
    assert apply_symbols("5 ° warm") == "5 degrees warm"
    assert apply("Costs $5 © today") == "Costs 5 dollars copyright today"
    assert "  " not in apply_symbols("a & b & c")



def test_a_user_override_replaces_the_shipped_rule():
    """Not merely precedes it, and not silently dropped.

    Skipping a duplicate keeps the shipped pronunciation and discards the
    user's, so the setting saves and does nothing.
    """
    from executive_reader.textproc import shared_rules

    fake = {"builtin": [{"match": "GIF", "say": "jiff"},
                        {"match": "SQL", "say": "sequel"}],
            "user": [{"match": "GIF", "say": "gif"},
                     {"match": "Kashix", "say": "cash icks"}]}
    real = shared_rules.load
    shared_rules.load = lambda name: fake if name == "pronunciation" else real(name)
    try:
        rules = {r["match"].lower(): r["say"]
                 for r in shared_rules.pronunciations([("x", "y")])}
    finally:
        shared_rules.load = real

    assert rules["gif"] == "gif", "the user's override must win"
    assert rules["sql"] == "sequel", "untouched shipped rules stay"
    assert rules["kashix"] == "cash icks", "new user rules are added"


def test_no_shipped_rule_rewrites_another_rules_output():
    """Rules apply in sequence, so one can silently undo another.

    A rule turning GIF into "gif" followed by a rule matching "gif" produces
    something neither rule intended, and nothing reports it. This holds today;
    the test is here so a future addition cannot break it quietly.
    """
    from executive_reader.textproc.pronounce import Dictionary, default_rules

    rules = default_rules()
    dictionary = Dictionary(rules)
    collisions = [(r.pattern, r.replacement, dictionary.apply(r.replacement))
                  for r in rules
                  if dictionary.apply(r.replacement) != r.replacement]
    assert collisions == [], collisions
    assert len(rules) > 30, "a suspiciously small ruleset would pass trivially"


def test_overrides_respect_word_boundaries_in_both_directions():
    """UI must not fire inside GUI, and env must not fire inside environment."""
    from executive_reader.textproc.pronounce import Dictionary

    d = Dictionary()
    assert "gooey" in d.apply("the GUI is open"), d.apply("the GUI is open")
    assert "U I" in d.apply("the UI is open")
    # The shorter rule must not have eaten the longer word.
    assert "gU Iooey" not in d.apply("the GUI is open")
    assert d.apply("check the environment") == "check the environment"
    assert "environment" in d.apply("check the env")



def test_applying_every_rule_twice_changes_nothing_the_second_time():
    """The user-facing property: applying the dictionary again is safe.

    Weaker than the pairwise check above, not stronger, which is the opposite of
    what it looks like. Rules apply sequentially over the whole text, so a rule
    that rewrites an earlier rule's output usually fires within the same pass
    and the result is already stable. Measured across three arrangements of a
    planted chain, pairwise caught all three and this caught one: the case where
    the feeding rule runs last and has no later rule to clean up after it.

    Kept because idempotence is worth stating directly, not because it adds
    coverage. The pairwise check is the one doing the work.
    """
    from executive_reader.textproc.pronounce import Dictionary

    from executive_reader.textproc.pronounce import default_rules

    # Built from the rule set rather than hand-written, so every rule fires.
    # A hand-picked sentence only exercises the words it happens to contain,
    # and a chain among the others passes unnoticed: the first version of this
    # test missed a planted GIF -> jiff -> whiffle chain for exactly that
    # reason, and only the pairwise check caught it.
    rules = [r for r in default_rules() if not r.is_regex]
    assert len(rules) > 30, len(rules)
    sentence = " ".join(r.pattern for r in rules)

    dictionary = Dictionary()
    once = dictionary.apply(sentence)
    twice = dictionary.apply(once)
    assert once != sentence, "the fixture must actually trigger rules"
    assert twice == once, "a second pass moved, so some rule rewrites another"


def test_every_shared_file_is_load_bearing():
    """Proves the data is consulted, not merely shipped.

    The extension shipped a pronunciation file that nothing read while both
    READMEs claimed it mitigated mispronounced names. The equivalent failure
    here is quieter: a missing shared file falls through to a built-in fallback
    and everything keeps working, slightly worse, with nothing reporting it.

    This blanks each file in turn and requires the behaviour to change. It
    proves the file is used; it cannot prove it is used correctly.
    """
    from executive_reader.textproc import normalize as normalize_module
    from executive_reader.textproc import shared_rules
    from executive_reader.textproc.normalize import normalize
    from executive_reader.textproc.pronounce import Dictionary
    from executive_reader.textproc.segment import _ABBREV

    # Shared with the classification above, so a file cannot be listed as
    # covered here without a check that actually empties it.
    checks = _blank_checks()
    assert {n + ".json" for n in checks} == _BLANKED_HERE, (
        "the classification and the checks have diverged")
    real_load = shared_rules.load
    baseline = {}
    for name, measure in checks.items():
        shared_rules.load.cache_clear()
        baseline[name] = measure()

    for name, measure in checks.items():
        shared_rules.load.cache_clear()
        shared_rules.load = lambda key, _n=name, _r=real_load: (
            {} if key == _n else _r(key))
        try:
            without = measure()
        finally:
            shared_rules.load = real_load
            shared_rules.load.cache_clear()
        assert without < baseline[name], (
            "blanking shared/" + name + ".json changed nothing, so the file "
            "is shipped but not consulted")

    # The segmenter reads its list once at import, so assert the loaded value
    # rather than re-measuring it.
    assert len(_ABBREV) > 100, len(_ABBREV)
    normalize_module.reload_rules()
    assert "engine ex" in Dictionary().apply("nginx")
    assert "50 dollars" in normalize("It cost $50.")



#: Every file under shared/, and how this suite accounts for it.
#:
#: The guard on the guard. The blank-and-measure test below covers three files;
#: without this, a fourth could arrive and sit unexamined forever, which is
#: exactly how the extension ended up shipping a pronunciation file nothing
#: read. A new shared file must be classified here or the suite fails.
def _blank_checks() -> dict:
    """The files test_every_shared_file_is_load_bearing actually empties, and
    what to measure for each.

    The single source of truth for both that test and the classification below.
    Listing the names separately would let the classification claim a file is
    covered when no check exists, which is the classification lying about
    itself rather than about the file. Deriving one from the other makes that
    impossible rather than merely detectable.
    """
    from executive_reader.textproc import shared_rules
    return {
        "abbreviations": lambda: len(shared_rules.abbreviations({"mr"})),
        "pronunciation": lambda: len(shared_rules.pronunciations([("x", "y")])),
        "normalization": lambda: (len(shared_rules.symbols())
                                  + len(shared_rules.currency())
                                  + len(shared_rules.expansions({"x": "y"}))),
    }


_BLANKED_HERE = {name + ".json" for name in _blank_checks()}
_COVERED_ELSEWHERE = {
    "fingerprint.json": "test_store: rules-stamp detection and the unknown path",
    "cmudict/cmudict.txt.gz": "test_phonemes: dictionary size and pronunciation",
    "cmudict/homographs.json": "test_phonemes: noun and verb readings",
    "kokoro/vocab.json": "test_phonemes: alphabet completeness and tokenizing",
}
_NOT_READ_BY_THE_DESKTOP = {
    "pagination.json": "next-page DOM selectors; the desktop has no pages to advance",
    "sites.json": "per-site DOM rules; browser-only",
    "cmudict/LICENSE": "licence text, not data",
    "kokoro/NOTICE.md": "licence text, not data",
}


def test_every_shared_file_is_accounted_for():
    """No shared file may sit unclassified.

    Being shipped and being used look identical from a directory listing, and
    the failure is silent in both directions: unused here, or used and never
    verified.
    """
    from _paths import REPO_ROOT

    shared = REPO_ROOT / "shared"
    on_disk = {str(p.relative_to(shared)).replace("\\", "/")
               for p in shared.rglob("*") if p.is_file()}
    classified = (_BLANKED_HERE | set(_COVERED_ELSEWHERE)
                  | set(_NOT_READ_BY_THE_DESKTOP))

    unclassified = sorted(on_disk - classified)
    assert not unclassified, (
        "new shared files with no account of how they are verified: "
        + ", ".join(unclassified))

    stale = sorted(classified - on_disk)
    assert not stale, (
        "classified but no longer present, so the entry is now fiction: "
        + ", ".join(stale))
    assert len(on_disk) >= 8, "only found " + str(len(on_disk)) + " shared files"


def test_a_wrapped_line_does_not_become_a_false_full_stop():
    """The reason the voice sounded like it was skipping words.

    Rejoining a hard wrap used to need the broken line to end in a lowercase
    letter or a comma. Technical prose ends lines on acronyms, numbers and
    brackets constantly, and recognised screen text is wrapped at every
    single line, so sentence after sentence was cut in half and spoken as two
    with a full stop dropped into the middle of it.

    What decides a wrap is the line that follows. Prose does not begin a
    sentence in lower case.
    """
    from executive_reader.textproc.normalize import normalize
    from executive_reader.textproc.segment import segment
    nl = chr(10)

    def spoken(text):
        return segment(normalize(text), 320)

    # Ends on an acronym.
    assert spoken("The LG" + nl + "is scaled and the others are not.") == [
        "The LG is scaled and the others are not."]
    # Ends on a number.
    assert spoken("I ran 253" + nl + "tests before lunch.") == [
        "I ran 253 tests before lunch."]
    # Ends on a bracket, and the next line is indented.
    assert spoken("He left (quietly)" + nl + "   before anyone noticed.") == [
        "He left (quietly) before anyone noticed."]

    # A whole recognised screenful comes back as whole sentences.
    screen = nl.join([
        "Found it, and it explains both symptoms at once. Your middle",
        "monitor runs at 125 percent, and the app never knew. The LG",
        "is scaled; the Samsung and the Acer are not."])
    assert spoken(screen) == [
        "Found it, and it explains both symptoms at once.",
        "Your middle monitor runs at 125 percent, and the app never knew.",
        "The LG is scaled; the Samsung and the Acer are not."]


def test_a_heading_is_still_not_glued_to_the_paragraph_under_it():
    """The narrow rule existed to protect this, so widening it must not."""
    from executive_reader.textproc.normalize import normalize
    from executive_reader.textproc.segment import segment
    nl = chr(10)

    out = segment(normalize("Chapter Two" + nl + "The morning came quietly."), 320)
    assert out == ["Chapter Two.", "The morning came quietly."], out


def test_a_list_is_spoken_as_a_list_and_not_as_one_long_sentence():
    """Stripping the marker threw away the only thing saying where an item
    ended, so three items ran together into one breathless line."""
    from executive_reader.textproc.normalize import normalize
    from executive_reader.textproc.segment import segment
    nl = chr(10)

    out = segment(normalize(nl.join([
        "Things to do:", "- wash the car", "- feed the cat",
        "- call the bank"])), 320)
    assert out == ["Things to do:", "wash the car.", "feed the cat.",
                   "call the bank."], out

    # An item that wraps is still one item.
    wrapped = segment(normalize(nl.join([
        "- wash the car and", "  then dry it properly", "- feed the cat"])), 320)
    assert wrapped == ["wash the car and then dry it properly.",
                       "feed the cat."], wrapped

    # A numbered list was never broken and must stay that way.
    numbered = segment(normalize(nl.join([
        "1. open the door", "2. walk inside", "3. sit down"])), 320)
    assert len(numbered) == 3, numbered


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
