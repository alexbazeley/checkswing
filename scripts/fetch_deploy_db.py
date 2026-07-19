#!/usr/bin/env python3
"""Fetch data/master.db from Cloudflare R2 for the Pages build.

Part of the LFS-bandwidth fix (docs/DEPLOY_lfs_r2_2026-07.md). The Pages build
runs with GIT_LFS_SKIP_SMUDGE=1, so the clone leaves data/master.db as its
~130-byte LFS pointer; this script overwrites it with the real database pulled
privately from R2 before mockup/build_data.py reads it.

Why a script instead of `aws s3 cp` in the build command:
  1. Cloudflare's build-command field collapses multi-line commands onto one
     line, turning `\\` continuations into literal escaped spaces — the shell
     then looks for a command named " aws" (observed: `/bin/sh: 1:  aws: not
     found`).
  2. `pip install awscli` mid-build drops the `aws` console script into the
     asdf-managed Python env *after* its shims were generated, so `aws` is not
     on PATH even when the command is well-formed.
Invoking `python` (always on PATH) with a repo script sidesteps both, and makes
the fetch reviewable and testable instead of a string in a dashboard field.

Fails loudly rather than falling back to Git LFS: a silent fallback would mask a
misconfiguration and quietly re-incur the bandwidth cost this exists to avoid.

Env (set as Cloudflare Pages environment variables):
  RAW_ARCHIVE_S3_BUCKET     bucket holding deploy/master.db
  RAW_ARCHIVE_S3_ENDPOINT   https://<ACCOUNT_ID>.r2.cloudflarestorage.com
  AWS_ACCESS_KEY_ID         read-only R2 token
  AWS_SECRET_ACCESS_KEY     read-only R2 token
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEST = REPO_ROOT / "data" / "master.db"
KEY = "deploy/master.db"
SQLITE_MAGIC = b"SQLite format 3\x00"


def fail(msg: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    bucket = os.environ.get("RAW_ARCHIVE_S3_BUCKET")
    endpoint = os.environ.get("RAW_ARCHIVE_S3_ENDPOINT")
    if not bucket or not endpoint:
        fail(
            "RAW_ARCHIVE_S3_BUCKET / RAW_ARCHIVE_S3_ENDPOINT are not set. "
            "Set them (and the read-only AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) "
            "in the Cloudflare Pages environment — see docs/DEPLOY_lfs_r2_2026-07.md §4b."
        )
    if not os.environ.get("AWS_ACCESS_KEY_ID") or not os.environ.get("AWS_SECRET_ACCESS_KEY"):
        fail(
            "AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are not set — the R2 object is "
            "private by design (master.db carries the unpublished review_queue)."
        )

    try:
        import boto3  # noqa: PLC0415  (deliberately late: only the build needs it)
        from botocore.exceptions import ClientError
    except ImportError:
        fail(
            "boto3 is not installed. The Pages build command should be:\n"
            "  python -m pip install -r requirements.txt boto3 && "
            "python scripts/fetch_deploy_db.py && python mockup/build_data.py"
        )

    # R2: region is the literal "auto". The checksum behaviours are also set as
    # Pages env vars (AWS_*_CHECKSUM_*=when_required) because newer botocore
    # otherwise sends integrity headers R2 rejects.
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.environ.get("AWS_DEFAULT_REGION", "auto"),
    )

    DEST.parent.mkdir(parents=True, exist_ok=True)
    print(f"Fetching s3://{bucket}/{KEY} → {DEST.relative_to(REPO_ROOT)}")
    try:
        client.download_file(bucket, KEY, str(DEST))
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "?")
        if code in ("404", "NoSuchKey"):
            fail(
                f"s3://{bucket}/{KEY} does not exist. Seed it by running the "
                "'Sync master.db to R2 (deploy)' workflow (Actions → Run workflow)."
            )
        if code in ("403", "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
            fail(
                f"R2 rejected the credentials ({code}). Check AWS_ACCESS_KEY_ID / "
                "AWS_SECRET_ACCESS_KEY belong to a token with Object Read access to "
                f"'{bucket}', and that RAW_ARCHIVE_S3_ENDPOINT is this account's R2 endpoint."
            )
        fail(f"R2 download failed ({code}): {e}")

    # The whole point is to replace an LFS pointer with a real database; if the
    # object itself were ever a pointer, build_data.py would fail confusingly
    # later. Check the magic bytes here instead.
    with DEST.open("rb") as fh:
        head = fh.read(len(SQLITE_MAGIC))
    if head != SQLITE_MAGIC:
        fail(
            f"{DEST.name} is not a SQLite database (got {head!r}). The R2 object may be "
            "a stale Git-LFS pointer — re-run the 'Sync master.db to R2 (deploy)' workflow."
        )

    mb = DEST.stat().st_size / 1024 / 1024
    print(f"OK: {DEST.name} is {mb:.1f} MB of SQLite — build can proceed.")


if __name__ == "__main__":
    main()
