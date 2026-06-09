"""Congress.gov API client for bill metadata (Phase 3, Tier-1).

Fetches bill identity, sponsors, cosponsors, latest action, and enacted status
from api.congress.gov (official Library of Congress / GPO). Congress.gov is
fronted by api.data.gov, so the same key system as FEC — CONGRESS_API_KEY, with a
fallback to FEC_API_KEY (an api.data.gov key works for both).

Network and parsing are separated: parse_bill() / parse_sponsors() are pure
functions over the API's JSON so they are unit-testable without a network call.
Raw responses are persisted before parsing (GOVERNANCE.md §1.4).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from .paths import legislation_raw_dir, relpath


BASE_URL = "https://api.congress.gov/v3"
MIN_REQUEST_INTERVAL_S = 1.0  # api.data.gov default is 1,000/hour; be polite.


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")


class CongressClient:
    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        load_dotenv(os.path.join(os.getcwd(), ".env"))
        self.api_key = api_key or os.environ.get("CONGRESS_API_KEY") or os.environ.get("FEC_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "No Congress API key. Set CONGRESS_API_KEY (or FEC_API_KEY — an "
                "api.data.gov key works for both) in .env."
            )
        self.session = session or requests.Session()
        self._last_request_ts = 0.0
        self.calls_made = 0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < MIN_REQUEST_INTERVAL_S:
            time.sleep(MIN_REQUEST_INTERVAL_S - elapsed)

    def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = {**(params or {}), "api_key": self.api_key, "format": "json"}
        last_exc: Exception | None = None
        for attempt in range(4):
            self._throttle()
            self._last_request_ts = time.monotonic()
            try:
                resp = self.session.get(BASE_URL + path, params=params, timeout=60)
                self.calls_made += 1
                if resp.status_code == 429:
                    time.sleep(int(resp.headers.get("Retry-After", "30")))
                    continue
                if resp.status_code == 404:
                    raise FileNotFoundError(f"Congress.gov 404: {path}")
                if resp.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                return resp.json()
            except FileNotFoundError:
                raise
            except (requests.RequestException, ValueError) as e:
                last_exc = e
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Congress.gov request failed after retries: {path}: {last_exc}")

    def _persist_raw(self, label: str, path: str, payload: dict) -> Path:
        raw_path = legislation_raw_dir() / f"{_utc_now_filename()}__{label}.json"
        envelope = {
            "_meta": {"endpoint": path, "fetched_at": _utc_now_iso(), "source": "congress.gov"},
            "response": payload,
        }
        raw_path.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
        return raw_path

    def fetch_bill(self, congress: int, bill_type: str, number: int) -> tuple[dict, Path]:
        """GET /bill/{congress}/{type}/{number}. Returns (raw_response, raw_path)."""
        path = f"/bill/{congress}/{bill_type.lower()}/{number}"
        payload = self._request(path)
        raw_path = self._persist_raw(f"bill-{congress}-{bill_type.lower()}-{number}", path, payload)
        return payload, raw_path

    def fetch_cosponsors(self, congress: int, bill_type: str, number: int) -> tuple[list[dict], Path]:
        """GET /bill/{congress}/{type}/{number}/cosponsors. Returns (cosponsors, raw_path)."""
        path = f"/bill/{congress}/{bill_type.lower()}/{number}/cosponsors"
        payload = self._request(path, params={"limit": 250})
        raw_path = self._persist_raw(
            f"cosponsors-{congress}-{bill_type.lower()}-{number}", path, payload
        )
        return payload.get("cosponsors") or [], raw_path

    def fetch_bill_committees(self, congress: int, bill_type: str, number: int) -> tuple[list[dict], Path]:
        """GET /bill/{congress}/{type}/{number}/committees. Returns (committees, raw_path).

        These are the committee(s) of referral — the join target for
        committee-membership donation surfaces.
        """
        path = f"/bill/{congress}/{bill_type.lower()}/{number}/committees"
        payload = self._request(path, params={"limit": 250})
        raw_path = self._persist_raw(
            f"committees-{congress}-{bill_type.lower()}-{number}", path, payload
        )
        return payload.get("committees") or [], raw_path


def parse_bill(raw: dict, *, raw_payload_path: str | None = None) -> dict:
    """Parse a /bill response into bills-table fields (the FEC-neutral subset).

    Does NOT set mlb_issue_area / relevance_basis — those come from the curated
    YAML, not the API. Returns the API-sourced fields only.
    """
    bill = raw.get("bill") or {}
    congress = bill.get("congress")
    bill_type = (bill.get("type") or "").lower()
    number = bill.get("number")
    laws = bill.get("laws") or []
    latest = bill.get("latestAction") or {}
    titles = bill.get("title")
    congress_url = bill.get("url")
    # Build a human congress.gov web URL from the canonical parts.
    web_url = None
    if congress and bill_type and number is not None:
        type_path = {
            "hr": "house-bill", "s": "senate-bill",
            "hjres": "house-joint-resolution", "sjres": "senate-joint-resolution",
            "hconres": "house-concurrent-resolution", "sconres": "senate-concurrent-resolution",
            "hres": "house-resolution", "sres": "senate-resolution",
        }.get(bill_type, bill_type)
        web_url = f"https://www.congress.gov/bill/{congress}th-congress/{type_path}/{number}"

    return {
        "congress": int(congress) if congress is not None else None,
        "bill_type": bill_type,
        "number": int(number) if number is not None else None,
        "title": titles,
        "introduced_date": bill.get("introducedDate"),
        "latest_action": latest.get("text"),
        "latest_action_date": latest.get("actionDate"),
        "enacted": 1 if laws else 0,
        "congress_dot_gov_url": web_url or congress_url,
        "source": "congress.gov",
        "raw_payload_path": raw_payload_path,
        "fetched_at": _utc_now_iso(),
    }


def parse_sponsors(raw_bill: dict, cosponsors: list[dict]) -> list[dict]:
    """Parse sponsor + cosponsor bioguide rows from a bill + its cosponsor list.

    Returns rows of {bioguide_id, role} (role ∈ {'sponsor','cosponsor'}), deduped,
    skipping any entry without a bioguide id.
    """
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    bill = raw_bill.get("bill") or {}
    for sp in bill.get("sponsors") or []:
        bid = sp.get("bioguideId")
        if bid and ("sponsor", bid) not in seen:
            seen.add(("sponsor", bid))
            rows.append({"bioguide_id": bid, "role": "sponsor"})
    for cs in cosponsors or []:
        bid = cs.get("bioguideId")
        if bid and ("cosponsor", bid) not in seen and ("sponsor", bid) not in seen:
            seen.add(("cosponsor", bid))
            rows.append({"bioguide_id": bid, "role": "cosponsor"})
    return rows


def _system_code_to_thomas_id(system_code: str | None) -> str | None:
    """Map a Congress.gov committee systemCode to a congress-legislators thomas_id.

    Full-committee codes are the thomas_id lower-cased with a trailing '00'
    (e.g. ssju00 → SSJU); subcommittee codes carry a non-zero suffix (ssju01).
    Stripping the trailing two digits and upper-casing yields the parent full
    committee, which is the membership-join target. Returns None if unmappable.
    """
    if not system_code or len(system_code) < 3:
        return None
    return system_code[:-2].upper() if system_code[-2:].isdigit() else system_code.upper()


def parse_bill_committees(committees: list[dict]) -> list[dict]:
    """Parse the /bill/.../committees list into bill_committees rows.

    Returns {system_code, thomas_id, chamber, name}, deduped on system_code.
    """
    rows: list[dict] = []
    seen: set[str] = set()
    for c in committees or []:
        if not isinstance(c, dict):
            continue
        sc = c.get("systemCode")
        if not sc or sc in seen:
            continue
        seen.add(sc)
        rows.append(
            {
                "system_code": sc,
                "thomas_id": _system_code_to_thomas_id(sc),
                "chamber": (c.get("chamber") or "").lower() or None,
                "name": c.get("name"),
            }
        )
    return rows
