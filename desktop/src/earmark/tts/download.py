"""Fetch voice models on demand, with resume and progress reporting."""
from __future__ import annotations

import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

ProgressFn = Callable[[int, int], None]
_CHUNK = 1 << 18


def download(url: str, dest: Path, on_progress: ProgressFn | None = None,
             timeout: int = 60) -> Path:
    """Download url to dest.

    Writes to a .part file and renames only on success, so an interrupted
    download never leaves a truncated file that looks complete. A partial
    .part file is resumed with a Range request rather than restarted.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest

    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "Earmark/1.0"})
    if have:
        req.add_header("Range", "bytes=" + str(have) + "-")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resuming = getattr(resp, "status", 200) == 206
            total = int(resp.headers.get("Content-Length") or 0)
            if resuming:
                total += have
            mode = "ab" if resuming else "wb"
            done = have if resuming else 0
            with open(part, mode) as fh:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
    except urllib.error.HTTPError as exc:
        # 416 means the server says we already have the whole file.
        if exc.code == 416 and part.exists():
            part.replace(dest)
            return dest
        raise

    part.replace(dest)
    return dest


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return ("%.0f %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024
    return "%.1f TB" % n


def free_space(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0
