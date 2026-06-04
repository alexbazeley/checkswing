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

# The authoritative Tier-1 bulk export: the California Secretary of State's full
# CAL-ACCESS database web export (~1.5 GB zip, refreshed daily). This IS the
# source of record (GOVERNANCE.md §1.11). The CCDC mirror is an optional
# convenience copy, used only if the SoS export is unavailable.
SOS_DBWEBEXPORT_URL = "https://campaignfinance.cdn.sos.ca.gov/dbwebexport.zip"
CCDC_LATEST_URL = "https://calaccess.californiacivicdata.org/downloads/latest/"

# CAL-ACCESS TSVs can carry stray bytes; csv with a generous field-size limit and
# replace-on-error decoding is the documented-robust way to read them.
csv.field_size_limit(10_000_000)


def _iter_tsv(fileobj) -> Iterator[dict]:
    """Yield dict rows from an already-open text file object (tab-delimited)."""
    reader = csv.DictReader(fileobj, delimiter="\t")
    for row in reader:
        yield row


def iter_rcpt_rows(rcpt_tsv: Path) -> Iterator[dict]:
    """Yield each RCPT_CD row as a dict keyed by the file's header columns.

    Tab-delimited with an uppercase header row (CTRIB_NAML, AMOUNT, RCPT_DATE,
    TRAN_ID, FILING_ID, FILER_ID, …).
    """
    with rcpt_tsv.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        yield from _iter_tsv(fh)


# Default member paths inside the SoS dbwebexport.zip (nested under CalAccess/DATA/).
ZIP_RCPT_MEMBER = "CalAccess/DATA/RCPT_CD.TSV"
ZIP_FILERNAME_MEMBER = "CalAccess/DATA/FILERNAME_CD.TSV"


def _zip_member(zip_path: Path, member: str, stem: str) -> str:
    """Resolve a member name inside the zip case-insensitively by file stem,
    falling back to the provided default. Lets us tolerate path/case variation
    across SoS export revisions without extracting the 3.7 GB RCPT table to disk.
    """
    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    if member in names:
        return member
    for n in names:
        base = n.rsplit("/", 1)[-1]
        if base.upper() == f"{stem.upper()}.TSV":
            return n
    return member


def iter_rcpt_rows_from_zip(zip_path: Path, member: str | None = None) -> Iterator[dict]:
    """Stream RCPT_CD rows directly from the dbwebexport.zip — no 3.7 GB extraction.

    Opens the member as a binary stream wrapped in a text decoder, so memory stays
    flat regardless of file size (GOVERNANCE.md §1.4: the zip is the persisted raw).
    """
    import io
    import zipfile

    member = member or _zip_member(zip_path, ZIP_RCPT_MEMBER, "RCPT_CD")
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member, "r") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
            yield from _iter_tsv(text)


def build_filer_index_from_zip(zip_path: Path, member: str | None = None) -> dict[str, dict]:
    """Build the FILER_ID → recipient index by streaming FILERNAME_CD from the zip."""
    import io
    import zipfile

    member = member or _zip_member(zip_path, ZIP_FILERNAME_MEMBER, "FILERNAME_CD")
    index: dict[str, dict] = {}
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member, "r") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
            for row in _iter_tsv(text):
                _index_filer_row(index, row)
    return index


def _index_filer_row(index: dict[str, dict], row: dict) -> None:
    """Add one FILERNAME_CD row to the FILER_ID → recipient index (first wins)."""
    fid = _clean(row.get("FILER_ID"))
    if not fid:
        return
    naml = _clean(row.get("NAML")) or _clean(row.get("FILER_NAML"))
    namf = _clean(row.get("NAMF")) or _clean(row.get("FILER_NAMF"))
    name = f"{naml}, {namf}" if namf else naml
    ftype = (_clean(row.get("FILER_TYPE")) or "").lower() or None
    # First non-empty wins; CAL-ACCESS repeats a filer across amendments.
    index.setdefault(fid, {"name": name, "type": ftype})


