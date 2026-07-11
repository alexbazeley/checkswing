# Design / scoping — off-runner raw-payload archival

**Status:** design-first proposal (IMPROVEMENT_PLAN_2026-07 §2.1, the (L) ★ item). **Blocked on one user decision** (storage backend + credentials — §3). Not yet implemented.
**Author:** 2026-07-11 session. Grounded in live repo + `master.db` + `refresh.yml`.
**Companion rules:** [GOVERNANCE.md §1.4](../GOVERNANCE.md) (raw is persisted before parsing; master.db is the source of truth).

---

## 1. The gap (measured)

GOVERNANCE §1.4: *"Every API call's response is saved verbatim … before any parsing … the best-effort ground truth for re-verification and reclassification."* That guarantee is **structurally broken for automation-era rows**:

- `data/raw/` is **gitignored** and exists on exactly one laptop (**4.9 GB** — 4.0 GB state portal extracts, ~0.9 GB federal owner raw).
- The monthly `refresh.yml` fetches on ephemeral GitHub runners and uploads **only** `data/master.db` + `owners/*.yaml` + `PROVENANCE_LOG.md` as the bucket artifact (7-day retention). **Raw is never uploaded — it dies with the runner.**
- So **every cron-ingested row's `raw_payload_path` points at a file that exists nowhere.** Measured now: **557 of 4,292** federal donations already reference a raw file missing on this disk (the malone-john 54 that FEC can no longer return, plus cron-era rows).

This is the single largest gap between GOVERNANCE's promises and reality. `reclassify` is guarded against silently dropping rows whose raw is missing — but the guard turns a broken-provenance row into an *un-reclassifiable* row, which is a correctness ceiling, not a fix.

---

## 2. Design goals & non-goals

**Goals**
1. Persist per-run `data/raw` **deltas** to durable off-runner storage, keyed by run id, from every fetch workflow (federal `refresh.yml`, state `refresh-state.yml`).
2. Backfill the existing local 4.9 GB once.
3. Make a row's raw **retrievable** given its `raw_payload_path` (so reclassify/audit can rehydrate on demand).
4. Record the bucket in `SOURCES.md`; add an honest GOVERNANCE §1.4 caveat **until** this ships.
5. Stay inside the free/cheap tier — the project deliberately runs monthly to fit GitHub's free Git-LFS tier; raw archival must not reintroduce a cost cliff.

**Non-goals**
- Not making master.db reconstructible-from-raw (GOVERNANCE already says master.db is the source of truth; raw is a re-verification aid).
- Not moving raw *into* git/LFS (huge, and the user has said leave LFS as-is).
- Not real-time/streaming — a per-run batch upload is sufficient.

---

## 3. ★ USER DECISION — storage backend + credentials

This is the one thing that blocks implementation, and it is the maintainer's call (it provisions an account, a bucket, and a secret — I should not create credentials or pick a paid vendor unilaterally).

| Option | $/mo for ~5 GB + monthly delta | Egress | Notes |
|---|---|---|---|
| **Cloudflare R2** *(recommended)* | ~$0.08/mo storage (10 GB free tier) | **$0 egress, always** | Same vendor as the Pages deploy; one dashboard; S3-compatible API; the free tier likely covers this project outright. |
| Backblaze B2 | ~$0.006/GB (~$0.03/mo) | first 3× storage free, then $0.01/GB | Cheapest raw storage; S3-compatible; separate account. |
| AWS S3 | ~$0.023/GB (~$0.12/mo) | $0.09/GB egress | Most ubiquitous; egress cost matters if rehydrating often. |

**Recommendation: Cloudflare R2.** The archive already deploys on Cloudflare, its 10 GB free tier likely covers the whole 4.9 GB + monthly deltas, and **zero egress** means rehydrating raw for a reclassify costs nothing. All three are S3-compatible, so the implementation is backend-agnostic (an `S3_*`/`R2_*` env block) and the choice is reversible.

**What the maintainer provisions (I cannot):** the bucket, and a scoped access key id + secret added as GitHub Actions secrets (e.g. `RAW_ARCHIVE_S3_ENDPOINT`, `RAW_ARCHIVE_S3_BUCKET`, `RAW_ARCHIVE_ACCESS_KEY_ID`, `RAW_ARCHIVE_SECRET_ACCESS_KEY`). Handling those secrets is the maintainer's action per the credential rules.

---

## 4. Proposed design (once the backend is chosen)

### 4.1 Key layout

Mirror the on-disk layout under a run-scoped prefix so a `raw_payload_path` maps deterministically to an object:

