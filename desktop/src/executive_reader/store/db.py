"""History, bookmarks, pronunciation rules and per-source settings.

One SQLite file under %APPDATA%/Executive Reader. Everything stays on this machine.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import data_dir
from ..textproc import shared_rules
from ..textproc.pronounce import Rule, default_rules
from .anchors import Anchor

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    uri           TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    snippet       TEXT NOT NULL DEFAULT '',
    total         INTEGER NOT NULL DEFAULT 0,
    last_index    INTEGER NOT NULL DEFAULT 0,
    anchor        TEXT NOT NULL DEFAULT '',
    finished      INTEGER NOT NULL DEFAULT 0,
    seconds       REAL NOT NULL DEFAULT 0,
    first_read    REAL NOT NULL DEFAULT 0,
    last_read     REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS history_recent ON history(last_read DESC);

CREATE TABLE IF NOT EXISTS bookmarks (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    uri      TEXT NOT NULL,
    title    TEXT NOT NULL DEFAULT '',
    note     TEXT NOT NULL DEFAULT '',
    anchor   TEXT NOT NULL DEFAULT '',
    seg_index INTEGER NOT NULL DEFAULT 0,
    created  REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS bookmarks_uri ON bookmarks(uri, created DESC);

CREATE TABLE IF NOT EXISTS pronunciations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern     TEXT NOT NULL UNIQUE,
    replacement TEXT NOT NULL DEFAULT '',
    is_regex    INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS site_prefs (
    host   TEXT PRIMARY KEY,
    engine TEXT NOT NULL DEFAULT '',
    voice  TEXT NOT NULL DEFAULT '',
    speed  REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""


@dataclass
class HistoryRow:
    uri: str
    title: str
    source: str
    snippet: str
    total: int
    last_index: int
    finished: bool
    seconds: float
    last_read: float

    @property
    def progress(self) -> float:
        return (self.last_index / self.total) if self.total else 0.0


@dataclass
class BookmarkRow:
    id: int
    uri: str
    title: str
    note: str
    seg_index: int
    created: float
    anchor: Anchor
    #: The shared rules were edited after this bookmark was saved, so its
    #: quoted sentence may have been rewritten since.
    rules_changed: bool = False


_PREVIOUS_DB = "aloud.db"


def default_db_path() -> Path:
    """The database, carried across from the previous product name.

    Renaming the folder is not enough on its own: the file inside it is named
    too, and leaving that behind orphans an existing history and bookmarks
    while the app silently starts a fresh, empty one.
    """
    current = data_dir() / "earmark.db"
    if not current.exists():
        previous = current.parent / _PREVIOUS_DB
        if previous.is_file():
            try:
                previous.rename(current)
            except OSError:
                pass  # locked or cross-device; start fresh rather than fail
    return current


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_db_path()
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._migrate()
        self._seed_pronunciations()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        with self._lock:
            for table in ("history", "bookmarks"):
                columns = {row["name"] for row in
                           self._conn.execute("PRAGMA table_info(" + table + ")")}
                if "rules_stamp" not in columns:
                    self._conn.execute(
                        "ALTER TABLE " + table +
                        " ADD COLUMN rules_stamp TEXT NOT NULL DEFAULT ''")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- history ---------------------------------------------------------
    def touch(self, uri: str, title: str, source: str, snippet: str,
              total: int) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO history (uri,title,source,snippet,total,first_read,last_read)"
                " VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(uri) DO UPDATE SET title=excluded.title,"
                " source=excluded.source, snippet=excluded.snippet,"
                " total=excluded.total, last_read=excluded.last_read",
                (uri, title, source, snippet, total, now, now))
            self._conn.commit()

    def save_position(self, uri: str, index: int, anchor: Anchor | None = None,
                      seconds: float = 0.0, finished: bool = False) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE history SET last_index=?, anchor=?, last_read=?,"
                " seconds=seconds+?, finished=?, rules_stamp=? WHERE uri=?",
                (index, json.dumps(anchor.to_dict()) if anchor else "",
                 time.time(), max(0.0, seconds), 1 if finished else 0,
                 shared_rules.fingerprint(), uri))
            self._conn.commit()

    def position(self, uri: str) -> tuple[int, Anchor | None, bool]:
        """Saved index, anchor, and whether the shared rules have changed since.

        A rules change rewrites the sentences a position was captured from, so
        the caller needs to know rather than silently trusting a stale quote.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT last_index, anchor, rules_stamp FROM history WHERE uri=?",
                (uri,)).fetchone()
        if row is None:
            return 0, None, False
        anchor = None
        if row["anchor"]:
            try:
                anchor = Anchor.from_dict(json.loads(row["anchor"]))
            except (json.JSONDecodeError, TypeError):
                anchor = None
        stored = row["rules_stamp"] or ""
        current = shared_rules.fingerprint()
        # An empty stored stamp predates stamping; treat it as unknown, not changed.
        changed = bool(stored and current and stored != current)
        return int(row["last_index"] or 0), anchor, changed

    def history(self, limit: int = 100, unfinished_only: bool = False) -> list[HistoryRow]:
        sql = "SELECT * FROM history"
        if unfinished_only:
            sql += " WHERE finished=0 AND last_index>0"
        sql += " ORDER BY last_read DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (limit,)).fetchall()
        return [HistoryRow(uri=r["uri"], title=r["title"], source=r["source"],
                           snippet=r["snippet"], total=int(r["total"] or 0),
                           last_index=int(r["last_index"] or 0),
                           finished=bool(r["finished"]),
                           seconds=float(r["seconds"] or 0),
                           last_read=float(r["last_read"] or 0)) for r in rows]

    def forget(self, uri: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM history WHERE uri=?", (uri,))
            self._conn.execute("DELETE FROM bookmarks WHERE uri=?", (uri,))
            self._conn.commit()

    def clear_history(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM history")
            self._conn.commit()

    # --- bookmarks -------------------------------------------------------
    def add_bookmark(self, uri: str, title: str, index: int, anchor: Anchor,
                     note: str = "") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO bookmarks"
                " (uri,title,note,anchor,seg_index,created,rules_stamp)"
                " VALUES (?,?,?,?,?,?,?)",
                (uri, title, note, json.dumps(anchor.to_dict()), index,
                 time.time(), shared_rules.fingerprint()))
            self._conn.commit()
            return int(cur.lastrowid)

    def bookmarks(self, uri: str | None = None, limit: int = 200) -> list[BookmarkRow]:
        if uri:
            sql, args = ("SELECT * FROM bookmarks WHERE uri=? ORDER BY created DESC"
                         " LIMIT ?", (uri, limit))
        else:
            sql, args = "SELECT * FROM bookmarks ORDER BY created DESC LIMIT ?", (limit,)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        out = []
        for r in rows:
            try:
                anchor = Anchor.from_dict(json.loads(r["anchor"] or "{}"))
            except (json.JSONDecodeError, TypeError):
                anchor = Anchor(exact="")
            stored = (r["rules_stamp"] or "") if "rules_stamp" in r.keys() else ""
            current = shared_rules.fingerprint()
            out.append(BookmarkRow(id=int(r["id"]), uri=r["uri"], title=r["title"],
                                   note=r["note"], seg_index=int(r["seg_index"] or 0),
                                   created=float(r["created"] or 0), anchor=anchor,
                                   rules_changed=bool(stored and current
                                                      and stored != current)))
        return out

    def delete_bookmark(self, bookmark_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM bookmarks WHERE id=?", (bookmark_id,))
            self._conn.commit()

    # --- pronunciation ---------------------------------------------------
    def _seed_pronunciations(self) -> None:
        """Install the shipped overrides, and pick up later additions to them.

        Seeding only on an empty table would freeze the dictionary at whatever
        shared/pronunciation.json held the first time the app ran. Instead the
        set is fingerprinted: when it changes, new entries are added without
        touching anything the user has edited. Entries the user deleted only
        return if the shipped set itself changed, which is rare.
        """
        rules = default_rules()
        fingerprint = hashlib.sha1(
            "|".join(sorted(r.pattern + "=" + r.replacement for r in rules))
            .encode("utf-8")).hexdigest()
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key='pronunciation_seed'").fetchone()
            if row is not None and row["value"] == fingerprint:
                return
            self._conn.executemany(
                "INSERT OR IGNORE INTO pronunciations"
                " (pattern,replacement,is_regex) VALUES (?,?,?)",
                [(r.pattern, r.replacement, int(r.is_regex)) for r in rules])
            self._conn.execute(
                "INSERT INTO meta (key,value) VALUES ('pronunciation_seed',?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (fingerprint,))
            self._conn.commit()

    def rules(self) -> list[Rule]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT pattern,replacement,is_regex,enabled FROM pronunciations"
            ).fetchall()
        return [Rule(pattern=r["pattern"], replacement=r["replacement"],
                     is_regex=bool(r["is_regex"]), enabled=bool(r["enabled"]))
                for r in rows]

    def upsert_rule(self, rule: Rule) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO pronunciations (pattern,replacement,is_regex,enabled)"
                " VALUES (?,?,?,?) ON CONFLICT(pattern) DO UPDATE SET"
                " replacement=excluded.replacement, is_regex=excluded.is_regex,"
                " enabled=excluded.enabled",
                (rule.pattern, rule.replacement, int(rule.is_regex),
                 int(rule.enabled)))
            self._conn.commit()

    def delete_rule(self, pattern: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM pronunciations WHERE pattern=?",
                               (pattern,))
            self._conn.commit()

    # --- per-site voice --------------------------------------------------
    def site_pref(self, host: str) -> tuple[str, str, float] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT engine,voice,speed FROM site_prefs WHERE host=?",
                (host,)).fetchone()
        if row is None:
            return None
        return row["engine"], row["voice"], float(row["speed"] or 0)

    def set_site_pref(self, host: str, engine: str, voice: str,
                      speed: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO site_prefs (host,engine,voice,speed) VALUES (?,?,?,?)"
                " ON CONFLICT(host) DO UPDATE SET engine=excluded.engine,"
                " voice=excluded.voice, speed=excluded.speed",
                (host, engine, voice, speed))
            self._conn.commit()