def build_filer_index(filername_tsv: Path) -> dict[str, dict]:
    """FILER_ID → {"name": str, "type": str|None} from FILERNAME_CD.

    FILERNAME_CD maps a filer id to the committee/candidate name. NAML/NAMF hold
    the filer's name; FILER_TYPE distinguishes candidate vs committee where present.
    """
    index: dict[str, dict] = {}
    if not filername_tsv.exists():
        return index
    with filername_tsv.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        for row in _iter_tsv(fh):
            _index_filer_row(index, row)
    return index


ZIP_CVR_MEMBER = "CalAccess/DATA/CVR_CAMPAIGN_DISCLOSURE_CD.TSV"


def _recipient_from_cvr_row(row: dict) -> dict:
    """Derive a recipient identity from a CVR_CAMPAIGN_DISCLOSURE_CD (cover-page) row.

    The filer of a campaign-disclosure filing IS the recipient of the contributions
    itemized on it. FILER_NAML is the committee name (e.g. "Scott for State Senate");
    CAND_NAML marks a candidate-controlled committee, BAL_NAME a ballot-measure one.
    """
    filer_name = _clean(row.get("FILER_NAML"))
    namf = _clean(row.get("FILER_NAMF"))
    if filer_name and namf:
        filer_name = f"{filer_name}, {namf}"
    cand = _clean(row.get("CAND_NAML"))
    bal = _clean(row.get("BAL_NAME"))
    if not filer_name:
        filer_name = cand or _clean(row.get("BUS_NAME")) or bal
    if cand:
        rtype = "candidate"
    elif bal:
        rtype = "ballot_measure"
    else:
        rtype = "committee"
    return {"filer_id": _clean(row.get("FILER_ID")) or None, "name": filer_name, "type": rtype}


def build_recipient_index_from_zip(zip_path: Path, member: str | None = None) -> dict[str, dict]:
    """FILING_ID → recipient identity, streamed from CVR_CAMPAIGN_DISCLOSURE_CD.

    This is the recipient resolver for RCPT rows (they carry FILING_ID, not FILER_ID).
    Keyed by FILING_ID; the highest AMEND_ID wins so the current cover page names the
    recipient.
    """
    import io
    import zipfile

    member = member or _zip_member(zip_path, ZIP_CVR_MEMBER, "CVR_CAMPAIGN_DISCLOSURE_CD")
    index: dict[str, dict] = {}
    best_amend: dict[str, int] = {}
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member, "r") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
            for row in _iter_tsv(text):
                fid = _clean(row.get("FILING_ID"))
                if not fid:
                    continue
                try:
                    amend = int(_clean(row.get("AMEND_ID")) or 0)
                except ValueError:
                    amend = 0
                if fid not in best_amend or amend >= best_amend[fid]:
                    best_amend[fid] = amend
                    index[fid] = _recipient_from_cvr_row(row)
    return index


def make_recipient_resolver_by_filing(recipient_index: dict[str, dict]) -> Callable[[dict], dict]:
    """Resolver mapping an RCPT row → recipient via FILING_ID (cover-page filer).

    Unknown filing → honest empty name, still keyed by the contribution's filing id.
    """

    def _resolve(rcpt: dict) -> dict:
        fid = _clean(rcpt.get("FILING_ID"))
        meta = recipient_index.get(fid) if fid else None
        if meta:
            return dict(meta)
        return {"filer_id": None, "name": "", "type": None}

    return _resolve


