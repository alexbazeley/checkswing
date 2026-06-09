"""Self-maintaining refresh layer.

CHARTER §Phase 5: loop every owner with status in {pilot, active}, fetch
incrementally since their audit.last_ingestion, classify through the existing
pipeline, and regenerate mockup/data.json once at the end.

GOVERNANCE.md §1.7 boundary: this script is READ-ONLY on signal blocks. The only
YAML field touched (indirectly, via ingest_entity) is `audit.last_ingestion`,
and only on successful per-owner completion.

GOVERNANCE.md §1.9 (conservative tie-breaks): a failed owner leaves
`audit.last_ingestion` unchanged so the next refresh retries the same window.
Per-owner failures DO NOT abort the run — every owner gets attempted.

Operational notes:
  - File lock at `data/.refresh.lock` prevents concurrent runs corrupting the
    shared SQLite DB.
  - `mockup/data.json` is regenerated once at the end IFF at least one owner
    ingested new records. Skipped on dry-run.
  - The run summary is appended to `catalog/PROVENANCE_LOG.md` as a
    structured REFRESH RUN block so `#/runs` can surface it.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import yaml

from .ingest import ingest_entity
from .paths import DATA_DIR, OWNERS_DIR, PROVENANCE_LOG, RAW_DIR, REPO_ROOT


REFRESH_LOCK = DATA_DIR / ".refresh.lock"
BUILD_DATA_SCRIPT = REPO_ROOT / "mockup" / "build_data.py"

# Statuses considered "live" for refresh. `queued` owners are not yet
# pipeline-ready; `paused` owners are intentionally held out.
ACTIVE_STATUSES = {"pilot", "active"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── File lock ───────────────────────────────────────────────────────────────


# A lock whose owning pid is dead, or that is older than this, is treated as
# stale and reclaimed — so a crashed run doesn't wedge every later refresh.
# 8h sits just above the 6h GitHub Actions job cap; mirrors the fetch
# checkpoint's staleness rule (fetch_fec.CHECKPOINT_STALE_DAYS).
REFRESH_LOCK_STALE_HOURS = 8


def _lock_is_stale(content: str) -> bool:
    """Heuristic for a reclaimable refresh lock.

    Stale if the recorded pid is dead (the common case: a crashed run, or a lock
    left by a now-gone CI runner) or — when no live pid can be confirmed — if the
    lock is older than REFRESH_LOCK_STALE_HOURS. A confirmably-alive pid is never
    stale, so a genuinely-running refresh is always respected.
    """
    pid_m = re.search(r"pid=(\d+)", content or "")
    if pid_m:
        pid = int(pid_m.group(1))
        try:
            os.kill(pid, 0)
            return False  # alive — genuinely running
        except ProcessLookupError:
            return True   # dead pid — stale
        except PermissionError:
            return False  # alive but owned by another user — respect it
    ts_m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", content or "")
    if ts_m:
        try:
            ts = datetime.strptime(ts_m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - ts).total_seconds() / 3600 > REFRESH_LOCK_STALE_HOURS
        except ValueError:
            pass
    return False  # unparseable — be conservative, don't reclaim


@contextmanager
def _acquire_lock(path: Path | None = None) -> Iterator[None]:
    """Acquire an exclusive file lock; raise if another refresh is running.

    Uses O_EXCL — atomic on POSIX. The lock file content records the holder's
    pid + start time so a stuck-process recovery is humanly debuggable. A stale
    lock (dead pid, or older than REFRESH_LOCK_STALE_HOURS) is reclaimed
    automatically rather than wedging the run.

    `path` defaults to the module-level REFRESH_LOCK, but is resolved *at call
    time* (not bound as a parameter default at import time). This keeps the lock
    monkeypatchable — `monkeypatch.setattr(refresh, "REFRESH_LOCK", tmp)` is
    honored even by a no-argument `_acquire_lock()` call, so tests stay hermetic
    and never touch the repo's real `data/.refresh.lock`.
    """
    if path is None:
        path = REFRESH_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if _lock_is_stale(existing):
            print(f"[refresh] Reclaiming stale lock at {path} (contents: {existing.strip() or '(empty)'}).")
            try:
                path.unlink()
            except OSError:
                pass
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                raise RuntimeError(
                    f"Refresh lock at {path} reappeared after stale reclamation — "
                    f"another run started concurrently. Retry."
                )
        else:
            raise RuntimeError(
                f"Refresh already running (lock at {path}). "
                f"Lock contents: {existing.strip() or '(empty)'}. "
                f"If you're sure no refresh is running, delete the lock file and retry."
            )
    try:
        os.write(fd, f"{_utc_now_iso()} · pid={os.getpid()}\n".encode())
        os.close(fd)
        yield
    finally:
        try:
            path.unlink()
        except OSError:
            pass


# ─── Owner selection ─────────────────────────────────────────────────────────


def _list_active_owners(only: list[str] | None = None) -> list[str]:
    """Return slugs of owners with status in ACTIVE_STATUSES, alphabetical.

    If `only` is provided, intersect with that list. Any slug in `only` that
    isn't an active owner raises — refresh should fail fast on typos rather
    than silently skip.
    """
    by_slug: dict[str, dict] = {}
    for path in sorted(OWNERS_DIR.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        if data.get("status") not in ACTIVE_STATUSES:
            continue
        slug = data.get("slug")
        if slug:
            by_slug[slug] = data

    if only:
        missing = [s for s in only if s not in by_slug]
        if missing:
            raise RuntimeError(
                f"Owner(s) not found or not in {sorted(ACTIVE_STATUSES)}: {missing}. "
                f"Active owners: {sorted(by_slug.keys())}"
            )
        # Preserve caller order so tests / users get predictable execution.
        return [s for s in only if s in by_slug]
    return sorted(by_slug.keys())


def select_bucket(bucket_index: int, bucket_count: int) -> list[str]:
    """Pick this bucket's slugs from active owners, balanced by paginate volume.

    The GHA refresh runs as N parallel matrix jobs sharing one FEC API key. To
    avoid one bucket hitting the 6h cap while others sit idle, we order owners
    by how heavy their raw-payload history is (proxy for how slow their next
    fetch will be) and round-robin into buckets. Heaviest owners (kendrick-ken,
    cohen-steven, johnson-greg, sherman-john) land in different buckets.

    Falls back to alphabetical ordering for owners with zero raw history yet.
    """
    if bucket_count <= 0 or bucket_index < 0 or bucket_index >= bucket_count:
        raise RuntimeError(
            f"Invalid bucket: {bucket_index}/{bucket_count}. "
            f"Expected 0 <= bucket_index < bucket_count."
        )
    all_active = _list_active_owners()

    def _weight(slug: str) -> int:
        slug_dir = RAW_DIR / slug
        if not slug_dir.is_dir():
            return 0
        return sum(1 for p in slug_dir.glob("*.json") if not p.name.startswith("_"))

    # Sort desc by weight, then alpha for ties. Round-robin into buckets so
    # neighbouring weights end up in different buckets.
    weighted = sorted(all_active, key=lambda s: (-_weight(s), s))
    return [s for i, s in enumerate(weighted) if i % bucket_count == bucket_index]


# ─── PROVENANCE_LOG append ───────────────────────────────────────────────────


def _append_refresh_log(summary: dict) -> None:
    """Append a REFRESH RUN block to catalog/PROVENANCE_LOG.md."""
    PROVENANCE_LOG.parent.mkdir(parents=True, exist_ok=True)
    existing = PROVENANCE_LOG.read_text(encoding="utf-8") if PROVENANCE_LOG.exists() else ""
    lines: list[str] = []
    started = summary.get("started_at", "")
    lines.append(f"\n### {started[:10]} — REFRESH RUN {summary['refresh_id']}")
    lines.append("")
    for k in (
        "started_at",
        "completed_at",
        "dry_run",
        "owners_attempted",
        "owners_succeeded",
        "owners_failed",
        "total_records_fetched",
        "data_json_regenerated",
    ):
        if k in summary:
            lines.append(f"- **{k}**: `{summary[k]}`")
    if summary.get("failed_owners"):
        lines.append(f"- **failed_owners**: `{summary['failed_owners']}`")
    if summary.get("data_json_error"):
        lines.append(f"- **data_json_error**: `{summary['data_json_error']}`")
    PROVENANCE_LOG.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")


# ─── Data.json regen (subprocess to keep refresh decoupled) ──────────────────


def _rebuild_data_json() -> tuple[bool, str | None]:
    """Run mockup/build_data.py to regenerate mockup/data.json.

    Returns (ok, error_string). Subprocess is the boundary so a build_data
    crash doesn't lose the refresh run's DB work.
    """
    try:
        result = subprocess.run(
            [sys.executable, str(BUILD_DATA_SCRIPT)],
            cwd=str(REPO_ROOT),
            check=True,
            capture_output=True,
            text=True,
        )
        return True, (result.stdout or "").strip() or None
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "").strip() or str(e)
        return False, err
    except FileNotFoundError as e:
        return False, str(e)


# ─── Main entry point ────────────────────────────────────────────────────────


def refresh_all(
    *,
    only: list[str] | None = None,
    dry_run: bool = False,
    skip_data_json: bool = False,
    full_refetch: bool = False,
    chunk_by_cycle: bool = False,
) -> dict:
    """Iterate active owners, run ingest_entity for each, regenerate data.json.

    Returns a summary dict. Per-owner failures are caught and recorded — they
    do NOT abort the run. The summary is also appended to PROVENANCE_LOG.md
    (unless dry_run).

    The CLI layer translates summary["owners_failed"] > 0 into a non-zero
    exit code.
    """
    refresh_id = uuid.uuid4().hex[:8]
    started_at = _utc_now_iso()
    summary: dict = {
        "refresh_id": refresh_id,
        "started_at": started_at,
        "completed_at": None,
        "dry_run": 1 if dry_run else 0,
        "owners_attempted": 0,
        "owners_succeeded": 0,
        "owners_failed": 0,
        "total_records_fetched": 0,
        "failed_owners": [],
        "per_owner": {},
        "data_json_regenerated": False,
    }

    with _acquire_lock(REFRESH_LOCK):
        owners = _list_active_owners(only=only)
        summary["owners_attempted"] = len(owners)
        if not owners:
            print("[refresh] No active owners to process.")

        new_records_seen = False

        for slug in owners:
            print(f"\n========== {slug} ==========")
            try:
                run_summary = ingest_entity(
                    slug,
                    dry_run=dry_run,
                    full_refetch=full_refetch,
                    chunk_by_cycle=chunk_by_cycle,
                )
                summary["owners_succeeded"] += 1
                summary["total_records_fetched"] += run_summary.get("records_fetched") or 0
                summary["per_owner"][slug] = {
                    "status": "ok",
                    "records_fetched": run_summary.get("records_fetched"),
                    "confirmed_count": run_summary.get("confirmed_count"),
                    "probable_count": run_summary.get("probable_count"),
                    "uncertain_count": run_summary.get("uncertain_count"),
                    "audit_last_ingestion_set": run_summary.get("audit_last_ingestion_set"),
                }
                if (run_summary.get("records_fetched") or 0) > 0 and not dry_run:
                    new_records_seen = True
            except Exception as e:
                # Per-owner failure isolation. GOVERNANCE.md §1.9 — prefer "try
                # again next time" (audit.last_ingestion stays put) over
                # silently skipping data.
                summary["owners_failed"] += 1
                summary["failed_owners"].append(slug)
                summary["per_owner"][slug] = {"status": "error", "error": str(e)}
                print(f"[{slug}] ERROR: {e}")
                continue

        # Regenerate data.json only if at least one owner pulled new records.
        # On dry-run, skip — nothing's in the DB.
        if (not dry_run) and (not skip_data_json) and new_records_seen:
            print("\n========== regenerating mockup/data.json ==========")
            ok, msg = _rebuild_data_json()
            summary["data_json_regenerated"] = ok
            if msg:
                summary["data_json_stdout" if ok else "data_json_error"] = msg
            if not ok:
                print(f"[refresh] data.json regen failed: {msg}")
        elif skip_data_json:
            print("\n[refresh] --skip-data-json: leaving mockup/data.json untouched.")
        elif not new_records_seen and not dry_run:
            print("\n[refresh] No new records this run; mockup/data.json untouched.")

    summary["completed_at"] = _utc_now_iso()
    if not dry_run:
        _append_refresh_log(summary)
    return summary
