"""One-shot: collapse state cross-filing / fan-out duplicates (§1.2).

Some portals stored multiple `state_donations` rows for ONE logical contribution
because the old native-id dedup couldn't fold them:

  - **IL / TX** — the same contribution re-filed across multiple filings
    (overlapping reporting periods), different `source_filing_id`, same
    donor+date+amount+recipient (e.g. Reinsdorf $50k under 4 filings).
  - **AZ** — a ×N fetcher fan-out that returned the contribution several times
    with DIFFERENT `source_tran_id`s (104/105 dup-groups are exactly ×3).

This migration groups CONFIRMED/PROBABLE rows by content key (jurisdiction,
entity_slug, contributor_name_raw, date, amount, recipient_name) and, within each
group >1, keeps the highest `source_filing_id` row (latest filing, deterministic)
and marks the rest **SUPERSEDED** — never deleted (GOVERNANCE §1.10), still
queryable, excluded from every total (which filters status IN CONFIRMED/PROBABLE).

DEFERRED: **CA and WA**. Their duplicates are same-filing rows with distinct
tran-ids — exactly what the CAL-ACCESS fetch dedup *deliberately preserves* as
separate line items. Collapsing them overrides a documented pilot design decision
and would need the CA fetcher changed too; left for explicit sign-off.

Snapshots state.db and appends a CORRECTION entry to PROVENANCE_LOG. Idempotent
(re-running finds no live dup-groups). Companion to the content-key dedup now in
the IL/TX/AZ fetchers, which prevents the next cron from re-introducing them.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from . import state_db
from .paths import PROVENANCE_LOG, STATE_DB
from .provenance import append_provenance

DEFAULT_JURISDICTIONS = ("IL", "TX", "AZ")


def _filing_sort_key(filing_id: str | None, state_txn_id: str) -> tuple:
    f = (filing_id or "").strip()
    try:
        fnum = int(f)
    except ValueError:
        fnum = -1
    return (fnum, f, state_txn_id)


def dedupe(
    jurisdictions: tuple[str, ...] = DEFAULT_JURISDICTIONS,
    db_path: Path = STATE_DB,
    dry_run: bool = False,
) -> dict:
    """Collapse content-key duplicates in the given jurisdictions. Returns a summary."""
    state_db.init(db_path)
    summary: dict = {
        "jurisdictions": list(jurisdictions),
        "dry_run": dry_run,
        "per_jurisdiction": {},
        "superseded_rows": 0,
        "superseded_amount": 0.0,
    }

    with state_db.connect(db_path) as conn:
        snap = None if dry_run else state_db.snapshot("dedupe_state_crossfilings", db_path)
        summary["snapshot_path"] = str(snap) if snap else None

        for j in jurisdictions:
            before = conn.execute(
                "SELECT COUNT(*) n, ROUND(COALESCE(SUM(amount),0),2) amt FROM state_donations "
                "WHERE status IN ('CONFIRMED','PROBABLE') AND jurisdiction = ?",
                (j,),
            ).fetchone()
            rows = conn.execute(
                """
                SELECT state_txn_id, source_filing_id, amount,
                       entity_slug, contributor_name_raw, date, recipient_name
                  FROM state_donations
                 WHERE status IN ('CONFIRMED','PROBABLE') AND jurisdiction = ?
                """,
                (j,),
            ).fetchall()

            groups: dict[tuple, list] = defaultdict(list)
            for r in rows:
                key = (r["entity_slug"], r["contributor_name_raw"], r["date"],
                       r["amount"], r["recipient_name"])
                groups[key].append(r)

            losers: list[tuple[str, str, float]] = []  # (loser_txn, winner_txn, amount)
            for key, grp in groups.items():
                if len(grp) < 2:
                    continue
                winner = max(grp, key=lambda r: _filing_sort_key(r["source_filing_id"], r["state_txn_id"]))
                for r in grp:
                    if r["state_txn_id"] != winner["state_txn_id"]:
                        losers.append((r["state_txn_id"], winner["state_txn_id"], r["amount"]))

            if not dry_run:
                for loser_txn, winner_txn, _amt in losers:
                    conn.execute(
                        """
                        UPDATE state_donations
                           SET status = 'SUPERSEDED',
                               superseded_by = ?,
                               superseded_reason = 'cross-filing/fan-out duplicate (content-key dedup §1.2)'
                         WHERE state_txn_id = ?
                        """,
                        (winner_txn, loser_txn),
                    )

            after = conn.execute(
                "SELECT COUNT(*) n, ROUND(COALESCE(SUM(amount),0),2) amt FROM state_donations "
                "WHERE status IN ('CONFIRMED','PROBABLE') AND jurisdiction = ?",
                (j,),
            ).fetchone()
            j_amt = round(sum(a for _, _, a in losers), 2)
            summary["per_jurisdiction"][j] = {
                "dup_rows_superseded": len(losers),
                "amount_removed": j_amt,
                "before": {"rows": before["n"], "amount": before["amt"]},
                "after": {"rows": after["n"], "amount": after["amt"]},
            }
            summary["superseded_rows"] += len(losers)
            summary["superseded_amount"] = round(summary["superseded_amount"] + j_amt, 2)

    if not dry_run:
        _append_provenance(summary)
    return summary


def _append_provenance(summary: dict) -> None:
    PROVENANCE_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = state_db._utc_now_iso()
    block = [
        f"\n### {ts[:10]} — CORRECTION — state cross-filing/fan-out dedup (§1.2)",
        "",
        "- **what**: collapsed content-key duplicates (same donor+date+amount+recipient) "
        "in IL/TX (cross-filing re-reports) and AZ (×N fetcher fan-out); losers marked "
        "SUPERSEDED (never deleted, §1.10), highest filing kept.",
        f"- **rows superseded**: `{summary['superseded_rows']}` / `${summary['superseded_amount']:,.2f}`",
    ]
    for j, d in summary["per_jurisdiction"].items():
        block.append(
            f"- **{j}**: {d['dup_rows_superseded']} rows / ${d['amount_removed']:,.2f} removed — "
            f"${d['before']['amount']:,.2f} ({d['before']['rows']}) → "
            f"${d['after']['amount']:,.2f} ({d['after']['rows']})"
        )
    block.append("- **deferred**: CA + WA (same-filing distinct-tran rows CA's fetch dedup preserves — needs sign-off).")
    block.append(f"- **snapshot_path**: `{summary.get('snapshot_path')}`")
    block.append("")
    append_provenance("\n".join(block), PROVENANCE_LOG)
