"""Append-only, lock-guarded writes to catalog/PROVENANCE_LOG.md (§2.2).

Every gated mutation logs a block here. The historical pattern read the whole
850KB+ file and rewrote it (`existing + block`) — O(n) per append, and, with no
locking, a manual CLI op running during a scheduled refresh could clobber the
other's entry (last-writer-wins → a silently dropped provenance record). This
appends in `'a'` mode under an OS advisory lock (flock), so entries never drop
and each append costs O(block), not O(file).

Blocks are expected to start with a newline (the callers' convention), so append
mode reproduces the old `existing + block` result exactly.
"""
from __future__ import annotations

from pathlib import Path

from .paths import PROVENANCE_LOG

try:
    import fcntl  # POSIX only
    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - non-POSIX fallback
    _HAVE_FCNTL = False


def append_provenance(block: str, path: Path = PROVENANCE_LOG) -> None:
    """Append `block` to the provenance log atomically (append mode + flock)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        if _HAVE_FCNTL:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(block)
        finally:
            if _HAVE_FCNTL:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
