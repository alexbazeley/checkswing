"""OpenFEC API client.

Fetches /schedules/schedule_a/ records for each owner name variant.
Every raw response is persisted to data/raw/<slug>/<UTC>__schedule_a.json
BEFORE parsing (GOVERNANCE.md §1.4). The DB is reconstructible from raw alone.

Strategy: name-anchored fetch. By default, when the caller passes a `states`
list (the owner's documented residence states from verifying_signals.states),
the fetch passes those as `contributor_state` filters to FEC. This narrows
the result set ~10-20x while remaining name-anchored — not a violation of
GOVERNANCE.md §3's prohibition on employer-only aggregated queries.

For owners with multi-state residence (e.g., Henry, FL+MA), all states are
passed in one call; FEC ORs them. Donations filed with an out-of-state
address would be missed by this filter — tradeoff accepted in
PROVENANCE_LOG.md (Cohen broad-fetch abort, 2026-05-22).

To override (discovery mode), pass states=None.
"""
from __future__ import annotations

import json
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from .paths import raw_dir_for, relpath


BASE_URL = "https://api.open.fec.gov/v1"
SCHEDULE_A = "/schedules/schedule_a/"
PER_PAGE = 100  # FEC max
DEFAULT_MIN_DATE = "2000-01-01"

# Polite spacing between requests. FEC's per-key cap is 1,000/hour ≈ 3.6s/req.
# We target ~4.0s between requests because the weekly refresh now runs as 4
# parallel matrix jobs sharing one key — 4 workers × ~4s spacing ≈ 60 req/min
# combined, comfortably under the cap with retry/backoff headroom. Single-owner
# `cli ingest` invocations also use this spacing; the wall-clock penalty there
# is small relative to FEC response time.
MIN_REQUEST_INTERVAL_S = 4.0

# Auto-switch to cycle chunking when a name variant's first page reports more
# pages than this. Common-name + populous-state fetches (Cohen NY+CT, Malone
# CO, Davis TX) blow past this; cycle chunking keeps each fetch session
# bounded so a timeout in one cycle doesn't lose the others.
DEFAULT_AUTO_CHUNK_THRESHOLD = 20

# FEC's two_year_transaction_period is the canonical cycle bucket (even year
# of the cycle's end). Cycles start at 2000 for our scope.
EARLIEST_CYCLE = 2000

# A _fetch_state.json older than this is treated as stale and ignored unless
# --force-resume is passed. Catches the case where a developer abandons a
# fetch in progress and comes back weeks later expecting a fresh run.
CHECKPOINT_STALE_DAYS = 7


def _utc_now_filename() -> str:
    # Microsecond resolution so two persists inside the same second don't
    # collide and silently clobber the earlier raw payload. The DB rows from
    # the clobbered page point at a file that no longer contains their
    # transaction_ids — observed at 21% loss before this fix.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cycles_from(min_date: str) -> list[int]:
    """Return FEC two_year_transaction_period values to query from min_date forward.

    FEC cycles are even years (2000, 2002, …, 2026). A donation made in 2025
    rolls up to the 2026 cycle. We start at the cycle containing min_date and
    go through the next even year after today.
    """
    try:
        start_year = int(str(min_date)[:4])
    except (TypeError, ValueError):
        start_year = EARLIEST_CYCLE
    if start_year < EARLIEST_CYCLE:
        start_year = EARLIEST_CYCLE
    first_cycle = start_year if start_year % 2 == 0 else start_year + 1
    current_year = datetime.now(timezone.utc).year
    last_cycle = current_year if current_year % 2 == 0 else current_year + 1
    if last_cycle < first_cycle:
        last_cycle = first_cycle
    return list(range(first_cycle, last_cycle + 1, 2))


# ─── Checkpoint sidecar ─────────────────────────────────────────────────────


def _checkpoint_path(slug: str) -> Path:
    return raw_dir_for(slug) / "_fetch_state.json"


def _read_checkpoint(slug: str, force_resume: bool) -> dict | None:
    """Load data/raw/<slug>/_fetch_state.json if present and not stale.

    Returns None if no checkpoint, or if it's > CHECKPOINT_STALE_DAYS old and
    force_resume is False.
    """
    p = _checkpoint_path(slug)
    if not p.exists():
        return None
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[{slug}] Warning: could not parse {p}: {e}. Ignoring checkpoint.")
        return None
    started_at = state.get("started_at")
    if started_at:
        try:
            started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - started_dt).total_seconds() / 86400
            if age_days > CHECKPOINT_STALE_DAYS and not force_resume:
                print(
                    f"[{slug}] Checkpoint at {p.name} is {age_days:.1f}d old "
                    f"(>{CHECKPOINT_STALE_DAYS}d). Starting fresh; pass --force-resume to honor."
                )
                return None
        except ValueError:
            pass
    return state


