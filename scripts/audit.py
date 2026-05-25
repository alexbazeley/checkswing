"""Read-only audit tool for calibrating an owner's signal block.

Used by the `audit <slug>` CLI command. Surfaces:
  - Current signal-block summary (counts per signal type)
  - Classification counts from the DB (CONFIRMED / PROBABLE / UNCERTAIN)
  - PROBABLE records grouped by employer string + by ZIP (top 10 each)
  - CONFIRMED ZIPs (candidate strong_signals.zip_codes seed)
  - REVIEW_QUEUE reasons histogram + sample UNCERTAIN entries
  - Suggestion checklist (employer promotion / ZIP promotion / doppelgänger / city gap)

Nothing here writes. The output is meant to inform a human's YAML edit
decisions per the Cohen-playbook calibration loop in
docs/CALIBRATION_PLAYBOOK.md (TODO — to be written as part of PR 4 wrap).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml
from tabulate import tabulate

from . import db
from .paths import OWNERS_DIR


@dataclass
class AuditFindings:
    """Structured view of one owner's audit. Used for testing + printing."""
    slug: str
    owner_name: str
    owner_team: str
    signal_summary: dict
    classification_counts: dict
    probable_by_employer: list[dict]
    probable_by_zip: list[dict]
    confirmed_by_zip: list[dict]
    queue_reasons: list[dict]
    uncertain_sample: list[dict]
    suggestions: list[str] = field(default_factory=list)


def _load_owner(slug: str) -> dict:
    path = OWNERS_DIR / f"{slug}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"owners/{slug}.yaml not found")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _signal_summary(owner: dict) -> dict:
    vs = owner.get("verifying_signals") or {}
    ss = owner.get("strong_signals") or {}
    ns = owner.get("negative_signals") or {}
    return {
        "name_variants": len(owner.get("name_variants") or []),
        "cities": len(vs.get("cities") or []),
        "states": len(vs.get("states") or []),
        "employers": len(vs.get("employers") or []),
        "occupations": len(vs.get("occupations") or []),
        "strong_signal_employers": len(ss.get("employers") or []),
        "strong_signal_zip_codes": len(ss.get("zip_codes") or []),
        "negative_signal_employers": len(ns.get("employers") or []),
        "related_entities": len(owner.get("related_entities") or []),
    }


def _employer_in_signals(emp: str, owner: dict) -> str | None:
    """Return 'strong', 'verifying', 'negative', or None for where this
    employer string matches in the owner's signal block."""
    if not emp:
        return None
    e = emp.lower()
    for s in (owner.get("strong_signals") or {}).get("employers") or []:
        if s and s.lower() in e:
            return "strong"
    for s in (owner.get("negative_signals") or {}).get("employers") or []:
        if s and s.lower() in e:
            return "negative"
    for s in (owner.get("verifying_signals") or {}).get("employers") or []:
        if s and s.lower() in e:
            return "verifying"
    return None


