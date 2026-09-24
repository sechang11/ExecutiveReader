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
from datetime import datetime
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

# --- which conversation you are actually in ------------------------------
#
# "The newest transcript" is not the answer, and on a busy machine it is not
# even close. Claude Code writes a file per conversation, autonomous agents
# write theirs as fast as anything else, and a dozen of them can be appending
# within the same second. Following the newest file meant following whichever
# background agent happened to flush last, so the reader would start speaking
# a conversation nobody was having.
#
# A human typing is the signal that matters. A message someone typed arrives
# as a user entry whose content is a plain string, or a list of text blocks.
# Tool results also arrive as user entries -- there are twenty of those for
# every typed line -- but their content is a list of tool_result blocks, so
# the two never have to be guessed between. A session nobody has ever typed
# in is an agent running on its own and is never followed by accident.

#: How much of the end of a transcript to read when asking about it.
#:
#: Measured rather than guessed. On this machine the transcripts run from one
#: to eighty megabytes, and the last message a person typed sits between a
#: tenth of a megabyte and six megabytes from the end, because every tool call
#: and its output goes in the same file. A quarter of a megabyte, which was
#: the first guess, missed almost all of them and reported live conversations
#: as agents nobody was typing in.
#:
#: Two megabytes is also the right answer and not only an affordable one: a
#: conversation whose last typed message is buried under more tool output than
#: that is, by definition, not the one you just typed in.
TAIL_BYTES = 2_000_000

#: Long enough to tell two conversations apart in a list.
TITLE_CHARS = 60


def _entries(path: Path, start: int = -1, window: int = TAIL_BYTES) -> Iterator[dict]:
    """Decodable entries from `start`, or from the last `window` bytes."""
    try:
        size = path.stat().st_size
        begin = max(0, size - window) if start < 0 else min(start, size)
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            if begin:
                handle.seek(begin)
                handle.readline()      # discard the half line we landed in
            for line in handle:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(entry, dict):
                    yield entry
    except OSError:
        return


def was_typed(entry: dict) -> bool:
    """True when a person typed this, rather than a tool answering."""
    if entry.get("type") != "user":
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        kinds = {b.get("type") for b in content if isinstance(b, dict)}
        return "text" in kinds and "tool_result" not in kinds
    return False


def _stamp(text: str) -> float:
    """An ISO timestamp as a number, or 0 when it cannot be read."""
    if not text:
        return 0.0
    try:
        cleaned = text.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).timestamp()
    except (ValueError, TypeError):
        return 0.0


@dataclass
class Conversation:
    """One conversation, described the way it appears in the sidebar."""
    path: Path
    project: str = ""
    title: str = ""
    last_typed: float = 0.0      # when a person last typed in it
    modified: float = 0.0
    #: Whether the whole transcript was read to decide the above. When only
    #: the end was read, finding nobody proves nothing: it may be an agent,
    #: or a conversation whose last typed line is a long way back.
    complete: bool = False
    _scanned: int = 0            # bytes examined, so a re-read is incremental

    @property
    def by_hand(self) -> bool:
        """Whether anyone has typed in it recently, as opposed to an agent."""
        return self.last_typed > 0

    @property
    def certainly_an_agent(self) -> bool:
        """Nobody has ever typed in it, and the whole file was read."""
        return self.complete and not self.last_typed


def project_name(path: Path) -> str:
    """The project folder, as a person would say it.

    Claude Code slugifies the working directory, so the folder is the whole
    path with the separators beaten out of it. The last part is the project.
    """
    raw = path.parent.name.replace("-", "/")
    while "//" in raw:
        raw = raw.replace("//", "/")
    return raw.rstrip("/").rsplit("/", 1)[-1] or path.parent.name


#: What has already been worked out about each transcript, so that asking
#: again costs only the bytes written since. Without it, drawing the list
#: re-read two megabytes per conversation every time, which is tens of
#: megabytes for one refresh of a window nobody may even be looking at.
_known: dict = {}


def describe_session(path: Path) -> Conversation:
    """Describe a transcript, reading only what has not been read before."""
    try:
        stat = path.stat()
        size, modified = stat.st_size, stat.st_mtime
    except OSError:
        return Conversation(path=path, project=project_name(path))

    before = _known.get(str(path))
    if before is not None and before._scanned >= size:
        before.modified = modified
        return before

    if before is None:
        found = Conversation(path=path, project=project_name(path),
                             modified=modified, complete=size <= TAIL_BYTES)
        start = -1
    else:
        # Only the bytes appended since last time. What a person typed
        # earlier cannot un-happen, so the old answer still stands.
        found = before
        found.modified = modified
        start = before._scanned

    name = custom = prompt = ""
    for entry in _entries(path, start=start):
        kind = entry.get("type")
        if kind == "agent-name":
            name = (entry.get("agentName") or "").strip() or name
        elif kind == "custom-title":
            custom = (entry.get("customTitle") or "").strip() or custom
        elif kind == "last-prompt":
            prompt = (entry.get("lastPrompt") or "").strip() or prompt
        elif was_typed(entry):
            found.last_typed = max(found.last_typed,
                                   _stamp(entry.get("timestamp", "")) or modified)
    title = name or custom or prompt
    if title:
        found.title = (title if len(title) <= TITLE_CHARS
                       else title[:TITLE_CHARS].rstrip() + "...")
    elif not found.title:
        found.title = path.stem[:8]
    found._scanned = size
    _known[str(path)] = found
    return found


def forget_sessions() -> None:
    """Drop what is remembered about transcripts. For tests, and for a
    projects directory that changed underneath us."""
    _known.clear()


def conversations(root: Path | None = None, limit: int = 30,
                  by_hand_only: bool = False) -> list:
    """Recent conversations, the ones you typed in first.

    Ordered by when a person last spoke in them rather than by when the file
    was last written, because a file that is being written every second may
    well be an agent nobody is watching.
    """
    found = [describe_session(path) for path in sessions(root, limit=limit)]
    if by_hand_only:
        found = [c for c in found if c.by_hand]
    found.sort(key=lambda c: (c.last_typed, c.modified), reverse=True)
    return found


def active_session(root: Path | None = None) -> Path | None:
    """The conversation you most recently typed in.

    Which is the one whose reply you are waiting for. Switching conversations
    moves this by itself, and no amount of activity in an agent's transcript
    can take it, because an agent never types.
    """
    found = conversations(root, by_hand_only=True)
    return found[0].path if found else None


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
