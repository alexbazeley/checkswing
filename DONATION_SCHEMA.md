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
| `amount` | REAL NOT NULL | USD; FEC `contribution_receipt_amount`. **Refunds/reattributions are negative and counted net** (see below). Never fabricated: a row whose FEC amount is missing/unparseable is rejected at ingest, not stored as `0.0` (GOVERNANCE §3). |
| `date` | TEXT NOT NULL | `contribution_receipt_date`, ISO 8601 |
| `election_cycle` | INTEGER | Two-year cycle (e.g., 2026) |
| `report_type` | TEXT | FEC report code (Q1 / Q2 / YE / 12P / 48H / etc.) |
| `filing_id` | TEXT NOT NULL | FEC `file_number` / `report_id`, or the sentinel `FEC-PRE2006-NOID` when FEC returns no file number (pre-2006 paper filings). Never blank — a row with no usable filing reference is rejected at ingest (§1.3). |
| `raw_payload_path` | TEXT NOT NULL | Relative path to JSON in `data/raw/` |
| `ingested_at` | TEXT NOT NULL | ISO 8601 UTC timestamp |
| `superseded_by` | TEXT | On an archived (status=SUPERSEDED) row, the canonical `transaction_id` of the live row that replaced it. NULL on live rows. |
| `superseded_reason` | TEXT | On an archived row, which FEC-substance fields changed (e.g. "FEC restatement: amount"). |
| `image_number` · `pdf_url` · `filing_form` · `line_number` · `receipt_type_full` · `recipient_committee_type` | TEXT | v3: per-transaction FEC fields, baked on at ingest so the dashboard's donation card (image link, filing PDF, receipt type) needn't re-read raw payloads. NULL when the source payload didn't carry them. |
| `memo_code` · `memo_text` | TEXT | v8: FEC memo fields, stored for provenance on earmarked/conduit rows. `memo_code` is set on FEC memo lines; note it is NOT the dedup key (it is null on both legs of an ActBlue/WinRed earmark). |
| `is_individual` | INTEGER | v8: FEC's flag distinguishing a countable individual contribution (1) from a conduit pass-through leg (0). Drives `counted`. NULL when unrecoverable (raw payload gone) — treated as countable. |
| `counted` | INTEGER NOT NULL DEFAULT 1 | v8 **derived** dedup flag (not an FEC field). `0` marks an earmark/conduit pass-through leg (`is_individual=0`) that has a countable sibling in the same (entity_slug, contributor_name_raw, date, amount) group — the genuine double-count. Every published SUM filters `counted = 1`. Recomputed by `db.recompute_counted` after each ingest/reclassify; the row is never deleted (§1.10). |
| `sub_id` | TEXT | v9: FEC's **globally-unique** record id. `transaction_id` (the PK) is filer-assigned and NOT unique across committees; `sub_id` is the authoritative identity `insert_donation` uses to tell a genuine restatement (same `sub_id`) from a cross-committee `transaction_id` collision (different `sub_id` → distinct contributions, not a restatement — see below). NULL on legacy rows whose raw is gone (backfill via `cli backfill-sub-id`); a NULL falls back to the `transaction_id` identity. |

**transaction_id collisions (GOVERNANCE.md §1.5; schema v9).** `transaction_id` is filer-assigned (e.g. `SA11AI.20387`) and not unique across committees, so two distinct contributions can share one. When `insert_donation` finds an existing row with the same `transaction_id` but a **different** `sub_id`, it returns `("collision", …)` and writes nothing — refusing to supersede the wrong record — and the ingest run logs it. (Zero occurrences in the live DB; this converts a would-be silent wrong-supersession into a loud, auditable skip.) The `review_queue` PK is likewise `(transaction_id, entity_slug)` as of v9, so two owners can each flag the same FEC transaction without one silently dropping the other.

**Earmark/conduit dedup (GOVERNANCE.md §1.10; schema v8).** A contribution earmarked through a conduit (ActBlue/WinRed) is reported to the FEC twice — the conduit's pass-through leg and the ultimate recipient's own report — under distinct `transaction_id`s. FEC excludes the pass-through leg from its individual-contribution totals (`is_individual=0`); the archive mirrors that via `counted`. A **lone** conduit leg (the only record of a real gift FEC itemized only at the conduit) keeps `counted=1` so it is never silently dropped. Both legs remain in the table; only the counted view is deduplicated.

**Refunds and negative amounts (net totals).** FEC records refunds, reattributions, and chargebacks as **negative** `contribution_receipt_amount` rows (≈138 federally). The archive's policy is **net**: negative rows are stored as filed and included in every `SUM(amount)`, so an owner's total reflects money actually retained, not gross inflow. `receipt_type_full` carries the FEC receipt-type label (e.g. "REFUND", "REATTRIBUTION") for a row that needs to be read as a reversal. This is a deliberate, documented choice (the alternative — gross, ignoring refunds — would overstate giving).

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
| `transaction_id` | TEXT | Part of the composite PK. |
| `entity_slug` | TEXT NOT NULL | The entity name-matched against. v9: PK is `(transaction_id, entity_slug)` so two owners can flag the same FEC transaction. |
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
