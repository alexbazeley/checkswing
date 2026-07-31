#!/usr/bin/env python3
"""Federal review-queue burndown, gated on re-verification from raw.

`cli bulk-discard` selects by `reason LIKE`, which is the right tool when the
reason itself is the verdict (the §5.4 state burndown worked that way — state
has no per-owner raw to re-read). Federal does have per-owner raw, so a stronger
test is available and is used here: **re-score every open item against the
current owner YAML and discard only what still classifies UNCERTAIN.**

The distinction matters because of a defect this archive has already paid for
once. In 2026-05-30 a bulk triage discarded 9,303 items by reason; classifier
ordering had demoted strong-signal rows into that same reason class, so the
disposal buried real donations (PR #133, +$84,720 recovered). The lesson
recorded then — *a queue emptied by disposal looks exactly like an empty
queue* — is the reason this script refuses to act on anything it cannot read.

Disposition, per open item:

  PROMOTE       every raw record for the id classifies CONFIRMED/PROBABLE and
                is not already attributed → left in the queue, reported. These
                need `reclassify <slug>`, not a queue verdict; a reclassify
                attributes them through the normal path instead of pinning them
                with a manual override.
  DISCARD       every raw record still classifies UNCERTAIN → standing
                DISCARDED verdict.
  HOLD          mixed: some legs attributable, some not. The queue PK is
                (transaction_id, entity_slug) so one row can stand for several
                genuinely distinct contributions sharing a filer id. Never
                auto-resolved.
  UNVERIFIABLE  no raw record found on disk for the id → **preserved**. This is
                the §2.1 archival gap, and it is exactly the situation in which
                disposing of the item would be indistinguishable from having
                adjudicated it.

Attribution is never touched — the review queue holds only UNCERTAIN records,
so no counted total can move. Reversible per item via `cli unresolve`.
GOVERNANCE.md §1.6 — snapshots master.db before writing.
"""
from __future__ import annotations

import collections
import glob
import json
import sys
from datetime import datetime, timezone

import yaml

from . import db
from .paths import MASTER_DB, PROVENANCE_LOG
from .provenance import append_provenance
from .resolve_entities import classify

ATTRIBUTABLE = ("CONFIRMED", "PROBABLE")


def _signal_blocks() -> dict:
    """slug -> the YAML block carrying its signals (owners AND related entities)."""
    blocks = {}
    for f in glob.glob("owners/*.yaml"):
        if "_template" in f or "_registry" in f:
            continue
        o = yaml.safe_load(open(f)) or {}
        if o.get("slug"):
            blocks[o["slug"]] = o
        for rel in o.get("related_entities") or []:
            if rel.get("slug"):
                blocks[rel["slug"]] = rel
    return blocks


def _raw_index(slug: str, _cache={}) -> dict:
    """txn -> [every distinct raw record carrying it].

    A LIST, not a single record: since schema v12 one owner can hold several
    live rows sharing a filer-assigned transaction_id, and collapsing them here
    would re-introduce the very defect v12 fixed. Deduped on record_uid.
    """
    if slug in _cache:
        return _cache[slug]
    m = collections.defaultdict(list)
    seen = set()
    for p in sorted(glob.glob(f"data/raw/{slug}/*.json")):
        try:
            d = json.load(open(p))
        except Exception:
            continue
        for r in (d.get("response") or {}).get("results") or []:
            txn = str(r.get("transaction_id") or "")
            uid = db.record_uid_for(r.get("sub_id"), txn)
            if not txn or uid in seen:
                continue
            seen.add(uid)
            m[txn].append(r)
    _cache[slug] = m
    return m


def assess(db_path=MASTER_DB) -> dict:
    """Read-only. Returns {disposition: [(slug, txn, reason, detail), ...]}."""
    blocks = _signal_blocks()
    with db.connect(db_path) as conn:
        live_uids = {
            r[0] for r in conn.execute(
                "SELECT record_uid FROM donations WHERE superseded_by IS NULL"
            )
        }
        items = conn.execute(
            """
            SELECT q.entity_slug, q.transaction_id, q.reason
              FROM review_queue q
              LEFT JOIN review_resolutions r
                ON r.transaction_id = q.transaction_id AND r.entity_slug = q.entity_slug
             WHERE r.transaction_id IS NULL AND q.resolution IS NULL
            """
        ).fetchall()

    out = collections.defaultdict(list)
    for it in items:
        slug, txn, reason = it["entity_slug"], it["transaction_id"], it["reason"]
        block = blocks.get(slug)
        recs = _raw_index(slug).get(txn) or []
        if not block or not recs:
            out["UNVERIFIABLE"].append((slug, txn, reason, "no raw record on disk"))
            continue
        verdicts = []
        for rec in recs:
            c = classify(rec, block, process_related_entities=False)
            uid = db.record_uid_for(rec.get("sub_id"), rec.get("transaction_id"))
            verdicts.append((getattr(c, "status", None), uid in live_uids, rec))
        attributable = [v for v in verdicts if v[0] in ATTRIBUTABLE]
        unattributed = [v for v in attributable if not v[1]]
        if not attributable:
            out["DISCARD"].append((slug, txn, reason, "still UNCERTAIN under current YAML"))
        elif not unattributed:
            # Its attributable leg is already in donations; this queue row is
            # the other, genuinely-uncertain leg.
            out["DISCARD"].append(
                (slug, txn, reason, "attributable leg already attributed; this leg UNCERTAIN")
            )
        elif len(attributable) == len(verdicts):
            detail = "; ".join(
                f"{v[0]} ${float(v[2].get('contribution_receipt_amount') or 0):,.2f} "
                f"{v[2].get('contributor_employer') or ''!r}"
                for v in unattributed
            )
            out["PROMOTE"].append((slug, txn, reason, detail))
        else:
            out["HOLD"].append((slug, txn, reason, "mixed legs under one transaction_id"))
    return out


