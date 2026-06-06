"""Fetch + parse the Texas Ethics Commission (TEC) bulk campaign-finance export.

The TEC publishes the whole database as one public zip, `TEC_CF_CSV.zip` (no login
or API key). Itemized contributions are split across many `contribs_NN.csv` members
(plus `cont_ss.csv` / `cont_t.csv`, identical schema); `filers.csv` maps a filer id
to the recipient's name, type, and office sought.

Like the CA fetcher, contribution members are STREAMED straight from the zip (no
multi-GB extraction) — the zip is the persisted raw source (GOVERNANCE.md §1.4).
The recipient is pre-joined onto each candidate row (mirroring PA), so the resolver
is a stateless reader. Parsing + the join are pure and unit-tested against small
fixtures; only the network download is untested (same split as CA/PA).
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .tx_adapter import _clean, recipient_type_of, surname_of

csv.field_size_limit(10_000_000)

# Itemized-contribution members share one schema: contribs_NN.csv + cont_ss/cont_t.
_CONTRIB_RE = re.compile(r"^contribs_\d+\.csv$", re.IGNORECASE)
_EXTRA_CONTRIB_MEMBERS = {"cont_ss.csv", "cont_t.csv"}
FILERS_MEMBER = "filers.csv"


def _is_contrib_member(name: str) -> bool:
    base = name.rsplit("/", 1)[-1].lower()
    return bool(_CONTRIB_RE.match(base)) or base in _EXTRA_CONTRIB_MEMBERS


def contrib_members(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return sorted(n for n in zf.namelist() if _is_contrib_member(n))


def _iter_csv(text) -> Iterator[dict]:
    # utf-8-sig (applied by the caller's TextIOWrapper) strips any BOM; DictReader
    # keys off the per-member header line.
    yield from csv.DictReader(text)


def iter_contrib_rows_from_zip(zip_path: Path) -> Iterator[dict]:
    """Stream every itemized-contribution row across all contribution members."""
    with zipfile.ZipFile(zip_path) as zf:
        for member in sorted(n for n in zf.namelist() if _is_contrib_member(n)):
            with zf.open(member, "r") as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
                yield from _iter_csv(text)


def _find_member(zf: zipfile.ZipFile, target: str) -> str | None:
    for n in zf.namelist():
        if n.rsplit("/", 1)[-1].lower() == target:
            return n
    return None


def build_filer_index_from_zip(zip_path: Path) -> dict[str, dict]:
    """filerIdent → {name, type, office} from filers.csv (first non-empty wins)."""
    index: dict[str, dict] = {}
    with zipfile.ZipFile(zip_path) as zf:
        member = _find_member(zf, FILERS_MEMBER)
        if not member:
            return index
        with zf.open(member, "r") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
            for row in _iter_csv(text):
                _index_filer_row(index, row)
    return index


def _index_filer_row(index: dict[str, dict], row: dict) -> None:
    fid = _clean(row.get("filerIdent"))
    if not fid:
        return
    office = (
        _clean(row.get("ctaSeekOfficeDescr"))
        or _clean(row.get("filerHoldOfficeDescr"))
        or _clean(row.get("contestSeekOfficeDescr"))
    )
    index.setdefault(fid, {
        "name": _clean(row.get("filerName")),
        "type": recipient_type_of(row.get("filerTypeCd")),
        "office": office,
    })


def _prejoin_recipient(row: dict, filer_index: dict[str, dict]) -> dict:
    """Stamp the resolved recipient onto a copy of the row (PA-style pre-join).

    Name + type are inline on the contribution row (filerName / filerTypeCd); the
    filer index only adds the office sought.
    """
    fid = _clean(row.get("filerIdent"))
    filer = filer_index.get(fid, {})
    out = dict(row)
    out["_recipient_name"] = _clean(row.get("filerName")) or filer.get("name", "")
    out["_recipient_type"] = recipient_type_of(row.get("filerTypeCd")) or filer.get("type")
    out["_recipient_office"] = filer.get("office", "")
    return out


def make_recipient_resolver(*_args) -> Callable[[dict], dict]:
    """Recipient is pre-joined onto each row by bucket_rows_by_owner; just read it.

    Accepts (and ignores) a positional arg so it satisfies the StateSource
    `recipient_resolver(input_path)` registry signature.
    """

    def _resolve(row: dict) -> dict:
        return {
            "filer_id": _clean(row.get("filerIdent")) or None,
            "name": _clean(row.get("_recipient_name")) or _clean(row.get("filerName")),
            "type": row.get("_recipient_type") or recipient_type_of(row.get("filerTypeCd")),
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


def bucket_rows_by_owner(
    zip_path_or_rows, owners: list[tuple[str, dict]]
) -> dict[str, list[dict]]:
    """Single streaming pass over all contributions → {slug: [candidate rows]}.

    Accepts either a zip path (the live source — builds the filer index and streams
    members) or an already-built iterable of rows (tests). Each kept row carries the
    pre-joined recipient fields.
    """
    if isinstance(zip_path_or_rows, (str, Path)):
        zip_path = Path(zip_path_or_rows)
        filer_index = build_filer_index_from_zip(zip_path)
        rows: Iterable[dict] = iter_contrib_rows_from_zip(zip_path)
    else:
        rows = zip_path_or_rows
        filer_index = {}

    surname_map = [(slug, _surname_set(o)) for slug, o in owners]
    buckets: dict[str, list[dict]] = {slug: [] for slug, _ in owners}
    for row in rows:
        sn = surname_of(row)
        if not sn:
            continue
        matching = [slug for slug, sns in surname_map if sns and any(s in sn for s in sns)]
        if not matching:
            continue
        # Pre-join the recipient only for kept rows (cheap; the vast majority of
        # contribution rows never match an owner surname and are skipped untouched).
        joined = _prejoin_recipient(row, filer_index)
        for slug in matching:
            buckets[slug].append(joined)
    return buckets


def dedupe(rows: Iterable[dict]) -> list[dict]:
    """Dedup on the native contributionInfoId (idempotent across re-exports)."""
    seen: dict[str, dict] = {}
    for row in rows:
        key = _clean(row.get("contributionInfoId")) or id(row)
        seen.setdefault(key, row)
    return list(seen.values())


def download_latest(dest: Path | None = None, url: str | None = None) -> Path:  # pragma: no cover - network
    """Download the TEC bulk CF zip. Public, no key required.

    Default URL is the live TEC portal endpoint (ethicsefile.com). Returns the zip
    path; stream members with iter_contrib_rows_from_zip (no extraction needed).
    """
    import shutil
    from urllib.request import urlopen

    from .paths import state_raw_dir
    url = url or DEFAULT_CF_URL
    out_dir = dest or state_raw_dir("tx")
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / "TEC_CF_CSV.zip"
    with urlopen(url) as resp, zip_path.open("wb") as out:  # noqa: S310
        shutil.copyfileobj(resp, out, length=1024 * 1024)
    return zip_path


DEFAULT_CF_URL = "https://prd.tecprd.ethicsefile.com/public/cf/public/TEC_CF_CSV.zip"
