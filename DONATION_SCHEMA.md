# DONATION_SCHEMA — Donation Record Schema

Donations live in `data/master.db` (SQLite). This file specifies the schema. The CSV exports in `data/donations/<slug>/` mirror the same columns.

## Tables

### `donations`

The canonical record. One row per FEC transaction, attributed to one entity (owner OR related entity, never both).

| Column | Type | Notes |
|---|---|---|
| `transaction_id` | TEXT PK | FEC `transaction_id_number` — unique per filing |
| `entity_slug` | TEXT NOT NULL | Owner slug OR related-entity slug. The attribution. |
| `entity_kind` | TEXT NOT NULL | `owner` / `spouse` / `child` / `parent` / `sibling` / `pac` / `business_entity` |
| `parent_owner_slug` | TEXT | For non-owner entities, the owner they roll up to (e.g., `cohen-steven` for `cohen-alexandra`) |
| `status` | TEXT NOT NULL | `CONFIRMED` / `PROBABLE` / `UNCERTAIN` / `SUPERSEDED` |
| `status_reason` | TEXT | Human-readable explanation of why this tier (e.g., "two confirming signals: city+state, employer") |
| `signals_matched` | TEXT | JSON array of matched signal types (e.g., `["city_state", "employer:Point72 Asset Management"]`) |
| `contributor_name_raw` | TEXT NOT NULL | As filed |
| `contributor_employer_raw` | TEXT | As filed |
| `contributor_occupation_raw` | TEXT | As filed |
| `contributor_city` | TEXT | As filed |
| `contributor_state` | TEXT | As filed, 2-letter |
| `contributor_zip` | TEXT | As filed |
| `recipient_committee_id` | TEXT NOT NULL | FEC committee_id |
| `recipient_committee_name` | TEXT NOT NULL | Committee name as filed |
| `recipient_candidate_id` | TEXT | FEC candidate_id when applicable |
| `recipient_candidate_name` | TEXT | Candidate name when applicable |
| `recipient_party` | TEXT | DEM / REP / IND / LIB / etc., as classified by FEC |
| `recipient_office` | TEXT | H / S / P (House / Senate / Presidential) when applicable |
| `amount` | REAL NOT NULL | USD; FEC `contribution_receipt_amount` |
| `date` | TEXT NOT NULL | `contribution_receipt_date`, ISO 8601 |
| `election_cycle` | INTEGER | Two-year cycle (e.g., 2026) |
| `report_type` | TEXT | FEC report code (Q1 / Q2 / YE / 12P / 48H / etc.) |
| `filing_id` | TEXT NOT NULL | FEC `file_number` / `report_id`, or the sentinel `FEC-PRE2006-NOID` when FEC returns no file number (pre-2006 paper filings). Never blank — a row with no usable filing reference is rejected at ingest (§1.3). |
| `raw_payload_path` | TEXT NOT NULL | Relative path to JSON in `data/raw/` |
| `ingested_at` | TEXT NOT NULL | ISO 8601 UTC timestamp |
| `superseded_by` | TEXT | On an archived (status=SUPERSEDED) row, the canonical `transaction_id` of the live row that replaced it. NULL on live rows. |
| `superseded_reason` | TEXT | On an archived row, which FEC-substance fields changed (e.g. "FEC restatement: amount"). |

**Supersession (GOVERNANCE.md §1.5).** When FEC restates an already-ingested transaction (a change in amount, date, recipient, filing reference, or image), `insert_donation` archives the old row under a derived key (`<transaction_id>~superseded~<UTC>`) with `status=SUPERSEDED` and `superseded_by` pointing at the canonical id, then inserts the restated payload under the canonical `transaction_id`. The old row is never deleted (§1.10). Live queries filter `status IN ('CONFIRMED','PROBABLE')`, so archived rows never reach exports or the dashboard. Supersession compares FEC-sourced substance only, not our derived `status`/`signals_matched`, so a reclassification does not trip it.