def _build_findings(slug: str, *, db_path: Path | None = None) -> AuditFindings:
    owner = _load_owner(slug)
    db_kwargs = {"db_path": db_path} if db_path else {}

    with db.connect(**db_kwargs) as conn:
        cur = conn.cursor()
        confirmed_count = cur.execute(
            "SELECT COUNT(*) FROM donations WHERE entity_slug = ? AND status = 'CONFIRMED'",
            (slug,),
        ).fetchone()[0]
        probable_count = cur.execute(
            "SELECT COUNT(*) FROM donations WHERE entity_slug = ? AND status = 'PROBABLE'",
            (slug,),
        ).fetchone()[0]
        uncertain_open = cur.execute(
            "SELECT COUNT(*) FROM review_queue WHERE entity_slug = ? AND resolution IS NULL",
            (slug,),
        ).fetchone()[0]
        last_run = cur.execute(
            "SELECT MAX(completed_at) FROM ingestion_runs WHERE entity_slug = ?",
            (slug,),
        ).fetchone()[0]

        probable_emp = [
            dict(r) for r in cur.execute(
                """
                SELECT
                    contributor_employer_raw AS employer,
                    COUNT(*) AS n,
                    GROUP_CONCAT(DISTINCT contributor_city) AS cities,
                    GROUP_CONCAT(DISTINCT contributor_state) AS states
                FROM donations
                WHERE entity_slug = ? AND status = 'PROBABLE'
                GROUP BY contributor_employer_raw
                ORDER BY n DESC
                LIMIT 10
                """,
                (slug,),
            ).fetchall()
        ]
        probable_zip = [
            dict(r) for r in cur.execute(
                """
                SELECT
                    contributor_zip AS zip,
                    COUNT(*) AS n,
                    GROUP_CONCAT(DISTINCT contributor_city) AS cities,
                    GROUP_CONCAT(DISTINCT contributor_state) AS states,
                    GROUP_CONCAT(DISTINCT contributor_employer_raw) AS employers
                FROM donations
                WHERE entity_slug = ? AND status = 'PROBABLE' AND contributor_zip <> ''
                GROUP BY contributor_zip
                ORDER BY n DESC
                LIMIT 10
                """,
                (slug,),
            ).fetchall()
        ]
        confirmed_zip = [
            dict(r) for r in cur.execute(
                """
                SELECT
                    contributor_zip AS zip,
                    COUNT(*) AS n,
                    GROUP_CONCAT(DISTINCT contributor_city) AS cities,
                    GROUP_CONCAT(DISTINCT contributor_state) AS states
                FROM donations
                WHERE entity_slug = ? AND status = 'CONFIRMED' AND contributor_zip <> ''
                GROUP BY contributor_zip
                ORDER BY n DESC
                LIMIT 10
                """,
                (slug,),
            ).fetchall()
        ]
        queue_reasons = [
            dict(r) for r in cur.execute(
                """
                SELECT reason, COUNT(*) AS n
                FROM review_queue
                WHERE entity_slug = ? AND resolution IS NULL
                GROUP BY reason
                ORDER BY n DESC
                LIMIT 10
                """,
                (slug,),
            ).fetchall()
        ]
        uncertain_sample = [
            dict(r) for r in cur.execute(
                """
                SELECT transaction_id, reason, raw_payload_path
                FROM review_queue
                WHERE entity_slug = ? AND resolution IS NULL
                ORDER BY RANDOM()
                LIMIT 5
                """,
                (slug,),
            ).fetchall()
        ]

    findings = AuditFindings(
        slug=slug,
        owner_name=owner.get("name") or slug,
        owner_team=owner.get("team") or "?",
        signal_summary=_signal_summary(owner),
        classification_counts={
            "CONFIRMED": confirmed_count,
            "PROBABLE": probable_count,
            "UNCERTAIN_open": uncertain_open,
            "last_run": last_run,
        },
        probable_by_employer=probable_emp,
        probable_by_zip=probable_zip,
        confirmed_by_zip=confirmed_zip,
        queue_reasons=queue_reasons,
        uncertain_sample=uncertain_sample,
    )

    # Suggestions — heuristic-only, the human still decides.
    findings.suggestions = _suggest(findings, owner)
    return findings


