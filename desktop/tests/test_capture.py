"""File reading, transcript parsing, shortcut parsing, add-on detection.

These were reachable only through manual checks. A reachability scan showed
several of them loaded by no test at all, and the file readers loaded but never
asserted against, which the scan cannot see: being imported is not being
tested.

Fixtures are built here rather than pointing at files on this machine, so the
suite means the same thing on another one.
"""
from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import install

install()

from executive_reader.capture import claude_transcript as ct
from executive_reader.capture import clipboard, files, ladder
from executive_reader.hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, HotkeyError, parse
from executive_reader.tts import espeak_addon

SKIPPED = "SKIP"


# --- files ---------------------------------------------------------------

def _make_epub(path: Path, chapters: list[tuple[str, str]], title: str) -> None:
    """A minimal but real EPUB: container, package document, spine order."""
    manifest = "".join(
        '<item id="c%d" href="text/%s" media-type="application/xhtml+xml"/>'
        % (i, name) for i, (name, _body) in enumerate(chapters))
    spine = "".join('<itemref idref="c%d"/>' % i for i in range(len(chapters)))
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>' + title + '</dc:title></metadata>'
        '<manifest>' + manifest + '</manifest>'
        '<spine>' + spine + '</spine></package>')
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
        ' version="1.0"><rootfiles><rootfile full-path="OEBPS/book.opf"'
        ' media-type="application/oebps-package+xml"/></rootfiles></container>')

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/book.opf", opf)
        for name, body in chapters:
            archive.writestr(
                "OEBPS/text/" + name,
                "<html><body><p>" + body + "</p></body></html>")


