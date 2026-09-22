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
import time
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



def _docx_fixture(path):
    """A document whose tables sit between paragraphs and use both merges."""
    import docx

    doc = docx.Document()
    doc.add_paragraph("First paragraph, before the table.")

    table = doc.add_table(rows=4, cols=3)
    header = table.rows[0].cells
    header[0].text, header[1].text, header[2].text = "Region", "Q1", "Q2"
    body = table.rows[1].cells
    # Two neighbouring cells holding the same value on purpose. A merge check
    # that compares text instead of identity deletes one of these.
    body[0].text, body[1].text, body[2].text = "North", "Yes", "Yes"

    # Vertical merge: one label covering the next two rows.
    down = table.rows[2].cells[0].merge(table.rows[3].cells[0])
    down.text = "South"
    table.rows[2].cells[1].text = "No"
    table.rows[2].cells[2].text = "No"
    table.rows[3].cells[1].text = "Maybe"
    table.rows[3].cells[2].text = "Maybe"

    doc.add_paragraph("Second paragraph, after the table.")
    doc.save(str(path))


def test_docx_tables_are_read_where_they_appear():
    """`document.paragraphs` then `document.tables` moves every table to the end.

    The listener gets a report's page-two table after page fifty, with nothing
    in the audio to say it moved.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "report.docx"
        _docx_fixture(path)
        text = files.read_docx(path).text
    before = text.index("before the table")
    table = text.index("Region")
    after = text.index("after the table")
    assert before < table < after, text


def test_a_merged_docx_cell_is_spoken_once():
    """`row.cells` returns a merged cell once per column or row it covers.

    A three-column totals row came out as the same phrase three times.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "report.docx"
        _docx_fixture(path)
        text = files.read_docx(path).text
    assert text.count("South") == 1, "vertical merge repeated: " + text


def test_a_docx_table_keeps_values_that_genuinely_repeat():
    """Identity separates a merge from a row that repeats a value.

    Two guards regressed this while it was being written, and both deleted
    content rather than raising. Comparing cell *text* collapses "Yes, Yes"
    into one. Comparing id() of an lxml proxy nobody holds a reference to
    collides with an unrelated cell, because the proxy is freed and CPython
    reuses the address; that one dropped "North" from the row entirely.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "report.docx"
        _docx_fixture(path)
        text = files.read_docx(path).text
    assert "North, Yes, Yes" in text, text
    assert "Maybe, Maybe" in text, text


def _blank_pdf(pages: int = 3) -> bytes:
    """A structurally valid PDF whose pages carry no text operators.

    That is what a scan looks like to a parser: real pages, nothing to read.
    Built here rather than checked in, so the fixture cannot rot into a binary
    nobody can regenerate or explain.
    """
    objs = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(str(i + 3) + " 0 R" for i in range(pages))
    objs.append("<< /Type /Pages /Kids [" + kids + "] /Count " + str(pages) + " >>")
    for _ in range(pages):
        objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>")

    nl = "\n"
    out = bytearray(("%PDF-1.4" + nl).encode("ascii"))
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += (str(i) + " 0 obj" + nl + body + nl + "endobj" + nl).encode("ascii")
    start = len(out)
    out += ("xref" + nl + "0 " + str(len(objs) + 1) + nl
            + "0000000000 65535 f " + nl).encode("ascii")
    for off in offsets:
        out += (str(off).rjust(10, "0") + " 00000 n " + nl).encode("ascii")
    out += ("trailer" + nl + "<< /Size " + str(len(objs) + 1) + " /Root 1 0 R >>"
            + nl + "startxref" + nl + str(start) + nl + "%%EOF" + nl).encode("ascii")
    return bytes(out)


def test_a_scanned_pdf_says_so_instead_of_falling_through_in_silence():
    """The advice used to be written onto a document that was then discarded.

    `from_file` set `doc.meta["hint"]` and then returned None for having no
    text, and nothing anywhere read that key. The one sentence that tells a
    user what to do about a scanned PDF could not be delivered by this path.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scan.pdf"
        path.write_bytes(_blank_pdf(3))
        notes = []
        assert ladder.from_file(path, notes) is None
    assert any("scanned" in n for n in notes), notes
    assert any("OCR" in n for n in notes), notes


