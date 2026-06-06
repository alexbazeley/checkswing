"""Fetch + parse the Pennsylvania DOS full campaign-finance export.

The PA Department of State publishes per-cycle exports as a zip of comma-delimited,
header-bearing files: `* ECF Contribution.txt` (contributions) and `* ECF Filer.txt`
(the filer/recipient index, carrying FILERNAME / PARTY / OFFICE).

Parsing + the recipient join are pure and unit-tested against small fixtures; the
network download is the only untested surface (same split as the CA fetcher).

A contribution row can hold up to three (CONTDATEn, CONTAMTn) pairs; `iter_contributions`
explodes them into one logical contribution each, pre-joins the recipient from the
filer index, and stamps a stable content-hash id (`_tran`) since PA has no native
per-contribution id.
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .pa_adapter import _clean

csv.field_size_limit(10_000_000)

# A PA-DOS cycle zip contains files like "May 2024 ECF Contribution.txt" /
# "... ECF Filer.txt"; match by the stable suffix, case-insensitively.
CONTRIB_SUFFIX = "ecf contribution.txt"
FILER_SUFFIX = "ecf filer.txt"


def find_pa_file(extract_dir: Path, suffix: str) -> Path | None:
    for p in sorted(extract_dir.rglob("*")):
        if p.is_file() and p.name.lower().endswith(suffix):
            return p
    return None


def _dictreader(path: Path):
    # utf-8-sig strips the BOM PA prepends to the header's first column name.
    fh = path.open("r", encoding="utf-8-sig", errors="replace", newline="")
    return fh, csv.DictReader(fh)


def build_filer_index(filer_txt: Path) -> dict[str, dict]:
    """FILERID → {name, party, office, type} from the PA Filer file."""
    index: dict[str, dict] = {}
    if not filer_txt or not filer_txt.exists():
        return index
    fh, reader = _dictreader(filer_txt)
    try:
        for row in reader:
            fid = _clean(row.get("FILERID") or row.get("FilerID"))
            if not fid:
                continue
            index.setdefault(fid, {
                "name": _clean(row.get("FILERNAME")),
                "party": _clean(row.get("PARTY")),
                "office": _clean(row.get("OFFICE")),
                "type": (_clean(row.get("FILERTYPE")) or "").lower() or None,
            })
    finally:
        fh.close()
    return index


def _content_tran(filing_id: str, contributor: str, ename: str, date: str, amount: str, idx: int) -> str:
    h = hashlib.sha1(f"{filing_id}|{contributor}|{ename}|{date}|{amount}|{idx}".encode("utf-8"))
    return h.hexdigest()[:16]


def iter_contributions(contrib_txt: Path, filer_index: dict[str, dict] | None = None) -> Iterator[dict]:
    """Yield one exploded, recipient-joined contribution dict per (row, amount pair)."""
    filer_index = filer_index or {}
    fh, reader = _dictreader(contrib_txt)
    try:
        for row in reader:
            fid = _clean(row.get("FilerID") or row.get("FILERID"))
            filer = filer_index.get(fid, {})
            contributor = _clean(row.get("CONTRIBUTOR"))
            ename = _clean(row.get("ENAME"))
            cf_id = _clean(row.get("CampaignFinanceID"))
            for idx in (1, 2, 3):
                amt = _clean(row.get(f"CONTAMT{idx}"))
                dt = _clean(row.get(f"CONTDATE{idx}"))
                if not amt and not dt:
                    continue
                out = dict(row)
                out["_amount"] = amt
                out["_date"] = dt
                out["_tran"] = _content_tran(cf_id, contributor, ename, dt, amt, idx)
                out["_recipient_name"] = filer.get("name", "")
                out["_recipient_party"] = filer.get("party", "")
                out["_recipient_office"] = filer.get("office", "")
                out["_filer_type"] = filer.get("type")
                yield out
    finally:
        fh.close()


def make_recipient_resolver() -> Callable[[dict], dict]:
    """Recipient is pre-joined onto each row by iter_contributions; just read it."""

    def _resolve(row: dict) -> dict:
        return {
            "filer_id": _clean(row.get("FilerID") or row.get("FILERID")) or None,
            "name": _clean(row.get("_recipient_name")),
            "type": row.get("_filer_type"),
        }

    return _resolve


def _surname_set(owner: dict) -> set[str]:
    surnames: set[str] = set()
    for v in owner.get("name_variants") or []:
        v = (v or "").strip()
        if not v:
            continue
        surnames.add((v.split(",")[0] if "," in v else v.split()[-1]).strip().lower())
    return {s for s in surnames if s}


def bucket_rows_by_owner(rows: Iterable[dict], owners: list[tuple[str, dict]]) -> dict[str, list[dict]]:
    """Single pass over contributions → {slug: [candidate rows]} across owners."""
    from .pa_adapter import surname_of

    surname_map = [(slug, _surname_set(o)) for slug, o in owners]
    buckets: dict[str, list[dict]] = {slug: [] for slug, _ in owners}
    for row in rows:
        sn = surname_of(row)
        if not sn:
            continue
        for slug, sns in surname_map:
            if sns and any(s in sn for s in sns):
                buckets[slug].append(row)
    return buckets


def dedupe(rows: Iterable[dict]) -> list[dict]:
    """Dedup on the content-hash tran id (idempotent across re-exports)."""
    seen: dict[str, dict] = {}
    for row in rows:
        seen.setdefault(_clean(row.get("_tran")), row)
    return list(seen.values())


def download_latest(dest: Path | None = None, url: str | None = None) -> Path:  # pragma: no cover - network
    """Download a PA DOS export zip. The live post-migration URL is TBD (the old
    dos.pa.gov path 302s after the pa.gov migration); pass `url` explicitly until the
    new bulk endpoint is confirmed. Returns the zip path (extract separately)."""
    import shutil
    from urllib.request import urlopen

    from .paths import state_raw_dir
    if not url:
        raise ValueError("PA bulk download URL not configured — pass url= explicitly.")
    out_dir = dest or state_raw_dir("pa")
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / "pa_export.zip"
    with urlopen(url) as resp, zip_path.open("wb") as out:  # noqa: S310
        shutil.copyfileobj(resp, out, length=1024 * 1024)
    return zip_path