def dedupe_receipts(rows: Iterable[dict]) -> list[dict]:
    """Collapse CAL-ACCESS double-reporting of the same logical contribution.

    Two distinct duplication mechanisms both produce repeated rows for one real
    contribution; this folds both:

    1. **Amendments.** Every amendment re-files all of a filing's transactions, so
       the same receipt appears once per AMEND_ID within a FILING_ID.
    2. **Overlapping reporting periods.** A filer reports the same contribution
       (same filer-assigned TRAN_ID) on more than one filing (e.g. a pre-election
       report and a later semi-annual report) — different FILING_IDs, same TRAN_ID,
       same amount/date/donor. Keying on FILING_ID alone would keep both and inflate
       dollar totals (observed: ~$580k / 13% of the CA pilot total).

    Dedup key = (TRAN_ID, amount, date, contributor last+first). Two *genuinely
    separate* identical contributions are preserved, because line items within one
    filing carry distinct TRAN_IDs. Among duplicates the row with the max
    (AMEND_ID, FILING_ID) wins — deterministic, so the chosen citing filing is
    stable across re-ingests (idempotency).

    Not folded here: donor-side vs recipient-side cross-filing where each filer
    assigns its OWN TRAN_ID (keys differ). That harder, fuzzier case is a documented
    pilot limitation (STATE_DONATION_SCHEMA.md), not silently merged.
    """
    best: dict[tuple, tuple[tuple, dict]] = {}
    for row in rows:
        tid = _clean(row.get("TRAN_ID"))
        amount = _clean(row.get("AMOUNT"))
        date = _clean(row.get("RCPT_DATE"))
        naml = _clean(row.get("CTRIB_NAML")).lower()
        namf = _clean(row.get("CTRIB_NAMF")).lower()
        try:
            amend = int(_clean(row.get("AMEND_ID")) or 0)
        except ValueError:
            amend = 0
        key = (tid, amount, date, naml, namf)
        rank = (amend, _clean(row.get("FILING_ID")))
        if key not in best or rank > best[key][0]:
            best[key] = (rank, row)
    return [v[1] for v in best.values()]


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


def bucket_rows_by_owner(
    rows: Iterable[dict], owners: list[tuple[str, dict]]
) -> dict[str, list[dict]]:
    """Single pass over RCPT rows → {slug: [candidate rows]} across MANY owners.

    `owners` is a list of (slug, owner_dict). A row is added to every owner whose
    surname set substring-matches the row's CTRIB_NAML (the classifier makes the
    precise call later). This lets one stream over the 3.7 GB RCPT table feed all
    owners at once, instead of a pass per owner.
    """
    surname_map = [(slug, _surname_set(o)) for slug, o in owners]
    buckets: dict[str, list[dict]] = {slug: [] for slug, _ in owners}
    for row in rows:
        naml = _clean(row.get("CTRIB_NAML")).lower()
        if not naml:
            continue
        for slug, sns in surname_map:
            if sns and any(s in naml for s in sns):
                buckets[slug].append(row)
    return buckets


def download_latest(
    dest: Path | None = None, url: str = SOS_DBWEBEXPORT_URL
) -> Path:  # pragma: no cover - network
    """Download the CA Secretary of State CAL-ACCESS bulk export zip; return its path.

    NETWORK STEP — not unit-tested (the live archive is ~1.5 GB, refreshed daily).
    Streams the zip to data/raw/state/ca/dbwebexport.zip (persisting the raw archive
    before parsing, GOVERNANCE.md §1.4) and returns the ZIP path. It is deliberately
    NOT extracted: the ingest pipeline streams RCPT_CD / CVR / FILERNAME straight
    from the zip member (iter_rcpt_rows_from_zip etc.), so the 3.7 GB receipts table
    never has to land on disk. Only HTTP plumbing lives here; data correctness lives
    in the parsing functions above.
    """
    import shutil
    from urllib.request import urlopen

    out_dir = dest or state_raw_dir("ca")
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / "dbwebexport.zip"
    # Stream to disk (do NOT buffer 1.5 GB in memory; do NOT extract ~6 GB).
    with urlopen(url) as resp, zip_path.open("wb") as out:  # noqa: S310 (trusted gov source)
        shutil.copyfileobj(resp, out, length=1024 * 1024)
    return zip_path