```
s3://<bucket>/raw/<owner-slug>/<UTC-timestamp>__<endpoint>.json     # federal
s3://<bucket>/raw/state/<juris>/<...>                                # state
```

`raw_payload_path` in master.db **stays the relative repo path** (`data/raw/owner/…json`) — no schema change. Retrieval maps `data/raw/…` → `s3://<bucket>/raw/…` by prefix swap. (A future option: store a full `s3://` URI, but the prefix convention avoids a migration and keeps local-disk lookups working unchanged.)

### 4.2 Workflow hook

Add one step to each fetch workflow, after the fetch/backfill and **before** the runner is torn down. Upload only the **delta** this run produced (new files under `data/raw/` created this run — trivially, everything, since the runner starts clean):

```yaml
- name: Archive raw payloads (delta) to durable store
  if: always() && steps.gate.outputs.run == 'true' && env.RAW_ARCHIVE_S3_BUCKET != ''
  env: { ...the S3_* secrets... }
  run: |
    aws s3 cp data/raw/ "s3://$RAW_ARCHIVE_S3_BUCKET/raw/" \
      --recursive --no-progress --only-show-errors \
      --endpoint-url "$RAW_ARCHIVE_S3_ENDPOINT"
```

- Guarded on the secret being set → **no-op until the maintainer provisions the bucket** (safe to merge the plumbing first).
- `aws s3 cp --recursive` is idempotent (re-uploads overwrite the same key); state extracts are large but change monthly, so scope state to changed files or accept the monthly re-put (R2 egress is free; storage is pennies).
- Runs on **both** `refresh.yml` and `refresh-state.yml`; the state one is where the 4 GB lives, so gate it to only upload the freshly-downloaded portal file, not the whole state tree, to bound bandwidth.

### 4.3 Retrieval

A small `cli fetch-raw <transaction_id|path>` (read-only): map the stored `raw_payload_path` to the bucket key, download to a temp dir, return the path — so `reclassify`/`audit`/the raw-coverage probe can rehydrate a missing row on demand instead of aborting. This makes the reclassify guard *recoverable* (fetch raw → reclassify) instead of a dead end.

### 4.4 Backfill (one-time)

A documented one-shot: `aws s3 cp data/raw/ s3://…/raw/ --recursive` from the laptop that holds the 4.9 GB, logged as a PROVENANCE entry. After it, `raw-coverage` gains a bucket-aware mode that reports "missing locally **and** in bucket" (the truly-lost, e.g. the FEC-unreturnable malone rows) vs "missing locally but in bucket" (recoverable).

---

## 5. The small, unblocked companion fix (do now, no bucket needed)

§2.1 also flags (S): beneficiary rows all cite the **last** pagination page's raw file (`ingest_committee_disbursements.py:225`) — a per-row provenance inaccuracy independent of the bucket. Thread the per-page raw path through so each disbursement row points at the page it actually came from. This is a normal code PR and needs no infrastructure decision — recommend landing it separately and immediately.

---

## 6. Interim honesty (do now)

Until the bucket exists, add a one-line caveat to GOVERNANCE §1.4 stating plainly that **automation-era rows' raw payloads are not currently preserved off the fetching runner** — so the doc matches reality. (The doc currently implies raw is always available; §1.4 already hedges "not guaranteed to be preserved," but it should name the cron gap explicitly.) This is a docs-only change and can ride with the beneficiary fix.

---

## 7. Sequencing

1. **Now, unblocked:** (a) the beneficiary per-page raw path fix (§5); (b) the GOVERNANCE §1.4 cron caveat (§6). One small PR each, no infra.
2. **Maintainer decision:** pick R2 / B2 / S3, provision the bucket, add the four GitHub secrets (§3).
3. **Then:** land the secret-guarded upload step on both workflows (no-op until secrets exist), run the one-time backfill, add `cli fetch-raw` + bucket-aware `raw-coverage`, record the bucket in `SOURCES.md`, and update the GOVERNANCE §1.4 caveat to "preserved in <bucket>."

**Acceptance:** a cron run's raw is retrievable from the bucket by `raw_payload_path`; `raw-coverage` distinguishes recoverable-from-bucket vs truly-lost; the local 4.9 GB is backfilled; GOVERNANCE §1.4 is true again.

---

## 8. Why this is design-first, not just-do-it

The upload plumbing is ~20 lines of YAML. The blocker is entirely the **storage-backend + credentials decision (§3)**, which is the maintainer's to make — it commits an account and a (small) recurring cost and requires provisioning secrets. Everything downstream is backend-agnostic S3 API, so the decision is low-stakes and reversible, but it is a decision, and per the working rules a fresh credential/secret is provisioned by the maintainer, not the agent.
