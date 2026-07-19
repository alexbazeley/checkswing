# Deploy: taking `master.db` off the Git-LFS path (LFS-bandwidth fix)

**Status:** 📋 **runbook — not yet activated.** The mitigation in place today is a
raised GitHub Git-LFS budget ($5/mo cap, set 2026-07-19). This document is the
durable fix: stop Cloudflare Pages from pulling the 128 MB LFS database on every
build. Activating it needs three dashboard/credential actions only the
maintainer can take (marked **[maintainer]** below) plus one workflow step
(ready to paste). Everything is designed to activate in a single coordinated
pass and verify against a real deploy.

---

## 1. How the deploy works today

Cloudflare Pages builds the site from the repo via Git integration
(README §Deployment):

- **Production branch:** `main` · **Build output:** `mockup`
- **Build command:** `python -m pip install -r requirements.txt && python mockup/build_data.py`
- Every push to **any** branch triggers a build. Each build **clones the repo
  with Git LFS**, which pulls `data/master.db` (128 MB), then `build_data.py`
  reads it to regenerate `mockup/data.json` + `donations.json` +
  `provenance.json` + `beneficiaries/` (the generated JSON is gitignored, not
  committed).

## 2. The problem

Git LFS on the free tier includes **1 GB/mo of bandwidth**. Measured July 2026:

| consumer | LFS bandwidth | share |
|---|---|---|
| `cloudflare-workers-and-pages[bot]` | **8.2 GB** | **82 %** |
| `github-actions[bot]` (the monthly refresh) | 1.1 GB | 11 % |
| local / other | 0.7 GB | 7 % |
| **total** | **10.0 GB** | — |

≈ **63 Cloudflare builds** × 128 MB = the 8.2 GB. Because Pages rebuilds on
every push to every branch, the DB is re-pulled dozens of times a month purely
to regenerate ~36 MB of static JSON that only changes on the monthly refresh.
On 2026-07-19 the included allowance was exhausted, LFS downloads were
hard-stopped by a $0 budget, and **every deploy failed** (`This repository
exceeded its LFS budget`) until the budget was raised.

CI (`ci.yml`) is **not** a contributor — it checks out without `lfs: true` and
the tests use temp DBs, so it only ever pulls the LFS pointer. Leave it as is.
The monthly refresh jobs (`refresh.yml`) legitimately need the real DB and pull
it a handful of times a month; that ~1 GB is unavoidable and fine.

**So the entire excess is Cloudflare's per-build LFS pull.** That is the only
thing to change.

## 3. Why the obvious fix is wrong

The tempting fix — make the DB fetchable over a plain public URL and `curl` it —
is **not acceptable here.** `data/master.db` contains the `review_queue` table:
UNCERTAIN records that are, by governance policy (GOVERNANCE.md; VERIFICATION.md
three-tier standard), **deliberately never published**. A public object URL
would leak them. The deploy fetch must therefore be **private (credentialed)**.

## 4. The fix: fetch `master.db` from R2, skip LFS in the build

The repo already has a Cloudflare R2 bucket wired for raw-payload archival
(`RAW_ARCHIVE_*` secrets; `scripts/archive_raw.sh`; `docs/DESIGN_raw_archival_2026-07.md`).
Reuse it. R2 has **zero egress cost**, so the Cloudflare build can pull the DB
from R2 for free, and the LFS pointer is left un-smudged.

```
 refresh.yml (monthly)            Cloudflare Pages build (every push)
 ─────────────────────            ──────────────────────────────────
 fetch → classify → build         GIT_LFS_SKIP_SMUDGE=1  ← pointer only, 0 LFS
 commit master.db to main         aws s3 cp s3://…/deploy/master.db  ← private, free
 aws s3 cp master.db → R2  ─────▶ python mockup/build_data.py
```

### 4a. Workflow step — mirror the DB to R2 after each refresh **[ready to paste]**

