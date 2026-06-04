# STATE_DONATION_SCHEMA.md — the `data/state.db` schema (Phase 4)

This is the spec for the Phase 4 state campaign-finance database. The implementation
is `scripts/state_db.py`; this document is the authority on field meaning. Read
[CHARTER.md](CHARTER.md) §Phase 4, [GOVERNANCE.md](GOVERNANCE.md) §1.11, and the
Phase 4 addendum in [SOURCES.md](SOURCES.md) first.

`data/state.db` is a **separate** SQLite database from `master.db` (federal/FEC) and
`legislation.db` (Phase 3). Like `legislation.db` it is a normal committed git blob,
**not** Git LFS: it holds only owner-*matched* state contributions and the recipient
filers they point at — a few MB even at a full multi-state buildout — so a state-data
commit never re-pushes `master.db`'s ~124 MB LFS object. The full state-portal bulk
dump is never stored here; it lives gitignored under `data/raw/state/<juris>/`, the
ground truth (GOVERNANCE.md §1.4).

The verification model is identical to the federal data: the same three-tier
classifier (`scripts/resolve_entities.py`) runs unchanged; only a per-portal input
adapter (`scripts/calaccess_adapter.py` for California) maps source rows into the
record shape the classifier reads.

## Tables

### `state_donations` — one row per matched state contribution

| Column | Type | Meaning |
|---|---|---|
| `state_txn_id` | TEXT PK | Composed stable key `JURIS:SOURCE:FILING_ID:TRAN_ID` (see below). |
| `jurisdiction` | TEXT | USPS state code, e.g. `CA`. |
| `source` | TEXT | Official portal that is the **record**, e.g. `CAL-ACCESS` (GOVERNANCE.md §1.11). |
| `source_tran_id` | TEXT | Portal's per-item id (CAL-ACCESS `TRAN_ID`). |
| `source_filing_id` | TEXT | Portal's filing id (CAL-ACCESS `FILING_ID`) — the citable filing. |
| `discovery_source` | TEXT | Aggregator that *surfaced* the record (`TAP`/`FTM`) or NULL for a direct portal scan. Never the source of the fact. |
| `entity_slug` | TEXT | Owner (or related entity) the row is attributed to. |
| `entity_kind` | TEXT | `owner` / `spouse` / … (same vocabulary as `master.db`). |
| `parent_owner_slug` | TEXT | Rollup parent for a related entity. |
| `status` | TEXT | `CONFIRMED` / `PROBABLE` / `UNCERTAIN` / `SUPERSEDED`. Same tiers, same rules (§1.2). |
| `status_reason` | TEXT | Why this tier (signal summary or `manual attribution (…)`). |
| `signals_matched` | TEXT | JSON array of matched signal strings. |
| `contributor_*` | TEXT | Name (raw), employer, occupation, city, state, zip — as filed. |
| `recipient_filer_id` | TEXT | → `state_filers.filer_id` (who received it). |
| `recipient_name` | TEXT | Recipient committee/candidate name as filed. |
| `recipient_type` | TEXT | `candidate` / `committee` / `ballot_measure` / NULL. |
| `recipient_party` / `recipient_office` | TEXT | Usually NULL — CAL-ACCESS receipts don't carry them. |
| `amount` | REAL | Contribution amount (USD). |
| `date` | TEXT | ISO 8601 contribution date. **Required** — a row with no parseable date is routed to the review queue, never invented (§1.3, §1.6). |
| `election_cycle` | INTEGER | **Calendar year** of the contribution. State cycles vary by office, so we do NOT force FEC's even-year two-year cycle here (§1.11). |
| `report_type` | TEXT | Portal report code where available. |
| `raw_payload_path` | TEXT | Path to the persisted portal extract (`data/raw/state/<juris>/…`). |
| `ingested_at` | TEXT | UTC ISO timestamp of ingest. |
| `superseded_by` / `superseded_reason` | TEXT | Set when the portal restated the item (§1.5, §1.10). |

**`state_txn_id` composition.** `compose_state_txn_id()` joins
`JURISDICTION:SOURCE:SOURCE_FILING_ID:SOURCE_TRAN_ID`. CAL-ACCESS `TRAN_ID` is unique
to an item only *within* a filing, so the filing id is part of the key. This makes
re-ingesting the same extract idempotent and lets an amended filing supersede the
prior version cleanly (substance compared over `STATE_DONATION_SUBSTANCE_COLS` =
amount, date, recipient_filer_id, source_filing_id).

### `state_filers` — recipient committees / candidates

The state-level analog of `master.db`'s `committees`. Keyed by
`(jurisdiction, source, filer_id)`; carries `name`, `filer_type`, and (where the
portal provides) `party` / `office`. Upserted from the portal's filer/cover-page
lookup so a contribution can name who it went to.

### `state_review_queue` — UNCERTAIN awaiting adjudication

Mirrors `master.db`'s `review_queue` plus a `source` discriminator. The hybrid
reconciliation (`discover_state`, planned) lands aggregator-only hits here with
reason `aggregator-only — verify against <portal>` until found in the official bulk.

### Durable verdict tables (survive `reclassify`)

- `state_review_resolutions` — standing `DISCARDED` verdicts; suppress re-queuing.
- `state_manual_attributions` — `CONFIRMED`/`PROBABLE` force a status, `EXCLUDED`
  drops a row entirely (documented "not this owner" for an inseparable same-named
  relative). Every entry carries a `reason` + `source`. Same model as `master.db`
  (`db.py`), applied at ingest/reclassify and never wiped by it.

### `state_ingestion_runs` / `state_schema_version`

Run log (counts, snapshot path, extract label) and the migration trail.

## Invariants

- A `CONFIRMED`/`PROBABLE` row always cites an official-portal filing
  (`source` + `source_filing_id` + `raw_payload_path`). No aggregator is ever the
  cited source (GOVERNANCE.md §1.11, §3).
- `status IN ('CONFIRMED','PROBABLE')` is the canonical export; `UNCERTAIN` and
  `SUPERSEDED` are excluded from it.
- The federal `master.db` is never written by the state pipeline, and vice versa.
