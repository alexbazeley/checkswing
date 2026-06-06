"""StateSource registry — the scalable seam for multi-state ingestion.

Each state's portal differs in format (CA: CAL-ACCESS zip; PA: DOS CSV export) but
the rest of the pipeline (the classifier, state.db, the dashboard) is source-agnostic.
A StateSource bundles the per-portal specifics — how to get an owner's candidate
rows, how to resolve a recipient, how to map a row into the classifier/DB shapes,
and how to key + dedup transactions — behind one interface. Adding a state is a new
adapter + fetcher + one registry entry; nothing downstream changes.

Input convention per source:
  * CA — `input` is the CAL-ACCESS `dbwebexport.zip` (rows streamed from the zip).
  * PA — `input` is the extracted PA-DOS export dir (the `* ECF Contribution.txt`
    and `* ECF Filer.txt` files).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import calaccess_adapter, fetch_calaccess, fetch_ny, fetch_pa, ny_adapter, pa_adapter


@dataclass(frozen=True)
class StateSource:
    code: str                     # jurisdiction, e.g. 'CA', 'PA', 'NY'
    source: str                   # official portal label, e.g. 'CAL-ACCESS', 'PA-DOS', 'NYSBOE'
    label: str                    # site source chip, e.g. 'CA · CAL-ACCESS'
    candidate_rows_by_owner: Callable[[Path, list[tuple[str, dict]]], dict[str, list[dict]]]
    recipient_resolver: Callable[[Path], Callable[[dict], dict]]
    record_adapter: Callable[[dict], dict]
    row_builder: Callable
    filing_id_of: Callable[[dict], str | None]
    tran_id_of: Callable[[dict], str | None]
    dedupe: Callable[[Iterable[dict]], list[dict]]
    # File-based sources (CA zip, PA dir) need a local --input path; API-based
    # sources (NY Socrata) do not — they fetch live, and `raw_ref` is the citable
    # source recorded as each row's raw_payload_path.
    requires_input: bool = True
    raw_ref: str = ""


# ── California (CAL-ACCESS zip) ──────────────────────────────────────────────

def _ca_candidates(zip_path: Path, owners: list[tuple[str, dict]]) -> dict[str, list[dict]]:
    return fetch_calaccess.bucket_rows_by_owner(
        fetch_calaccess.iter_rcpt_rows_from_zip(zip_path), owners
    )


def _ca_resolver(zip_path: Path):
    return fetch_calaccess.make_recipient_resolver_by_filing(
        fetch_calaccess.build_recipient_index_from_zip(zip_path)
    )


CA = StateSource(
    code="CA", source="CAL-ACCESS", label="CA · CAL-ACCESS",
    candidate_rows_by_owner=_ca_candidates,
    recipient_resolver=_ca_resolver,
    record_adapter=calaccess_adapter.to_classifier_record,
    row_builder=calaccess_adapter.to_state_donation_row,
    filing_id_of=lambda r: str(r.get("FILING_ID") or "").strip() or None,
    tran_id_of=lambda r: str(r.get("TRAN_ID") or "").strip() or None,
    dedupe=fetch_calaccess.dedupe_receipts,
)


# ── Pennsylvania (DOS full export, extracted dir) ────────────────────────────

def _pa_candidates(extract_dir: Path, owners: list[tuple[str, dict]]) -> dict[str, list[dict]]:
    contrib = fetch_pa.find_pa_file(extract_dir, fetch_pa.CONTRIB_SUFFIX)
    if contrib is None:
        raise FileNotFoundError(f"No '* {fetch_pa.CONTRIB_SUFFIX}' under {extract_dir}")
    filer = fetch_pa.find_pa_file(extract_dir, fetch_pa.FILER_SUFFIX)
    filer_index = fetch_pa.build_filer_index(filer) if filer else {}
    return fetch_pa.bucket_rows_by_owner(
        fetch_pa.iter_contributions(contrib, filer_index), owners
    )


def _pa_resolver(extract_dir: Path):
    return fetch_pa.make_recipient_resolver()


PA = StateSource(
    code="PA", source="PA-DOS", label="PA · PA DOS",
    candidate_rows_by_owner=_pa_candidates,
    recipient_resolver=_pa_resolver,
    record_adapter=pa_adapter.to_classifier_record,
    row_builder=pa_adapter.to_state_donation_row,
    filing_id_of=pa_adapter.filing_id_of,
    tran_id_of=pa_adapter.tran_id_of,
    dedupe=fetch_pa.dedupe,
)


# ── New York (NYSBOE via data.ny.gov SODA API — no input file) ───────────────

NY = StateSource(
    code="NY", source="NYSBOE", label="NY · NYSBOE",
    candidate_rows_by_owner=fetch_ny.candidate_rows_by_owner,
    recipient_resolver=fetch_ny.make_recipient_resolver,
    record_adapter=ny_adapter.to_classifier_record,
    row_builder=ny_adapter.to_state_donation_row,
    filing_id_of=ny_adapter.filing_id_of,
    tran_id_of=ny_adapter.tran_id_of,
    dedupe=fetch_ny.dedupe,
    requires_input=False,
    raw_ref=fetch_ny.SODA_URL,
)


REGISTRY: dict[str, StateSource] = {s.code: s for s in (CA, PA, NY)}


def get_source(code: str) -> StateSource:
    code = (code or "").upper()
    if code not in REGISTRY:
        raise KeyError(f"Unknown state '{code}'. Known: {', '.join(sorted(REGISTRY))}")
    return REGISTRY[code]
