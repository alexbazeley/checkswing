"""The READ side of the §2.1 off-runner raw archive.

`scripts/archive_raw.sh` writes `data/raw/` to `s3://<bucket>/raw/`. This module
reads it back, so the rest of the codebase can tell two very different situations
apart:

  * **missing locally, present in the bucket** — recoverable. Common and
    growing: every cron-fetched payload lives only in R2, because the runner
    uploads it and is then destroyed. Nothing about such a row is wrong.
  * **missing locally AND absent from the bucket** — truly lost. The raw is
    gone for good (FEC will not re-serve an old Schedule A page; the
    `malone-john` rows are the standing example).

Until now every consumer checked local disk alone, so those two collapsed into
one "missing raw" verdict — which made the reclassify guard a dead end rather
than a recoverable state, exactly the outcome the §2.1 design set out to avoid.

Design notes:

* **boto3 is imported lazily.** It is deliberately NOT in `requirements.txt` —
  the pins there are installed by every CI job and the Cloudflare build, and
  only these R2 paths need an S3 client (`fetch_deploy_db.py` takes the same
  approach). A missing boto3 is reported, never guessed at.
* **One paginated LIST, not N HEADs.** Answering "are these 379 paths present?"
  with a HEAD per path is 379 round trips; listing the `raw/` prefix once is
  ~11 requests for the whole ~11k-object archive and gives an exact set.
* **Absent credentials are not an error.** `bucket_status()` returns
  UNCONFIGURED, and callers degrade to local-only reporting. A developer with
  no R2 access must still be able to run `raw-coverage` and `reclassify`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .paths import REPO_ROOT

# archive_raw.sh runs `aws s3 sync data/raw/ s3://<bucket>/raw/`, so the key is
# the repo-relative path with `data/raw/` swapped for `raw/`.
LOCAL_PREFIX = "data/raw/"
BUCKET_PREFIX = "raw/"

UNCONFIGURED = "unconfigured"
UNAVAILABLE = "unavailable"
OK = "ok"


@dataclass
class BucketStatus:
    """Outcome of trying to read the archive. `keys` is meaningful only on OK."""

    state: str
    keys: set[str] = field(default_factory=set)
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.state == OK


def bucket_key_for(rel_path: str) -> str:
    """`data/raw/<slug>/<file>` → `raw/<slug>/<file>`.

    Paths already relative to `data/raw/` (or absolute, as in tests) are mapped
    on their tail so the function is total rather than raising on odd input.
    """
    p = str(rel_path)
    if p.startswith(LOCAL_PREFIX):
        return BUCKET_PREFIX + p[len(LOCAL_PREFIX):]
    # Absolute path inside the repo → make it repo-relative first.
    try:
        rel = Path(p).resolve().relative_to(REPO_ROOT)
        s = str(rel)
        if s.startswith(LOCAL_PREFIX):
            return BUCKET_PREFIX + s[len(LOCAL_PREFIX):]
    except (ValueError, OSError):
        pass
    return BUCKET_PREFIX + p.lstrip("/")


def is_configured() -> bool:
    """True when the four env vars an R2 read needs are all present."""
    return all(
        os.environ.get(k)
        for k in (
            "RAW_ARCHIVE_S3_BUCKET",
            "RAW_ARCHIVE_S3_ENDPOINT",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
        )
    )


def _client():
    """A boto3 S3 client pointed at R2. Raises RuntimeError with a fixable message."""
    try:
        import boto3  # noqa: PLC0415  (deliberately late — see module docstring)
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "boto3 is not installed, so the R2 archive cannot be read. "
            "It is intentionally not in requirements.txt (only the R2 paths need it): "
            "`pip install boto3`."
        ) from exc
    return boto3.client(
        "s3",
        endpoint_url=os.environ["RAW_ARCHIVE_S3_ENDPOINT"],
        # R2 wants the literal "auto", and newer botocore otherwise sends
        # integrity headers R2 rejects (same settings archive_raw.sh exports).
        region_name=os.environ.get("AWS_DEFAULT_REGION", "auto"),
    )


def bucket_status(prefix: str = BUCKET_PREFIX) -> BucketStatus:
    """List every key under `prefix`, or explain why we can't.

    Never raises: an unreadable archive degrades reporting, it must not break
    `raw-coverage` or abort a reclassify.
    """
    if not is_configured():
        return BucketStatus(
            UNCONFIGURED,
            detail=(
                "RAW_ARCHIVE_* / AWS_* env vars are not set — reporting local disk only. "
                "See SOURCES.md for the bucket and endpoint (they are configuration, "
                "not secrets); only the key pair is secret."
            ),
        )
    try:
        client = _client()
        keys: set[str] = set()
        token = None
        bucket = os.environ["RAW_ARCHIVE_S3_BUCKET"]
        while True:
            kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kwargs["ContinuationToken"] = token
            resp = client.list_objects_v2(**kwargs)
            keys.update(o["Key"] for o in resp.get("Contents", ()))
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
            if not token:
                break
        return BucketStatus(OK, keys=keys)
    except Exception as exc:  # noqa: BLE001 - any failure degrades to local-only
        return BucketStatus(UNAVAILABLE, detail=f"{type(exc).__name__}: {exc}")


def download(rel_path: str, dest: Path | None = None) -> Path:
    """Pull one raw payload out of R2 to its canonical local location.

    Restores to `dest` (default: the repo-relative path the DB records), so a
    rehydrated payload is picked up by `load_raw_payloads` with no further
    bookkeeping — that is the whole point of the key layout mirroring the
    on-disk one.
    """
    if not is_configured():
        raise RuntimeError("RAW_ARCHIVE_* / AWS_* are not set — cannot read the R2 archive.")
    target = Path(dest) if dest else (REPO_ROOT / rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    client = _client()
    client.download_file(
        os.environ["RAW_ARCHIVE_S3_BUCKET"], bucket_key_for(rel_path), str(target)
    )
    return target
