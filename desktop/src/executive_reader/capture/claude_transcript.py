"""Read Claude Code sessions from their transcript files.

Claude Code appends every session to a JSONL file on disk as it goes. Reading
that is far better than looking at the screen: the text is exact, thinking and
response blocks are separate fields, and tool traffic can be skipped entirely.
None of that is possible from pixels.

Transcripts live at:
    %USERPROFILE%/.claude/projects/<project-slug>/<session-id>.jsonl
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..document import Document


@dataclass
class Turn:
    role: str                 # "assistant" or "user"
    text: str = ""            # spoken response text
    thinking: str = ""        # reasoning block, read only when asked for
    uuid: str = ""
    timestamp: str = ""
    tools: list[str] = field(default_factory=list)

    def speakable(self, include_thinking: bool = False) -> str:
        parts = []
        if include_thinking and self.thinking.strip():
            parts.append("Thinking. " + self.thinking.strip())
        if self.text.strip():
            parts.append(self.text.strip())
        return "\n\n".join(parts)


def projects_dir(override: str | Path | None = None) -> Path:
    if override:
        return Path(override)
    return Path.home() / ".claude" / "projects"


def sessions(root: Path | None = None, limit: int = 40) -> list[Path]:
    """Every session transcript, newest first."""
    root = root or projects_dir()
    if not root.exists():
        return []
    files = [p for p in root.glob("*/*.jsonl") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def latest_session(root: Path | None = None) -> Path | None:
    found = sessions(root, limit=1)
    return found[0] if found else None


def session_for_project(project_dir: str | Path,
                        root: Path | None = None) -> Path | None:
    """Newest transcript for a working directory, matched by its slug.

    Claude Code slugifies the path by replacing separators and colons with
    dashes, so C:\\Users\\me\\proj becomes C--Users-me-proj.
    """
    slug = str(project_dir).replace(":", "-").replace("\\", "-").replace("/", "-")
    folder = (root or projects_dir()) / slug
    if not folder.is_dir():
        return None
    files = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    return files[0] if files else None


def _blocks(content) -> tuple[str, str, list[str]]:
    """Split a message body into spoken text, thinking, and tool names."""
    if isinstance(content, str):
        return content, "", []
    text_parts, think_parts, tools = [], [], []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                text_parts.append(block.get("text") or "")
            elif kind == "thinking":
                think_parts.append(block.get("thinking") or "")
            elif kind == "tool_use":
                tools.append(str(block.get("name") or "tool"))
            # tool_result blocks are deliberately ignored: they are payloads,
            # not something anyone wants read aloud.
    return ("\n".join(t for t in text_parts if t).strip(),
            "\n".join(t for t in think_parts if t).strip(),
            tools)


def parse_line(line: str) -> Turn | None:
    line = line.strip()
    if not line:
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict) or record.get("type") not in ("assistant", "user"):
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    text, thinking, tools = _blocks(message.get("content"))
    if not text and not thinking:
        return None
    return Turn(role=record.get("type", ""), text=text, thinking=thinking,
                uuid=str(record.get("uuid") or ""),
                timestamp=str(record.get("timestamp") or ""), tools=tools)


def read_turns(path: Path, roles: tuple[str, ...] = ("assistant",)) -> list[Turn]:
    turns = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            turn = parse_line(line)
            if turn is not None and turn.role in roles:
                turns.append(turn)
    return turns


def as_document(path: Path, include_thinking: bool = False,
                last_n: int | None = None) -> Document | None:
    """The whole session, or just its last few turns, as one readable document."""
    turns = read_turns(path)
    if last_n:
        turns = turns[-last_n:]
    body = "\n\n".join(t.speakable(include_thinking) for t in turns
                       if t.speakable(include_thinking))
    if not body.strip():
        return None
    return Document(text=body, title="Claude session " + path.stem[:8],
                    uri="claude://" + path.stem, source="claude",
                    meta={"path": str(path), "turns": len(turns),
                          "thinking": include_thinking})


class Tail:
    """Follow a transcript and yield assistant turns as they are written.

    Starts at the end of the file, so switching this on reads what Claude says
    next rather than replaying the whole conversation.
    """

    def __init__(self, path: Path, include_thinking: bool = False,
                 from_start: bool = False) -> None:
        self.path = Path(path)
        self.include_thinking = include_thinking
        self._offset = 0 if from_start else self._size()
        self._seen: set[str] = set()

    def _size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def poll(self) -> list[Turn]:
        """Return turns appended since the last call."""
        size = self._size()
        if size < self._offset:      # file replaced or truncated
            self._offset = 0
        if size == self._offset:
            return []
        out = []
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self._offset)
                for line in fh:
                    if not line.endswith("\n"):
                        break        # a partial write; pick it up next poll
                    self._offset += len(line.encode("utf-8", "replace"))
                    turn = parse_line(line)
                    if turn is None or turn.role != "assistant":
                        continue
                    if turn.uuid and turn.uuid in self._seen:
                        continue
                    if turn.uuid:
                        self._seen.add(turn.uuid)
                    if turn.speakable(self.include_thinking):
                        out.append(turn)
        except OSError:
            return []
        return out

    def follow(self, interval: float = 1.0) -> Iterator[Turn]:
        while True:
            for turn in self.poll():
                yield turn
            time.sleep(interval)