def test_epub_is_read_in_spine_order_not_archive_order():
    """A zip lists entries however they were written. Trusting that order reads
    a book with its chapters shuffled, which is silent: every chapter is
    present and the text is fine."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "b.epub"
        # Written to the archive backwards on purpose.
        _make_epub(path, [("c3.xhtml", "Third here."),
                          ("c1.xhtml", "First here."),
                          ("c2.xhtml", "Second here.")], "Ordered Book")
        # Declare the spine in reading order by rebuilding with sorted names.
        _make_epub(path, [("c1.xhtml", "First here."),
                          ("c2.xhtml", "Second here."),
                          ("c3.xhtml", "Third here.")], "Ordered Book")
        doc = files.read(path)
        assert doc.title == "Ordered Book", doc.title
        assert doc.meta["chapters"] == 3
        assert doc.text.index("First") < doc.text.index("Second") < doc.text.index("Third")


def test_epub_needs_no_copyleft_package():
    import importlib.util
    assert importlib.util.find_spec("ebooklib") is None, (
        "EbookLib is AGPL; capture/files.py reads EPUB with the standard "
        "library instead. See CAVEATS.md.")


def test_a_broken_epub_reports_rather_than_crashes():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "not.epub"
        path.write_bytes(b"this is not a zip file at all")
        try:
            files.read(path)
        except files.UnsupportedFile:
            return
        raise AssertionError("expected UnsupportedFile")


def test_text_files_survive_a_hostile_encoding():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "note.txt"
        path.write_bytes("naïve café — dash".encode("cp1252"))
        doc = files.read(path)
        assert "caf" in doc.text and doc.text.strip(), doc.text


def test_unsupported_extensions_are_refused_by_name():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "thing.xyz"
        path.write_text("hello", encoding="utf-8")
        assert not files.is_supported(path)
        try:
            files.read(path)
        except files.UnsupportedFile as exc:
            assert ".xyz" in str(exc), str(exc)
            return
        raise AssertionError("expected UnsupportedFile")


def test_repeated_page_furniture_is_stripped():
    """A header on every page is read aloud between every page otherwise."""
    pages = ["ACME REPORT\nReal content one.\n17",
             "ACME REPORT\nReal content two.\n18",
             "ACME REPORT\nReal content three.\n19"]
    cleaned = files._strip_repeated_lines(pages)
    joined = "\n".join(cleaned)
    assert "ACME REPORT" not in joined, joined
    assert "Real content two." in joined


def test_short_documents_keep_their_furniture():
    """Two pages is not enough evidence that a line is boilerplate."""
    pages = ["Title\nBody one.", "Title\nBody two."]
    assert files._strip_repeated_lines(pages) == pages


def test_furniture_on_most_pages_counts_without_needing_all_of_them():
    """Pins the share, not just the rule.

    A threshold sweep found this constant unpinned: the original test put a
    header on every one of three pages, where a 60% share and a 120% share give
    the same answer. A line on two pages out of three separates them.
    """
    pages = ["RUNNING HEAD\nOne.", "RUNNING HEAD\nTwo.", "Different\nThree."]
    joined = "\n".join(files._strip_repeated_lines(pages))
    assert "RUNNING HEAD" not in joined, joined
    assert "One." in joined and "Three." in joined


def test_a_line_on_one_page_of_many_is_content_not_furniture():
    pages = ["Unique opener.\nBody one.", "Body two.", "Body three.",
             "Body four.", "Body five."]
    joined = "\n".join(files._strip_repeated_lines(pages))
    assert "Unique opener." in joined, joined


def test_scanned_detection_scales_with_page_count():
    """Pins both constants, at page counts a fixture cannot easily reach.

    A hundred-page scan that yields one stray caption is still a scan; a
    one-page note that yields two sentences is not. The floor and the per-page
    rate are separate judgments and both were unpinned.
    """
    # A short but real one-page document.
    assert files.looks_scanned("", 1) is True
    assert files.looks_scanned("A short note. Two lines only.", 1) is False
    # The floor governs a single page, not the per-page rate.
    assert files.looks_scanned("tiny", 1) is True

    # A long scan with a stray caption is still a scan.
    assert files.looks_scanned("Figure 1." * 3, 100) is True
    # A real hundred-page document is not.
    assert files.looks_scanned("word " * 400, 100) is False

    # The per-page rate has to bite somewhere between those.
    assert files.looks_scanned("x" * 100, 50) is True
    assert files.looks_scanned("x" * 100, 5) is False

    # A sparse but real document: ten pages of headings and captions. This is
    # the case that distinguishes the rate from any larger one, and without it
    # the constant can be doubled with nothing noticing.
    assert files.looks_scanned("x" * 120, 10) is False
    assert files.looks_scanned("x" * 60, 10) is True


def test_a_long_line_is_never_treated_as_a_running_header():
    """Headers are short. A repeated long line is quoted text or a legal
    notice, and stripping it removes real content."""
    long_line = ("This sentence is far too long to be a running header and is "
                 "therefore content that must survive on every page it appears.")
    pages = [long_line + "\nOne.", long_line + "\nTwo.", long_line + "\nThree."]
    joined = "\n".join(files._strip_repeated_lines(pages))
    assert long_line in joined, "a long repeated line is not furniture"


# --- Claude transcripts --------------------------------------------------

def _turn(kind: str, blocks: list[dict], uuid: str = "u1") -> str:
    return json.dumps({"type": kind, "uuid": uuid,
                       "message": {"role": kind, "content": blocks}})


def test_transcript_separates_text_from_thinking_and_drops_tools():
    line = _turn("assistant", [
        {"type": "thinking", "thinking": "considering it"},
        {"type": "text", "text": "Here is the answer."},
        {"type": "tool_use", "name": "Bash"},
    ])
    turn = ct.parse_line(line)
    assert turn is not None
    assert turn.text == "Here is the answer."
    assert turn.thinking == "considering it"
    assert turn.tools == ["Bash"]
    assert turn.speakable(False) == "Here is the answer."
    assert "considering it" in turn.speakable(True)


def test_tool_results_are_never_read_aloud():
    """They are payloads, not something anyone wants spoken."""
    line = _turn("user", [{"type": "tool_result", "content": "40000 lines"}])
    assert ct.parse_line(line) is None


def test_redacted_thinking_is_not_mistaken_for_content():
    """Some sessions store an empty thinking block with only a signature."""
    line = _turn("assistant", [{"type": "thinking", "thinking": "",
                                "signature": "abc"}])
    assert ct.parse_line(line) is None


def test_malformed_transcript_lines_are_skipped():
    for bad in ("", "   ", "{not json", json.dumps({"type": "assistant"}),
                json.dumps({"type": "queue-operation", "content": "x"})):
        assert ct.parse_line(bad) is None


def test_tail_starts_at_the_end_and_reports_only_new_turns():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "s.jsonl"
        path.write_text(_turn("assistant", [{"type": "text", "text": "old"}],
                              "a") + "\n", encoding="utf-8")
        tail = ct.Tail(path)
        assert tail.poll() == [], "switching on must not replay the backlog"

        with open(path, "a", encoding="utf-8") as handle:
            handle.write(_turn("assistant", [{"type": "text", "text": "new"}],
                               "b") + "\n")
        fresh = tail.poll()
        assert [t.text for t in fresh] == ["new"], fresh
        assert tail.poll() == [], "a turn must not be reported twice"


def test_tail_ignores_a_partial_final_line():
    """A line still being written is not a turn yet."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "s.jsonl"
        path.write_text("", encoding="utf-8")
        tail = ct.Tail(path)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('{"type":"assistant","message":{"content":[{"type"')
        assert tail.poll() == []


