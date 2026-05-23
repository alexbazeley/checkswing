"""OpenFEC API client.

Fetches /schedules/schedule_a/ records for each owner name variant.
Every raw response is persisted to data/raw/<slug>/<UTC>__schedule_a.json
BEFORE parsing (CLAUDE.md §1.4). The DB is reconstructible from raw alone.

Strategy: name-anchored fetch. By default, when the caller passes a `states`
list (the owner's documented residence states from verifying_signals.states),
the fetch passes those as `contributor_state` filters to FEC. This narrows
the result set ~10-20x while remaining name-anchored — not a violation of
CLAUDE.md §3's prohibition on employer-only aggregated queries.

For owners with multi-state residence (e.g., Henry, FL+MA), all states are
passed in one call; FEC ORs them. Donations filed with an out-of-state
address would be missed by this filter — tradeoff accepted in
PROVENANCE_LOG.md (Cohen broad-fetch abort, 2026-05-22).

To override (discovery mode), pass states=None.
"""
from __future__ import annotations

import json
import os
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

# Polite spacing between requests. FEC default cap is 1,000/hour ≈ 3.6s/req.
# We target ~1.2s between requests with 429 backoff. 7 name variants × ~10 pages
# = ~70 calls for Cohen, well under the cap.
MIN_REQUEST_INTERVAL_S = 1.2


def _utc_now_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
                    time.sleep(2 ** attempt * 2)
                    continue
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as e:
                last_exc = e
                time.sleep(2 ** attempt * 2)
        raise RuntimeError(
            f"FEC request failed after 5 retries: {endpoint} {safe_params}: {last_exc}"
        )

    def fetch_schedule_a_for_name(
        self,
        slug: str,
        name_variant: str,
        min_date: str = DEFAULT_MIN_DATE,
        max_pages: int | None = None,
        states: list[str] | None = None,
    ) -> tuple[list[dict], list[Path]]:
        """Fetch all schedule_a records for one name variant.

        Returns (records, raw_payload_paths). Each record has a synthetic
        `_raw_payload_path` field pointing back to the JSON file it came from.

        If `states` is provided (e.g., ["CT", "NY"]), the FEC search is
        narrowed to records with `contributor_state` in that list.
        """
        records: list[dict] = []
        raw_paths: list[Path] = []
        params: dict[str, Any] = {
            "contributor_name": name_variant,
            "min_date": min_date,
            "per_page": PER_PAGE,
            "sort": "-contribution_receipt_date",
        }
        if states:
            # requests serializes lists as repeated query params:
            # contributor_state=CT&contributor_state=NY (FEC ORs them).
            params["contributor_state"] = list(states)
        page_count = 0
        while True:
            payload = self._request(SCHEDULE_A, params)
            page_count += 1

            raw_path = raw_dir_for(slug) / f"{_utc_now_filename()}__schedule_a.json"
            envelope = {
                "_meta": {
                    "endpoint": SCHEDULE_A,
                    "params": {k: v for k, v in params.items() if k != "api_key"},
                    "name_variant": name_variant,
                    "slug": slug,
                    "fetched_at": _utc_now_iso(),
                    "page_count": page_count,
                },
                "response": payload,
            }
            raw_path.write_text(json.dumps(envelope, indent=2, default=str))
            raw_paths.append(raw_path)

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
            # Stable deep pagination: pass last_indexes back as params.
            params.update(last_indexes)

        return records, raw_paths

    def fetch_all_name_variants(
        self,
        slug: str,
        name_variants: list[str],
        min_date: str = DEFAULT_MIN_DATE,
        max_pages: int | None = None,
        states: list[str] | None = None,
    ) -> tuple[list[dict], list[Path]]:
        """Fetch all schedule_a records for every name variant; dedupe by transaction_id.

        Variants that differ only in punctuation (e.g., "Steven A Cohen" vs
        "Steven A. Cohen") would produce identical FEC search results — FEC's
        contributor_name search strips punctuation. We dedupe variants at the
        FEC-search-canonical level so we don't waste API calls. The full
        original list is still recorded in ingestion_runs.name_variants_queried.

        Records keep the `_raw_payload_path` from the FIRST raw payload they
        appeared in — that path is the authoritative reference for that row.
        """
        # Dedupe variants by FEC-search-canonical form.
        seen_canon: set[str] = set()
        unique_variants: list[str] = []
        for v in name_variants:
            canon = re.sub(r"\s+", " ", v.lower().replace(".", " ").replace(",", " ")).strip()
            if canon and canon not in seen_canon:
                seen_canon.add(canon)
                unique_variants.append(v)

        all_records: list[dict] = []
        all_raws: list[Path] = []
        for variant in unique_variants:
            print(f"[{slug}]   variant {variant!r}…", flush=True)
            recs, raws = self.fetch_schedule_a_for_name(
                slug, variant, min_date=min_date, max_pages=max_pages, states=states
            )
            all_records.extend(recs)
            all_raws.extend(raws)

        seen: dict[str, dict] = {}
        for r in all_records:
            key = r.get("transaction_id") or r.get("sub_id")
            if not key:
                # Skip records FEC didn't give us a stable key for — they'd
                # break idempotency (CLAUDE.md §1.5). They're still in the raw
                # payload for forensic recovery if needed.
                continue
            key = str(key)
            if key not in seen:
                seen[key] = r
        return list(seen.values()), all_raws


def load_raw_payloads(slug: str, raw_dir: Path | None = None) -> tuple[list[dict], list[Path]]:
    """Read records straight from on-disk raw payloads — no network calls.

    Used when an earlier fetch wrote raw JSONs but the run aborted before
    classification. Honors CLAUDE.md §1.4: the DB is reconstructible from raw
    alone, and this is the function that does the reconstruction.

    Returns (records, raw_paths). Records are deduped by transaction_id
    just like the fetch path, and each carries `_raw_payload_path`.
    """
    from .paths import raw_dir_for, relpath as _relpath

    base = raw_dir or raw_dir_for(slug)
    raw_paths = sorted(base.glob("*.json"))
    all_records: list[dict] = []
    for p in raw_paths:
        try:
            envelope = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        response = envelope.get("response") or envelope
        meta = envelope.get("_meta") or {}
        rel = _relpath(p)
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
