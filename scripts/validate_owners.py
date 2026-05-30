"""Validate every owner YAML against OWNER_SCHEMA.md rules.

Run at the start of every ingestion. Fail loudly on schema violations.
Warnings (e.g., empty source URLs) print to stderr but do not fail.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml

from .paths import OWNERS_DIR


@dataclass
class OwnerValidation:
    yaml_path: Path
    slug: str | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _is_nonempty_str(value) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _validate_related_entity(idx: int, entity: dict, owner_slug: str) -> list[str]:
    errs: list[str] = []
    prefix = f"related_entities[{idx}]"
    kind = entity.get("kind")
    if kind not in {"spouse", "child", "parent", "sibling", "pac", "business_entity"}:
        errs.append(f"{prefix}.kind invalid: {kind!r}")
    slug = entity.get("slug")
    if not _is_nonempty_str(slug):
        errs.append(f"{prefix}.slug is missing or empty")
    if not _is_nonempty_str(entity.get("name")):
        errs.append(f"{prefix}.name is missing or empty")

    if kind == "pac":
        if not _is_nonempty_str(entity.get("committee_id")):
            errs.append(f"{prefix}.committee_id required for PAC")
        link = entity.get("ownership_link_documented")
        if not isinstance(link, dict) or not _is_nonempty_str(link.get("description")):
            errs.append(f"{prefix}.ownership_link_documented.description required for PAC")
    else:
        # Per OWNER_SCHEMA.md rule 7: every related_entity has its own verifying_signals.
        vs = entity.get("verifying_signals")
        if not isinstance(vs, dict):
            errs.append(f"{prefix}.verifying_signals missing")
        else:
            if not vs.get("cities"):
                errs.append(f"{prefix}.verifying_signals.cities is empty")
            if not vs.get("states"):
                errs.append(f"{prefix}.verifying_signals.states is empty")
            if not vs.get("employers") and not vs.get("occupations"):
                errs.append(
                    f"{prefix}.verifying_signals needs at least one employer or occupation"
                )
        if not entity.get("name_variants") or len(entity["name_variants"]) < 1:
            errs.append(f"{prefix}.name_variants required (≥1)")
    return errs


def validate_owner_file(path: Path) -> OwnerValidation:
    res = OwnerValidation(yaml_path=path, slug=None)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        res.errors.append(f"YAML parse error: {e}")
        return res
    if not isinstance(data, dict):
        res.errors.append("top-level YAML is not a mapping")
        return res

    res.slug = data.get("slug")

    # Rule 1: slug matches filename
    expected_slug = path.stem
    if data.get("slug") != expected_slug:
        res.errors.append(
            f"slug {data.get('slug')!r} does not match filename stem {expected_slug!r}"
        )

    # Required scalar fields
    for required in ("name", "team", "role", "status"):
        if not _is_nonempty_str(data.get(required)):
            res.errors.append(f"{required} is missing or empty")

    if data.get("status") not in {"queued", "pilot", "active", "paused"}:
        res.errors.append(f"status {data.get('status')!r} not in {{queued,pilot,active,paused}}")

    # Rule 8: tenure_start_date present and valid
    tsd = data.get("tenure_start_date")
    if tsd is None:
        res.errors.append("tenure_start_date is missing")
    elif _parse_date(tsd) is None:
        res.errors.append(f"tenure_start_date {tsd!r} is not a valid YYYY-MM-DD")

    ted = data.get("tenure_end_date")
    if ted is not None and _parse_date(ted) is None:
        res.errors.append(f"tenure_end_date {ted!r} is not a valid YYYY-MM-DD")

    # family_tenure_start_date — optional. When present, must be a valid date
    # and must not be AFTER tenure_start_date (family ownership precedes or
    # equals the person's formal control-person role).
    ftsd = data.get("family_tenure_start_date")
    if ftsd is not None:
        ftsd_parsed = _parse_date(ftsd)
        if ftsd_parsed is None:
            res.errors.append(
                f"family_tenure_start_date {ftsd!r} is not a valid YYYY-MM-DD"
            )
        elif tsd is not None:
            tsd_parsed = _parse_date(tsd)
            if tsd_parsed is not None and ftsd_parsed > tsd_parsed:
                res.errors.append(
                    f"family_tenure_start_date {ftsd!r} is AFTER tenure_start_date "
                    f"{tsd!r} — family ownership should precede or equal the person's "
                    f"control-person date"
                )

    # Rule 2: name_variants ≥ 2
    nv = data.get("name_variants")
    if not isinstance(nv, list) or len(nv) < 2:
        res.errors.append("name_variants must have at least 2 entries")
    elif not all(_is_nonempty_str(x) for x in nv):
        res.errors.append("name_variants entries must all be non-empty strings")

    # Rules 3+4: verifying_signals
    vs = data.get("verifying_signals")
    if not isinstance(vs, dict):
        res.errors.append("verifying_signals block missing")
    else:
        if not vs.get("cities"):
            res.errors.append("verifying_signals.cities is empty (rule 3)")
        if not vs.get("states"):
            res.errors.append("verifying_signals.states is empty (rule 3)")
        if not vs.get("employers"):
            res.errors.append("verifying_signals.employers is empty (rule 4)")
        # Sanity: cities lowercase, states USPS
        for c in vs.get("cities") or []:
            if not isinstance(c, str):
                res.errors.append(f"verifying_signals.cities contains non-string: {c!r}")
            elif c != c.lower():
                res.warnings.append(
                    f"verifying_signals.cities has non-lowercase entry {c!r} — normalization will lowercase at match time"
                )
        for s in vs.get("states") or []:
            if not (isinstance(s, str) and len(s) == 2 and s.isupper()):
                res.warnings.append(
                    f"verifying_signals.states entry {s!r} doesn't look like a USPS 2-letter code"
                )

    # Rule 6: strong_signals.employers disjoint from verifying_signals.employers
    ss = data.get("strong_signals")
    if not isinstance(ss, dict):
        res.errors.append("strong_signals block missing (may have empty arrays, but must exist)")
    else:
        s_emps = set(ss.get("employers") or [])
        v_emps = set((vs or {}).get("employers") or [])
        dups = s_emps & v_emps
        if dups:
            res.errors.append(
                f"strong_signals.employers overlaps verifying_signals.employers: {sorted(dups)} (rule 6)"
            )

    # Rule 5: sources ≥ 1
    sources = data.get("sources")
    if not isinstance(sources, list) or len(sources) < 1:
        res.errors.append("sources must have at least 1 entry (rule 5)")
    else:
        for i, s in enumerate(sources):
            if not isinstance(s, dict):
                res.errors.append(f"sources[{i}] is not a mapping")
                continue
            if not _is_nonempty_str(s.get("description")):
                res.errors.append(f"sources[{i}].description is empty")
            if not _is_nonempty_str(s.get("url")):
                res.warnings.append(
                    f"sources[{i}].url is empty — populate during review"
                )

    # Rule 7: related entities have own verifying_signals
    related = data.get("related_entities") or []
    if isinstance(related, list):
        for i, ent in enumerate(related):
            if not isinstance(ent, dict):
                res.errors.append(f"related_entities[{i}] is not a mapping")
                continue
            res.errors.extend(_validate_related_entity(i, ent, res.slug or ""))

    # Rule 9: change_log dates ≤ today. Use UTC (the rest of the pipeline
    # stamps UTC) so a change_log entry dated "today" doesn't fail validation
    # near the midnight boundary in a behind-UTC local timezone.
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


def validate_all(owners_dir: Path = OWNERS_DIR) -> list[OwnerValidation]:
    results: list[OwnerValidation] = []
    for path in sorted(owners_dir.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        results.append(validate_owner_file(path))
    return results


def format_report(results: Iterable[OwnerValidation]) -> str:
    lines: list[str] = []
    n_ok = 0
    n_err = 0
    n_warn = 0
    for r in results:
        if r.ok:
            n_ok += 1
            mark = "OK"
        else:
            n_err += 1
            mark = "FAIL"
        head = f"[{mark}] {r.yaml_path.name} (slug={r.slug})"
        lines.append(head)
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