def _write_checkpoint(slug: str, state: dict) -> None:
    p = _checkpoint_path(slug)
    p.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _delete_checkpoint(slug: str) -> None:
    p = _checkpoint_path(slug)
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass


class FECClient:
    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        load_dotenv()
        self.api_key = api_key or os.environ.get("FEC_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "FEC_API_KEY not set. Put it in .env or pass api_key= explicitly."
            )
        self.session = session or requests.Session()
        self._last_request_ts: float = 0.0
        self.calls_made = 0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < MIN_REQUEST_INTERVAL_S:
            time.sleep(MIN_REQUEST_INTERVAL_S - elapsed)

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        # Don't log api_key in error messages.
        safe_params = {k: v for k, v in params.items() if k != "api_key"}
        params = {**params, "api_key": self.api_key}
        last_exc: Exception | None = None
        # Up to 5 attempts on transient errors / 429 / read-timeouts.
        # FEC's API can be slow on broad queries (15-60 sec response times
        # observed), so we use a generous timeout and exponential backoff.
        for attempt in range(5):
            self._throttle()
            self._last_request_ts = time.monotonic()
            try:
                resp = self.session.get(BASE_URL + endpoint, params=params, timeout=120)
                self.calls_made += 1
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    time.sleep(min(retry_after, 120))
                    continue
                if resp.status_code >= 500:
                    # Jitter the exponential backoff so concurrent retries don't
                    # sync-hammer FEC.
                    time.sleep(2 ** attempt * 2 * random.uniform(0.7, 1.3))
                    continue
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as e:
                last_exc = e
                time.sleep(2 ** attempt * 2 * random.uniform(0.7, 1.3))
        raise RuntimeError(
            f"FEC request failed after 5 retries: {endpoint} {safe_params}: {last_exc}"
        )

    def _persist_raw(
        self,
        slug: str,
        params: dict,
        payload: dict,
        name_variant: str,
        page_count: int,
        cycle: int | None,
    ) -> Path:
        """Write one page's response to data/raw/<slug>/<timestamp>__schedule_a.json.

        GOVERNANCE.md §1.4 — raw payloads are written BEFORE parsing and are the
        ground truth. The DB is rebuildable from raw alone.
        """
        raw_path = raw_dir_for(slug) / f"{_utc_now_filename()}__schedule_a.json"
        envelope = {
            "_meta": {
                "endpoint": SCHEDULE_A,
                "params": {k: v for k, v in params.items() if k != "api_key"},
                "name_variant": name_variant,
                "slug": slug,
                "fetched_at": _utc_now_iso(),
                "page_count": page_count,
                "cycle": cycle,
            },
            "response": payload,
        }
        raw_path.write_text(json.dumps(envelope, indent=2, default=str))
        return raw_path

    def _paginate(
        self,
        slug: str,
        base_params: dict,
        name_variant: str,
        max_pages: int | None = None,
        cycle: int | None = None,
    ) -> tuple[list[dict], list[Path], dict]:
        """Paginate one base_params set via FEC's last_indexes cursor.

        Returns (records, raw_paths, first_page_pagination_meta). The third
        element lets the caller inspect `pages` from page 1 for auto-chunking.
        """
        records: list[dict] = []
        raw_paths: list[Path] = []
        params = dict(base_params)
        page_count = 0
        first_pagination: dict = {}

        while True:
            payload = self._request(SCHEDULE_A, params)
            page_count += 1
            raw_path = self._persist_raw(slug, params, payload, name_variant, page_count, cycle)
            raw_paths.append(raw_path)

            if page_count == 1:
                first_pagination = payload.get("pagination") or {}

            rel = relpath(raw_path)
            for r in payload.get("results", []) or []:
                r["_raw_payload_path"] = rel
                r["_name_variant_queried"] = name_variant
                records.append(r)

            if max_pages is not None and page_count >= max_pages:
                break

            pagination = payload.get("pagination") or {}
            last_indexes = pagination.get("last_indexes")
            results = payload.get("results") or []
            if not last_indexes or not results:
                break
            params.update(last_indexes)

        return records, raw_paths, first_pagination

    def _fetch_by_cycle(
        self,
        slug: str,
        name_variant: str,
        base_params: dict,
        min_date: str,
        max_pages: int | None,
        completed_cycles: set[int],
        on_cycle_complete,
    ) -> tuple[list[dict], list[Path]]:
        """Walk each FEC 2-year cycle as its own paginate session.

        Each cycle's pages live in their own raw payloads so partial failure
        across cycles is recoverable. completed_cycles is honored — those
        cycles are skipped (used during checkpoint resume).
        """
        records: list[dict] = []
        raw_paths: list[Path] = []
        for cycle in _cycles_from(min_date):
            if cycle in completed_cycles:
                print(f"[{slug}]     cycle {cycle}: skipped (checkpoint)")
                continue
            cycle_params = {**base_params, "two_year_transaction_period": cycle}
            cycle_records, cycle_raws, _ = self._paginate(
                slug, cycle_params, name_variant, max_pages=max_pages, cycle=cycle,
            )
            records.extend(cycle_records)
            raw_paths.extend(cycle_raws)
            if on_cycle_complete is not None:
                on_cycle_complete(cycle)
        return records, raw_paths

    def fetch_schedule_a_for_name(
        self,
        slug: str,
        name_variant: str,
        min_date: str = DEFAULT_MIN_DATE,
        max_pages: int | None = None,
        states: list[str] | None = None,
        chunk_by_cycle: bool = False,
        auto_chunk_threshold: int = DEFAULT_AUTO_CHUNK_THRESHOLD,
        completed_cycles: set[int] | None = None,
        on_cycle_complete=None,
    ) -> tuple[list[dict], list[Path]]:
        """Fetch all schedule_a records for one name variant.

        Returns (records, raw_payload_paths). Each record carries
        `_raw_payload_path` pointing back to the JSON file it came from.

        If `chunk_by_cycle=True`, fetch is split per FEC 2-year cycle so a
        timeout in one cycle doesn't abort the others. Otherwise unified
        pagination is attempted; if the first page reports more than
        `auto_chunk_threshold` total pages, fetch auto-switches to cycle
        mode for the remainder.

        `completed_cycles` + `on_cycle_complete` are the resume/checkpoint
        hooks called by fetch_all_name_variants. Direct callers can ignore
        them.
        """
        completed_cycles = completed_cycles or set()

        base_params: dict[str, Any] = {
            "contributor_name": name_variant,
            "min_date": min_date,
            "per_page": PER_PAGE,
            "sort": "-contribution_receipt_date",
        }
        if states:
            # requests serializes lists as repeated query params:
            # contributor_state=CT&contributor_state=NY (FEC ORs them).
            base_params["contributor_state"] = list(states)

        if chunk_by_cycle:
            return self._fetch_by_cycle(
                slug, name_variant, base_params, min_date, max_pages,
                completed_cycles, on_cycle_complete,
            )

        # Sniff page 1 to decide between unified pagination and cycle mode.
        sniff_records, sniff_raws, first_pagination = self._paginate(
            slug, base_params, name_variant, max_pages=1, cycle=None,
        )
        total_pages = (first_pagination or {}).get("pages") or 0

        if total_pages > auto_chunk_threshold:
            print(
                f"[{slug}]   {name_variant!r} reports {total_pages} pages "
                f"(> {auto_chunk_threshold}) — switching to cycle chunking"
            )
            # Discard the sniff records (cycle fetch will refetch them via the
            # cycle param); the raw payload stays on disk per §1.4.
            cycle_records, cycle_raws = self._fetch_by_cycle(
                slug, name_variant, base_params, min_date, max_pages,
                completed_cycles, on_cycle_complete,
            )
            return cycle_records, sniff_raws + cycle_raws

        # Continue unified pagination from page 2 using page 1's last_indexes.
        last_indexes = (first_pagination or {}).get("last_indexes")
        if last_indexes and sniff_records and (max_pages is None or max_pages > 1):
            cont_params = {**base_params, **last_indexes}
            remaining = (max_pages - 1) if max_pages is not None else None
            more_records, more_raws, _ = self._paginate(
                slug, cont_params, name_variant, max_pages=remaining, cycle=None,
            )
            return sniff_records + more_records, sniff_raws + more_raws

        return sniff_records, sniff_raws

    def fetch_all_name_variants(
        self,
        slug: str,
        name_variants: list[str],
        min_date: str = DEFAULT_MIN_DATE,
        max_pages: int | None = None,
        states: list[str] | None = None,
        chunk_by_cycle: bool = False,
        force_resume: bool = False,
    ) -> tuple[list[dict], list[Path]]:
        """Fetch all schedule_a records for every name variant; dedupe by transaction_id.

        Variants that differ only in punctuation (e.g., "Steven A Cohen" vs
        "Steven A. Cohen") would produce identical FEC search results — FEC's
        contributor_name search strips punctuation. We dedupe variants at the
        FEC-search-canonical level so we don't waste API calls. The full
        original list is still recorded in ingestion_runs.name_variants_queried.

        Records keep the `_raw_payload_path` from the FIRST raw payload they
        appeared in — that path is the authoritative reference for that row.

        Checkpoint behavior: progress is persisted to
        data/raw/<slug>/_fetch_state.json after each variant and after each
        cycle (when chunking). On entry, a non-stale checkpoint skips already
        completed (variant, cycle) work. The checkpoint is deleted on full
        success. `force_resume=True` honors a checkpoint older than the
        staleness threshold.
        """
        # Dedupe variants by FEC-search-canonical form.
        seen_canon: set[str] = set()
        unique_variants: list[str] = []
        for v in name_variants:
            canon = re.sub(r"\s+", " ", v.lower().replace(".", " ").replace(",", " ")).strip()
            if canon and canon not in seen_canon:
                seen_canon.add(canon)
                unique_variants.append(v)

        # Load checkpoint (or start fresh).
        checkpoint = _read_checkpoint(slug, force_resume)
        if checkpoint is None:
            checkpoint = {
                "slug": slug,
                "started_at": _utc_now_iso(),
                "completed_variants": [],
                "completed_cycles_by_variant": {},
            }
        else:
            print(
                f"[{slug}] Resuming from checkpoint (started {checkpoint.get('started_at')}, "
                f"{len(checkpoint.get('completed_variants') or [])} variant(s) done)"
            )
        completed_variants: set[str] = set(checkpoint.get("completed_variants") or [])
        completed_cycles_by_variant: dict = dict(checkpoint.get("completed_cycles_by_variant") or {})

        all_records: list[dict] = []
        all_raws: list[Path] = []
        for variant in unique_variants:
            if variant in completed_variants:
                print(f"[{slug}]   variant {variant!r}: skipped (checkpoint)")
                continue
            print(f"[{slug}]   variant {variant!r}…", flush=True)

            done_for_variant = set(completed_cycles_by_variant.get(variant) or [])

            def _on_cycle_complete(cycle: int, _v=variant) -> None:
                lst = completed_cycles_by_variant.setdefault(_v, [])
                if cycle not in lst:
                    lst.append(cycle)
                checkpoint["completed_cycles_by_variant"] = completed_cycles_by_variant
                _write_checkpoint(slug, checkpoint)

            recs, raws = self.fetch_schedule_a_for_name(
                slug,
                variant,
                min_date=min_date,
                max_pages=max_pages,
                states=states,
                chunk_by_cycle=chunk_by_cycle,
                completed_cycles=done_for_variant,
                on_cycle_complete=_on_cycle_complete,
            )
            all_records.extend(recs)
            all_raws.extend(raws)

            completed_variants.add(variant)
            checkpoint["completed_variants"] = sorted(completed_variants)
            _write_checkpoint(slug, checkpoint)

        # Dedup by transaction_id.
        seen: dict[str, dict] = {}
        for r in all_records:
            key = r.get("transaction_id") or r.get("sub_id")
            if not key:
                # Skip records FEC didn't give us a stable key for — they'd
                # break idempotency (GOVERNANCE.md §1.5). They're still in the raw
                # payload for forensic recovery if needed.
                continue
            key = str(key)
            if key not in seen:
                seen[key] = r

        # All variants completed cleanly — drop the checkpoint.
        _delete_checkpoint(slug)

        return list(seen.values()), all_raws


