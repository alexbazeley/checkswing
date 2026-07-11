"""Yearly rotation for catalog/PROVENANCE_LOG.md (§2.2).

The provenance log is append-only and grows without bound — every gated mutation
adds a block, and the dashboard build parses the whole file into provenance.json
on every Cloudflare deploy. Rotation seals *completed prior years* into
`catalog/provenance/PROVENANCE_LOG-<YYYY>.md` so the active log (and every append
to it, and the file git/LFS diffs each run) stays bounded to the current year.

Rotation is a **move, never a delete** (GOVERNANCE.md §1.10): entry text is copied
verbatim into the yearly archive and removed from the active log, with a ROTATION
note recorded. The dashboard build parses the active log + all archives together
(`parse_provenance_corpus`), so provenance.json stays complete.

`plan_rotation` is pure (no I/O) and is what the tests exercise; `apply_rotation`
performs the file moves. The CLI (`cli rotate-provenance`) defaults to a dry run.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .paths import PROVENANCE_ARCHIVE_DIR, PROVENANCE_LOG
from .provenance import append_provenance

# A block starts at a `### YYYY-...` heading and runs to just before the next one.
_HEADING_YEAR_RE = re.compile(r"^###\s+(\d{4})-\d{2}-\d{2}")

_ARCHIVE_HEADER = (
    "# PROVENANCE_LOG — {year} (sealed archive)\n\n"
    "Entries for {year}, rotated out of the active `catalog/PROVENANCE_LOG.md` by\n"
    "`cli rotate-provenance` (§2.2). Append-only and never edited after sealing;\n"
    "the dashboard build parses this alongside the active log so the changelog\n"
    "stays complete. Do not add new entries here by hand.\n"
)


def split_log(text: str) -> tuple[str, list[tuple[int, str]]]:
    """Split raw log text into (preamble, [(year, block_text), …]).

    `preamble` is everything before the first `### ` heading (kept verbatim on the
    active log). Each block is one entry's raw text (its heading through the line
    before the next heading), tagged with the year parsed from its heading. A
    heading whose date can't be parsed is attached to the previous block (so no
    text is ever dropped); a leading unparseable heading stays in the preamble."""
    lines = text.splitlines(keepends=True)
    heading_idx = [i for i, l in enumerate(lines) if l.startswith("### ")]
    if not heading_idx:
        return text, []
    preamble = "".join(lines[: heading_idx[0]])
    bounds = heading_idx + [len(lines)]
    blocks: list[tuple[int, str]] = []
    for start, end in zip(bounds[:-1], bounds[1:]):
        block = "".join(lines[start:end])
        m = _HEADING_YEAR_RE.match(lines[start])
        if m:
            blocks.append((int(m.group(1)), block))
        elif blocks:
            # Unparseable heading year — fold into the previous block rather than
            # lose it (defensive; the parser warns on these separately).
            prev_year, prev_block = blocks[-1]
            blocks[-1] = (prev_year, prev_block + block)
        else:
            preamble += block
    return preamble, blocks


def plan_rotation(text: str, cutoff_year: int) -> dict:
    """Decide what a rotation with the given cutoff would move. Pure.

    Years strictly **less than** `cutoff_year` are sealed; the cutoff year and
    later stay in the active log. Returns a summary dict with the per-year blocks
    to archive and the rebuilt active-log text (preamble + retained blocks)."""
    preamble, blocks = split_log(text)
    archive_by_year: dict[int, list[str]] = {}
    retained: list[str] = []
    for year, block in blocks:
        if year < cutoff_year:
            archive_by_year.setdefault(year, []).append(block)
        else:
            retained.append(block)
    new_active = preamble + "".join(retained)
    return {
        "cutoff_year": cutoff_year,
        "years": {y: len(bs) for y, bs in sorted(archive_by_year.items())},
        "n_archived": sum(len(bs) for bs in archive_by_year.values()),
        "n_retained": len(retained),
        "archive_by_year": archive_by_year,
        "new_active_text": new_active,
    }


def _current_utc_year() -> int:
    return datetime.now(timezone.utc).year


def apply_rotation(
    *,
    cutoff_year: int | None = None,
    active_path: Path = PROVENANCE_LOG,
    archive_dir: Path = PROVENANCE_ARCHIVE_DIR,
    apply: bool = False,
) -> dict:
    """Rotate completed years out of the active log into yearly archive files.

    Dry run by default (`apply=False`) — computes and returns the plan without
    touching any file. With `apply=True`, appends each sealed year's blocks to
    `PROVENANCE_LOG-<YYYY>.md` (creating it with a header), rewrites the active
    log to preamble + retained blocks, and records a ROTATION note. Never deletes
    entry text (§1.10)."""
    cutoff_year = cutoff_year if cutoff_year is not None else _current_utc_year()
    text = active_path.read_text(encoding="utf-8") if active_path.exists() else ""
    plan = plan_rotation(text, cutoff_year)
    plan["applied"] = False
    plan["archive_files"] = {}

    if not apply or plan["n_archived"] == 0:
        return plan

    archive_dir.mkdir(parents=True, exist_ok=True)
    for year, blocks in plan["archive_by_year"].items():
        dest = archive_dir / f"PROVENANCE_LOG-{year}.md"
        if not dest.exists():
            dest.write_text(_ARCHIVE_HEADER.format(year=year), encoding="utf-8")
        # Append verbatim; blocks already carry their leading/trailing newlines.
        append_provenance("".join(blocks), path=dest)
        plan["archive_files"][year] = str(dest)

    active_path.write_text(plan["new_active_text"], encoding="utf-8")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    years_str = ", ".join(str(y) for y in sorted(plan["archive_by_year"]))
    note = (
        f"\n### {stamp} — NOTE — provenance log rotation (§2.2)\n\n"
        f"Sealed {plan['n_archived']} entr"
        f"{'y' if plan['n_archived'] == 1 else 'ies'} for year(s) {years_str} out of "
        f"the active log into `catalog/provenance/PROVENANCE_LOG-<YYYY>.md`. Entry "
        f"text moved verbatim, not deleted (§1.10); the dashboard build parses the "
        f"active log + archives together so the changelog stays complete.\n"
    )
    append_provenance(note, path=active_path)
    plan["applied"] = True
    return plan
