"""Apply hand-curated external links to the committees table.

Reads `catalog/committee_external_links.yaml` and writes the link fields onto
the committees row for each committee_id present in the YAML. The committees
row must already exist (run `cli ingest-committees` first).

YAML shape:

    - committee_id: C00401224
      external_link: https://en.wikipedia.org/wiki/Senate_Majority_PAC
      external_link_label: Wikipedia
      external_link_source: manual; added by a maintainer 2026-05-25

Per GOVERNANCE.md §3, external sources are pointers, never primary fact. This
script only writes the URL+label+source — it never touches FEC-sourced fields.
The UI labels these links as "Read more" cross-references, not authoritative
data.

Re-runnable. Entries with all three fields null are no-ops (use that to clear
a previously-set link).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from . import db
from .paths import CATALOG_DIR, MASTER_DB


EXTERNAL_LINKS_YAML = CATALOG_DIR / "committee_external_links.yaml"


def apply_external_links(
    yaml_path: Path = EXTERNAL_LINKS_YAML,
    db_path: Path = MASTER_DB,
) -> dict:
    """Apply YAML entries to the committees table. Returns a summary dict."""
    summary = {
        "yaml_path": str(yaml_path),
        "entries_read": 0,
        "applied": 0,
        "missing_committee_rows": [],
        "skipped_empty": 0,
    }

    if not yaml_path.exists():
        summary["error"] = f"YAML not found: {yaml_path}"
        return summary

    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        summary["error"] = f"YAML root must be a list, got {type(raw).__name__}"
        return summary

    summary["entries_read"] = len(raw)
    db.init(db_path)

    with db.connect(db_path) as conn:
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            cid = entry.get("committee_id")
            if not cid:
                continue
            link = entry.get("external_link")
            label = entry.get("external_link_label")
            source = entry.get("external_link_source")

            # Verify the committees row exists; we don't insert from here.
            row = conn.execute(
                "SELECT 1 FROM committees WHERE committee_id = ?", (cid,)
            ).fetchone()
            if row is None:
                summary["missing_committee_rows"].append(cid)
                continue

            # All-null entry → no-op (treated as "leave whatever is there").
            if link is None and label is None and source is None:
                summary["skipped_empty"] += 1
                continue

            conn.execute(
                """
                UPDATE committees
                   SET external_link = ?,
                       external_link_label = ?,
                       external_link_source = ?
                 WHERE committee_id = ?
                """,
                (link, label, source, cid),
            )
            summary["applied"] += 1

    return summary
