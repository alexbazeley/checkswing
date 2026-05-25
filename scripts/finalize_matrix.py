"""Adopt per-bucket YAML + log changes into the consolidated tree.

Companion to scripts/merge_buckets.py (which handles master.db). Runs in the
consolidate job of the matrix refresh workflow after the DB merge.

Inputs:
  --base <dir>         The pre-refresh tree (the consolidate job's checkout).
                       Modified in place.
  --artifacts <dir>    Directory containing one subdir per bucket artifact,
                       each laid out like the repo (owners/, catalog/, …).

What it does
------------
1. owners/*.yaml: each bucket only modifies the YAMLs of owners it processed.
   Disjoint by construction (matrix bucketing). For each bucket artifact, copy
   any owner YAML that differs from base into base.
2. catalog/PROVENANCE_LOG.md and catalog/REVIEW_QUEUE.md: append-only logs.
   For each bucket, take the lines added relative to base and append them to
   the consolidated file. Order: bucket-0 first, then bucket-1, etc.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


BUCKET_DIR_PATTERN = re.compile(r"^refresh-bucket-(\d+)$")


def _bucket_dirs(artifacts: Path) -> list[Path]:
    """Return artifact subdirs sorted by bucket index."""
    dirs: list[tuple[int, Path]] = []
    for child in artifacts.iterdir():
        m = BUCKET_DIR_PATTERN.match(child.name) if child.is_dir() else None
        if m:
            dirs.append((int(m.group(1)), child))
    dirs.sort(key=lambda x: x[0])
    return [p for _, p in dirs]


def _adopt_yamls(
    base: Path, bucket_root: Path, original_base_yamls: dict[str, str]
) -> list[str]:
    """Copy YAMLs the bucket actually changed (vs. the pre-refresh snapshot).

    Each bucket's artifact contains ALL owner YAMLs, not just the ones it
    processed. To avoid bucket B silently reverting bucket A's edits to a
    shared YAML (won't happen with disjoint bucketing, but defensively safe),
    we compare each bucket YAML against the pre-refresh snapshot of base.
    A YAML is adopted only when bucket differs from the original.
    """
    adopted: list[str] = []
    base_owners = base / "owners"
    bucket_owners = bucket_root / "owners"
    if not bucket_owners.is_dir():
        return adopted
    for ypath in sorted(bucket_owners.glob("*.yaml")):
        bucket_text = ypath.read_text(encoding="utf-8")
        original = original_base_yamls.get(ypath.name)
        if bucket_text == original:
            continue  # bucket left this YAML untouched
        target = base_owners / ypath.name
        target.write_text(bucket_text, encoding="utf-8")
        adopted.append(ypath.stem)
    return adopted


def _append_log_suffix(base_text: str, bucket_file: Path, target_file: Path, label: str) -> int:
    """If bucket_file == base_text + suffix, append suffix to target_file. Returns line count.

    PROVENANCE_LOG.md and REVIEW_QUEUE.md are strictly append-only inside
    refresh_all (see refresh.py:_append_refresh_log and the review pipeline).
    Passing the pre-refresh base_text snapshot lets multiple bucket appends
    each diff against the same baseline rather than each other's writes.
    """
    if not bucket_file.exists():
        return 0
    bucket_text = bucket_file.read_text(encoding="utf-8")
    if len(bucket_text) <= len(base_text):
        return 0
    if not bucket_text.startswith(base_text):
        print(
            f"warn: {label} bucket file does not start with base content; "
            f"skipping append from {bucket_file}.",
            file=sys.stderr,
        )
        return 0
    suffix = bucket_text[len(base_text):]
    target_file.parent.mkdir(parents=True, exist_ok=True)
    with target_file.open("a", encoding="utf-8") as f:
        f.write(suffix)
    return suffix.count("\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--base", required=True, type=Path)
    p.add_argument("--artifacts", required=True, type=Path)
    args = p.parse_args(argv)

    if not args.base.is_dir():
        print(f"--base not a directory: {args.base}", file=sys.stderr)
        return 2
    if not args.artifacts.is_dir():
        print(f"--artifacts not a directory: {args.artifacts}", file=sys.stderr)
        return 2

    bucket_dirs = _bucket_dirs(args.artifacts)
    if not bucket_dirs:
        print(f"no bucket artifacts under {args.artifacts}", file=sys.stderr)
        return 2

    # Snapshot pre-refresh state BEFORE we start applying buckets. Every
    # bucket's diff is against this snapshot — never against the
    # partially-consolidated state — so applying bucket B can't undo bucket A.
    provenance_base = args.base / "catalog" / "PROVENANCE_LOG.md"
    review_base = args.base / "catalog" / "REVIEW_QUEUE.md"
    base_provenance_text = (
        provenance_base.read_text(encoding="utf-8") if provenance_base.exists() else ""
    )
    base_review_text = (
        review_base.read_text(encoding="utf-8") if review_base.exists() else ""
    )
    base_owners_dir = args.base / "owners"
    original_yamls: dict[str, str] = {
        p.name: p.read_text(encoding="utf-8")
        for p in base_owners_dir.glob("*.yaml")
    } if base_owners_dir.is_dir() else {}

    print(f"base: {args.base}")
    for bdir in bucket_dirs:
        adopted = _adopt_yamls(args.base, bdir, original_yamls)
        prov_added = _append_log_suffix(
            base_provenance_text, bdir / "catalog" / "PROVENANCE_LOG.md",
            provenance_base, label="PROVENANCE_LOG",
        )
        rev_added = _append_log_suffix(
            base_review_text, bdir / "catalog" / "REVIEW_QUEUE.md",
            review_base, label="REVIEW_QUEUE",
        )
        print(
            f"  {bdir.name}: adopted {len(adopted)} YAML(s), "
            f"appended {prov_added} PROVENANCE line(s), {rev_added} REVIEW_QUEUE line(s)"
        )
        if adopted:
            print(f"    yaml slugs: {adopted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
