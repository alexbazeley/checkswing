"""Fetch + parse the Pennsylvania DOS full campaign-finance export.

The PA Department of State publishes the "Full Campaign Finance Export" as one
**per-year zip** (`<YEAR>.zip`) at pa.gov. Each zip holds five comma-delimited,
header-bearing files; we read two:

  * `contrib_<YEAR>.txt` — itemized contributions
  * `filer_<YEAR>.txt`   — the filer/recipient index (FILERNAME / PARTY / OFFICE)

(the other three — `receipt_*`, `expense_*`, `debt_*` — are ignored).

Note (2026 migration): the export moved from the old dos.pa.gov "ECF" naming
(`* ECF Contribution.txt`) to the pa.gov `contrib_<YEAR>.txt` convention and to
**one zip per cycle year**. The matchers below accept both names so a stray
older extract still parses, but the live format is the per-year zip.

Parsing + the recipient join are pure and unit-tested against small fixtures;
the network download is the only untested surface (same split as the CA fetcher).

A contribution row can hold up to three (CONTDATEn, CONTAMTn) pairs;
`iter_contributions` explodes them into one logical contribution each, pre-joins
the recipient from the filer index, and stamps a stable content-hash id (`_tran`)
since PA has no native per-contribution id.

Coverage is multi-year: point the ingest at a directory holding several
`<YEAR>.zip` files and every year is streamed in one pass, the filer index merged
across all years (GOVERNANCE.md §1.4 — the downloaded zips are the ground truth,
gitignored under data/raw/state/pa/).
"""
from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from pathlib import Path
from typing import Callable, Iterable, Iterator, TextIO

from .pa_adapter import _clean

csv.field_size_limit(10_000_000)

# A PA cycle zip member is "contrib_2026.txt" / "filer_2026.txt" (current pa.gov)
# or "May 2024 ECF Contribution.txt" / "... ECF Filer.txt" (pre-2026 dos.pa.gov).
# Match by the stable token, case-insensitively, so both conventions parse.
PA_CDN_BASE = (
    "https://www.pa.gov/content/dam/copapwp-pagov/en/dos/resources/"
    "voting-and-elections/campaign-finance/campaign-finance-data"
)


def _is_contrib_name(name: str) -> bool:
    n = name.lower()
    return n.endswith(".txt") and (n.split("/")[-1].startswith("contrib") or n.endswith("ecf contribution.txt"))


def _is_filer_name(name: str) -> bool:
    n = name.lower()
    return n.endswith(".txt") and (n.split("/")[-1].startswith("filer") or n.endswith("ecf filer.txt"))


# ── Pure parsers (operate on a text handle; path/zip wrappers below) ──────────

def _reader(fh: TextIO) -> csv.DictReader:
    return csv.DictReader(fh)


def build_filer_index_fh(fh: TextIO, index: dict[str, dict] | None = None) -> dict[str, dict]:
    """FILERID → {name, party, office, type} from a PA Filer handle.

    `index` lets callers accumulate across multiple years' filer files; the first
    occurrence of a FILERID wins (setdefault), which is fine — filer identity is
    stable across cycles.
    """
    index = {} if index is None else index
    for row in _reader(fh):
        fid = _clean(row.get("FILERID") or row.get("FilerID"))
        if not fid:
            continue
        index.setdefault(fid, {
            "name": _clean(row.get("FILERNAME")),
            "party": _clean(row.get("PARTY")),
            "office": _clean(row.get("OFFICE")),
            "type": (_clean(row.get("FILERTYPE")) or "").lower() or None,
        })
    return index


def _content_tran(filing_id: str, contributor: str, ename: str, date: str, amount: str, idx: int) -> str:
    h = hashlib.sha1(f"{filing_id}|{contributor}|{ename}|{date}|{amount}|{idx}".encode("utf-8"))
    return h.hexdigest()[:16]


def iter_contributions_fh(fh: TextIO, filer_index: dict[str, dict] | None = None) -> Iterator[dict]:
    """Yield one exploded, recipient-joined contribution dict per (row, amount pair)."""
    filer_index = filer_index or {}
    for row in _reader(fh):
        fid = _clean(row.get("FilerID") or row.get("FILERID"))
        filer = filer_index.get(fid, {})
        contributor = _clean(row.get("CONTRIBUTOR"))
        ename = _clean(row.get("ENAME"))
        cf_id = _clean(row.get("CampaignFinanceID") or row.get("CampaignfinanceID"))
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


# ── Path wrappers (kept for fixtures + extracted-dir use) ─────────────────────

