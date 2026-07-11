"""Validate the curated legislation YAML (legislation/issues.yaml + bills/*.yaml).

Mirrors validate_owners.py: fail loudly on schema violations, warn on soft
issues. The curated YAML is the source of truth for which bills are indexed and
why (the sourced relevance_basis); ingest enriches each from Congress.gov.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml

from .paths import LEGISLATION_BILLS_DIR, LEGISLATION_DIR


VALID_BILL_TYPES = {"hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"}


@dataclass
class LegValidation:
    yaml_path: Path
    ident: str | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_nonempty_str(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def load_issue_keys(issues_path: Path | None = None) -> set[str]:
    p = issues_path or (LEGISLATION_DIR / "issues.yaml")
    if not p.exists():
        return set()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return set((data.get("issues") or {}).keys())


def validate_issues_file(issues_path: Path | None = None) -> LegValidation:
    p = issues_path or (LEGISLATION_DIR / "issues.yaml")
    res = LegValidation(yaml_path=p, ident="issues")
    if not p.exists():
        res.errors.append("legislation/issues.yaml is missing")
        return res
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        res.errors.append(f"YAML parse error: {e}")
        return res
    issues = (data or {}).get("issues")
    if not isinstance(issues, dict) or not issues:
        res.errors.append("issues mapping is missing or empty")
        return res
    for key, val in issues.items():
        if not isinstance(val, dict):
            res.errors.append(f"issues.{key} is not a mapping")
            continue
        if not _is_nonempty_str(val.get("label")):
            res.errors.append(f"issues.{key}.label is empty")
        if not _is_nonempty_str(val.get("description")):
            res.errors.append(f"issues.{key}.description is empty")
    return res


def validate_bill_file(path: Path, issue_keys: set[str], known_bill_ids: set[str]) -> LegValidation:
    res = LegValidation(yaml_path=path, ident=None)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        res.errors.append(f"YAML parse error: {e}")
        return res
    if not isinstance(data, dict):
        res.errors.append("top-level YAML is not a mapping")
        return res

    res.ident = data.get("bill_id")
    congress = data.get("congress")
    bill_type = data.get("bill_type")
    number = data.get("number")

    # bill_id matches filename + is the canonical {congress}-{type}-{number}.
    if data.get("bill_id") != path.stem:
        res.errors.append(f"bill_id {data.get('bill_id')!r} != filename stem {path.stem!r}")
    if not isinstance(congress, int):
        res.errors.append("congress must be an integer")
    if bill_type not in VALID_BILL_TYPES:
        res.errors.append(f"bill_type {bill_type!r} not in {sorted(VALID_BILL_TYPES)}")
    if not isinstance(number, int):
        res.errors.append("number must be an integer")
    if isinstance(congress, int) and bill_type in VALID_BILL_TYPES and isinstance(number, int):
        expected = f"{congress}-{bill_type}-{number}"
        if data.get("bill_id") != expected:
            res.errors.append(f"bill_id {data.get('bill_id')!r} should be {expected!r}")

    area = data.get("mlb_issue_area")
    if not _is_nonempty_str(area):
        res.errors.append("mlb_issue_area is missing")
    elif issue_keys and area not in issue_keys:
        res.errors.append(f"mlb_issue_area {area!r} not in issues.yaml {sorted(issue_keys)}")

    if not _is_nonempty_str(data.get("relevance_basis")):
        res.errors.append("relevance_basis is missing (the sourced factual reason this bill is indexed)")

    carried = data.get("carried_by_bill_id")
    if carried is not None:
        if not _is_nonempty_str(carried):
            res.errors.append("carried_by_bill_id present but not a string")
        elif known_bill_ids and carried not in known_bill_ids:
            res.warnings.append(
                f"carried_by_bill_id {carried!r} has no bill file yet (index it to enable the carrier join)"
            )

    sources = data.get("sources")
    if not isinstance(sources, list) or len(sources) < 1:
        res.errors.append("sources must have at least 1 entry")
    else:
        for i, s in enumerate(sources):
            if not isinstance(s, dict):
                res.errors.append(f"sources[{i}] is not a mapping")
                continue
            if not _is_nonempty_str(s.get("description")):
                res.errors.append(f"sources[{i}].description is empty")
            if not _is_nonempty_str(s.get("url")):
                res.warnings.append(f"sources[{i}].url is empty")

    # roll_calls is optional; when present, each entry must be well-formed so
    # ingest-votes can fetch it.
    rcs = data.get("roll_calls")
    if rcs is not None:
        if not isinstance(rcs, list):
            res.errors.append("roll_calls must be a list")
        else:
            for i, rc in enumerate(rcs):
                if not isinstance(rc, dict):
                    res.errors.append(f"roll_calls[{i}] is not a mapping")
                    continue
                if rc.get("chamber") not in {"house", "senate"}:
                    res.errors.append(f"roll_calls[{i}].chamber must be house|senate")
                for k in ("congress", "session", "roll"):
                    if not isinstance(rc.get(k), int):
                        res.errors.append(f"roll_calls[{i}].{k} must be an integer")
                if rc.get("chamber") == "house" and not isinstance(rc.get("year"), int):
                    res.errors.append(f"roll_calls[{i}].year required (integer) for House votes")

    cl = data.get("change_log")
    today = datetime.now(timezone.utc).date()
    if not isinstance(cl, list) or not cl:
        res.errors.append("change_log must have at least one entry")
    else:
        for i, entry in enumerate(cl):
            if not isinstance(entry, dict):
                res.errors.append(f"change_log[{i}] is not a mapping")
                continue
            d = _parse_date(entry.get("date"))
            if d is None:
                res.errors.append(f"change_log[{i}].date is missing or invalid")
            elif d > today:
                res.errors.append(f"change_log[{i}].date {d.isoformat()} is in the future")
            if not _is_nonempty_str(entry.get("change")):
                res.errors.append(f"change_log[{i}].change is empty")

    return res


# Chamber rosters — a roll call with far fewer recorded positions than the
# chamber's size is likely an incomplete parse rather than mass absence.
_CHAMBER_SIZE = {"house": 435, "senate": 100}
_CHAMBER_MIN_POSITIONS = {"house": 400, "senate": 90}


def validate_legislation_db(
    bills_dir: Path = LEGISLATION_BILLS_DIR,
    db_path: Path | None = None,
) -> list[LegValidation]:
    """DB-vs-curated-YAML consistency checks (§5.6). Read-only; skipped cleanly if
    legislation.db doesn't exist yet.

    - **bill-set parity:** a `bills` row with no `legislation/bills/<id>.yaml` is an
      orphan — the curated YAML is the source of truth, but ingest is upsert-only,
      so deleting a YAML silently leaves its DB row behind. Warn so it's re-noticed.
    - **vote completeness:** a roll call whose `vote_positions` count is far below the
      chamber roster (e.g. the 1992 PASPA Senate vote holds 55/100) is likely a
      partial parse; flag it rather than let an undercount pass silently."""
    import sqlite3

    from .paths import LEGISLATION_DB

    db_path = db_path or LEGISLATION_DB
    results: list[LegValidation] = []
    if not db_path.exists():
        return results

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        parity = LegValidation(yaml_path=Path("legislation.db"), ident="db-yaml-parity")
        yaml_ids = {p.stem for p in bills_dir.glob("*.yaml") if not p.name.startswith("_")}
        try:
            db_ids = {r["bill_id"] for r in conn.execute("SELECT bill_id FROM bills")}
        except sqlite3.OperationalError:
            return results  # schema not initialized
        for bid in sorted(db_ids - yaml_ids):
            parity.warnings.append(
                f"bills row {bid!r} has no legislation/bills/{bid}.yaml — deleted YAML? "
                f"(ingest is upsert-only; re-ingest won't remove the orphaned DB row)"
            )
        results.append(parity)

        votes = LegValidation(yaml_path=Path("legislation.db"), ident="vote-completeness")
        try:
            rows = conn.execute(
                """
                SELECT v.vote_id, v.chamber, v.bill_id, COUNT(vp.vote_id) AS n
                  FROM votes v LEFT JOIN vote_positions vp ON vp.vote_id = v.vote_id
                 GROUP BY v.vote_id
                """
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for r in rows:
            floor = _CHAMBER_MIN_POSITIONS.get((r["chamber"] or "").lower())
            if floor is not None and r["n"] < floor:
                size = _CHAMBER_SIZE.get((r["chamber"] or "").lower())
                votes.warnings.append(
                    f"roll call {r['vote_id']} ({r['bill_id']}) has only {r['n']} recorded "
                    f"positions of ~{size} {r['chamber']} seats — likely an incomplete parse"
                )
        results.append(votes)
    finally:
        conn.close()
    return results


def validate_all(
    bills_dir: Path = LEGISLATION_BILLS_DIR,
    issues_path: Path | None = None,
    db_path: Path | None = None,
) -> list[LegValidation]:
    results: list[LegValidation] = [validate_issues_file(issues_path)]
    issue_keys = load_issue_keys(issues_path)
    bill_paths = sorted(p for p in bills_dir.glob("*.yaml") if not p.name.startswith("_"))
    known_bill_ids = {p.stem for p in bill_paths}
    for path in bill_paths:
        results.append(validate_bill_file(path, issue_keys, known_bill_ids))
    results.extend(validate_legislation_db(bills_dir, db_path))
    return results


def format_report(results: Iterable[LegValidation]) -> str:
    lines: list[str] = []
    n_ok = n_err = n_warn = 0
    for r in results:
        mark = "OK" if r.ok else "FAIL"
        if r.ok:
            n_ok += 1
        else:
            n_err += 1
        lines.append(f"[{mark}] {r.yaml_path.name} (id={r.ident})")
        for e in r.errors:
            lines.append(f"    ERROR: {e}")
        for w in r.warnings:
            n_warn += 1
            lines.append(f"    warn:  {w}")
    lines.append(f"\n{n_ok} OK · {n_err} failed · {n_warn} warnings")
    return "\n".join(lines)


def main() -> int:
    results = validate_all()
    print(format_report(results))
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
