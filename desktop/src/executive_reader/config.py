"""User settings, persisted as JSON under %APPDATA%/Executive Reader/config.json."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


APP_NAME = "Earmark"
_PREVIOUS_NAME = "Aloud"


def data_dir() -> Path:
    """Settings, history and downloaded voices, all on this machine.

    Carries across the folder from the previous product name if one is there,
    so renaming does not orphan an existing history, bookmarks and voice
    downloads. Only ever runs once: after the move the old folder is gone.
    """
    base = Path(os.environ.get("APPDATA") or Path.home() / ".config")
    d = base / APP_NAME
    if not d.exists():
        old = base / _PREVIOUS_NAME
        if old.is_dir():
            try:
                old.rename(d)
            except OSError:
                pass  # in use or cross-device; fall through and start fresh
    d.mkdir(parents=True, exist_ok=True)
    return d


def voices_dir() -> Path:
    d = data_dir() / "voices"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Config:
    # --- voice ---
    engine: str = "sapi"            # sapi | kokoro | piper
    #: False until the user picks a voice themselves. While it is False the
    #: app upgrades to a neural voice as soon as one is installed, because
    #: the shipped default is a Windows system voice and someone who has just
    #: downloaded 310 MB of neural voices did not mean to keep listening to
    #: Microsoft David.
    voice_chosen: bool = False
    voice: str = ""                 # engine-specific voice id; blank = engine default
    speed: float = 1.0              # 0.5 .. 4.0, applied at synthesis time
    volume: float = 1.0             # 0.0 .. 1.0

    # --- reading behaviour ---
    max_segment_chars: int = 320    # long sentences are split so playback starts fast
    prefetch_segments: int = 3      # how far ahead to synthesize
    skip_code_blocks: bool = False
    read_urls: str = "domain"       # full | domain | skip
    auto_resume: bool = True        # jump to the saved position when re-reading a source

    # --- claude transcript mode ---
    window_watch: str = "off"        # off | follow | locked
    window_watch_interval: float = 1.5
    #: Reading Claude's replies is what this application is for, and it is
    #: also the one route that needs no setting up: the transcript is exact
    #: text on disk, so there is no area to choose and nothing to recognise.
    #: It was off by default and the switch was on the sixth tab, which left
    #: the best route hidden behind the worst one.
    #:
    #: Follows the same shape as voice_chosen: on until somebody says
    #: otherwise, and once they have said it their answer is kept.
    claude_watch: bool = True
    claude_watch_chosen: bool = False
    claude_read_thinking: bool = False
    #: A conversation to stay on. Blank follows whichever one you last typed
    #: in, which is the one whose reply you are waiting for. Following the
    #: most recently written file instead meant following whichever agent
    #: flushed last, and agents write far more often than people type.
    claude_session: str = ""
    claude_projects_dir: str = ""   # blank = %USERPROFILE%/.claude/projects

    # --- capture ---
    ocr_enabled: bool = True
    clipboard_watch: bool = False   # off by default; it reads everything you copy

    # --- hotkeys (ctrl/alt/shift/win + key) ---
    hotkeys: dict = field(default_factory=lambda: {
        "read_smart":   "ctrl+alt+r",   # run the capture ladder on the focused window
        "read_clip":    "ctrl+alt+v",   # read whatever is on the clipboard
        "read_ocr":     "ctrl+alt+o",   # force screen OCR
        # Not ctrl+alt+space: that combination is commonly already taken.
        "play_pause":   "ctrl+alt+p",
        "stop":         "ctrl+alt+x",
        "next_sent":    "ctrl+alt+right",
        "prev_sent":    "ctrl+alt+left",
        "faster":       "ctrl+alt+up",
        "slower":       "ctrl+alt+down",
        "bookmark":     "ctrl+alt+b",
    })

    # --- ui ---
    mini_player: bool = True
    sleep_timer_minutes: int = 0    # 0 = off

    @classmethod
    def path(cls) -> Path:
        return data_dir() / "config.json"

    @classmethod
    def load(cls) -> "Config":
        p = cls.path()
        if not p.exists():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in raw.items() if k in known}
        # A setting nobody has answered follows the default, even when an
        # older file recorded one. Reading Claude's replies used to be off
        # and buried; every config written before that changed carries the
        # old answer, and treating it as a decision would hide the new
        # default from exactly the people who already have the app.
        if not values.get("claude_watch_chosen"):
            values.pop("claude_watch", None)
        return cls(**values)

    def save(self) -> None:
        tmp = self.path().with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(self.path())

    def claude_dir(self) -> Path:
        if self.claude_projects_dir:
            return Path(self.claude_projects_dir)
        return Path.home() / ".claude" / "projects"
