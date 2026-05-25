"""
Parse catalog/PROVENANCE_LOG.md into structured entries for the dashboard.

The log is the audit trail (see CLAUDE.md §1, PROVENANCE_LOG.md preamble).
Each entry is a level-3 markdown heading followed by a body. The body uses
a small subset of markdown: paragraphs (blank-line separated), bullet
lists (- prefix), bold (**text**), inline code (`text`).

This parser is intentionally minimal — no external markdown library — and
intentionally defensive: malformed entries are skipped with a stderr
warning rather than crashing the build.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path
from typing import Iterable

# Heading: ### YYYY-MM-DD[THH:MMZ] — TYPE [trailing subject]
# em-dash is U+2014; the source uses it consistently.
# After TYPE, accept either ` — subject` or ` (parenthetical)` or nothing.
# The trailing block is captured raw and stripped of a leading em-dash later.
_HEADING_RE = re.compile(
    r"^###\s+(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2})Z)?\s+—\s+([A-Z_]+)\b\s*(.*?)\s*$"
)

# Inline formatting markers applied AFTER html-escape so they themselves
# can survive escaping. The markers are pure ASCII, not HTML special chars.
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")

# Known TYPE values (anything else is bucketed under NOTE for the chip color).
KNOWN_TYPES = {
    "INGESTION",
    "SIGNAL_CHANGE",
    "STATUS_CHANGE",
    "REVIEW_RESOLUTION",
    "SCHEMA_MIGRATION",
    "DELETION",
    "SETUP",
    "NOTE",
}


def _inline(text: str) -> str:
    """Escape HTML, then apply **bold** and `code` substitutions."""
    out = html.escape(text)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = _CODE_RE.sub(r"<code>\1</code>", out)
    return out


def _render_body(lines: Iterable[str]) -> str:
    """
    Convert body lines to HTML. Supports:
      - blank-line-separated paragraphs (<p>)
      - runs of '- ' bullets grouped into <ul><li>
      - inline-section headings: '#### text' → <h4>, '##### text' → <h5>
      - bold and inline code via _inline()
    """
    out: list[str] = []
    para: list[str] = []
    bullets: list[str] = []

    def flush_para() -> None:
        if para:
            out.append("<p>" + _inline(" ".join(s.strip() for s in para)) + "</p>")
            para.clear()

    def flush_bullets() -> None:
        if bullets:
            items = "".join(f"<li>{_inline(b)}</li>" for b in bullets)
            out.append(f"<ul>{items}</ul>")
            bullets.clear()

    for raw in lines:
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped:
            # Blank line: flush both buffers.
            flush_para()
            flush_bullets()
            continue
        # Inline section headings inside an entry body.
        if stripped.startswith("##### "):
            flush_para(); flush_bullets()
            out.append(f"<h5>{_inline(stripped[6:].strip())}</h5>")
            continue
        if stripped.startswith("#### "):
            flush_para(); flush_bullets()
            out.append(f"<h4>{_inline(stripped[5:].strip())}</h4>")
            continue
        if stripped.startswith("- "):
            flush_para()
            bullets.append(stripped[2:].strip())
            continue
        # Regular text line: a bullet run ends here.
        flush_bullets()
        para.append(stripped)

    flush_para()
    flush_bullets()
    return "".join(out)


def parse_heading(line: str) -> dict | None:
    """Parse one '### ...' heading. Returns dict or None if malformed."""
    m = _HEADING_RE.match(line)
    if not m:
        return None
    date, time_part, type_, trailing = m.groups()
    # Strip leading em-dash separator if present (the "TYPE — subject" form).
    subject = (trailing or "").lstrip("— ").strip()
    return {
        "date": date,
        "timestamp_utc": f"{date}T{time_part}Z" if time_part else date,
        "type": type_ if type_ in KNOWN_TYPES else "NOTE",
        "raw_type": type_,
        "subject": subject,
    }


def parse_provenance_log(text: str) -> list[dict]:
    """
    Parse the full PROVENANCE_LOG.md text into a list of entry dicts:
        {date, timestamp_utc, type, raw_type, subject, body_html}

    Entries are returned in source order (forward-chronological,
    oldest first — the file is append-only). The renderer is expected
    to reverse for display.

    Malformed headings are skipped with a stderr warning.
    """
    lines = text.splitlines()
    entries: list[dict] = []

    # Find all heading positions.
    heading_idx = [i for i, l in enumerate(lines) if l.startswith("### ")]
    if not heading_idx:
        return []

    heading_idx.append(len(lines))  # sentinel for last-slice end

    for start, end in zip(heading_idx[:-1], heading_idx[1:]):
        head = parse_heading(lines[start])
        if head is None:
            print(
                f"parse_provenance: skipping malformed heading at line {start + 1}: "
                f"{lines[start][:80]!r}",
                file=sys.stderr,
            )
            continue
        body_lines = lines[start + 1 : end]
        try:
            head["body_html"] = _render_body(body_lines)
        except Exception as exc:  # pragma: no cover — defensive
            print(
                f"parse_provenance: skipping entry at line {start + 1} "
                f"({head.get('date')} {head.get('type')}): body render failed: {exc}",
                file=sys.stderr,
            )
            continue
        entries.append(head)

    return entries


def parse_provenance_file(path: str | Path) -> list[dict]:
    """Convenience wrapper that reads the file from disk."""
    return parse_provenance_log(Path(path).read_text(encoding="utf-8"))