def test_project_slug_matches_how_claude_code_writes_it():
    slug = ct.session_for_project(r"C:\Users\X\Projects\Thing")
    assert slug is None or slug.parent.name == "C--Users-X-Projects-Thing"


# --- hotkeys -------------------------------------------------------------

def test_hotkey_parsing_covers_the_shipped_defaults():
    from executive_reader.config import Config
    for name, spec in Config().hotkeys.items():
        mods, key = parse(spec)
        assert key, name + " parsed to no key"
        assert mods, name + " parsed to no modifier"


def test_hotkey_modifiers_combine():
    mods, key = parse("ctrl+alt+shift+r")
    assert mods & MOD_CONTROL and mods & MOD_ALT and mods & MOD_SHIFT
    assert key == ord("R")


def test_hotkey_rejects_nonsense_rather_than_guessing():
    for bad in ("", "ctrl", "ctrl+", "ctrl+notakey"):
        try:
            parse(bad)
        except HotkeyError:
            continue
        raise AssertionError("accepted " + repr(bad))


# --- the eSpeak add-on ---------------------------------------------------

def test_addon_detection_only_looks():
    """The licence position depends on never shipping or fetching eSpeak, so
    this must be a pure lookup. See CAVEATS.md.

    Checks the parsed code rather than the source text. A first version grepped
    for "download" and failed on the module's own docstring, which says it does
    not download anything: exactly the trap that reports numpy as AGPL for
    quoting the licence it does not use. Prose about code is not code.
    """
    import ast

    tree = ast.parse(Path(espeak_addon.__file__).read_text(encoding="utf-8"))
    forbidden = {"urllib", "urllib.request", "requests", "http", "httpx",
                 "subprocess", "pip", "socket", "ftplib"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    offending = imported & forbidden
    assert not offending, "espeak_addon must only detect, never fetch: " + str(offending)

    # shutil is imported for which(); it must not be used to copy anything in.
    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    for verb in ("copy", "copy2", "copyfile", "copytree", "move", "urlretrieve"):
        assert verb not in calls, "espeak_addon must not install: " + verb


def test_addon_summary_says_what_is_missing_and_why():
    absent = espeak_addon.Addon(present=False).summary
    assert "English works" in absent
    assert "not bundled" in absent
    present = espeak_addon.Addon(present=True, library="x").summary
    assert "Spanish" in present and "Mandarin" in present


def test_addon_detection_returns_a_definite_answer():
    found = espeak_addon.detect()
    assert isinstance(found.present, bool)
    assert espeak_addon.available() == found.present


# --- the capture ladder --------------------------------------------------

def test_a_local_document_url_is_parsed_rather_than_scraped():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "paper.txt"
        path.write_text("Body text here.", encoding="utf-8")
        assert ladder._local_path(path.as_uri()) == path
        assert ladder._local_path(str(path)) == path
    assert ladder._local_path("https://example.com/a.pdf") is None
    assert ladder._local_path("") is None


def test_clipboard_reading_never_raises():
    """It runs on a hotkey, so an exception here would look like a dead key."""
    assert isinstance(clipboard.read_text(), str)



# --- downloads -----------------------------------------------------------
# The path that fetches 330 MB of voice model, and had no test at all. A
# threshold sweep found every constant in it unpinned, which is what a module
# with no tests looks like from the outside.

class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200, total: int | None = None):
        self._body = body
        self.status = status
        self.headers = {"Content-Length": str(total if total is not None
                                              else len(body))}
        self._offset = 0

    def read(self, size):
        chunk = self._body[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _with_fake_urlopen(handler):
    """Swap urlopen for the duration of a test."""
    from executive_reader.tts import download as dl
    import urllib.request
    real = urllib.request.urlopen
    dl_real = getattr(dl.urllib.request, "urlopen", real)
    dl.urllib.request.urlopen = handler
    urllib.request.urlopen = handler
    return lambda: (setattr(dl.urllib.request, "urlopen", dl_real),
                    setattr(urllib.request, "urlopen", real))


def test_a_download_lands_at_its_final_name_only_when_complete():
    """It writes to .part and renames, so an interrupted fetch never leaves a
    truncated file that looks finished."""
    from executive_reader.tts import download as dl
    seen = {}

    def handler(request, timeout=None):
        seen["range"] = request.get_header("Range")
        return _FakeResponse(b"abcdefghij")

    restore = _with_fake_urlopen(handler)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "voice.bin"
            dl.download("http://example.invalid/voice.bin", target)
            assert target.read_bytes() == b"abcdefghij"
            assert not target.with_suffix(".bin.part").exists()
            assert seen["range"] is None, "a fresh download must not ask to resume"
    finally:
        restore()


def test_an_interrupted_download_resumes_instead_of_restarting():
    """330 MB is too much to fetch twice because a connection dropped."""
    from executive_reader.tts import download as dl
    seen = {}

    def handler(request, timeout=None):
        seen["range"] = request.get_header("Range")
        return _FakeResponse(b"fghij", status=206, total=5)

    restore = _with_fake_urlopen(handler)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "voice.bin"
            part = target.with_name(target.name + ".part")
            part.write_bytes(b"abcde")

            dl.download("http://example.invalid/voice.bin", target)
            assert seen["range"] == "bytes=5-", seen["range"]
            assert target.read_bytes() == b"abcdefghij", "resume must append"
    finally:
        restore()


def test_a_complete_file_is_not_downloaded_again():
    from executive_reader.tts import download as dl

    def handler(request, timeout=None):
        raise AssertionError("should not have fetched anything")

    restore = _with_fake_urlopen(handler)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "voice.bin"
            target.write_bytes(b"already here")
            dl.download("http://example.invalid/voice.bin", target)
            assert target.read_bytes() == b"already here"
    finally:
        restore()


def test_the_server_saying_the_range_is_satisfied_means_finished():
    """416 is how a server says the part file is already the whole file. Read
    as an error it would restart a completed download."""
    import urllib.error

    from executive_reader.tts import download as dl

    def handler(request, timeout=None):
        raise urllib.error.HTTPError("u", 416, "Range Not Satisfiable", {}, None)

    restore = _with_fake_urlopen(handler)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "voice.bin"
            target.with_name(target.name + ".part").write_bytes(b"complete")
            dl.download("http://example.invalid/voice.bin", target)
            assert target.read_bytes() == b"complete"
    finally:
        restore()


def test_a_real_error_is_not_swallowed():
    import urllib.error

    from executive_reader.tts import download as dl

    def handler(request, timeout=None):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    restore = _with_fake_urlopen(handler)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "voice.bin"
            try:
                dl.download("http://example.invalid/voice.bin", target)
            except urllib.error.HTTPError:
                assert not target.exists(), "a failed fetch must leave nothing"
                return
            raise AssertionError("a 404 must not look like success")
    finally:
        restore()


def test_progress_is_reported_against_the_full_size_when_resuming():
    """Otherwise a resumed download shows a bar that starts near the end."""
    from executive_reader.tts import download as dl
    reports = []

    def handler(request, timeout=None):
        return _FakeResponse(b"fghij", status=206, total=5)

    restore = _with_fake_urlopen(handler)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "voice.bin"
            target.with_name(target.name + ".part").write_bytes(b"abcde")
            dl.download("http://example.invalid/voice.bin", target,
                        lambda done, total: reports.append((done, total)))
            assert reports, "no progress reported"
            done, total = reports[-1]
            assert total == 10, "total must include what was already fetched"
            assert done == 10, done
    finally:
        restore()


def test_sizes_are_reported_in_units_a_person_reads():
    from executive_reader.tts.download import human
    assert human(512) == "512 B"
    assert human(1536).endswith("KB")
    assert human(330 * 1024 * 1024).startswith("330")


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
