"""Fetch + parse the Illinois State Board of Elections (ISBE) bulk export.

The ISBE publishes the whole campaign-disclosure database as public, tab-delimited
bulk files at downloads.elections.il.gov (no login or API key):

  * `Receipts.txt`   — every itemized receipt (~1 GB; streamed line-by-line)
  * `Committees.txt` — the recipient lookup (CommitteeID → Name / TypeOfCommittee /
                       PartyAffiliation)

Input convention: the ISBE `--input` is the directory holding both files
(`data/raw/state/il/`). Like the CA/TX fetchers, `Receipts.txt` is STREAMED (never
loaded whole), and the downloaded files are the persisted raw source
(GOVERNANCE.md §1.4). The recipient is pre-joined onto each candidate row (PA/TX
style), so the resolver is a stateless reader. Parsing + the join are pure and
unit-tested against small fixtures; only the network download is untested.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .il_adapter import _clean, recipient_type_of, surname_of

csv.field_size_limit(10_000_000)

RECEIPTS_FILE = "Receipts.txt"
COMMITTEES_FILE = "Committees.txt"
DOWNLOAD_BASE = "https://downloads.elections.il.gov"


def _open_text(path: Path):
    # utf-8-sig strips any BOM; ISBE bulk files are tab-delimited with a header line.
    return path.open("r", encoding="utf-8-sig", errors="replace", newline="")


def _iter_tsv(fh) -> Iterator[dict]:
    yield from csv.DictReader(fh, delimiter="\t")


def find_file(input_dir: Path, name: str) -> Path | None:
    direct = Path(input_dir) / name
    if direct.exists():
        return direct
    for p in sorted(Path(input_dir).rglob("*")):
        if p.is_file() and p.name.lower() == name.lower():
            return p
    return None


def build_committee_index(committees_txt: Path) -> dict[str, dict]:
    """CommitteeID → {name, type, party} from Committees.txt (first non-empty wins)."""
    index: dict[str, dict] = {}
    if not committees_txt or not Path(committees_txt).exists():
        return index
    fh = _open_text(Path(committees_txt))
    try:
        for row in _iter_tsv(fh):
            cid = _clean(row.get("ID"))
            if not cid:
                continue
            index.setdefault(cid, {
                "name": _clean(row.get("Name")),
                "type": recipient_type_of(row.get("TypeOfCommittee")),
                "party": _clean(row.get("PartyAffiliation")) or None,
            })
    finally:
        fh.close()
    return index


def iter_receipts(receipts_txt: Path) -> Iterator[dict]:
    """Stream every receipt row from Receipts.txt (no whole-file load)."""
    fh = _open_text(Path(receipts_txt))
    try:
        yield from _iter_tsv(fh)
    finally:
        fh.close()


def _prejoin_recipient(row: dict, committee_index: dict[str, dict]) -> dict:
    """Stamp the resolved recipient (name/type/party) onto a copy of the row."""
    cid = _clean(row.get("CommitteeID"))
    cmte = committee_index.get(cid, {})
    out = dict(row)
    out["_recipient_name"] = cmte.get("name", "")
    out["_recipient_type"] = cmte.get("type")
    out["_recipient_party"] = cmte.get("party") or ""
    return out


def make_recipient_resolver(*_args) -> Callable[[dict], dict]:
    """Recipient is pre-joined onto each row by bucket_rows_by_owner; just read it.

    Accepts (and ignores) a positional arg to satisfy the StateSource
    `recipient_resolver(input_path)` registry signature.
    """

    def _resolve(row: dict) -> dict:
        return {
            "filer_id": _clean(row.get("CommitteeID")) or None,
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
    """Single streaming pass over Receipts.txt → {slug: [candidate rows]}.

    Accepts either an input dir (the live source — builds the committee index from
    Committees.txt and streams Receipts.txt) or an already-built iterable of rows
    (tests). Each kept row carries the pre-joined recipient fields.
    """
    if isinstance(input_dir_or_rows, (str, Path)):
        input_dir = Path(input_dir_or_rows)
        receipts = find_file(input_dir, RECEIPTS_FILE)
        if receipts is None:
            raise FileNotFoundError(f"No {RECEIPTS_FILE} under {input_dir}")
        committees = find_file(input_dir, COMMITTEES_FILE)
        committee_index = build_committee_index(committees) if committees else {}
        rows: Iterable[dict] = iter_receipts(receipts)
    else:
        rows = input_dir_or_rows
        committee_index = {}

    surname_map = [(slug, _surname_set(o)) for slug, o in owners]
    buckets: dict[str, list[dict]] = {slug: [] for slug, _ in owners}
    for row in rows:
        sn = surname_of(row)
        if not sn:
            continue
        matching = [slug for slug, sns in surname_map if sns and any(s in sn for s in sns)]
        if not matching:
            continue
        joined = _prejoin_recipient(row, committee_index)
        for slug in matching:
            buckets[slug].append(joined)
    return buckets


def dedupe(rows: Iterable[dict]) -> list[dict]:
    """Dedup on the native receipt ID (idempotent across re-exports)."""
    seen: dict[str, dict] = {}
    for row in rows:
        key = _clean(row.get("ID")) or id(row)
        seen.setdefault(key, row)
    return list(seen.values())


def download_latest(dest: Path | None = None, base_url: str | None = None) -> Path:  # pragma: no cover - network
    """Download Receipts.txt + Committees.txt into data/raw/state/il/. Public, no key.

    Returns the input dir (pass it to ingest-state-bulk IL --input). Receipts.txt is
    streamed at ingest, never loaded whole.
    """
    import shutil
    from urllib.request import urlopen

    from .paths import state_raw_dir
    base = (base_url or DOWNLOAD_BASE).rstrip("/")
    out_dir = dest or state_raw_dir("il")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in (COMMITTEES_FILE, RECEIPTS_FILE):
        with urlopen(f"{base}/{name}") as resp, (out_dir / name).open("wb") as out:  # noqa: S310
            shutil.copyfileobj(resp, out, length=1024 * 1024)
    return out_dir