Add this as the **last** step of the `committees_refresh` job in
`.github/workflows/refresh.yml` (it runs after the final `master.db` push, so R2
always mirrors `main`'s tip). It reuses the existing write-capable
`RAW_ARCHIVE_*` secrets and is a guarded no-op if they are unset — the same
pattern as `scripts/archive_raw.sh`.

```yaml
      - name: Mirror master.db to R2 for the Pages build
        if: always()
        env:
          RAW_ARCHIVE_S3_BUCKET: ${{ secrets.RAW_ARCHIVE_S3_BUCKET }}
          RAW_ARCHIVE_S3_ENDPOINT: ${{ secrets.RAW_ARCHIVE_S3_ENDPOINT }}
          AWS_ACCESS_KEY_ID: ${{ secrets.RAW_ARCHIVE_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.RAW_ARCHIVE_SECRET_ACCESS_KEY }}
          AWS_DEFAULT_REGION: auto
          AWS_REQUEST_CHECKSUM_CALCULATION: when_required
          AWS_RESPONSE_CHECKSUM_VALIDATION: when_required
        run: |
          set -euo pipefail
          if [ -z "${RAW_ARCHIVE_S3_BUCKET:-}" ]; then
            echo "RAW_ARCHIVE_* not set — skipping R2 mirror (deploy still on LFS)."
            exit 0
          fi
          # Pull the real DB in case this job checked out with the pointer.
          git lfs pull --include data/master.db || true
          aws s3 cp data/master.db "s3://${RAW_ARCHIVE_S3_BUCKET}/deploy/master.db" \
            --no-progress --only-show-errors \
            --endpoint-url "${RAW_ARCHIVE_S3_ENDPOINT}"
          echo "Mirrored master.db → s3://${RAW_ARCHIVE_S3_BUCKET}/deploy/master.db"
```

> One-time seed before the first post-activation refresh, run locally with the
> `RAW_ARCHIVE_*` values exported (so the Pages build has a DB to pull on day
> one rather than waiting for the next monthly cron):
>
> ```bash
> aws s3 cp data/master.db "s3://$RAW_ARCHIVE_S3_BUCKET/deploy/master.db" \
>   --endpoint-url "$RAW_ARCHIVE_S3_ENDPOINT"
> ```

### 4b. Cloudflare Pages settings **[maintainer — dashboard]**

Pages → **checkswing** → Settings → **Environment variables** (Production):

| variable | value |
|---|---|
| `GIT_LFS_SKIP_SMUDGE` | `1` |
| `RAW_ARCHIVE_S3_BUCKET` | the bucket name (e.g. `checkswing-raw`) |
| `RAW_ARCHIVE_S3_ENDPOINT` | `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` |
| `AWS_ACCESS_KEY_ID` | **a read-only** R2 token's Access Key ID (see 4c) |
| `AWS_SECRET_ACCESS_KEY` | that token's Secret Access Key |
| `AWS_DEFAULT_REGION` | `auto` |
| `AWS_REQUEST_CHECKSUM_CALCULATION` | `when_required` |
| `AWS_RESPONSE_CHECKSUM_VALIDATION` | `when_required` |

Then Settings → **Builds & deployments** → **Build command**:

```
python -m pip install -r requirements.txt awscli && \
aws s3 cp "s3://$RAW_ARCHIVE_S3_BUCKET/deploy/master.db" data/master.db \
  --endpoint-url "$RAW_ARCHIVE_S3_ENDPOINT" --only-show-errors && \
python mockup/build_data.py
```

`GIT_LFS_SKIP_SMUDGE=1` makes the clone leave `data/master.db` as its ~130-byte
pointer; the `aws s3 cp` overwrites it with the real DB before the build reads
it. `state.db` (3.7 MB) and `legislation.db` (1.8 MB) are **not** LFS objects, so
they clone normally and need no fetch.

### 4c. A read-only R2 token **[maintainer — Cloudflare R2]**

Do **not** put the write-capable `RAW_ARCHIVE_*` archival token into the Pages
build environment. Mint a second R2 API token scoped to **Object Read-only** on
this one bucket, and use its keys for `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` in 4b. If the Pages env ever leaks, the blast radius is
"can read a database of public FEC records," not "can delete the archive."

## 5. Verify (at activation)

1. Seed the object (4a note), or run `gh workflow run refresh.yml` and confirm
   the mirror step logs `Mirrored master.db → …`.
2. Trigger a Pages build (push a trivial commit or **Retry deployment**). The
   build log should show the `aws s3 cp` pulling ~128 MB from R2 and **no**
   `Downloading data/master.db (128 MB)` LFS line.
3. `curl -sI https://checkswing.pages.dev/` → `401` (the password gate answering
   = site served). Behind the gate, `data.json` loads.
4. A week later, check GitHub → Settings → Billing → Git LFS: the
   `cloudflare-workers-and-pages[bot]` line should be **~0**.

## 6. Rollback

Fully reversible, no data touched: in the Pages dashboard delete
`GIT_LFS_SKIP_SMUDGE` and restore the original build command
(`python -m pip install -r requirements.txt && python mockup/build_data.py`).
The next build clones with LFS again. The `refresh.yml` mirror step can stay —
it is a harmless no-op cost of one 128 MB R2 write per month.

## 7. Alternatives considered

- **Just keep the $5 LFS budget.** Already in place; caps the cost at
  ~$0.79/mo overage today with ~57 GB of headroom. Perfectly adequate if build
  volume stays flat — this fix is an optimization, not a rescue. Do it only if
  build counts (and thus LFS pulls) grow.
- **Cap Pages preview builds** (turn off automatic deploys for non-production
  branches). Free, one toggle, removes most of the 63 builds — but you lose
  per-PR preview URLs (which caught two real regressions during the GROUNDRULES
  pass), and it doesn't help the production branch's own rebuilds.
- **Commit the generated JSON, make the deploy a pure static serve.** Removes
  the DB from the build entirely, but commits ~36 MB/refresh (`data.json` +
  `donations.json` + `provenance.json` + 1,041 `beneficiaries/` files) — ~430
  MB/yr of git-history bloat and a churny tree. Rejected.
- **GitHub Actions + `wrangler pages deploy` (Direct Upload).** Cloudflare stops
  cloning the repo, so its LFS bandwidth goes to zero — but it reintroduces the
  `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` secrets that were
  deliberately dropped in `d448367`, loses preview URLs, and moves the DB pull
  into Actions. More surface area than the R2 fetch for the same result.

The R2 fetch wins: keeps preview builds, keeps `master.db` private, costs
nothing in egress, and reuses infrastructure the repo already has.