def _open_text(path: Path) -> TextIO:
    # utf-8-sig strips the BOM PA prepends to the header's first column name.
    return path.open("r", encoding="utf-8-sig", errors="replace", newline="")


def find_pa_file(extract_dir: Path, kind: str) -> Path | None:
    """First contrib/filer file under a dir. `kind` is 'contrib' or 'filer'."""
    pred = _is_contrib_name if kind == "contrib" else _is_filer_name
    for p in sorted(extract_dir.rglob("*")):
        if p.is_file() and pred(p.name):
            return p
    return None


def find_pa_files(extract_dir: Path, kind: str) -> list[Path]:
    """ALL contrib/filer files under a dir (multi-year), sorted."""
    pred = _is_contrib_name if kind == "contrib" else _is_filer_name
    return [p for p in sorted(extract_dir.rglob("*")) if p.is_file() and pred(p.name)]


def build_filer_index(filer_txt: Path, index: dict[str, dict] | None = None) -> dict[str, dict]:
    if not filer_txt or not filer_txt.exists():
        return {} if index is None else index
    fh = _open_text(filer_txt)
    try:
        return build_filer_index_fh(fh, index)
    finally:
        fh.close()


def iter_contributions(contrib_txt: Path, filer_index: dict[str, dict] | None = None) -> Iterator[dict]:
    fh = _open_text(contrib_txt)
    try:
        yield from iter_contributions_fh(fh, filer_index)
    finally:
        fh.close()


# ── Zip streaming (the live per-year format) ─────────────────────────────────

def _member_text(zf: zipfile.ZipFile, name: str) -> TextIO:
    # Decode the member as a text stream so the pure parsers read it directly
    # without extracting to disk (CI disk-friendly, mirrors CA/TX).
    raw = zf.open(name, "r")
    return io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")


def build_filer_index_from_zip(zip_path: Path, index: dict[str, dict] | None = None) -> dict[str, dict]:
    index = {} if index is None else index
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if _is_filer_name(name):
                fh = _member_text(zf, name)
                try:
                    build_filer_index_fh(fh, index)
                finally:
                    fh.close()
    return index


def iter_contributions_from_zip(zip_path: Path, filer_index: dict[str, dict] | None = None) -> Iterator[dict]:
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if _is_contrib_name(name):
                fh = _member_text(zf, name)
                try:
                    yield from iter_contributions_fh(fh, filer_index)
                finally:
                    fh.close()


def find_pa_zips(input_dir: Path) -> list[Path]:
    return [p for p in sorted(input_dir.rglob("*.zip")) if p.is_file()]


def iter_dir(input_dir: Path) -> Iterator[dict]:
    """Stream every contribution across an input dir, recipient-joined.

    Accepts either per-year `<YEAR>.zip` files (the live pa.gov format) or already
    extracted `contrib_*.txt`/`filer_*.txt` files. The filer index is built across
    ALL years first so a contribution in any year resolves its recipient.
    """
    input_dir = Path(input_dir)
    zips = find_pa_zips(input_dir)
    if zips:
        filer_index: dict[str, dict] = {}
        for z in zips:
            build_filer_index_from_zip(z, filer_index)
        for z in zips:
            yield from iter_contributions_from_zip(z, filer_index)
        return
    # Extracted-dir fallback (used by fixtures and manual extracts).
    filer_index = {}
    for f in find_pa_files(input_dir, "filer"):
        build_filer_index(f, filer_index)
    for c in find_pa_files(input_dir, "contrib"):
        yield from iter_contributions(c, filer_index)


# ── Recipient resolver / bucketing / dedup ───────────────────────────────────

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


# ── Download (live pa.gov per-year zips) ─────────────────────────────────────

def year_url(year: int) -> str:
    return f"{PA_CDN_BASE}/{year}.zip"


def download_years(years: Iterable[int], dest: Path | None = None) -> list[Path]:  # pragma: no cover - network
    """Download one zip per cycle year from pa.gov into data/raw/state/pa/.

    Returns the list of downloaded zip paths (stream them with iter_dir). The
    pa.gov CDN serves these as plain `application/zip` (no login/API key).
    """
    import shutil
    from urllib.request import urlopen

    from .paths import state_raw_dir
    out_dir = dest or state_raw_dir("pa")
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for y in years:
        zip_path = out_dir / f"{y}.zip"
        with urlopen(year_url(y)) as resp, zip_path.open("wb") as out:  # noqa: S310
            shutil.copyfileobj(resp, out, length=1024 * 1024)
        paths.append(zip_path)
    return paths
