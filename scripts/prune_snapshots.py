"""Prune old data/snapshots/*.db per a retention policy (§2.3).

Snapshots are gitignored, regenerable rollback aids taken before every gated
mutation (GOVERNANCE §1.6). They accrete ~130MB per op and had no retention
policy (~1.3GB / 800 files). Policy:

  - KEEP every snapshot younger than `keep_days` (default 30).
  - KEEP the most recent snapshot per operation group (so at least one rollback
    point per kind survives even if it's old).
  - PRUNE the rest.

The operation group is the run-id with volatile suffixes normalized: an embedded
`<UTC>` timestamp is stripped (so `committees_ingest_<ts>` groups together), and
an 8-hex-char ingestion-run uuid collapses to `ingestion-run`. Owner-scoped
run-ids (e.g. `pre-reclassify-<slug>`) keep their slug, so the newest snapshot
per owner-operation survives.

This deletes local files only (never anything committed); it appends a PRUNE
entry to PROVENANCE_LOG for the paper trail (§1.10-style honesty even though the
files are regenerable).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .paths import PROVENANCE_LOG, SNAPSHOTS_DIR
from .provenance import append_provenance

_TS_RE = re.compile(r"_?_?\d{4}-\d\d-\d\dT\d\d-\d\d-\d\dZ")
_HEX8_RE = re.compile(r"^[0-9a-f]{8}$")
_FNAME_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d-\d\d-\d\dZ)__(.+)\.db$")


def _parse(name: str) -> tuple[datetime, str] | None:
    """(snapshot_datetime, operation_group) from a snapshot filename, or None."""
    m = _FNAME_RE.match(name)
    if not m:
        return None
    ts_str, run_id = m.group(1), m.group(2)
    try:
        dt = datetime.strptime(ts_str, "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    group = _TS_RE.sub("", run_id).strip("_") or "unknown"
    if _HEX8_RE.match(group):
        group = "ingestion-run"
    return dt, group


def prune(
    keep_days: int = 30,
    dry_run: bool = True,
    snapshots_dir: Path = SNAPSHOTS_DIR,
    now: datetime | None = None,
) -> dict:
    """Prune snapshots older than keep_days except the newest per operation group."""
    now = now or datetime.now(timezone.utc)
    files: list[tuple[Path, datetime, str]] = []
    for p in snapshots_dir.glob("*.db"):
        parsed = _parse(p.name)
        if parsed:
            files.append((p, parsed[0], parsed[1]))

    # Newest file per operation group (always kept).
    newest_per_group: dict[str, datetime] = {}
    for _p, dt, group in files:
        if group not in newest_per_group or dt > newest_per_group[group]:
            newest_per_group[group] = dt

    to_prune: list[Path] = []
    kept = 0
    for p, dt, group in files:
        age_days = (now - dt).days
        is_newest = dt == newest_per_group[group]
        if age_days < keep_days or is_newest:
            kept += 1
        else:
            to_prune.append(p)

    bytes_pruned = sum(p.stat().st_size for p in to_prune)
    if not dry_run:
        for p in to_prune:
            p.unlink()

    summary = {
        "snapshots_dir": str(snapshots_dir),
        "keep_days": keep_days,
        "dry_run": dry_run,
        "total": len(files),
        "kept": kept,
        "pruned": len(to_prune),
        "bytes_freed": bytes_pruned,
        "mb_freed": round(bytes_pruned / 1_048_576, 1),
    }
    if not dry_run and to_prune:
        ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        append_provenance(
            f"\n### {ts[:10]} — SNAPSHOT PRUNE (§2.3)\n\n"
            f"- **pruned**: `{len(to_prune)}` snapshots / `{summary['mb_freed']} MB` "
            f"(kept all < {keep_days}d + newest per operation group)\n"
            f"- **kept**: `{kept}` of `{len(files)}`\n",
            PROVENANCE_LOG,
        )
    return summary