def test_a_file_that_cannot_be_parsed_is_reported():
    """Silence here is indistinguishable from a window with no text.

    The ladder then walks past the file to the clipboard and reads whatever the
    user copied an hour ago, with nothing saying the file was even tried.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "broken.pdf"
        path.write_bytes(b"not a pdf at all, no header, no objects")
        notes = []
        assert ladder.from_file(path, notes) is None
    assert notes, "a parse failure produced no explanation"
    assert "broken.pdf" in " ".join(notes), notes


def test_the_ladder_reaches_ocr_for_a_scanned_pdf_and_says_why():
    """End to end: the file rung must decline so the OCR rung can run.

    A scan often carries a few stray characters from a watermark or a form
    field. Reading those instead of the page is worse than reading nothing,
    because it looks like success.
    """
    from executive_reader.capture import ocr as ocr_mod
    from executive_reader.capture import uia as uia_mod
    from executive_reader.document import Document

    saved = (uia_mod.capture_selection, uia_mod.window_info, uia_mod.capture,
             clipboard.capture, ocr_mod.available, ocr_mod.capture)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scan.pdf"
        path.write_bytes(_blank_pdf(2))
        recognised = Document(text="Recognised from pixels.", title="scan",
                              uri="test:scan", source="ocr")
        try:
            uia_mod.capture_selection = lambda *a, **k: None
            uia_mod.window_info = lambda *a, **k: uia_mod.WindowInfo(
                title=str(path), url=str(path))
            uia_mod.capture = lambda *a, **k: None
            clipboard.capture = lambda *a, **k: None
            ocr_mod.available = lambda *a, **k: True
            ocr_mod.capture = lambda *a, **k: recognised
            result = ladder.smart()
        finally:
            (uia_mod.capture_selection, uia_mod.window_info, uia_mod.capture,
             clipboard.capture, ocr_mod.available, ocr_mod.capture) = saved

    assert result.rung == "ocr", result.rung
    assert any("scanned" in n for n in result.notes), result.notes



def test_a_download_refuses_before_writing_when_the_disk_is_too_small():
    """The Kokoro voices are about 330 MB.

    Running out of room part-way leaves a .part file and an error thrown from
    inside the write loop that says nothing about disk space. `free_space` was
    written for exactly this and nothing called it, so the case it existed for
    could not happen.
    """
    from executive_reader.tts import download as dl

    def handler(request, timeout=None):
        return _FakeResponse(b"0123456789")

    restore = _with_fake_urlopen(handler)
    real_free = dl.free_space
    try:
        dl.free_space = lambda path: 4          # room for four bytes
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "voice.bin"
            try:
                dl.download("http://example.invalid/voice.bin", target)
            except OSError as exc:
                message = str(exc)
            else:
                raise AssertionError("a download onto a full disk was allowed")
            assert "disk space" in message, message
            assert not target.exists(), "it wrote the file anyway"
            assert not target.with_suffix(".bin.part").exists(), (
                "it left a partial file behind")
    finally:
        dl.free_space = real_free
        restore()


def test_an_unmeasurable_disk_does_not_block_a_download():
    """free_space returns 0 when it cannot tell, and so does an absent
    Content-Length. Neither is a reason to refuse to download."""
    from executive_reader.tts import download as dl

    def handler(request, timeout=None):
        return _FakeResponse(b"0123456789")

    restore = _with_fake_urlopen(handler)
    real_free = dl.free_space
    try:
        dl.free_space = lambda path: 0
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "voice.bin"
            dl.download("http://example.invalid/voice.bin", target)
            assert target.read_bytes() == b"0123456789"
    finally:
        dl.free_space = real_free
        restore()



def test_the_chrome_hint_never_raises_out_of_the_error_path():
    """It runs while the app is already explaining why nothing could be read.

    An exception here would replace a useful message about the real failure
    with a traceback about the advice, which is the worst possible trade.
    """
    from executive_reader.capture import uia as uia_mod

    saved = uia_mod.chrome_accessibility_state
    try:
        uia_mod.chrome_accessibility_state = lambda: "off"
        hint = ladder.chrome_hint()
        assert "force-renderer-accessibility" in hint, hint

        uia_mod.chrome_accessibility_state = lambda: "on"
        assert ladder.chrome_hint() == "", "advice offered when nothing is wrong"

        def explode():
            raise RuntimeError("COM went away")

        uia_mod.chrome_accessibility_state = explode
        assert ladder.chrome_hint() == "", "the advice raised instead of staying quiet"
    finally:
        uia_mod.chrome_accessibility_state = saved


def test_the_claude_folder_follows_the_setting_when_one_is_given():
    from executive_reader.config import Config

    cfg = Config()
    cfg.claude_projects_dir = ""
    assert cfg.claude_dir().name == "projects", cfg.claude_dir()
    assert cfg.claude_dir().parent.name == ".claude", cfg.claude_dir()

    with tempfile.TemporaryDirectory() as tmp:
        cfg.claude_projects_dir = tmp
        assert cfg.claude_dir() == Path(tmp), cfg.claude_dir()



def _text_pdf(pages: list) -> bytes:
    """A PDF that really contains text, one list of lines per page.

    Base-14 Helvetica needs no embedded font, so this is a few hundred bytes of
    ASCII built here rather than a binary checked in that nobody can regenerate
    or explain. Until this existed, no test read a PDF with any text in it: the
    reader was loaded by every run and asserted against by nothing, which is
    what a module-level coverage scan cannot see.
    """
    nl = chr(10)
    backslash = chr(92)

    def escape(line: str) -> str:
        out = line.replace(backslash, backslash * 2)
        return out.replace("(", backslash + "(").replace(")", backslash + ")")

    first_page_obj = 4
    kids = " ".join(str(first_page_obj + 2 * i) + " 0 R" for i in range(len(pages)))
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [" + kids + "] /Count " + str(len(pages)) + " >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, lines in enumerate(pages):
        objects.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            " /Resources << /Font << /F1 3 0 R >> >>"
            " /Contents " + str(first_page_obj + 2 * i + 1) + " 0 R >>")
        body = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
        for line in lines:
            body.append("(" + escape(line) + ") Tj")
            body.append("T*")
        body.append("ET")
        stream = nl.join(body)
        objects.append("<< /Length " + str(len(stream)) + " >>" + nl
                       + "stream" + nl + stream + nl + "endstream")

    out = bytearray(("%PDF-1.4" + nl).encode("latin-1"))
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += (str(number) + " 0 obj" + nl + body + nl + "endobj" + nl).encode("latin-1")
    start = len(out)
    out += ("xref" + nl + "0 " + str(len(objects) + 1) + nl
            + "0000000000 65535 f " + nl).encode("latin-1")
    for off in offsets:
        out += (str(off).rjust(10, "0") + " 00000 n " + nl).encode("latin-1")
    out += ("trailer" + nl + "<< /Size " + str(len(objects) + 1)
            + " /Root 1 0 R >>" + nl + "startxref" + nl + str(start) + nl
            + "%%EOF" + nl).encode("latin-1")
    return bytes(out)


def _read_pdf_text(pages: list) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "book.pdf"
        path.write_bytes(_text_pdf(pages))
        return files.read(path).text


def test_a_pdf_yields_every_page_in_reading_order():
    """The claim the whole design rests on: all pages at once, in order, with
    no scrolling and no recognition pass."""
    text = _read_pdf_text([
        ["First page body."],
        ["Second page body."],
        ["Third page body."],
    ])
    for wanted in ("First page body.", "Second page body.", "Third page body."):
        assert wanted in text, wanted + " missing from " + repr(text)
    assert text.index("First") < text.index("Second") < text.index("Third"), text


def test_a_pdf_with_text_is_not_mistaken_for_a_scan():
    # Distinct pages on purpose. Three identical ones are correctly treated as
    # boilerplate and stripped to nothing, which then looks exactly like a scan.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "book.pdf"
        path.write_bytes(_text_pdf([
            ["Plenty of real words on the opening page."],
            ["A different set of words on the second."],
            ["And a third page that shares none of them."],
        ]))
        doc = files.read(path)
    assert not doc.meta.get("needs_ocr"), doc.meta
    assert doc.meta.get("pages") == 3, doc.meta


def test_page_numbers_go_whether_or_not_the_pdf_has_a_running_footer():
    """These were two jobs sharing one exit.

    Boilerplate is found statistically and needs several pages. A page number
    is found by its shape and needs nothing. Returning early when no
    boilerplate was detected skipped the page numbers too, so the same document
    kept or dropped them depending only on whether it happened to have a
    running footer, and a numbered PDF without a header read "Page 1",
    "Page 2" aloud between every page.
    """
    numbers = ("Page 1", "Page 2", "Page 3")

    bare = _read_pdf_text([["Body of page one.", "Page 1"],
                           ["Body of page two.", "Page 2"],
                           ["Body of page three.", "Page 3"]])
    for number in numbers:
        assert number not in bare, number + " survived in " + repr(bare)

    footed = _read_pdf_text([["Body of page one.", "A Running Footer", "Page 1"],
                             ["Body of page two.", "A Running Footer", "Page 2"],
                             ["Body of page three.", "A Running Footer", "Page 3"]])
    for number in numbers:
        assert number not in footed, number + " survived in " + repr(footed)
    assert "A Running Footer" not in footed, footed
    assert "Body of page two." in footed, footed


def test_hard_wrapped_pdf_prose_is_rejoined_into_one_sentence():
    """An exporter breaks a sentence across lines and the reader must not.

    On the 252-page test PDF this step alone removed 740 false sentence breaks,
    and until now nothing checked it against an actual PDF.
    """
    from executive_reader.config import Config
    from executive_reader.textproc.normalize import normalize
    from executive_reader.textproc.segment import segment

    text = _read_pdf_text([
        ["The exporter wrapped this sentence across",
         "three separate lines even though it is",
         "plainly one sentence."],
    ])
    cfg = Config()
    segments = segment(normalize(text, skip_code=cfg.skip_code_blocks,
                                 urls=cfg.read_urls), cfg.max_segment_chars)
    joined = [s for s in segments if "plainly one sentence" in s]
    assert len(joined) == 1, segments
    assert joined[0].startswith("The exporter wrapped"), joined[0]



def _watcher_over(pages, **kwargs):
    """A watcher whose window returns each page in turn, then repeats the last."""
    from executive_reader.capture import watch
    from executive_reader.document import Document

    state = {"i": 0}

    def fake_capture(*_a, **_k):
        i = min(state["i"], len(pages) - 1)
        state["i"] += 1
        return Document(text=pages[i], title="Fake Window", uri="", source="uia")

    spoken = []
    w = watch.WindowWatcher(on_text=lambda text, title: spoken.append(text),
                            interval=0.05, **kwargs)
    return w, spoken, fake_capture, state


def test_a_watcher_never_reads_what_was_already_on_screen():
    """Switching it on must not read the window back at you.

    This is also what keeps sidebars, toolbars and navigation out of it. They
    are present when watching starts, so they are never new, so they are never
    read — without any list of things to ignore.
    """
    from executive_reader.capture import uia as uia_mod
    from executive_reader.capture import watch

    pages = ["Navigation and a sidebar full of things that were already here.",
             "Navigation and a sidebar full of things that were already here."]
    w, spoken, fake, _state = _watcher_over(pages, mode=watch.FOLLOW)
    saved = uia_mod.capture
    try:
        uia_mod.capture = fake
        w.start()
        time.sleep(0.4)
    finally:
        w.stop()
        uia_mod.capture = saved
    assert spoken == [], spoken


def test_a_watcher_reads_only_the_text_that_appeared_after_it_started():
    from executive_reader.capture import uia as uia_mod
    from executive_reader.capture import watch

    before = "A sidebar item that is long enough to count as a line of prose."
    after = before + "\n" + "Here is a brand new sentence that arrived afterwards."
    w, spoken, fake, _state = _watcher_over([before, after], mode=watch.FOLLOW)
    saved = uia_mod.capture
    try:
        uia_mod.capture = fake
        w.start()
        deadline = time.time() + 6
        while time.time() < deadline and not spoken:
            time.sleep(0.05)
    finally:
        w.stop()
        uia_mod.capture = saved

    assert spoken, "nothing was read"
    body = "\n".join(spoken)
    assert "brand new sentence" in body, body
    assert "sidebar item" not in body, "it read what was already there"


def test_a_watcher_ignores_labels_and_counters():
    """Short lines are buttons, badges and clocks, not sentences.

    Without this the watcher announces every changing timestamp and unread
    count in the window, which is worse than silence.
    """
    from executive_reader.capture import uia as uia_mod
    from executive_reader.capture import watch

    before = "Something already present and long enough to be a real line."
    after = before + "\n3\nNew\n12:04\n" + "A genuine sentence that should be spoken aloud."
    w, spoken, fake, _state = _watcher_over([before, after], mode=watch.FOLLOW)
    saved = uia_mod.capture
    try:
        uia_mod.capture = fake
        w.start()
        deadline = time.time() + 6
        while time.time() < deadline and not spoken:
            time.sleep(0.05)
    finally:
        w.stop()
        uia_mod.capture = saved

    body = "\n".join(spoken)
    assert "genuine sentence" in body, body
    for noise in ("12:04", "New", "3"):
        assert noise not in body.replace("genuine sentence", ""), body


def test_locked_mode_reads_its_own_window_not_the_front_one():
    """The whole point of locking: the front window must be irrelevant."""
    from executive_reader.capture import uia as uia_mod
    from executive_reader.capture import watch
    from executive_reader.document import Document

    asked = []

    def fake_named(match, *_a, **_k):
        asked.append(match)
        return Document(text="Locked window text that is long enough to read.",
                        title=match, uri="", source="uia")

    def fake_front(*_a, **_k):
        raise AssertionError("locked mode must not read the foreground window")

    saved = (uia_mod.capture_window, uia_mod.capture)
    w = watch.WindowWatcher(on_text=lambda *a: None, mode=watch.LOCKED,
                            target="Claude", interval=0.05)
    try:
        uia_mod.capture_window, uia_mod.capture = fake_named, fake_front
        w.start()
        time.sleep(0.3)
    finally:
        w.stop()
        uia_mod.capture_window, uia_mod.capture = saved
    assert asked and set(asked) == {"Claude"}, asked


def test_stopping_a_watcher_waits_for_it():
    """Returning while it is still mid-read lets it touch a closed store."""
    from executive_reader.capture import uia as uia_mod
    from executive_reader.capture import watch
    from executive_reader.document import Document

    def slow(*_a, **_k):
        time.sleep(0.3)
        return Document(text="Some text long enough to be treated as a line.",
                        title="W", uri="", source="uia")

    saved = uia_mod.capture
    w = watch.WindowWatcher(on_text=lambda *a: None, mode=watch.FOLLOW,
                            interval=0.05)
    try:
        uia_mod.capture = slow
        w.start()
        time.sleep(0.1)
        w.stop()
        assert not w.running, "stop returned with the watcher still alive"
    finally:
        uia_mod.capture = saved



def _capture(text, words=None):
    from executive_reader.capture import region as region_mod
    made = []
    left = 0
    for raw in (words if words is not None else text.split()):
        made.append(region_mod.Word(text=raw, left=left, top=10,
                                    width=8 * len(raw), height=14))
        left += 8 * len(raw) + 6
    return region_mod.Capture(text=text, words=made)


def test_highlight_finds_the_words_of_a_sentence_after_normalisation():
    """The sentence has been rewritten by the time it is spoken.

    Punctuation is changed, abbreviations are expanded and whitespace is
    collapsed before anything reaches the voice, so matching the words as
    written would find nothing. Matching on letters alone is what makes the
    highlight land on the right words.
    """
    from executive_reader.capture import region as region_mod

    shot = _capture('The cat "sat" down, quietly.')
    hits = region_mod.words_for(shot, "The cat sat down quietly")
    assert [w.text for w in hits] == ['The', 'cat', '"sat"', 'down,', 'quietly.'], \
        [w.text for w in hits]
    assert hits[0].left < hits[-1].left, "boxes came back out of order"


def test_highlight_stays_dark_when_the_sentence_is_not_on_screen():
    """Better no highlight than one drawn over unrelated words."""
    from executive_reader.capture import region as region_mod

    shot = _capture("Something else entirely on the screen right now")
    assert region_mod.words_for(shot, "A sentence that is not here at all") == []
    assert region_mod.words_for(shot, "") == []


def test_a_region_watcher_reports_only_genuinely_new_text():
    """Recognition is not deterministic at the edges.

    Comparing raw text reports a change every couple of seconds on a screen
    that has not moved, so the watcher would interrupt itself forever.
    Comparing the letters alone does not.
    """
    from executive_reader.capture import region as region_mod

    pages = ["Hello there, this is the text on screen.",
             "Hello there , this is  the text on screen.",   # same, respaced
             "Hello there, this is the text on screen!",      # same, repunctuated
             "A completely different sentence has appeared now."]
    state = {"i": 0}

    def fake_read(_rect):
        i = min(state["i"], len(pages) - 1)
        state["i"] += 1
        return _capture(pages[i])

    seen = []
    saved = region_mod.read_region
    watcher = region_mod.RegionWatcher((0, 0, 100, 100),
                                       on_capture=lambda c: seen.append(c.text),
                                       interval=0.05, min_chars=10)
    try:
        region_mod.read_region = fake_read
        watcher.start()
        deadline = time.time() + 6
        while time.time() < deadline and len(seen) < 2:
            time.sleep(0.05)
    finally:
        watcher.stop()
        region_mod.read_region = saved

    assert len(seen) == 2, seen
    assert "Hello there" in seen[0]
    assert "completely different" in seen[1]


def test_a_region_watcher_ignores_a_stray_word():
    """A misread edge or a single word is not worth interrupting for."""
    from executive_reader.capture import region as region_mod

    pages = ["A long enough first line of text to be read aloud.", "ok"]
    state = {"i": 0}

    def fake_read(_rect):
        i = min(state["i"], len(pages) - 1)
        state["i"] += 1
        return _capture(pages[i])

    seen = []
    saved = region_mod.read_region
    watcher = region_mod.RegionWatcher((0, 0, 100, 100),
                                       on_capture=lambda c: seen.append(c.text),
                                       interval=0.05)
    try:
        region_mod.read_region = fake_read
        watcher.start()
        time.sleep(0.5)
    finally:
        watcher.stop()
        region_mod.read_region = saved

    assert len(seen) == 1, seen
    assert "first line" in seen[0]



def test_a_sidebar_is_dropped_before_the_conversation():
    """An Electron app renders its menu and its content in one region.

    Scoping to a document does not separate them, because there is only one
    document. What separates them is shape: the furniture is a long run of
    fragments and the content starts at the first real sentence. Measured on
    the Claude desktop app, 301 short lines came before the conversation.
    """
    from executive_reader.capture import uia

    chrome = [u"Resize sidebar", u"New", u"Artifacts", u"Customize", u"Pinned",
              u"More options", u"Projects", u"Recents", u"Settings", u"Help"]
    body = ["This is a real sentence of the kind a conversation is made of, "
            "long enough to count as prose rather than a label.",
            "And a second one, also long enough to be treated as content "
            "rather than as another menu entry in the sidebar."]
    text = chr(10).join(chrome + body)
    out = uia._drop_leading_chrome(text)
    assert out.startswith("This is a real sentence"), out[:60]
    assert "Resize sidebar" not in out
    assert "second one" in out


def test_a_short_opening_line_is_not_mistaken_for_a_sidebar():
    """A headline, a byline, then the article. Dropping those would be worse
    than reading a menu, so the run has to be long before it counts."""
    from executive_reader.capture import uia

    text = chr(10).join([
        "A Headline",
        "By Someone",
        "This is the opening paragraph of the article and it is comfortably "
        "long enough to be recognised as prose."])
    assert uia._drop_leading_chrome(text).startswith("A Headline")


def test_text_with_no_prose_is_left_alone():
    """A window that is genuinely all short lines must not come back empty."""
    from executive_reader.capture import uia

    text = chr(10).join(["One", "Two", "Three", "Four", "Five",
                         "Six", "Seven", "Eight", "Nine", "Ten"])
    assert uia._drop_leading_chrome(text) == text


def test_scrolling_continues_the_same_capture():
    """Scrolling a page being read must not start a near-copy of it.

    The lines that were at the bottom are at the top after a scroll, so a
    fresh capture repeats most of what was just read and the reading starts
    again from text already heard.
    """
    from executive_reader.capture import region as region_mod

    nl = chr(10)
    before = nl.join(["First line of the page.", "Second line.", "Third line."])
    after = nl.join(["Second line.", "Third line.", "Fourth line.", "Fifth line."])
    tail = region_mod.continuation(before, after)
    assert tail == "Fourth line." + nl + "Fifth line.", repr(tail)


def test_an_unchanged_screen_continues_with_nothing():
    from executive_reader.capture import region as region_mod
    nl = chr(10)
    same = nl.join(["One line.", "Another line."])
    assert region_mod.continuation(same, same) == ""


def test_unrelated_text_is_a_new_capture_not_a_continuation():
    """Pointing the area somewhere else is a different thing to read."""
    from executive_reader.capture import region as region_mod
    nl = chr(10)
    assert region_mod.continuation("One." + nl + "Two.", "Apples." + nl + "Pears.") is None


def test_a_neural_voice_is_not_pushed_past_the_speed_it_handles():
    """Its speed input saturates, and slurs what it does deliver.

    Measured on Kokoro: 2.0 came back as 1.78 and 3.0 as 2.09, unevenly
    squashed. So the model is asked for a speed inside its range and the rest
    is taken out of the waveform, which keeps the pitch where it is.
    """
    from executive_reader.tts.kokoro_engine import KokoroEngine
    from executive_reader.tts.piper_engine import PiperEngine

    for engine in (KokoroEngine, PiperEngine):
        limit = getattr(engine, "native_speed_limit", 0)
        assert 1.0 < limit <= 2.0, (engine.__name__, limit)
        assert limit < engine.max_speed, (engine.__name__, limit, engine.max_speed)


def test_speeding_up_audio_does_not_move_its_pitch():
    """Resampling is the obvious way and it transposes the voice.

    Overlapping and adding windows removes time instead, which is what a
    listener wants from a speed control.
    """
    import numpy as np
    from executive_reader.player.stretch import time_stretch

    rate = 24000
    t = np.arange(rate) / rate
    tone = (np.sin(2 * np.pi * 120 * t) + 0.5 * np.sin(2 * np.pi * 240 * t))
    tone = tone.astype(np.float32) * 0.3

    def pitch(x):
        x = x - x.mean()
        n = min(8000, len(x))
        ac = np.correlate(x[:n], x[:n], "full")[n - 1:]
        ac[:40] = 0
        return rate / float(np.argmax(ac[:400]))

    for speed in (1.5, 2.0, 3.0):
        out = time_stretch(tone, rate, speed)
        ratio = len(tone) / len(out)
        assert abs(ratio - speed) / speed < 0.08, (speed, ratio)
        assert abs(pitch(out) - pitch(tone)) < 3.0, (speed, pitch(out))

    # A speed of one must not rebuild the audio at all.
    same = time_stretch(tone, rate, 1.0)
    assert len(same) == len(tone)


def test_recognition_noise_is_not_read_aloud():
    """Recognition returns something for every mark on screen.

    Icons, bullets, checkboxes and borders come back as runs of lone letters
    and punctuation, and spoken they are a stream of single letters. Shape is
    what separates them from language, not a word list, so it holds whatever
    icon set an application happens to use.
    """
    from executive_reader.capture import region as region_mod

    for junk in ("O O O O O O O O O O", "| | |", "....", "a b c d e",
                 "> > >", "   "):
        assert region_mod.is_gibberish(junk), junk
    for real in ("This is a real sentence with words.", "I am a real line.",
                 "Page 12 of 40", "OK", "Yes"):
        assert not region_mod.is_gibberish(real), real


def test_cleaning_keeps_the_prose_and_drops_the_rest():
    from executive_reader.capture import region as region_mod
    nl = chr(10)
    raw = nl.join(["O O O O O O",
                   "The first real sentence of the passage.",
                   "| | | |",
                   "And the second one after it."])
    out = region_mod.clean(raw)
    assert "O O O" not in out, out
    assert "first real sentence" in out
    assert "second one" in out


# --- screen scaling ------------------------------------------------------
#
# The desktop this was found on: three monitors side by side, the middle one
# at 125%. Qt calls that monitor 4096x1152; a screenshot of it is 5120x1440.
# Every area chosen on it was grabbed from the wrong place, so the app read
# whatever happened to be up and to the left of the chosen text.
_LAYOUT = ([((-2560, 0, 2560, 1440), 1.0),
            ((0, 0, 2560, 1440), 1.0),
            ((2560, 0, 4096, 1152), 1.25)],
           # Deliberately out of screen order, which is how the capture
           # library really returns them.
           [(2560, 0, 5120, 1440), (-2560, 0, 2560, 1440), (0, 0, 2560, 1440)])


def test_an_area_on_a_scaled_monitor_is_captured_where_it_was_drawn():
    from executive_reader.capture import screens

    paired = screens.pair(*_LAYOUT)
    assert paired is not None, "the two descriptions of the desktop did not pair"

    # Middle of the 125% monitor: the origin shifts and every length grows.
    assert screens.to_physical((3000, 400, 800, 300), paired) == (3110, 500, 1000, 375)
    # The unscaled monitors either side must be left exactly alone.
    assert screens.to_physical((100, 200, 400, 300), paired) == (100, 200, 400, 300)
    assert screens.to_physical((-2000, 50, 300, 90), paired) == (-2000, 50, 300, 90)


def test_word_boxes_come_back_to_where_the_words_are():
    """The highlight paints in Qt's pixels but is told about them in real
    ones, so it needs the conversion in the other direction."""
    from executive_reader.capture import screens

    paired = screens.pair(*_LAYOUT)
    assert screens.to_logical((3110, 500, 1000, 375), paired) == (3000, 400, 800, 300)
    # Whatever is chosen must survive being converted and converted back,
    # otherwise the outline drifts away from the area every time it is shown.
    for box in ((3000, 400, 800, 300), (2560, 0, 100, 40), (0, 0, 2560, 1440)):
        there = screens.to_physical(box, paired)
        assert screens.to_logical(there, paired) == box, box


def test_a_desktop_with_no_scaling_is_left_alone():
    """The case that always worked has to keep working untouched."""
    from executive_reader.capture import screens

    paired = screens.pair([((0, 0, 1920, 1080), 1.0)], [(0, 0, 1920, 1080)])
    assert paired is not None
    for box in ((0, 0, 1920, 1080), (37, 91, 400, 250)):
        assert screens.to_physical(box, paired) == box
        assert screens.to_logical(box, paired) == box


def test_a_desktop_that_cannot_be_paired_is_passed_through_untouched():
    """Better to do what the app did before than to convert by guesswork.

    Pairing is by position and size because the two libraries do not agree on
    what a monitor is called. When that check fails the arrangement is not
    understood, and a wrong conversion is worse than none.
    """
    from executive_reader.capture import screens

    # Sizes that do not agree with the scale they claim.
    assert screens.pair([((0, 0, 1920, 1080), 1.0)], [(0, 0, 2560, 1440)]) is None
    # One list longer than the other.
    assert screens.pair([((0, 0, 1920, 1080), 1.0)],
                        [(0, 0, 1920, 1080), (1920, 0, 1920, 1080)]) is None
    assert screens.pair([], [(0, 0, 1920, 1080)]) is None
    # A negative scale is nonsense and must not reach a division.
    assert screens.pair([((0, 0, 1920, 1080), -2.0)], [(0, 0, 1920, 1080)]) is None
    # A screen that reports no scale is taken as unscaled, so it pairs only
    # when its size really is one to one. Refusing it outright would switch
    # the conversion off for every other monitor as well.
    assert screens.pair([((0, 0, 1920, 1080), 0.0)], [(0, 0, 1920, 1080)]) is not None
    assert screens.pair([((0, 0, 1920, 1080), None)], [(0, 0, 2560, 1440)]) is None
    # No layout means no conversion, not a crash and not a zero rectangle.
    assert screens.to_physical((10, 20, 30, 40), None) == (10, 20, 30, 40)
    assert screens.to_logical((10, 20, 30, 40), None) == (10, 20, 30, 40)


def test_a_corner_that_lands_between_monitors_still_maps():
    """Monitors of different heights leave gaps in the desktop, and a drag
    can end in one. Falling back to the nearest screen keeps the rectangle
    roughly right instead of dropping it on the wrong monitor entirely."""
    from executive_reader.capture import screens

    paired = screens.pair(*_LAYOUT)
    # Below the short 125% monitor, level with the tall ones beside it.
    mapped = screens.to_physical((3000, 1300, 200, 80), paired)
    assert mapped[0] == 3110, mapped
    assert mapped[2:] == (250, 100), mapped


def test_reading_the_screen_reads_the_display_being_looked_at():
    """Not whichever display the capture library lists first.

    On one monitor those are the same thing. On this three-monitor desk the
    first listed is not even the primary one, so reading the screen read a
    display nobody was looking at.
    """
    from executive_reader.capture import ocr

    # Entry zero is the whole virtual desktop, which is never the answer.
    monitors = [{"left": -2560, "top": 0, "width": 10240, "height": 1440},
                {"left": 2560, "top": 0, "width": 5120, "height": 1440,
                 "is_primary": False},
                {"left": -2560, "top": 0, "width": 2560, "height": 1440,
                 "is_primary": False},
                {"left": 0, "top": 0, "width": 2560, "height": 1440,
                 "is_primary": True}]

    # The mouse decides.
    assert ocr.choose_monitor(monitors, (3000, 700))["left"] == 2560
    assert ocr.choose_monitor(monitors, (-1000, 700))["left"] == -2560
    assert ocr.choose_monitor(monitors, (100, 700))["left"] == 0
    # No mouse to ask: the primary, not the first listed.
    assert ocr.choose_monitor(monitors, None)["left"] == 0
    # A position off every display falls back the same way.
    assert ocr.choose_monitor(monitors, (99999, 99999))["left"] == 0
    # Nothing claims to be primary: the first real display, never the union.
    plain = [monitors[0], monitors[1], monitors[2]]
    assert ocr.choose_monitor(plain, None)["width"] == 5120
    # A single display works with no list of extras to choose from.
    one = [{"left": 0, "top": 0, "width": 1920, "height": 1080}]
    assert ocr.choose_monitor(one, None)["width"] == 1920


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
