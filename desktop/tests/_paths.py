"""Locate the package and the repository root by searching, not by counting.

These tests have already been relocated twice. Positional paths such as
parents[1] break silently on the next move, and a test that quietly stops
checking anything is worse than one that fails, so both roots are found by
looking for a marker instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _ancestor_containing(*relative: str) -> tuple[Path, Path] | tuple[None, None]:
    """Nearest ancestor holding any of the given relative paths."""
    for parent in [_HERE, *_HERE.parents]:
        for rel in relative:
            candidate = parent.joinpath(*rel.split("/"))
            if candidate.exists():
                return parent, candidate
    return None, None

# The directory to put on sys.path so `import executive_reader` works.
_pkg_parent, _pkg = _ancestor_containing("src/executive_reader", "desktop/src/executive_reader")
SOURCE_ROOT: Path | None = _pkg.parent if _pkg is not None else None

# The repository root, identified by the rule data both halves share.
REPO_ROOT, _shared = _ancestor_containing("shared/abbreviations.json")
SHARED_DIR: Path | None = _shared.parent if _shared is not None else None


def install() -> Path:
    """Put the package on sys.path and return where it was found."""
    if SOURCE_ROOT is None:
        raise RuntimeError("Could not find the executive_reader package from " + str(_HERE))
    if str(SOURCE_ROOT) not in sys.path:
        sys.path.insert(0, str(SOURCE_ROOT))
    return SOURCE_ROOT
