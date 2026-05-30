# MLB Owner FEC Donations Archive

A continuously-updated database of every federal political donation reported to the FEC by MLB principal owners and their immediate, documented entities. Built for Tipping Pitches research.

## What this is

- A SQLite database (plus CSV exports) of donations attributed to MLB owners and their tracked related entities (spouses, family members, business PACs).
- Sourced from the OpenFEC API at `api.open.fec.gov`.
- Updated on a recurring cadence (quarterly FEC reporting cycle + as-needed).
- Conservative attribution: every donation is matched against a defined signal set before being marked CONFIRMED. PROBABLE matches are included with explicit qualification. UNCERTAIN matches go to a review queue, never to the export.

## What this is not

- Not state or local campaign-finance data. Federal only (Phase 4 may expand).
- Not "dark money" / 501(c)(4) giving. FEC data only.
- Not legislative cross-referencing. That's Phase 3.
- Not editorial. The data is the data. Narrative analysis lives in `reports/`.
- Not a replacement for OpenSecrets. It's complementary, with per-owner provenance OpenSecrets doesn't provide.

## Why it exists

MLB owners are political actors. The FEC publishes all of their donations. But reconstructing one owner's giving across cycles, name variations, and employer changes is slow and error-prone unless someone has done it carefully and documented the work. This archive is that.

## How to read it

- **Authoritative truth**: the YAML files in `owners/` define who is tracked and how matches are verified.
- **The data**: `data/master.db` (SQLite) is canonical. Per-owner CSVs at `data/donations/<slug>/` mirror the same content for spreadsheet use.
- **The audit trail**: `catalog/PROVENANCE_LOG.md` logs every ingestion, signal change, and adjudication.
- **The rules**: `CLAUDE.md` is the operating manual. Read it before any work in this folder.

## Quick start

To work on this project, read in order:
1. `CLAUDE.md` — operating rules
2. `CHARTER.md` — scope and phases
3. `VERIFICATION.md` — how donations are classified
4. `OWNER_SCHEMA.md` — how owner YAMLs are structured
5. `DONATION_SCHEMA.md` — the database schema
6. `SOURCES.md` — what counts as a source
7. `NAMING.md` — file conventions
8. `CLAUDE_CODE_PROMPT.md` — starter prompt for the implementation session

## Status

- **Phase 0** (setup): complete — files + project structure live 2026-05-22.
- **Phase 1** (Cohen pilot): complete — worked-pilot precedent for signal calibration.
- **Phase 1.5** (4 more pilot owners): complete.
- **Phase 2** (remaining owners): complete — 36 owners ingested across 14 election cycles.
- **Phase 3** (legislative cross-reference): not started.
- **Phase 4** (state/local): not started.
- **Phase 5** (maintenance automation): complete — `scripts/refresh.py` + weekly GitHub Actions cron.
- **CheckSwing dashboard** (public-facing site at `mockup/index.html`): live on Cloudflare Pages.

See `CHARTER.md` for phase definitions and exit criteria.

## Deployment

The public dashboard ships as a static site on **Cloudflare Pages**, using their direct Git integration.

**One-time Cloudflare setup:**
1. Cloudflare dashboard → **Workers & Pages** → **Create** → **Pages** tab → **Connect to Git** → authorize the GitHub repo `alexbazeley/checkswing`.
2. Configure the build:
   - **Production branch:** `main`
   - **Build command:** `python -m pip install -r requirements.txt && python mockup/build_data.py`
   - **Build output directory:** `mockup`
   - **Environment variables (Production):**
     - `PYTHON_VERSION` = `3.11` (Cloudflare's default Python is older; pin to match the codebase)
3. Optional: configure a custom domain in the Pages project settings.
4. Save & deploy — Cloudflare will run the build on every push to `main`.

**GitHub Secrets** (one only, for the weekly refresh):
   - `FEC_API_KEY` — for `.github/workflows/refresh.yml`. **Never** appears in Cloudflare's environment.

**How deploys happen:**
- Cloudflare watches `main` for any push. On push, it runs the build command above (`pip install` + `build_data.py`), then deploys the contents of `mockup/` to the Pages project.
- `mockup/_headers` sets CSP (allows Google Fonts + inline JS/CSS), cache controls (5min for `data.json`, immutable for `assets/*`), and `X-Frame-Options: DENY`.

**Why `data/master.db` is committed:** it is the project's **durable source of truth** (CLAUDE.md §1.4) for the archive and the dashboard. `mockup/data.json` is regenerated at every Cloudflare build from `master.db`, so committing it would just thrash the diff. Raw FEC payloads (`data/raw/`) stay out of git — they are large and best-effort ground truth (persisted before parsing for re-verification), but **not** a guaranteed backup: some historical rows reference raw files no longer on disk. The DB is therefore not assumed reconstructible from raw alone; `reclassify` is guarded against silently dropping rows whose raw is missing (`python -m scripts.cli raw-coverage` audits the gap).

## Refresh cadence

`.github/workflows/refresh.yml` runs weekly (Monday 12:00 UTC) and can also be triggered manually via the Actions tab (`workflow_dispatch`).

What it does, in order, per active owner:
1. Reads `audit.last_ingestion` from the owner's YAML.
2. Calls the OpenFEC API for filings since that date (jittered backoff, per-cycle chunking, per-variant checkpointing — see [scripts/fetch_fec.py](scripts/fetch_fec.py)).
3. Classifies new records against the owner's signal block.
4. Writes new CONFIRMED + PROBABLE rows to `data/master.db`; routes UNCERTAINs to `catalog/REVIEW_QUEUE.md`.
5. Updates `audit.last_ingestion` on success only — failures leave the field untouched so next run retries the same window.
6. Once all owners are processed: rebuilds `mockup/data.json` and pushes the result, which triggers a redeploy.

**⚠️ `--full-refetch` warning:** plain `python -m scripts.cli ingest <slug>` reads `audit.last_ingestion` and only fetches *since* that date. To re-fetch full history (e.g., after a classifier bug fix), pass `--full-refetch` explicitly. The refresh layer always uses the incremental mode.

## Calibration playbook

Every signal change to an owner YAML is a deliberate, version-controlled edit with a `change_log` entry inside the YAML (see CLAUDE.md §1.7). The reproducible workflow is:

1. `python -m scripts.cli audit <slug>` — read-only audit; prints signal block, PROBABLE clusters by employer/ZIP, review-queue reasons, suggestion checklist.
2. Inspect raw payloads for ambiguous records (`data/raw/<slug>/*.json`).
3. Edit the owner YAML; add a `change_log` entry citing pre-cal counts + rationale.
4. `python -m scripts.cli validate` — must pass.
5. `python -m scripts.cli reclassify <slug> --yes --reason "..."` — re-applies classifier against immutable raw payloads (no FEC API calls).
6. Verify post-cal counts. If CONFIRMED dropped > 5%, investigate before accepting (the 5% alarm caught real misattributions in fisher-john Tier-A calibration).

See change_log entries in `owners/cohen-steven.yaml` (Phase 1 precedent), the five Tier-A owners (Tier-A round 2026-05-24), and the Tier-B sweep (2026-05-25) for worked examples.
