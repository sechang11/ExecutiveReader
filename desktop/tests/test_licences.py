"""The licence position is a property of the dependency list, so check it.

A property nothing verifies is a property that lapses the first time someone
installs a package for convenience. Two copyleft dependencies were already
found by hand here: eSpeak NG via the phonemizer, and EbookLib for EPUB. Both
were replaced. This is what stops a third arriving unnoticed.

Strong copyleft (GPL, AGPL) is disqualifying for a distributed proprietary
application. Weak copyleft (LGPL) is usable under conditions, so it is listed
rather than failed. See CAVEATS.md.
"""
from __future__ import annotations

import importlib.metadata as metadata
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import install

install()

#: Packages that pull in eSpeak NG, which is GPL-3.0.
SPEECH_COPYLEFT = ("kokoro_onnx", "phonemizer", "espeakng_loader",
                   "piper_phonemize")

#: Weak copyleft we accept, with the conditions recorded in CAVEATS.md.
ALLOWED_WEAK = {"pyside6", "pyside6_addons", "pyside6_essentials", "shiboken6"}

_AGPL = re.compile(r"\bAGPL|AFFERO", re.I)
_LGPL = re.compile(r"\bLGPL|LESSER GENERAL PUBLIC", re.I)
_GPL = re.compile(r"\bGPL\b|GENERAL PUBLIC LICEN[CS]E", re.I)

#: A License field longer than this is the full licence text, not a name.
#: numpy's is 47,000 characters because it embeds the licences of everything it
#: bundles, and three of those mention the Affero GPL. Scanning that blob
#: reports numpy as AGPL, which is false and would make this test noise.
_DECLARATION_LIMIT = 200


def declared_licence(dist) -> str:
    """The package's own licence claim, most structured source first.

    Three sources, in descending order of trustworthiness:

    1. License-Expression, the SPDX field from PEP 639. Newer packaging emits
       it and older packaging does not, so leaving it out left 18 of 44
       dependencies here unclassifiable, which is a scan that skips 40% of what
       it claims to check.
    2. License classifiers, a controlled vocabulary.
    3. The free-text License field, but only when short enough to be a name
       rather than an entire bundled licence collection.
    """
    for key in ("License-Expression",):
        try:
            expression = (dist.metadata[key] or "").strip()
        except Exception:
            expression = ""
        if expression:
            return expression

    try:
        classifiers = [c for c in (dist.metadata.get_all("Classifier") or [])
                       if c.startswith("License ::")]
    except Exception:
        classifiers = []
    if classifiers:
        return " ".join(classifiers)

    try:
        field = (dist.metadata["License"] or "").strip()
    except Exception:
        return ""
    return field if len(field) <= _DECLARATION_LIMIT else ""


def classify(text: str) -> str:
    if not text:
        return "unknown"
    if _AGPL.search(text):
        return "AGPL"
    if _LGPL.search(text):
        return "LGPL"
    if _GPL.search(text):
        return "GPL"
    return "permissive"


def scan() -> dict[str, list[tuple[str, str]]]:
    found: dict[str, list[tuple[str, str]]] = {}
    for dist in metadata.distributions():
        try:
            name = dist.metadata["Name"] or ""
        except Exception:
            continue
        if not name:
            continue
        text = declared_licence(dist)
        found.setdefault(classify(text), []).append((name, text[:70]))
    return found


# --- the checks ----------------------------------------------------------

def test_no_strong_copyleft_dependency_is_installed():
    """GPL or AGPL in the dependency list rules out a proprietary release."""
    found = scan()
    strong = found.get("GPL", []) + found.get("AGPL", [])
    assert strong == [], (
        "strong copyleft installed: "
        + ", ".join(n + " (" + t + ")" for n, t in strong)
        + ". See CAVEATS.md.")


def test_weak_copyleft_is_only_the_packages_we_accepted():
    """LGPL is usable when linked dynamically and replaceable, which is how Qt
    ships. A new one arriving unnoticed is what this catches."""
    unexpected = [n for n, _t in scan().get("LGPL", [])
                  if n.lower().replace("-", "_") not in ALLOWED_WEAK]
    assert unexpected == [], unexpected


def test_no_speech_package_reintroduces_espeak():
    """Named directly as well, because these are the ones with a reason to come
    back: they are the convenient way to do the job."""
    import importlib.util
    for name in SPEECH_COPYLEFT:
        assert importlib.util.find_spec(name) is None, (
            name + " pulls in GPL-3.0 eSpeak NG. See CAVEATS.md.")


def test_the_scan_examined_something():
    """A scan of nothing passes every check above it."""
    found = scan()
    total = sum(len(v) for v in found.values())
    assert total > 20, "only " + str(total) + " distributions seen"
    assert found.get("permissive"), "no permissive packages found at all"
    # An unclassified package is one this scan did not actually check, so a
    # large unknown bucket is a scan quietly covering less than it claims.
    unknown = found.get("unknown", [])
    assert len(unknown) <= 2, (
        str(len(unknown)) + " packages have no readable licence declaration: "
        + ", ".join(n for n, _t in unknown))


def test_full_licence_text_is_not_mistaken_for_a_declaration():
    """The false positive that would make this test noise.

    numpy is BSD, but its License field embeds the licences of everything it
    bundles and three of those mention the Affero GPL. Reading the blob reports
    numpy as AGPL. The classifier is the reliable signal.
    """
    try:
        numpy_dist = metadata.distribution("numpy")
    except metadata.PackageNotFoundError:
        return
    raw = (numpy_dist.metadata["License"] or "")
    if len(raw) > _DECLARATION_LIMIT:
        assert _AGPL.search(raw), "expected the bundled text to mention Affero"
    assert classify(declared_licence(numpy_dist)) == "permissive", \
        declared_licence(numpy_dist)


def test_classifier_wins_over_a_long_free_text_field():
    class FakeMeta:
        def __init__(self, classifiers, licence):
            self._c, self._l = classifiers, licence

        def get_all(self, key):
            return self._c if key == "Classifier" else []

        def __getitem__(self, key):
            return {"License": self._l, "Name": "fake"}.get(key)

    class Fake:
        def __init__(self, classifiers, licence):
            self.metadata = FakeMeta(classifiers, licence)

    bundled = "x" * 300 + " GNU Affero General Public License " + "y" * 300
    bsd = Fake(["License :: OSI Approved :: BSD License"], bundled)
    assert classify(declared_licence(bsd)) == "permissive"

    # A short field with no classifier is a real declaration and must be read.
    agpl = Fake([], "GNU Affero General Public License")
    assert classify(declared_licence(agpl)) == "AGPL"
    gpl = Fake([], "GNU GENERAL PUBLIC LICENSE")
    assert classify(declared_licence(gpl)) == "GPL"

    # Full text with no classifier at all is unknown, not permissive: better to
    # report nothing than to clear a package we did not actually check.
    opaque = Fake([], bundled)
    assert classify(declared_licence(opaque)) == "unknown"


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
    summary = scan()
    print("\nlicence scan: "
          + ", ".join(k + "=" + str(len(v)) for k, v in sorted(summary.items())))
    print(str(passed) + " passed, " + str(failed) + " failed")
    sys.exit(1 if failed else 0)
