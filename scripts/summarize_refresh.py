"""Surface per-owner refresh failures from the matrix bucket summaries.

Runs in the consolidate job of the matrix refresh workflow, next to
scripts/finalize_matrix.py. Each bucket's `cli refresh --summary-json` file
rides in its artifact; this aggregates them.

Why it exists: the bucket refresh step is `continue-on-error` (one owner's
failure must not throw away the other owners' work), so the matrix job stays
green even when owners fail. On 2026-09-01 and 2026-10-01 the run was green
while 7 and 12 owners failed every FEC fetch; nothing noticed for two months.

Inputs:
  --artifacts <dir>    Directory containing one subdir per bucket artifact.

Outputs (never fails the step — reporting must not block the commit):
  - a `::error::` annotation per failed owner, and per bucket with no summary
    (its refresh step crashed before writing one);
  - a markdown table appended to $GITHUB_STEP_SUMMARY;
  - `owners_failed`, `failed_owners`, `buckets_without_summary` in $GITHUB_OUTPUT.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


BUCKET_DIR_PATTERN = re.compile(r"^refresh-bucket-(\d+)$")
SUMMARY_NAME = "refresh-summary.json"
MAX_ERROR_CHARS = 300


def _escape_annotation(msg: str) -> str:
    # GitHub workflow-command escaping for the message part.
    return msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def collect(artifacts: Path) -> dict:
    """Aggregate every bucket summary under `artifacts`.

    A bucket artifact without a summary means its refresh step died before
    writing one — reported separately, since which owners it lost is unknown.
    """
    failed: dict[str, str] = {}
    attempted = succeeded = 0
    without_summary: list[int] = []
    buckets: list[tuple[int, Path]] = []
    if artifacts.is_dir():
        for child in artifacts.iterdir():
            m = BUCKET_DIR_PATTERN.match(child.name) if child.is_dir() else None
            if m:
                buckets.append((int(m.group(1)), child))
    for idx, d in sorted(buckets):
        path = d / SUMMARY_NAME
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            without_summary.append(idx)
            continue
        attempted += summary.get("owners_attempted") or 0
        succeeded += summary.get("owners_succeeded") or 0
        per_owner = summary.get("per_owner") or {}
        for slug in summary.get("failed_owners") or []:
            err = str((per_owner.get(slug) or {}).get("error") or "unknown error")
            failed[slug] = err[:MAX_ERROR_CHARS]
    return {
        "buckets_seen": len(buckets),
        "owners_attempted": attempted,
        "owners_succeeded": succeeded,
        "failed": failed,
        "buckets_without_summary": without_summary,
    }


def render_markdown(result: dict) -> str:
    lines = ["### Per-owner refresh results", ""]
    lines.append(
        f"- owners attempted: **{result['owners_attempted']}** · "
        f"succeeded: **{result['owners_succeeded']}** · "
        f"failed: **{len(result['failed'])}**"
    )
    if result["buckets_without_summary"]:
        idxs = ", ".join(str(i) for i in result["buckets_without_summary"])
        lines.append(f"- buckets with no summary (refresh step crashed): **{idxs}**")
    if result["failed"]:
        lines += ["", "| owner | error |", "|---|---|"]
        for slug, err in sorted(result["failed"].items()):
            cell = err.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| `{slug}` | {cell} |")
        lines += [
            "",
            "Failed owners keep their previous data and `audit.last_ingestion`; "
            "the next run retries them.",
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--artifacts", type=Path, required=True)
    args = p.parse_args(argv)

    result = collect(args.artifacts)
    for slug, err in sorted(result["failed"].items()):
        print(f"::error title=Refresh failed: {slug}::{_escape_annotation(err)}")
    for idx in result["buckets_without_summary"]:
        print(
            f"::error title=Refresh bucket {idx} wrote no summary::"
            "The bucket's refresh step crashed before finishing; its owners may not have refreshed."
        )

    md = render_markdown(result)
    print(md)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(md)
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as f:
            f.write(f"owners_failed={len(result['failed'])}\n")
            f.write(f"failed_owners={','.join(sorted(result['failed']))}\n")
            f.write(
                "buckets_without_summary="
                + ",".join(str(i) for i in result["buckets_without_summary"])
                + "\n"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
