"""Fetch + parse the Colorado TRACER campaign-finance bulk export.

The Colorado Secretary of State publishes contributions through TRACER as one
**per-year zip** at a stable, constructable URL (no login/API key):

    https://tracer.sos.colorado.gov/PublicSite/Docs/BulkDataDownloads/<YEAR>_ContributionData.csv.zip

Each zip holds a single comma-delimited, header-bearing CSV
(`<YEAR>_ContributionData.csv`). Unlike PA, the recipient (CommitteeName /
CommitteeType / CO_ID) is INLINE on every contribution row, so there is no filer
index to build — the resolver is a stateless reader. Each row also carries a
native per-contribution `RecordID` used as the dedup key.

Parsing is pure and unit-tested against small fixtures; only the network download
is untested (same split as the CA/PA fetchers). Point the ingest at a directory
holding one or more `<YEAR>_ContributionData.csv.zip` files (or already-extracted
`*ContributionData.csv` files) and every year is streamed in one pass
(GOVERNANCE.md §1.4 — the downloaded zips are the ground truth, gitignored under
data/raw/state/co/).
"""
from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import Callable, Iterable, Iterator, TextIO

from .co_adapter import _clean, recipient_type_of, surname_of

csv.field_size_limit(10_000_000)

TRACER_BASE = "https://tracer.sos.colorado.gov/PublicSite/Docs/BulkDataDownloads"


def _is_contrib_csv(name: str) -> bool:
    n = name.lower().split("/")[-1]
    return n.endswith(".csv") and "contribution" in n


# ── Pure parsers (operate on a text handle; path/zip wrappers below) ──────────

def _reader(fh: TextIO) -> csv.DictReader:
    return csv.DictReader(fh)


def iter_contributions_fh(fh: TextIO) -> Iterator[dict]:
    """Yield one recipient-joined contribution dict per CSV row."""
    for row in _reader(fh):
        out = dict(row)
        out["_recipient_name"] = _clean(row.get("CommitteeName"))
        out["_recipient_type"] = recipient_type_of(row)
        yield out


def _open_text(path: Path) -> TextIO:
    # utf-8-sig strips the BOM TRACER prepends to the header's first column name.
    return path.open("r", encoding="utf-8-sig", errors="replace", newline="")


def _member_text(zf: zipfile.ZipFile, name: str) -> TextIO:
    raw = zf.open(name, "r")
    return io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")


# ── File / zip discovery + streaming ─────────────────────────────────────────

def find_co_zips(input_dir: Path) -> list[Path]:
    return [p for p in sorted(Path(input_dir).rglob("*.zip")) if p.is_file()]


def find_co_files(input_dir: Path) -> list[Path]:
    return [p for p in sorted(Path(input_dir).rglob("*")) if p.is_file() and _is_contrib_csv(p.name)]


def iter_contributions(contrib_csv: Path) -> Iterator[dict]:
    fh = _open_text(contrib_csv)
    try:
        yield from iter_contributions_fh(fh)
    finally:
        fh.close()


def iter_contributions_from_zip(zip_path: Path) -> Iterator[dict]:
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if _is_contrib_csv(name):
                fh = _member_text(zf, name)
                try:
                    yield from iter_contributions_fh(fh)
                finally:
                    fh.close()


def iter_dir(input_dir: Path) -> Iterator[dict]:
    """Stream every contribution across an input dir, recipient-joined.

    Accepts either per-year `<YEAR>_ContributionData.csv.zip` files (the live TRACER
    format) or already-extracted `*ContributionData.csv` files (fixtures / manual).
    """
    input_dir = Path(input_dir)
    zips = find_co_zips(input_dir)
    if zips:
        for z in zips:
            yield from iter_contributions_from_zip(z)
        return
    for c in find_co_files(input_dir):
        yield from iter_contributions(c)


# ── Recipient resolver / bucketing / dedup ───────────────────────────────────

def make_recipient_resolver(*_args) -> Callable[[dict], dict]:
    """Recipient is pre-joined onto each row by iter_contributions; just read it.

    Accepts (and ignores) a positional arg to satisfy the StateSource
    `recipient_resolver(input_path)` registry signature.
    """

    def _resolve(row: dict) -> dict:
        return {
            "filer_id": _clean(row.get("CO_ID")) or None,
            "name": _clean(row.get("_recipient_name")),
            "type": row.get("_recipient_type"),
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


def bucket_rows_by_owner(input_dir_or_rows, owners: list[tuple[str, dict]]) -> dict[str, list[dict]]:
    """Single streaming pass → {slug: [candidate rows]} across owners.

    Accepts either an input dir (the live source — streams the per-year zips/CSVs)
    or an already-built iterable of rows (tests). Each kept row already carries the
    inline recipient fields.
    """
    if isinstance(input_dir_or_rows, (str, Path)):
        rows: Iterable[dict] = iter_dir(Path(input_dir_or_rows))
    else:
        rows = input_dir_or_rows

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
    """Dedup on the native RecordID (idempotent across re-exports)."""
    seen: dict[str, dict] = {}
    for row in rows:
        key = _clean(row.get("RecordID")) or id(row)
        seen.setdefault(key, row)
    return list(seen.values())


# ── Download (live TRACER per-year zips) ─────────────────────────────────────

def year_url(year: int) -> str:
    return f"{TRACER_BASE}/{year}_ContributionData.csv.zip"


def download_years(years: Iterable[int], dest: Path | None = None) -> list[Path]:  # pragma: no cover - network
    """Download one zip per cycle year from TRACER into data/raw/state/co/.

    Returns the list of downloaded zip paths (stream them with iter_dir). TRACER
    serves these as plain `application/x-zip-compressed` (no login/API key).
    """
    import shutil
    from urllib.request import urlopen

    from .paths import state_raw_dir
    out_dir = dest or state_raw_dir("co")
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for y in years:
        zip_path = out_dir / f"{y}_ContributionData.csv.zip"
        with urlopen(year_url(y)) as resp, zip_path.open("wb") as out:  # noqa: S310
            shutil.copyfileobj(resp, out, length=1024 * 1024)
        paths.append(zip_path)
    return paths