def load_raw_payloads(slug: str, raw_dir: Path | None = None) -> tuple[list[dict], list[Path]]:
    """Read records straight from on-disk raw payloads — no network calls.

    Used when an earlier fetch wrote raw JSONs but the run aborted before
    classification. Honors GOVERNANCE.md §1.4: the DB is reconstructible from raw
    alone, and this is the function that does the reconstruction.

    Returns (records, raw_paths). Records are deduped by transaction_id
    just like the fetch path, and each carries `_raw_payload_path`.
    """
    base = raw_dir or raw_dir_for(slug)
    # Skip sidecar files (e.g., _fetch_state.json) — they are not FEC payloads.
    raw_paths = sorted(p for p in base.glob("*.json") if not p.name.startswith("_"))
    all_records: list[dict] = []
    for p in raw_paths:
        try:
            envelope = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        response = envelope.get("response") or envelope
        meta = envelope.get("_meta") or {}
        # Use module-level relpath (tests monkeypatch this for tmp_path).
        rel = relpath(p)
        variant = meta.get("name_variant", "")
        for r in response.get("results", []) or []:
            r["_raw_payload_path"] = rel
            r["_name_variant_queried"] = variant
            all_records.append(r)

    seen: dict[str, dict] = {}
    for r in all_records:
        key = r.get("transaction_id") or r.get("sub_id")
        if not key:
            continue
        key = str(key)
        if key not in seen:
            seen[key] = r
    return list(seen.values()), raw_paths