### `ingestion_runs`

Every API session is logged here.

| Column | Type | Notes |
|---|---|---|
| `run_id` | TEXT PK | UUID |
| `entity_slug` | TEXT NOT NULL | Which entity this run targeted |
| `started_at` | TEXT NOT NULL | ISO 8601 UTC |
| `completed_at` | TEXT | ISO 8601 UTC; null if interrupted |
| `period_start` | TEXT | Start of query window |
| `period_end` | TEXT | End of query window |
| `name_variants_queried` | TEXT | JSON array — which name variants were used |
| `api_calls_made` | INTEGER | Count |
| `records_fetched` | INTEGER | Count |
| `confirmed_count` | INTEGER | After classification |
| `probable_count` | INTEGER | After classification |
| `uncertain_count` | INTEGER | After classification |
| `snapshot_path` | TEXT | Path to pre-run snapshot DB |
| `notes` | TEXT | Anomalies, retries, etc. |

### `entities`

Read-only mirror of the owner YAML registry. Refreshed at the start of each ingestion run from `owners/*.yaml`. Lets the DB self-describe what was tracked.

| Column | Type | Notes |
|---|---|---|
| `slug` | TEXT PK | |
| `kind` | TEXT NOT NULL | `owner` / `spouse` / etc. |
| `parent_slug` | TEXT | For non-owners |
| `name` | TEXT NOT NULL | |
| `team` | TEXT | For owners |
| `tenure_start_date` | TEXT | |
| `tenure_end_date` | TEXT | |
| `yaml_path` | TEXT NOT NULL | |
| `yaml_sha256` | TEXT NOT NULL | Hash of the YAML at refresh time — lets us detect schema drift across runs |
| `refreshed_at` | TEXT NOT NULL | |

### `review_queue`

UNCERTAIN records routed for human adjudication.

| Column | Type | Notes |
|---|---|---|
| `transaction_id` | TEXT PK | |
| `entity_slug` | TEXT NOT NULL | The entity name-matched against |
| `reason` | TEXT NOT NULL | Why UNCERTAIN |
| `raw_payload_path` | TEXT NOT NULL | |
| `queued_at` | TEXT NOT NULL | |
| `resolution` | TEXT | `CONFIRMED` / `PROBABLE` / `DISCARDED` / null |
| `resolution_reason` | TEXT | |
| `resolution_at` | TEXT | |
| `resolved_by` | TEXT | Username or session identifier |

## Indexes

- `donations(entity_slug, date)` — primary access pattern
- `donations(status)` — filter exports by tier
- `donations(recipient_candidate_id, date)` — Phase 3 cross-reference
- `donations(election_cycle, entity_slug)` — cycle reports

## Export schemas

### `data/donations/<slug>/all.csv`

Mirror of `donations` table, filtered to that entity's rows. Both CONFIRMED and PROBABLE included; status column is always present so consumers must explicitly handle PROBABLE. UNCERTAIN and SUPERSEDED are not exported.

### `data/donations/<slug>/by_cycle/<cycle>.csv`

Same schema, partitioned by `election_cycle`. Convenience for time-series work.

### `data/donations/_aggregate/by_owner.csv`

Top-level aggregate. One row per owner, totals by cycle, by party, by office. CONFIRMED only by default; a `_with_probable` variant includes both with status preserved.

## Provenance recoverability test

**Source of truth.** The committed `data/master.db` is the durable source of truth (GOVERNANCE.md §1.4). Raw payloads in `data/raw/` are best-effort ground truth: written before parsing on every fetch path and used for re-verification/reclassification, but git-ignored and not guaranteed to persist (some historical rows reference raw files no longer on disk). Reconstruction from raw is therefore a best-effort aid, **not** a guarantee — `reclassify` is guarded against silently dropping rows whose raw is missing (run `cli raw-coverage` to audit the gap). Some information (e.g. human review-queue resolutions) lives only in master.db.