def _suggest(f: AuditFindings, owner: dict) -> list[str]:
    """Heuristic suggestions for tightening signals.

    These are framed as questions, not directives. The user makes the call.
    CLAUDE.md §1.7 — every signal change is a deliberate YAML edit with
    change_log entry. This tool only surfaces candidates.
    """
    suggestions: list[str] = []
    existing_strong_emps = {
        s.lower() for s in (owner.get("strong_signals") or {}).get("employers") or []
    }
    existing_strong_zips = {
        str(z) for z in (owner.get("strong_signals") or {}).get("zip_codes") or []
    }
    existing_neg_emps = {
        s.lower() for s in (owner.get("negative_signals") or {}).get("employers") or []
    }
    existing_cities = {
        s.lower() for s in (owner.get("verifying_signals") or {}).get("cities") or []
    }

    # Strong-employer promotion candidates: PROBABLE records whose employer
    # already matches a verifying_signals.employers entry AND has multiple hits.
    for row in f.probable_by_employer[:5]:
        emp = (row.get("employer") or "").strip()
        if not emp:
            continue
        match = _employer_in_signals(emp, owner)
        already_strong = any(s in emp.lower() for s in existing_strong_emps if s)
        if match == "verifying" and row["n"] >= 2 and not already_strong:
            suggestions.append(
                f"Consider promoting employer string matching {emp!r} from "
                f"verifying_signals → strong_signals.employers "
                f"({row['n']} PROBABLE records would become CONFIRMED)"
            )

    # Strong-ZIP promotion candidates: ZIPs that appear in CONFIRMED records
    # but aren't in strong_signals.zip_codes yet (proves it's tied to the owner).
    for row in f.confirmed_by_zip[:5]:
        zip_code = (row.get("zip") or "").strip()
        if not zip_code:
            continue
        if zip_code in existing_strong_zips:
            continue
        if row["n"] >= 2:
            suggestions.append(
                f"Consider adding ZIP {zip_code!r} ({row.get('cities','?')}) "
                f"to strong_signals.zip_codes "
                f"({row['n']} CONFIRMED records share this ZIP)"
            )

    # Doppelgänger candidates: PROBABLE records where the employer string
    # has no match anywhere in the owner's signal block. These are the
    # "name matched + city/state matched + employer is something we don't
    # know about" cases — often a same-name doppelgänger.
    # We filter out self-reported generic strings (SELF, RETIRED, etc.) —
    # those carry no entity information and aren't doppelgängers.
    GENERIC_EMPLOYERS = {
        "self", "self-employed", "self employed", "selfemployed",
        "retired", "not employed", "unemployed", "n/a", "none",
        "homemaker", "housewife", "house wife", "house-wife",
        "investor", "private investor",  # too generic to be a doppelgänger marker
    }
    for row in f.probable_by_employer[:10]:
        emp = (row.get("employer") or "").strip()
        if not emp:
            continue
        if emp.lower() in GENERIC_EMPLOYERS:
            continue
        if _employer_in_signals(emp, owner) is not None:
            continue
        if row["n"] >= 2 and emp.lower() not in existing_neg_emps:
            suggestions.append(
                f"Review employer {emp!r} ({row['n']} PROBABLE) — may be a "
                f"doppelgänger; consider negative_signals.employers if a "
                f"different same-name person is confirmed"
            )

    # City coverage gaps: PROBABLE records whose city isn't in
    # verifying_signals.cities. Could be a legitimate secondary residence
    # (add the city) OR a doppelgänger filing from elsewhere (don't add).
    seen_cities: dict[str, int] = {}
    for row in f.probable_by_employer:
        for c in (row.get("cities") or "").split(","):
            c = c.strip().lower()
            if c and c not in existing_cities:
                seen_cities[c] = seen_cities.get(c, 0) + row["n"]
    for c, n in sorted(seen_cities.items(), key=lambda kv: -kv[1])[:3]:
        if n >= 2:
            suggestions.append(
                f"PROBABLE records in city {c!r} ({n} record(s)) — verify "
                f"as a documented secondary residence before adding to "
                f"verifying_signals.cities; otherwise leave UNCERTAIN"
            )

    if not suggestions:
        suggestions.append(
            "No automated suggestions surfaced — owner signals look reasonably "
            "tight, or there are too few PROBABLE/CONFIRMED records for "
            "heuristics to bite. Inspect manually if calibration is desired."
        )
    return suggestions


# ─── Printing ────────────────────────────────────────────────────────────────


