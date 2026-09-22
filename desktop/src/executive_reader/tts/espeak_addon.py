"""Detect a user-installed eSpeak NG. Never ship one.

eSpeak is GPL-3.0. Bundling it would put copyleft over this whole application,
so it is an add-on: if the user installs it themselves we use it, and if they
do not, English still works through the permissive dictionary.

That distinction is the entire licence position, so it is enforced here rather
than left to habit. This module only ever *looks*. It does not download,
install, or vendor anything, and nothing in the project's dependencies pulls
eSpeak in.

Whether a proprietary program linking a user-installed GPL library forms a
derivative work is genuinely disputed. See CAVEATS.md before selling this.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

#: What installing it unlocks, for the settings UI to show.
UNLOCKS = ("Spanish", "French", "Italian", "Portuguese", "Hindi", "Japanese",
           "Mandarin")

_WINDOWS_HINTS = (
    r"C:\Program Files\eSpeak NG\libespeak-ng.dll",
    r"C:\Program Files (x86)\eSpeak NG\libespeak-ng.dll",
)
_UNIX_HINTS = (
    "/usr/lib/x86_64-linux-gnu/libespeak-ng.so.1",
    "/usr/local/lib/libespeak-ng.dylib",
    "/opt/homebrew/lib/libespeak-ng.dylib",
)


@dataclass(frozen=True)
class Addon:
    present: bool
    library: str = ""
    executable: str = ""

    @property
    def summary(self) -> str:
        """What installing it would change, including for English.

        This used to advertise other languages only, which undersells it and
        left the impression that English had nothing to gain. Kokoro was
        trained on eSpeak's phonemes, and without eSpeak the app works from a
        dictionary and letter-to-sound rules deliberately shaped to imitate
        them. Common words come out the same either way; names, acronyms and
        anything not in the dictionary are where the approximation shows, and
        a word given the wrong sounds is the kind of wrong that makes a whole
        sentence sound slurred.
        """
        if self.present:
            return ("eSpeak found. Non-English voices work (" + ", ".join(UNLOCKS)
                    + "), and the neural voices are getting the phonemes they "
                    "were trained on rather than an imitation of them.")
        return ("English works without anything extra, from a built-in "
                "dictionary. Installing eSpeak NG yourself adds "
                + ", ".join(UNLOCKS) + ", and sharpens English too: the "
                "neural voices were trained on eSpeak's phonemes, so unusual "
                "words and names are pronounced better with it than with the "
                "imitation of it used otherwise. It is not bundled because "
                "its licence would cover this whole application.")


def detect() -> Addon:
    """Look for eSpeak on this machine. Never installs or downloads."""
    override = os.environ.get("EXECUTIVE_READER_ESPEAK_LIBRARY", "").strip()
    if override and Path(override).is_file():
        return Addon(present=True, library=override)

    for candidate in _WINDOWS_HINTS + _UNIX_HINTS:
        if Path(candidate).is_file():
            return Addon(present=True, library=candidate)

    binary = shutil.which("espeak-ng") or shutil.which("espeak")
    if binary:
        return Addon(present=True, executable=binary)

    return Addon(present=False)


def available() -> bool:
    return detect().present
