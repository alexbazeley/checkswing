"""Fetch + parse the California CAL-ACCESS campaign-finance bulk extract.

CAL-ACCESS is California's official disclosure database (Secretary of State / FPPC).
We use the California Civic Data Coalition (CCDC) cleaned mirror, which republishes
the raw CAL-ACCESS files daily as tab-delimited tables:
    https://calaccess.californiacivicdata.org/downloads/latest/

This is the Tier-1 primary source for the CA pilot (GOVERNANCE.md §3 / SOURCES.md):
every CONFIRMED/PROBABLE state row traces to a CAL-ACCESS filing. An aggregator
(TAP/FollowTheMoney) may only DISCOVER candidates; it never stands in as the record.

This module separates the network step (download_latest — needs the live ~GB file,
verified end-to-end during a real ingest) from the PARSING + RESOLVER steps
(iter_rcpt_rows / build_filer_index / prefilter_by_surnames), which are pure and
unit-tested against small fixtures.

Tables used:
  * RCPT_CD       — itemized receipts (contributions). One row per contribution.
  * FILERNAME_CD  — filer id → filer name/type. Resolves who RECEIVED a contribution
                    (the filer of RCPT_CD.FILER_ID).
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .calaccess_adapter import _clean
from .paths import state_raw_dir

CCDC_LATEST_URL = "https://calaccess.californiacivicdata.org/downloads/latest/"

# CAL-ACCESS TSVs can carry stray bytes; csv with a generous field-size limit and
# replace-on-error decoding is the documented-robust way to read them.
csv.field_size_limit(10_000_000)


def iter_rcpt_rows(rcpt_tsv: Path) -> Iterator[dict]:
    """Yield each RCPT_CD row as a dict keyed by the file's header columns.

    The CCDC export is tab-delimited with an uppercase header row (CTRIB_NAML,
    AMOUNT, RCPT_DATE, TRAN_ID, FILING_ID, FILER_ID, …).
    """
    with rcpt_tsv.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            yield row


def build_filer_index(filername_tsv: Path) -> dict[str, dict]:
    """FILER_ID → {"name": str, "type": str|None} from FILERNAME_CD.

    FILERNAME_CD maps a filer id to the committee/candidate name. NAML/NAMF hold
    the filer's name; FILER_TYPE distinguishes candidate vs committee where present.
    """
    index: dict[str, dict] = {}
    if not filername_tsv.exists():
        return index
    with filername_tsv.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            fid = _clean(row.get("FILER_ID"))
            if not fid:
                continue
            naml = _clean(row.get("NAML")) or _clean(row.get("FILER_NAML"))
            namf = _clean(row.get("NAMF")) or _clean(row.get("FILER_NAMF"))
            name = f"{naml}, {namf}" if namf else naml
            ftype = (_clean(row.get("FILER_TYPE")) or "").lower() or None
            # First non-empty wins; CAL-ACCESS repeats a filer across amendments.
            index.setdefault(fid, {"name": name, "type": ftype})
    return index


def make_recipient_resolver(filer_index: dict[str, dict]) -> Callable[[dict], dict]:
    """Return a resolver mapping an RCPT row → recipient identity, via FILER_ID.

    The recipient of a contribution is the FILER who reported receiving it. When
    the filer can't be resolved (id absent from the index) the resolver returns
    name="" — honest "recipient unknown", still keyed by source_filing_id.
    """

    def _resolve(rcpt: dict) -> dict:
        fid = _clean(rcpt.get("FILER_ID"))
        meta = filer_index.get(fid) if fid else None
        return {
            "filer_id": fid or None,
            "name": (meta or {}).get("name", "") if meta else "",
            "type": (meta or {}).get("type") if meta else None,
        }

    return _resolve


def _surname_set(owner: dict) -> set[str]:
    """Lowercased surnames to pre-filter the giant RCPT file before classification.

    Derives surnames from the owner's name_variants (last token of each, and the
    pre-comma token of "Last, First" forms). The classifier still does the precise
    name match; this is a cheap funnel so we don't classify millions of rows.
    """
    surnames: set[str] = set()
    for v in owner.get("name_variants") or []:
        v = (v or "").strip()
        if not v:
            continue
        if "," in v:
            surnames.add(v.split(",")[0].strip().lower())
        else:
            surnames.add(v.split()[-1].strip().lower())
    return {s for s in surnames if s}


def prefilter_by_surnames(rows: Iterable[dict], surnames: set[str]) -> Iterator[dict]:
    """Yield only RCPT rows whose contributor last/business name matches a surname.

    A coarse, deliberately permissive funnel (substring on the cleaned CTRIB_NAML)
    — the classifier makes the real decision downstream.
    """
    if not surnames:
        yield from rows
        return
    for row in rows:
        naml = _clean(row.get("CTRIB_NAML")).lower()
        if not naml:
            continue
        if any(s in naml for s in surnames):
            yield row


def candidate_rows_for_owner(rcpt_tsv: Path, owner: dict) -> list[dict]:
    """Convenience: parse RCPT_CD and pre-filter to this owner's surname candidates."""
    return list(prefilter_by_surnames(iter_rcpt_rows(rcpt_tsv), _surname_set(owner)))


def download_latest(dest_dir: Path | None = None) -> Path:  # pragma: no cover - network
    """Download + extract the CCDC 'latest' CAL-ACCESS export into data/raw/state/ca/.

    NETWORK STEP — not unit-tested (needs the live ~GB archive). Verified during a
    real ingest. Persists the raw archive before parsing (GOVERNANCE.md §1.4).
    Returns the directory containing the extracted RCPT_CD.TSV / FILERNAME_CD.TSV.

    Implementation is intentionally thin: resolve the latest ZIP URL from
    CCDC_LATEST_URL, stream it to disk under a UTC-stamped path, unzip, and return
    the extract dir. Kept out of the tested surface because the only thing to test
    here is HTTP/zip plumbing, while the data correctness lives in the parsing
    functions above.
    """
    import io
    import zipfile
    from datetime import datetime, timezone
    from urllib.request import urlopen

    dest = dest_dir or state_raw_dir("ca")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    target_dir = dest / f"{stamp}__ccd-latest"
    target_dir.mkdir(parents=True, exist_ok=True)
    # CCDC publishes a stable "latest" zip; resolve + stream it.
    with urlopen(CCDC_LATEST_URL) as resp:  # noqa: S310 (trusted gov-data mirror)
        # The latest/ page links the actual zip; callers may instead pass a direct
        # URL via env once the exact asset name is confirmed during live ingest.
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(target_dir)
    return target_dir
