#!/usr/bin/env bash
# §2.1 — archive the raw FEC/state payloads this run fetched to durable
# off-runner storage (Cloudflare R2, S3-compatible), so cron-ingested rows'
# raw_payload_path resolves off the ephemeral runner (GOVERNANCE.md §1.4).
#
# NO-OP until the maintainer provisions the RAW_ARCHIVE_* secrets — safe to ship
# the plumbing first. Each runner starts clean, so `data/raw/` here contains only
# what THIS run fetched; the key layout mirrors the on-disk path
# (data/raw/<slug>/<file> → s3://<bucket>/raw/<slug>/<file>) so a stored
# raw_payload_path maps to an object by a simple prefix swap.
#
# See docs/DESIGN_raw_archival_2026-07.md for the provisioning + backfill steps.
set -euo pipefail

if [ -z "${RAW_ARCHIVE_S3_BUCKET:-}" ]; then
  echo "RAW_ARCHIVE_S3_BUCKET not set — skipping raw archival (§2.1 not yet provisioned)."
  exit 0
fi
if [ ! -d data/raw ]; then
  echo "No data/raw on this runner — nothing to archive."
  exit 0
fi

# R2 quirks vs. AWS S3: region is the literal "auto", and newer aws-cli integrity
# checksums must be requested only when required or R2 can reject the PUT.
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-auto}"
export AWS_REQUEST_CHECKSUM_CALCULATION="${AWS_REQUEST_CHECKSUM_CALCULATION:-when_required}"
export AWS_RESPONSE_CHECKSUM_VALIDATION="${AWS_RESPONSE_CHECKSUM_VALIDATION:-when_required}"

# Which aws CLI to invoke. GitHub runners ship one on PATH, so the default is
# right in CI. Override for the one-time local backfill when the CLI is only
# available inside a virtualenv: a pip-installed `aws` has an absolute-path
# shebang, which is BROKEN when the checkout path contains a space (as this
# project's does — ".../Tipping Pitches/..." → "bad interpreter"). The module
# entry point has no such problem:
#     AWS_CLI="python -m awscli" bash scripts/archive_raw.sh
# Deliberately unquoted below so a multi-word value word-splits into argv.
AWS_CLI="${AWS_CLI:-aws}"

echo "Archiving data/raw/ → s3://${RAW_ARCHIVE_S3_BUCKET}/raw/ (Cloudflare R2)…"
# `sync`, not `cp --recursive`: sync skips objects already present at the same
# size, so an interrupted transfer resumes instead of restarting. Identical
# behaviour in CI (each runner starts with an empty data/raw, so there is
# nothing to skip and everything uploads), but it is what makes the one-time
# ~4.9 GB / ~11k-file backfill from the maintainer's laptop survivable — that
# upload will not complete in one uninterrupted run, and re-running `cp
# --recursive` would re-send the whole 4.9 GB every attempt.
# shellcheck disable=SC2086  # AWS_CLI must word-split; see the note above.
$AWS_CLI s3 sync data/raw/ "s3://${RAW_ARCHIVE_S3_BUCKET}/raw/" \
  --no-progress --only-show-errors \
  --endpoint-url "${RAW_ARCHIVE_S3_ENDPOINT}"
echo "Raw archival complete."
