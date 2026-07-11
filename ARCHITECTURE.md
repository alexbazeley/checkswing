# ARCHITECTURE

A one-page map of the archive. Read [CHARTER.md](CHARTER.md) for scope and
[GOVERNANCE.md](GOVERNANCE.md) for the data-integrity rules; this is the "where
does each piece live" overview.

## Three databases

| DB | What | Source | Tracked as |
|---|---|---|---|
| `data/master.db` | Federal FEC donations + committee/filing/beneficiary enrichment (the core archive) | OpenFEC API | **git LFS** |
| `data/state.db` | Phase-4 state campaign-finance donations, 10 jurisdictions | Official state portals | plain git blob |
| `data/legislation.db` | Phase-3 bills, sponsors, roll-call votes, legislator↔committee membership | Congress.gov + Clerk/LIS XML | plain git blob |

`master.db` is the source of truth (GOVERNANCE §1.4). Raw API payloads land in
`data/raw/` (gitignored, best-effort); pre-mutation snapshots in `data/snapshots/`
(gitignored, pruned by `cli prune-snapshots`). Schema + migrations live in
`scripts/db.py` (federal, currently v9) and `scripts/state_db.py`.

## The pipeline (per owner)

`fetch_fec` (persist raw → §1.4) → `resolve_entities.classify` (the three-tier
standard → VERIFICATION.md) → `ingest` (write CONFIRMED/PROBABLE to `donations`,
UNCERTAIN to `review_queue`, log to `PROVENANCE_LOG`). State mirrors this via
`ingest_state` + per-portal adapters (`*_adapter.py`) and fetchers (`fetch_*.py`).
The CLI (`python -m scripts.cli`) is the entry point for every operation.

## Four workflows (`.github/workflows/`)

| Workflow | Cadence | Touches |
|---|---|---|
| `refresh.yml` | monthly (1st) | federal `master.db` — 4-bucket matrix → consolidate → committee enrichment → CSV export → push |
| `refresh-state.yml` | monthly (2nd) | `state.db` — per-state download/API → ingest → push |
| `il-backfill.yml` | manual | `state.db` — force IL current (uncapped) |
| `deploy` (Cloudflare Pages) | on push | rebuilds `mockup/data.json` + `state_data.json` and deploys |

All three refresh workflows share one concurrency group (`fec-archive-refresh`,
§4.5) so they never push concurrently. Failures open a tracking issue (§4.1).

## Build & deploy

`mockup/build_data.py` reads `master.db` (+ invokes `build_state_data.py` for
`state.db`) and writes `mockup/data.json` / `state_data.json` (gitignored,
regenerated at deploy time) plus lazy-loaded per-committee `beneficiaries/*.json`
chunks. `mockup/index.html` is the self-contained SPA (CheckSwing dashboard).
Cloudflare Pages runs the build and serves the result.