def burn(note: str, db_path=MASTER_DB, *, dry_run: bool = False) -> dict:
    disp = assess(db_path)
    to_discard = disp["DISCARD"]
    if dry_run or not to_discard:
        return disp

    db.snapshot("pre-burndown-federal-queue")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.connect(db_path) as conn:
        for slug, txn, _reason, detail in to_discard:
            full = f"{note} ({detail})"
            db.upsert_review_resolution(
                conn,
                transaction_id=txn,
                entity_slug=slug,
                resolution="DISCARDED",
                resolution_reason=full,
                resolved_at=ts,
            )
            # Keep the queue column in lockstep with the durable store — the
            # two disagreeing is its own defect (db.check_adjudication_integrity).
            conn.execute(
                "UPDATE review_queue SET resolution='DISCARDED', resolution_reason=?, "
                "resolution_at=? WHERE transaction_id=? AND entity_slug=?",
                (full, ts, txn, slug),
            )
    return disp


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in argv
    note = (
        "Federal queue burndown (2026-07-31): re-scored from raw against the "
        "current owner YAML and still classifies UNCERTAIN. Geographic/name-alone "
        "contradiction on a common name = same-named stranger. Queue-only; "
        "attribution untouched. Reversible via `cli unresolve`."
    )

    disp = burn(note, dry_run=dry_run)
    print("=== DISPOSITION ===")
    for k in ("PROMOTE", "DISCARD", "HOLD", "UNVERIFIABLE"):
        print(f"  {k:<14}{len(disp[k]):>5}")

    if disp["PROMOTE"]:
        print("\nPROMOTE — run `cli reclassify <slug>` to attribute these:")
        for slug, txn, _r, detail in disp["PROMOTE"]:
            print(f"  {slug} {txn} — {detail}")
    if disp["HOLD"]:
        print("\nHOLD — mixed legs, needs per-record judgment:")
        for slug, txn, reason, _d in disp["HOLD"]:
            print(f"  {slug} {txn} — {reason[:70]}")
    if disp["UNVERIFIABLE"]:
        print("\nUNVERIFIABLE — PRESERVED (raw missing; §2.1 gap):")
        for s, n in collections.Counter(x[0] for x in disp["UNVERIFIABLE"]).most_common():
            print(f"  {s:<24}{n:>5}")

    if dry_run:
        print("\n(dry run — nothing written)")
        return 0
    if not disp["DISCARD"]:
        print("\nNothing to discard.")
        return 0

    by_slug = collections.Counter(x[0] for x in disp["DISCARD"])
    lines = [
        f"\n### {datetime.now(timezone.utc).strftime('%Y-%m-%d')} — REVIEW_RESOLUTION — federal queue burndown (verified)",
        "",
        f"Discarded **{len(disp['DISCARD'])}** open federal review-queue item(s) after "
        "re-scoring each against the current owner YAML from `data/raw/`. Only items "
        "that still classify **UNCERTAIN** were discarded.",
        "",
        f"Preserved: **{len(disp['UNVERIFIABLE'])}** item(s) whose raw payload is missing "
        "on disk and therefore could not be re-verified (the §2.1 archival gap), plus "
        f"**{len(disp['PROMOTE'])}** that now classify attributable and "
        f"**{len(disp['HOLD'])}** with mixed legs under one transaction_id.",
        "",
        "Queue-only: the review queue holds UNCERTAIN records exclusively, so no "
        "counted total changed. Reversible per item via `cli unresolve`.",
        "",
        "Per owner: " + ", ".join(f"`{k}`={v}" for k, v in by_slug.most_common()),
        "",
    ]
    append_provenance("\n".join(lines), PROVENANCE_LOG)
    print(f"\nDiscarded {len(disp['DISCARD'])}. Logged to {PROVENANCE_LOG}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