def _trunc(s: str, n: int = 60) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def format_findings(f: AuditFindings) -> str:
    parts: list[str] = []
    parts.append(f"\n======== {f.slug} — {f.owner_name} · {f.owner_team} ========\n")

    parts.append("CURRENT SIGNAL BLOCK")
    rows = [[k, v] for k, v in f.signal_summary.items()]
    parts.append(tabulate(rows, headers=["", "count"], tablefmt="simple"))

    parts.append("\nCURRENT CLASSIFICATIONS")
    c = f.classification_counts
    ratio = (c["PROBABLE"] / c["CONFIRMED"]) if c["CONFIRMED"] else float("inf") if c["PROBABLE"] else 0.0
    parts.append(tabulate(
        [
            ["CONFIRMED", c["CONFIRMED"]],
            ["PROBABLE", c["PROBABLE"]],
            ["UNCERTAIN (open)", c["UNCERTAIN_open"]],
            ["P/C ratio", f"{ratio:.2f}" if c["CONFIRMED"] else "—"],
            ["last_run", c["last_run"] or "—"],
        ],
        headers=["", "value"],
        tablefmt="simple",
    ))

    parts.append("\nPROBABLE RECORDS BY EMPLOYER (top 10)")
    if f.probable_by_employer:
        rows = [
            [
                r["n"],
                _trunc(r.get("employer") or "(none)", 45),
                _trunc(r.get("cities") or "", 30),
                _trunc(r.get("states") or "", 12),
            ]
            for r in f.probable_by_employer
        ]
        parts.append(tabulate(rows, headers=["n", "employer", "cities", "states"], tablefmt="simple"))
    else:
        parts.append("  (none)")

    parts.append("\nPROBABLE RECORDS BY ZIP (top 10)")
    if f.probable_by_zip:
        rows = [
            [
                r["n"],
                r.get("zip") or "",
                _trunc(r.get("cities") or "", 22),
                _trunc(r.get("states") or "", 8),
                _trunc(r.get("employers") or "", 40),
            ]
            for r in f.probable_by_zip
        ]
        parts.append(tabulate(rows, headers=["n", "zip", "cities", "states", "employers"], tablefmt="simple"))
    else:
        parts.append("  (none)")

    parts.append("\nCONFIRMED ZIPS (candidate seed for strong_signals.zip_codes)")
    if f.confirmed_by_zip:
        rows = [
            [r["n"], r.get("zip") or "", _trunc(r.get("cities") or "", 22), _trunc(r.get("states") or "", 8)]
            for r in f.confirmed_by_zip
        ]
        parts.append(tabulate(rows, headers=["n", "zip", "cities", "states"], tablefmt="simple"))
    else:
        parts.append("  (none — owner has no CONFIRMED records yet)")

    parts.append("\nREVIEW QUEUE REASONS (top 10, unresolved)")
    if f.queue_reasons:
        rows = [[r["n"], _trunc(r.get("reason") or "", 80)] for r in f.queue_reasons]
        parts.append(tabulate(rows, headers=["n", "reason"], tablefmt="simple"))
    else:
        parts.append("  (queue empty)")

    parts.append("\nUNCERTAIN SAMPLE (random 5)")
    if f.uncertain_sample:
        rows = [
            [r.get("transaction_id"), _trunc(r.get("reason") or "", 50), _trunc(r.get("raw_payload_path") or "", 50)]
            for r in f.uncertain_sample
        ]
        parts.append(tabulate(rows, headers=["txn_id", "reason", "raw_payload"], tablefmt="simple"))
    else:
        parts.append("  (none)")

    parts.append("\nSUGGESTIONS")
    for s in f.suggestions:
        parts.append(f"  → {s}")
    parts.append("")
    parts.append("(audit is read-only — apply changes by editing the owner YAML")
    parts.append(" with a change_log entry, then `reclassify --from-raw <slug>`.)")
    return "\n".join(parts)


def audit_slug(slug: str, *, db_path: Path | None = None) -> str:
    """Top-level entry point: build findings and format for terminal output."""
    findings = _build_findings(slug, db_path=db_path)
    return format_findings(findings)
