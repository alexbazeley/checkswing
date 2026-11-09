"""Git merge driver for append-only logs (catalog/PROVENANCE_LOG.md).

Every scheduled workflow appends blocks to the provenance log and then runs
`git pull --rebase origin main` before pushing. When another workflow pushed its
own appends in the meantime, both sides have added text at end-of-file and git's
text merge reports a conflict — the 2026-11-09 IL backfill finished a 3h20m ISBE
download, ingested, validated, and then died on exactly that conflict, throwing
the run away. The same race exists for any run queued behind another in the
shared `fec-archive-refresh` concurrency group, because a queued run is pinned
to the commit that was current when it was created.

This driver resolves **only** the case that is safe to resolve mechanically:
both sides are byte-for-byte the common ancestor plus appended text. The result
is `ancestor + ours_tail + theirs_tail` — every entry from both sides, verbatim
and contiguous, nothing reordered inside a block. In a rebase `ours` is the
upstream being rebased onto, so the replayed commit's entries land after the
ones already on main (chronological).

Anything else — rotation (`cli rotate-provenance` rewrites the head of the
file), a hand edit, a truncation — is NOT append-only, and the driver falls back
to git's ordinary 3-way merge with conflict markers, i.e. exactly the behaviour
before this driver existed. It never guesses.

Why not the built-in `merge=union`: union re-diffs the two sides against each
other inside the conflict region, and provenance blocks share many identical
lines (`- **source**: ...`, blank separators). Tested on the real 2026-11-08
federal + 2026-11-02 state blocks it silently dropped the blank line between
two entries; on less regular content the same refinement can interleave lines
from different entries. For an audit trail that is worse than failing.

Wiring (see .gitattributes and the workflows' commit steps):

    .gitattributes:  catalog/PROVENANCE_LOG.md merge=append-log
    git config merge.append-log.driver 'python3 scripts/merge_append_log.py %O %A %B %P'

If the driver is not configured (e.g. a fresh local clone), git ignores the
attribute's driver name and uses its normal text merge, so nothing changes for
anyone who has not opted in.

Exit status: 0 = merged cleanly (result written to the %A file); non-zero =
conflict left in %A with standard markers.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def merge_append_only(base: bytes, ours: bytes, theirs: bytes) -> bytes | None:
    """Return the merged bytes if both sides are pure appends to `base`, else None.

    Pure: no I/O. `None` means "not safe to resolve mechanically"."""
    if not base:
        # No common ancestor: git passes an EMPTY base for an add/add conflict —
        # which is what a shallow checkout with no merge base produces (the
        # 2026-08-27 il-backfill failure). The empty string is a prefix of
        # everything, so without this guard both full logs would be concatenated
        # and every entry duplicated. Refuse.
        return None
    if not (ours.startswith(base) and theirs.startswith(base)):
        return None
    ours_tail = ours[len(base):]
    theirs_tail = theirs[len(base):]
    if ours_tail == theirs_tail:
        # Same text appended on both sides (e.g. a cherry-picked commit) — keep
        # one copy rather than duplicating entries.
        return ours
    return base + ours_tail + theirs_tail


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: merge_append_log.py BASE OURS THEIRS [PATH]", file=sys.stderr)
        return 2
    base_p, ours_p, theirs_p = (Path(a) for a in argv[:3])
    label = argv[3] if len(argv) > 3 else str(ours_p)

    merged = merge_append_only(
        base_p.read_bytes(), ours_p.read_bytes(), theirs_p.read_bytes()
    )
    if merged is not None:
        ours_p.write_bytes(merged)
        print(f"merge_append_log: {label}: both sides append-only — kept both "
              f"sides' new entries", file=sys.stderr)
        return 0

    print(f"merge_append_log: {label}: not a pure append on both sides — "
          f"falling back to a normal 3-way merge", file=sys.stderr)
    # Same result git would have produced without this driver: conflict markers
    # in the %A file and a non-zero exit when the sides really conflict.
    proc = subprocess.run(
        ["git", "merge-file", "-L", "ours", "-L", "base", "-L", "theirs",
         str(ours_p), str(base_p), str(theirs_p)],
    )
    return 1 if proc.returncode != 0 else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
