# PROVENANCE LOG

Append-only log of every event that affects the archive's data: ingestion runs, signal changes, status promotions, review-queue resolutions, schema migrations, deletions (which should be rare and reasoned).

The log is the audit trail. If a donation is later questioned ("did you actually pull this from FEC?"), the trail starts here and leads back to a specific raw payload.

## Format

Each entry is a level-3 heading with an ISO 8601 UTC timestamp, followed by a typed body. Entries are append-only — never edit a past entry; correct it with a new entry that supersedes.

Entry types:
- `INGESTION` — a pipeline run
- `SIGNAL_CHANGE` — a modification to an owner YAML's signal set
- `STATUS_CHANGE` — promotion / demotion of a record's status
- `REVIEW_RESOLUTION` — adjudication of a REVIEW_QUEUE item
- `SCHEMA_MIGRATION` — DB schema change
- `DELETION` — rare; deletion of a record with reason
- `SETUP` — project setup milestones
- `NOTE` — anything that doesn't fit but should be on the record

## Entries

### 2026-05-22 — SETUP

Project skeleton created. Base files:
- `GOVERNANCE.md`
- `CHARTER.md`
- `SOURCES.md`
- `VERIFICATION.md`
- `OWNER_SCHEMA.md`
- `DONATION_SCHEMA.md`
- `NAMING.md`
- `README.md`
- `CLAUDE_CODE_PROMPT.md`
- `owners/_registry.yaml` — 5 pilot entries (Cohen pilot; Crane/Henry/Castellini/Steinbrenner queued for Phase 1.5) plus 25 Phase 2 queued entries
- `owners/_template.yaml`
- `owners/cohen-steven.yaml` — populated as worked pilot example

No ingestion yet. No code written yet. Phase 0 setup files only.

### 2026-05-22 — SETUP (Phase 1 build)

Pipeline code written by automated pipeline:
- `scripts/{paths,db,validate_owners,fetch_fec,resolve_entities,ingest,export,cli}.py`
- `tests/test_resolve_entities.py` — 32 tests, all passing.
- `.env`, `.env.example`, `.gitignore`, `requirements.txt`, venv at `.venv/`
- `data/raw/`, `data/snapshots/`, `data/donations/{,_aggregate}/`, `reports/`, `reviews/` directories.
- `master.db` initialized with `donations`, `ingestion_runs`, `entities`, `review_queue` tables.
- Schema field naming aligned: `owners/_registry.yaml` `tenure_start` → `tenure_start_date` to match `OWNER_SCHEMA.md` and `owners/cohen-steven.yaml`.

### 2026-05-22 — SIGNAL_CHANGE — cohen-steven.yaml

Schema-compliance fix. Two employer strings — `Cohen Private Ventures` and `Point72 Asset Management` — were present in BOTH `verifying_signals.employers` AND `strong_signals.employers`, violating `OWNER_SCHEMA.md` rule 6 (no duplication across signal tiers). The validator caught it.

Resolution: removed both strings from `verifying_signals.employers`. They remain in `strong_signals.employers` (where they functionally belong — one match → CONFIRMED). No semantic change to classification; the duplication was redundant.

Recorded in cohen-steven.yaml `change_log` under date 2026-05-22.

### 2026-05-22T18:00Z — NOTE — Cohen broad-fetch attempt aborted

First Phase 1 ingestion attempt used `contributor_name`-only FEC queries (no state filter), per the initial reading of the spec ("cast a wide net at fetch, classify strict"). Result: FEC's `contributor_name` search is much broader than anticipated. Variant "Steve Cohen" alone returned 22,788 records across 228 pages (every Steve Cohen in the FEC nationwide — Anschutz Corp executive, Stanford academic, etc.).

After 240 raw API calls (mix of dry-run pages and real-run pages), the projected total was 1-2 hours of API calls and a review queue dominated by clearly-not-our-Cohen records that would all be classified UNCERTAIN.

Decision (with user approval): abort the broad fetch and switch to a state-filtered fetch. When an owner YAML has `verifying_signals.states` populated, the fetch will pass those as `contributor_state` filters to FEC. Cohen's states (CT, NY) narrow the result set ~10-20x while remaining name-anchored (so it does NOT violate GOVERNANCE.md §3's prohibition on employer-only aggregated queries).

Tradeoff accepted: this may miss the rare donation Cohen filed with a non-CT/NY address. Risk considered acceptable given the conservative-attribution preference and the audit cost of a flooded review queue.

The 240 raw payloads from the broad-fetch attempt have been archived to `data/raw/_aborted/cohen-steven_2026-05-22T18Z_broad-fetch/`. They are valid FEC responses and are preserved as audit evidence; they were never written to the DB.

No DB writes happened during the broad-fetch attempt.

### 2026-05-22 — INGESTION

- **run_id**: `3ea399e7`
- **entity_slug**: `cohen-steven`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Steven A Cohen", "Steven A. Cohen", "Steven Cohen", "Steve Cohen", "Cohen, Steven", "Cohen, Steven A", "Cohen, Steven A."]`
- **api_calls_made**: `0`
- **records_fetched**: `2961`
- **confirmed_count**: `98`
- **probable_count**: `936`
- **uncertain_count**: `1774`
- **snapshot_path**: `data/snapshots/2026-05-22T20-51-24Z__3ea399e7.db`
- **notes**: skipped(no-name-match)=153 · FROM-RAW

### 2026-05-22 — SCHEMA_MIGRATION — add negative_signals

`OWNER_SCHEMA.md` and `VERIFICATION.md` extended to document an optional
`negative_signals.employers` block on owner YAMLs. The classifier
(`scripts/resolve_entities.py`) now demotes any record whose employer matches
a negative-signal string to UNCERTAIN with reason "matches negative employer
signal: <string>", regardless of other signals. This catches manually-audited
same-name doppelgängers. 4 new tests added (`tests/test_resolve_entities.py`,
36 passing).

This is a schema extension that affects classification — owners that adopt
the block will see records demoted on re-classify. Cohen adopts it in the
next entry; other owners can adopt later if their audit identifies a
doppelgänger.

### 2026-05-22 — SIGNAL_CHANGE — cohen-steven.yaml (calibration round 1)

Four coordinated changes based on audit of run_id 3ea399e7 (78 raw payloads,
2,961 records classified). User-approved (alex).

1. **verifying_signals.employers** — added `"POINT 72"` (with space).
   10 Greenwich PROBABLEs at zip 06831 had this employer string (real
   Cohen-filed, occupation FINANCE, 2016-2017). The existing `"Point72"`
   substring did not match `"POINT 72"` because of the space.

2. **verifying_signals.cities** — removed `"new york"`. Was generating 910
   PROBABLE false-positives (NYC same-name donors with no other signal).
   Real Cohen NYC giving is now caught via the new strong-zip signal.

3. **strong_signals.zip_codes** — added `"10001"`. Cohen's NYC residence
   (Chelsea/NoMad), identified from 4 CONFIRMED records at zip 100012163
   with employer POINT72. A strong-zip + name match → CONFIRMED.

4. **negative_signals.employers** — added `["Elliott Management", "Elliott Mgmt"]`.
   A different Steven Cohen works at Elliott Management (Paul Singer's
   hedge fund) and files from Greenwich zips 068312665 and 068313102. 6
   PROBABLE Greenwich records filing to Republican causes (Nikki Haley,
   Stand For America PAC) traced to this doppelgänger.

Re-classify against existing raw payloads (no FEC calls) follows.

### 2026-05-22 — INGESTION

- **run_id**: `cae911f9`
- **entity_slug**: `cohen-steven`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Steven A Cohen", "Steven A. Cohen", "Steven Cohen", "Steve Cohen", "Cohen, Steven", "Cohen, Steven A", "Cohen, Steven A."]`
- **api_calls_made**: `0`
- **records_fetched**: `2961`
- **confirmed_count**: `102`
- **probable_count**: `10`
- **uncertain_count**: `2696`
- **snapshot_path**: `data/snapshots/2026-05-22T21-06-20Z__cae911f9.db`
- **notes**: skipped(no-name-match)=153 · FROM-RAW

### 2026-05-22 — DELETION — reclassify cohen-steven

- **entity_slug**: `cohen-steven`
- **reason**: smoke test of new command — should be a no-op
- **rows_deleted_donations**: `112`
- **rows_deleted_review_queue**: `2696` (of which 0 had resolutions)
- **snapshot_path**: `data/snapshots/2026-05-22T21-18-23Z__pre-reclassify-cohen-steven.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/cohen-steven/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-22 — INGESTION

- **run_id**: `9fb78c4b`
- **entity_slug**: `cohen-steven`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Steven A Cohen", "Steven A. Cohen", "Steven Cohen", "Steve Cohen", "Cohen, Steven", "Cohen, Steven A", "Cohen, Steven A."]`
- **api_calls_made**: `0`
- **records_fetched**: `2961`
- **confirmed_count**: `102`
- **probable_count**: `10`
- **uncertain_count**: `2696`
- **snapshot_path**: `data/snapshots/2026-05-22T21-18-23Z__9fb78c4b.db`
- **notes**: skipped(no-name-match)=153 · FROM-RAW

### 2026-05-22 — INGESTION

- **run_id**: `1f5cbf21`
- **entity_slug**: `crane-jim`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["James R Crane", "James R. Crane", "James Crane", "Jim Crane", "Crane, James", "Crane, James R", "Crane, James R."]`
- **api_calls_made**: `10`
- **records_fetched**: `51`
- **confirmed_count**: `24`
- **probable_count**: `11`
- **uncertain_count**: `14`
- **snapshot_path**: `data/snapshots/2026-05-22T21-52-51Z__1f5cbf21.db`
- **notes**: skipped(no-name-match)=2 · states=['TX']

### 2026-05-22 — INGESTION

- **run_id**: `dbcd04e1`
- **entity_slug**: `henry-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["John W. Henry", "John W Henry", "John William Henry", "John William Henry II", "John Henry", "Henry, John W", "Henry, John W.", "Henry, John"]`
- **api_calls_made**: `32`
- **records_fetched**: `1142`
- **confirmed_count**: `4`
- **probable_count**: `8`
- **uncertain_count**: `218`
- **snapshot_path**: `data/snapshots/2026-05-22T23-05-08Z__dbcd04e1.db`
- **notes**: skipped(no-name-match)=912 · states=['FL', 'MA']

### 2026-05-22 — INGESTION

- **run_id**: `3b72871b`
- **entity_slug**: `castellini-bob`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Robert H Castellini", "Robert H. Castellini", "Robert Castellini", "Bob Castellini", "Castellini, Robert", "Castellini, Robert H", "Castellini, Robert H.", "Castellini, Bob"]`
- **api_calls_made**: `24`
- **records_fetched**: `461`
- **confirmed_count**: `278`
- **probable_count**: `24`
- **uncertain_count**: `6`
- **snapshot_path**: `data/snapshots/2026-05-22T23-41-41Z__3b72871b.db`
- **notes**: skipped(no-name-match)=153 · states=['OH']

### 2026-05-22 — INGESTION

- **run_id**: `8baaf48b`
- **entity_slug**: `steinbrenner-hal`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Harold Z Steinbrenner", "Harold Z. Steinbrenner", "Harold Steinbrenner", "Hal Steinbrenner", "Steinbrenner, Harold", "Steinbrenner, Harold Z", "Steinbrenner, Harold Z.", "Steinbrenner, Hal"]`
- **api_calls_made**: `10`
- **records_fetched**: `15`
- **confirmed_count**: `11`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-22T23-55-13Z__8baaf48b.db`
- **notes**: skipped(no-name-match)=3 · states=['FL']

### 2026-05-22 — SCHEMA_MIGRATION — _norm() strips periods and commas

`scripts/resolve_entities.py` `_norm()` now strips `.` and `,` in addition to
whitespace-collapse + lowercase. This is the function used for employer,
occupation, and city matching. Name normalization already did the same; this
brings the other matchers into alignment.

Rationale: Henry's first ingestion (run_id dbcd04e1) caught only 4 CONFIRMED
because signal `"John W. Henry and Company"` did not substring-match record
`"JOHN W HENRY AND COMPANY INC"` — the period blocked the match. ~50 real
Henry filings sitting in UNCERTAIN/PROBABLE were the visible cost. Stripping
periods is consistent with the spec's intent (VERIFICATION.md: "case-insensitive,
whitespace-collapsed" — periods aren't semantic content). It is NOT stemming
or word removal, which the spec forbids.

Tests: 3 new regression tests added (`tests/test_resolve_entities.py`,
39 passing total). Existing tests still pass — the Point72 / Point Park
anti-pattern test confirms period-stripping doesn't create spurious matches.

Affects: all owners. Reclassification (with `cli reclassify`) will pick up
any newly-matching records.

### 2026-05-22 — SIGNAL_CHANGE — calibration round 1 across 4 owners

After first ingestion of Phase 1.5 pilots (Crane, Henry, Castellini, Steinbrenner),
audit identified the following per-owner signal-set changes (user-approved):

- **crane-jim.yaml** — added `"Self-Employed"` and `"Self Employed"` to
  `verifying_signals.employers`. Promotes ~11 PROBABLEs (~$130K of Democratic
  giving to Obama Victory Fund / Harris / DNC) to CONFIRMED.
- **henry-john.yaml** — added `"John W. Henry and Co"`, `"John W. Henry Company"`
  variants to `verifying_signals.employers`. Added `"boston"` to
  `verifying_signals.cities`. (Combined with the SCHEMA_MIGRATION above, expect
  Henry's CONFIRMED count to rise substantially from 4.)
- **castellini-bob.yaml** — added `"cincinatti"`, `"the village of ind"`,
  `"village of indian hill"` to `verifying_signals.cities` (catches typo +
  Indian Hill's municipal form, ~6 UNCERTAINs). Added
  `negative_signals.employers` block with `["UBS PAINE WEBBER",
  "Sena Weller Rohs & Williams", "Wells Fargo Advisors"]` (doppelgänger
  financial advisor pattern in Cincinnati).
- **steinbrenner-hal.yaml** — no changes. First ingestion produced clean
  data (11 CONFIRMED, 1 PROBABLE, 0 UNCERTAIN) and the strict name-variant
  design correctly excluded sibling/parent records.

Reclassification using `cli reclassify` follows for all 5 owners (Cohen also
gets re-run since the SCHEMA_MIGRATION may pull up additional CONFIRMs).

### 2026-05-23 — DELETION — reclassify cohen-steven

- **entity_slug**: `cohen-steven`
- **reason**: Calibration round 1 (cross-pilot): _norm() period-strip + YAML signal additions
- **rows_deleted_donations**: `112`
- **rows_deleted_review_queue**: `2696` (of which 0 had resolutions)
- **snapshot_path**: `data/snapshots/2026-05-23T00-04-55Z__pre-reclassify-cohen-steven.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/cohen-steven/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-23 — INGESTION

- **run_id**: `2ec4b4c2`
- **entity_slug**: `cohen-steven`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Steven A Cohen", "Steven A. Cohen", "Steven Cohen", "Steve Cohen", "Cohen, Steven", "Cohen, Steven A", "Cohen, Steven A."]`
- **api_calls_made**: `0`
- **records_fetched**: `2961`
- **confirmed_count**: `102`
- **probable_count**: `10`
- **uncertain_count**: `2696`
- **snapshot_path**: `data/snapshots/2026-05-23T00-04-55Z__2ec4b4c2.db`
- **notes**: skipped(no-name-match)=153 · FROM-RAW

### 2026-05-23 — DELETION — reclassify crane-jim

- **entity_slug**: `crane-jim`
- **reason**: Calibration round 1 (cross-pilot): _norm() period-strip + YAML signal additions
- **rows_deleted_donations**: `35`
- **rows_deleted_review_queue**: `14` (of which 0 had resolutions)
- **snapshot_path**: `data/snapshots/2026-05-23T00-04-56Z__pre-reclassify-crane-jim.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/crane-jim/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-23 — INGESTION

- **run_id**: `7408f814`
- **entity_slug**: `crane-jim`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["James R Crane", "James R. Crane", "James Crane", "Jim Crane", "Crane, James", "Crane, James R", "Crane, James R."]`
- **api_calls_made**: `0`
- **records_fetched**: `51`
- **confirmed_count**: `33`
- **probable_count**: `2`
- **uncertain_count**: `14`
- **snapshot_path**: `data/snapshots/2026-05-23T00-04-56Z__7408f814.db`
- **notes**: skipped(no-name-match)=2 · FROM-RAW

### 2026-05-23 — DELETION — reclassify henry-john

- **entity_slug**: `henry-john`
- **reason**: Calibration round 1 (cross-pilot): _norm() period-strip + YAML signal additions
- **rows_deleted_donations**: `12`
- **rows_deleted_review_queue**: `218` (of which 0 had resolutions)
- **snapshot_path**: `data/snapshots/2026-05-23T00-04-56Z__pre-reclassify-henry-john.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/henry-john/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-23 — INGESTION

- **run_id**: `a8588d7b`
- **entity_slug**: `henry-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["John W. Henry", "John W Henry", "John William Henry", "John William Henry II", "John Henry", "Henry, John W", "Henry, John W.", "Henry, John"]`
- **api_calls_made**: `0`
- **records_fetched**: `1142`
- **confirmed_count**: `11`
- **probable_count**: `5`
- **uncertain_count**: `214`
- **snapshot_path**: `data/snapshots/2026-05-23T00-04-56Z__a8588d7b.db`
- **notes**: skipped(no-name-match)=912 · FROM-RAW

### 2026-05-23 — DELETION — reclassify castellini-bob

- **entity_slug**: `castellini-bob`
- **reason**: Calibration round 1 (cross-pilot): _norm() period-strip + YAML signal additions
- **rows_deleted_donations**: `302`
- **rows_deleted_review_queue**: `6` (of which 0 had resolutions)
- **snapshot_path**: `data/snapshots/2026-05-23T00-04-56Z__pre-reclassify-castellini-bob.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/castellini-bob/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-23 — INGESTION

- **run_id**: `01937262`
- **entity_slug**: `castellini-bob`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Robert H Castellini", "Robert H. Castellini", "Robert Castellini", "Bob Castellini", "Castellini, Robert", "Castellini, Robert H", "Castellini, Robert H.", "Castellini, Bob"]`
- **api_calls_made**: `0`
- **records_fetched**: `461`
- **confirmed_count**: `279`
- **probable_count**: `21`
- **uncertain_count**: `8`
- **snapshot_path**: `data/snapshots/2026-05-23T00-04-56Z__01937262.db`
- **notes**: skipped(no-name-match)=153 · FROM-RAW

### 2026-05-23 — DELETION — reclassify steinbrenner-hal

- **entity_slug**: `steinbrenner-hal`
- **reason**: Calibration round 1 (cross-pilot): _norm() period-strip + YAML signal additions
- **rows_deleted_donations**: `12`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **snapshot_path**: `data/snapshots/2026-05-23T00-04-56Z__pre-reclassify-steinbrenner-hal.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/steinbrenner-hal/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-23 — INGESTION

- **run_id**: `3c5fac88`
- **entity_slug**: `steinbrenner-hal`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Harold Z Steinbrenner", "Harold Z. Steinbrenner", "Harold Steinbrenner", "Hal Steinbrenner", "Steinbrenner, Harold", "Steinbrenner, Harold Z", "Steinbrenner, Harold Z.", "Steinbrenner, Hal"]`
- **api_calls_made**: `0`
- **records_fetched**: `15`
- **confirmed_count**: `11`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-23T00-04-56Z__3c5fac88.db`
- **notes**: skipped(no-name-match)=3 · FROM-RAW

### 2026-05-23 — DELETION — reclassify cohen-steven

- **entity_slug**: `cohen-steven`
- **reason**: Calibration round 1 (honorific strip + structured-fields fallback)
- **rows_deleted_donations**: `112`
- **rows_deleted_review_queue**: `2696` (of which 0 had resolutions)
- **snapshot_path**: `data/snapshots/2026-05-23T00-08-30Z__pre-reclassify-cohen-steven.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/cohen-steven/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-23 — INGESTION

- **run_id**: `5af14960`
- **entity_slug**: `cohen-steven`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Steven A Cohen", "Steven A. Cohen", "Steven Cohen", "Steve Cohen", "Cohen, Steven", "Cohen, Steven A", "Cohen, Steven A."]`
- **api_calls_made**: `0`
- **records_fetched**: `2961`
- **confirmed_count**: `126`
- **probable_count**: `10`
- **uncertain_count**: `2785`
- **snapshot_path**: `data/snapshots/2026-05-23T00-08-30Z__5af14960.db`
- **notes**: skipped(no-name-match)=40 · FROM-RAW

### 2026-05-23 — DELETION — reclassify crane-jim

- **entity_slug**: `crane-jim`
- **reason**: Calibration round 1 (honorific strip + structured-fields fallback)
- **rows_deleted_donations**: `35`
- **rows_deleted_review_queue**: `14` (of which 0 had resolutions)
- **snapshot_path**: `data/snapshots/2026-05-23T00-08-31Z__pre-reclassify-crane-jim.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/crane-jim/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-23 — INGESTION

- **run_id**: `6ccf16dd`
- **entity_slug**: `crane-jim`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["James R Crane", "James R. Crane", "James Crane", "Jim Crane", "Crane, James", "Crane, James R", "Crane, James R."]`
- **api_calls_made**: `0`
- **records_fetched**: `51`
- **confirmed_count**: `34`
- **probable_count**: `2`
- **uncertain_count**: `14`
- **snapshot_path**: `data/snapshots/2026-05-23T00-08-31Z__6ccf16dd.db`
- **notes**: skipped(no-name-match)=1 · FROM-RAW

### 2026-05-23 — DELETION — reclassify henry-john

- **entity_slug**: `henry-john`
- **reason**: Calibration round 1 (honorific strip + structured-fields fallback)
- **rows_deleted_donations**: `16`
- **rows_deleted_review_queue**: `214` (of which 0 had resolutions)
- **snapshot_path**: `data/snapshots/2026-05-23T00-08-31Z__pre-reclassify-henry-john.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/henry-john/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-23 — INGESTION

- **run_id**: `a11f0175`
- **entity_slug**: `henry-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["John W. Henry", "John W Henry", "John William Henry", "John William Henry II", "John Henry", "Henry, John W", "Henry, John W.", "Henry, John"]`
- **api_calls_made**: `0`
- **records_fetched**: `1142`
- **confirmed_count**: `14`
- **probable_count**: `5`
- **uncertain_count**: `247`
- **snapshot_path**: `data/snapshots/2026-05-23T00-08-31Z__a11f0175.db`
- **notes**: skipped(no-name-match)=876 · FROM-RAW

### 2026-05-23 — DELETION — reclassify castellini-bob

- **entity_slug**: `castellini-bob`
- **reason**: Calibration round 1 (honorific strip + structured-fields fallback)
- **rows_deleted_donations**: `300`
- **rows_deleted_review_queue**: `8` (of which 0 had resolutions)
- **snapshot_path**: `data/snapshots/2026-05-23T00-08-31Z__pre-reclassify-castellini-bob.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/castellini-bob/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-23 — INGESTION

- **run_id**: `622b4090`
- **entity_slug**: `castellini-bob`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Robert H Castellini", "Robert H. Castellini", "Robert Castellini", "Bob Castellini", "Castellini, Robert", "Castellini, Robert H", "Castellini, Robert H.", "Castellini, Bob"]`
- **api_calls_made**: `0`
- **records_fetched**: `461`
- **confirmed_count**: `405`
- **probable_count**: `34`
- **uncertain_count**: `14`
- **snapshot_path**: `data/snapshots/2026-05-23T00-08-31Z__622b4090.db`
- **notes**: skipped(no-name-match)=8 · FROM-RAW

### 2026-05-23 — DELETION — reclassify steinbrenner-hal

- **entity_slug**: `steinbrenner-hal`
- **reason**: Calibration round 1 (honorific strip + structured-fields fallback)
- **rows_deleted_donations**: `12`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **snapshot_path**: `data/snapshots/2026-05-23T00-08-32Z__pre-reclassify-steinbrenner-hal.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/steinbrenner-hal/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-23 — INGESTION

- **run_id**: `241dc1d0`
- **entity_slug**: `steinbrenner-hal`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Harold Z Steinbrenner", "Harold Z. Steinbrenner", "Harold Steinbrenner", "Hal Steinbrenner", "Steinbrenner, Harold", "Steinbrenner, Harold Z", "Steinbrenner, Harold Z.", "Steinbrenner, Hal"]`
- **api_calls_made**: `0`
- **records_fetched**: `15`
- **confirmed_count**: `13`
- **probable_count**: `2`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-23T00-08-32Z__241dc1d0.db`
- **notes**: skipped(no-name-match)=0 · FROM-RAW

### 2026-05-23 — INGESTION

- **run_id**: `5f8213d7`
- **entity_slug**: `rubenstein-david`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["David Rubenstein", "David M. Rubenstein", "David M Rubenstein", "Rubenstein, David", "Rubenstein, David M", "Rubenstein, David M."]`
- **api_calls_made**: `8`
- **records_fetched**: `31`
- **confirmed_count**: `2`
- **probable_count**: `6`
- **uncertain_count**: `23`
- **snapshot_path**: `data/snapshots/2026-05-23T00-32-02Z__5f8213d7.db`
- **notes**: skipped(no-name-match)=0 · states=['MD', 'DC', 'MA']

### 2026-05-23 — INGESTION

- **run_id**: `8f092363`
- **entity_slug**: `moreno-arte`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Arturo Moreno", "Arte Moreno", "Arturo R. Moreno", "Arturo R Moreno", "Moreno, Arturo", "Moreno, Arte", "Moreno, Arturo R"]`
- **api_calls_made**: `13`
- **records_fetched**: `79`
- **confirmed_count**: `47`
- **probable_count**: `26`
- **uncertain_count**: `6`
- **snapshot_path**: `data/snapshots/2026-05-23T00-52-44Z__8f092363.db`
- **notes**: skipped(no-name-match)=0 · states=['AZ', 'CA']

### 2026-05-23 — INGESTION

- **run_id**: `dc4e5575`
- **entity_slug**: `attanasio-mark`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Mark Attanasio", "Mark L Attanasio", "Mark L. Attanasio", "Attanasio, Mark", "Attanasio, Mark L", "Attanasio, Mark L."]`
- **api_calls_made**: `8`
- **records_fetched**: `58`
- **confirmed_count**: `26`
- **probable_count**: `4`
- **uncertain_count**: `28`
- **snapshot_path**: `data/snapshots/2026-05-23T01-04-12Z__dc4e5575.db`
- **notes**: skipped(no-name-match)=0 · states=['CA', 'WI']

### 2026-05-23 — INGESTION

- **run_id**: `864d1936`
- **entity_slug**: `kendrick-ken`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Ken Kendrick", "Earl G. Kendrick", "Earl G Kendrick", "Earl G. Kendrick Jr.", "Earl G. Kendrick, Jr.", "Earl Kendrick", "E. G. Kendrick", "E.G. Kendrick", "E G Kendrick", "Earl Gentry Kendrick", "Kendrick, Ken", "Kendrick, Earl G", "Kendrick, Earl G.", "Kendrick, Earl G., Jr."]`
- **api_calls_made**: `94`
- **records_fetched**: `3473`
- **confirmed_count**: `640`
- **probable_count**: `60`
- **uncertain_count**: `9`
- **snapshot_path**: `data/snapshots/2026-05-23T01-09-22Z__864d1936.db`
- **notes**: skipped(no-name-match)=2764 · states=['AZ']

### 2026-05-23 — INGESTION

- **run_id**: `8c8cbacc`
- **entity_slug**: `ricketts-tom`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Tom Ricketts", "Thomas Ricketts", "Thomas S Ricketts", "Thomas S. Ricketts", "Thomas Stuart Ricketts", "Ricketts, Tom", "Ricketts, Thomas", "Ricketts, Thomas S", "Ricketts, Thomas S."]`
- **api_calls_made**: `0`
- **records_fetched**: `63`
- **confirmed_count**: `47`
- **probable_count**: `15`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-23T02-35-43Z__8c8cbacc.db`
- **notes**: skipped(no-name-match)=1 · FROM-RAW

### 2026-05-23 — INGESTION

- **run_id**: `ef2a7eae`
- **entity_slug**: `walter-mark`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Mark Walter", "Mark R. Walter", "Mark R Walter", "Mark Richard Walter", "Walter, Mark", "Walter, Mark R", "Walter, Mark R."]`
- **api_calls_made**: `19`
- **records_fetched**: `579`
- **confirmed_count**: `20`
- **probable_count**: `0`
- **uncertain_count**: `41`
- **snapshot_path**: `data/snapshots/2026-05-23T03-08-31Z__ef2a7eae.db`
- **notes**: skipped(no-name-match)=518 · states=['IL']

### 2026-05-23 — INGESTION

- **run_id**: `a5b90844`
- **entity_slug**: `dewitt-bill`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["William O DeWitt Jr", "William O. DeWitt Jr.", "William O. DeWitt Jr", "William DeWitt Jr", "William DeWitt Jr.", "Bill DeWitt Jr", "Bill DeWitt Jr.", "William DeWitt", "Bill DeWitt", "DeWitt, William", "DeWitt, William O", "DeWitt, William O Jr", "DeWitt, Bill"]`
- **api_calls_made**: `28`
- **records_fetched**: `359`
- **confirmed_count**: `189`
- **probable_count**: `32`
- **uncertain_count**: `124`
- **snapshot_path**: `data/snapshots/2026-05-23T03-35-34Z__a5b90844.db`
- **notes**: skipped(no-name-match)=14 · states=['OH', 'MO']

### 2026-05-23 — INGESTION

- **run_id**: `ad9d887a`
- **entity_slug**: `reinsdorf-jerry`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Jerry Reinsdorf", "Jerry M Reinsdorf", "Jerry M. Reinsdorf", "Jerry Michael Reinsdorf", "Reinsdorf, Jerry", "Reinsdorf, Jerry M", "Reinsdorf, Jerry M."]`
- **api_calls_made**: `0`
- **records_fetched**: `425`
- **confirmed_count**: `257`
- **probable_count**: `167`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-23T03-48-43Z__ad9d887a.db`
- **notes**: skipped(no-name-match)=1 · FROM-RAW

### 2026-05-23 — INGESTION

- **run_id**: `155dc60e`
- **entity_slug**: `middleton-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["John Middleton", "John S. Middleton", "John S Middleton", "John Staubus Middleton", "Middleton, John", "Middleton, John S", "Middleton, John S."]`
- **api_calls_made**: `0`
- **records_fetched**: `100`
- **confirmed_count**: `33`
- **probable_count**: `38`
- **uncertain_count**: `19`
- **snapshot_path**: `data/snapshots/2026-05-23T04-37-35Z__155dc60e.db`
- **notes**: skipped(no-name-match)=10 · FROM-RAW

### 2026-05-23 — INGESTION

- **run_id**: `fe188090`
- **entity_slug**: `nutting-bob`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Robert Nutting", "Bob Nutting", "Nutting, Robert", "Nutting, Bob"]`
- **api_calls_made**: `6`
- **records_fetched**: `24`
- **confirmed_count**: `23`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-23T04-37-50Z__fe188090.db`
- **notes**: skipped(no-name-match)=0 · states=['WV', 'PA']

### 2026-05-23 — INGESTION

- **run_id**: `9392b8c0`
- **entity_slug**: `fisher-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["John Fisher", "John J. Fisher", "John J Fisher", "John Joseph Fisher", "Fisher, John", "Fisher, John J", "Fisher, John J."]`
- **api_calls_made**: `0`
- **records_fetched**: `1698`
- **confirmed_count**: `147`
- **probable_count**: `246`
- **uncertain_count**: `1267`
- **snapshot_path**: `data/snapshots/2026-05-23T05-04-41Z__9392b8c0.db`
- **notes**: skipped(no-name-match)=38 · FROM-RAW

### 2026-05-23 — INGESTION

- **run_id**: `aae57a83`
- **entity_slug**: `ilitch-chris`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Christopher P Ilitch", "Christopher P. Ilitch", "Christopher Ilitch", "Chris Ilitch", "Christopher Paul Ilitch", "Ilitch, Christopher", "Ilitch, Christopher P", "Ilitch, Christopher P.", "Ilitch, Chris"]`
- **api_calls_made**: `11`
- **records_fetched**: `30`
- **confirmed_count**: `12`
- **probable_count**: `0`
- **uncertain_count**: `18`
- **snapshot_path**: `data/snapshots/2026-05-23T16-57-53Z__aae57a83.db`
- **notes**: skipped(no-name-match)=0 · states=['MI']

### 2026-05-23 — INGESTION

- **run_id**: `2252e059`
- **entity_slug**: `monfort-dick`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Richard L Monfort", "Richard L. Monfort", "Richard Monfort", "Dick Monfort", "Monfort, Richard", "Monfort, Richard L", "Monfort, Richard L.", "Monfort, Dick"]`
- **api_calls_made**: `12`
- **records_fetched**: `106`
- **confirmed_count**: `45`
- **probable_count**: `40`
- **uncertain_count**: `17`
- **snapshot_path**: `data/snapshots/2026-05-23T16-57-54Z__2252e059.db`
- **notes**: skipped(no-name-match)=4 · states=['CO']

### 2026-05-23 — INGESTION

- **run_id**: `ba257fa3`
- **entity_slug**: `dolan-paul`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Paul J Dolan", "Paul J. Dolan", "Paul Dolan", "Paul Joseph Dolan", "Dolan, Paul", "Dolan, Paul J", "Dolan, Paul J."]`
- **api_calls_made**: `9`
- **records_fetched**: `68`
- **confirmed_count**: `60`
- **probable_count**: `7`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-23T17-25-42Z__ba257fa3.db`
- **notes**: skipped(no-name-match)=1 · states=['OH']

### 2026-05-23 — INGESTION

- **run_id**: `a0b17326`
- **entity_slug**: `lerner-mark`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Mark D Lerner", "Mark D. Lerner", "Mark Lerner", "Lerner, Mark", "Lerner, Mark D", "Lerner, Mark D."]`
- **api_calls_made**: `14`
- **records_fetched**: `240`
- **confirmed_count**: `62`
- **probable_count**: `13`
- **uncertain_count**: `163`
- **snapshot_path**: `data/snapshots/2026-05-23T17-25-40Z__a0b17326.db`
- **notes**: skipped(no-name-match)=2 · states=['MD']

### 2026-05-23 — INGESTION

- **run_id**: `ad15ab15`
- **entity_slug**: `stanton-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["John W Stanton", "John W. Stanton", "John Stanton", "Stanton, John", "Stanton, John W", "Stanton, John W."]`
- **api_calls_made**: `14`
- **records_fetched**: `326`
- **confirmed_count**: `68`
- **probable_count**: `19`
- **uncertain_count**: `96`
- **snapshot_path**: `data/snapshots/2026-05-23T17-37-10Z__ad15ab15.db`
- **notes**: skipped(no-name-match)=143 · states=['WA']

### 2026-05-23 — INGESTION

- **run_id**: `d87f0012`
- **entity_slug**: `sherman-bruce`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Bruce S Sherman", "Bruce S. Sherman", "Bruce Sherman", "Sherman, Bruce", "Sherman, Bruce S", "Sherman, Bruce S."]`
- **api_calls_made**: `12`
- **records_fetched**: `196`
- **confirmed_count**: `51`
- **probable_count**: `14`
- **uncertain_count**: `131`
- **snapshot_path**: `data/snapshots/2026-05-23T17-25-37Z__d87f0012.db`
- **notes**: skipped(no-name-match)=0 · states=['FL']

### 2026-05-23 — INGESTION

- **run_id**: `2f7503b4`
- **entity_slug**: `pohlad-joe`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Joseph C Pohlad", "Joseph C. Pohlad", "Joseph Pohlad", "Joe Pohlad", "Pohlad, Joseph", "Pohlad, Joseph C", "Pohlad, Joseph C.", "Pohlad, Joe"]`
- **api_calls_made**: `0`
- **records_fetched**: `171`
- **confirmed_count**: `92`
- **probable_count**: `14`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-05-23T18-01-50Z__2f7503b4.db`
- **notes**: skipped(no-name-match)=64 · FROM-RAW

### 2026-05-23 — INGESTION

- **run_id**: `cd0c46ce`
- **entity_slug**: `sherman-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["John J Sherman", "John J. Sherman", "John Sherman", "Sherman, John", "Sherman, John J", "Sherman, John J."]`
- **api_calls_made**: `28`
- **records_fetched**: `526`
- **confirmed_count**: `34`
- **probable_count**: `6`
- **uncertain_count**: `305`
- **snapshot_path**: `data/snapshots/2026-05-23T17-37-08Z__cd0c46ce.db`
- **notes**: skipped(no-name-match)=181 · states=['MO', 'KS', 'FL']

### 2026-05-23 — INGESTION

- **run_id**: `7a4292ae`
- **entity_slug**: `johnson-greg`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Gregory E Johnson", "Gregory E. Johnson", "Gregory Eugene Johnson", "Greg E Johnson", "Greg E. Johnson", "Greg Johnson", "Johnson, Gregory", "Johnson, Gregory E", "Johnson, Gregory E.", "Johnson, Greg"]`
- **api_calls_made**: `76`
- **records_fetched**: `2497`
- **confirmed_count**: `98`
- **probable_count**: `9`
- **uncertain_count**: `2328`
- **snapshot_path**: `data/snapshots/2026-05-23T17-44-31Z__7a4292ae.db`
- **notes**: skipped(no-name-match)=62 · states=['CA']

### 2026-05-23 — NOTE — Phase 2 batch 3 summary

Nine owners promoted from `queued` → `pilot` and ingested in this batch. All YAMLs validate; per-owner CSV exports refreshed (`data/donations/<slug>/` and `data/donations/_aggregate/`).

Per-owner batch-3 outcomes:

| slug          | team                  | states         | C / P / U      | API calls | notes                                          |
|---------------|-----------------------|----------------|----------------|-----------|------------------------------------------------|
| dolan-paul    | Cleveland Guardians   | OH             |   60 /  7 /   0 |  9       | cleanest run — tight Cleveland Indians/Guardians + Thrasher Dinsmore signals |
| ilitch-chris  | Detroit Tigers        | MI             |   12 /  0 /  18 | 11       | rare surname + strong "Ilitch Holdings" signal |
| johnson-greg  | San Francisco Giants  | CA             |   98 /  9 /2328 | 76       | HIGH doppelganger — strong_signals empty by design; 2328 UNCERTAIN is "Greg/Gregory Johnson" CA flood awaiting calibration |
| lerner-mark   | Washington Nationals  | MD             |   62 / 13 / 163 | 14       | "Lerner Enterprises" strong; 163 UNCERTAIN likely other Mark Lerners in MD |
| monfort-dick  | Colorado Rockies      | CO             |   45 / 40 /  17 | 12       | high PROBABLE — many Denver records hit one signal only |
| pohlad-joe    | Minnesota Twins       | MN (FROM-RAW)  |   92 / 14 /   1 |  0       | FORMER exec chair (tenure_end 2025-12-17 per Castellini precedent). Initial fetch timed out on `Pohlad, Joseph C` mid-pagination; classified from 9 saved raw payloads (171 unique records). Defensible first pass. |
| sherman-bruce | Miami Marlins         | FL             |   51 / 14 / 131 | 12       | First attempt timed out on `Sherman, Bruce S` variant; retry succeeded. M4 Capital strong_signal hit. 131 UNCERTAIN likely other Bruce Shermans in FL |
| sherman-john  | Kansas City Royals    | MO, KS, FL     |   34 /  6 / 305 | 28       | HIGH doppelganger (common name + 3 states). 305 UNCERTAIN awaiting calibration |
| stanton-john  | Seattle Mariners      | WA             |   68 / 19 /  96 | 14       | Pre-emptive negative_signals (BuzzFeed/CongressDaily journalist doppelganger) in YAML at creation |
| **TOTAL**     |                       |                | **522 / 122 / 3059** | **176** |                                                |

Cumulative archive after batch 3: **25 owners ingested**, ~2,543 CONFIRMED records (2,021 carried forward + 522 new). Aggregate CSV: 438 confirmed-only rows, 640 with-probable rows.

Operational gotchas:
- FEC `/schedules/schedule_a/` timed out (5 retries) on two specific paginated queries:
  - `sherman-bruce` variant `"Sherman, Bruce S"` state=['FL'] — retry succeeded.
  - `pohlad-joe` variant `"Pohlad, Joseph C"` state=['MN'] mid-pagination (`last_index=4120220111147314698`) — both initial run and retry failed. Classified from saved raw payloads (`--from-raw`); the missed pages of that one variant may include a small number of additional Joe Pohlad records. Acceptable for first pass; can resume the targeted variant in a future calibration round if a known-missing donation surfaces.

Top calibration targets (in order):
1. `johnson-greg` — 2328 UNCERTAINs dominated by CA Greg/Gregory Johnson population. Need: identify doppelgangers via spot-check of sample, add negative_signals.employers, possibly populate strong_signals if a unique Greg-Johnson-specific employer string surfaces in CONFIRMED audit.
2. `sherman-john` — 305 UNCERTAINs across MO/KS/FL. Expected hits: Ellsworth-KS attorney (negative_signal on law firm if observed), Brooks Sherman of Knothole Sports (DIFFERENT first name — won't collide via name_variants, no action needed), other KC-area John Shermans.
3. `lerner-mark` — 163 UNCERTAINs likely other Mark Lerners in MD. The Lerner Enterprises strong_signal is doing the right work; spot-check UNCERTAINs for any near-miss patterns.
4. `sherman-bruce` — 131 UNCERTAINs in FL. Probably other Bruce Shermans (research flagged a fintech Bruce Sherman at Tipalti).
5. `stanton-john` — 96 UNCERTAINs. Already has pre-emptive BuzzFeed/CongressDaily negative_signal; spot-check for other doppelgangers (e.g., St. Joseph's University food-marketing professor).
6. `monfort-dick` — 40 PROBABLEs may include real Dick records with only one signal; check whether McGregor Square / West Lot LLC or specific occupation patterns can promote some via signal additions.

Family / separate-slug candidates surfaced during this batch (not added, per GOVERNANCE.md §1.7; queued for future batches):
- `dolan-larry` (Paul Dolan's father, d. 2025-02-23) — historical-only.
- `dolan-matthew` (Paul Dolan's brother, OH State Senator, 2x US Senate primary candidate) — separate donor.
- `monfort-charlie` (Dick Monfort's brother, co-owner Rockies).
- `monfort-walker` (Dick's son, Rockies team president).
- `ilitch-marian` (Mike Ilitch's widow; documented major political donor in own right; MotorCity Casino owner).
- `pohlad-tom` (Joe's older brother, CURRENT Twins controlling owner since 2025-12-17) — high priority for batch 4 or follow-on.
- `johnson-charles` (Greg Johnson's father, LARGEST Giants individual shareholder ~26%, Palm Beach FL; major Republican donor).
- Lerner family in-laws: `cohen-edward` (Edward L. Cohen, Nationals Vice Chairman), `tanenbaum-robert` (Robert K. Tanenbaum, Nationals Vice Chairman), plus `lerner-annette`, `lerner-judy`, `lerner-debra`, `lerner-marla` — all named Nationals principal owners with potential own donor profiles.

What's next per the project briefing:
- Batch 4 (new principals after sale closures): `feliciano-jose` (Padres), `zalupski-patrick` (Rays), `davis-ray` and/or `simpson-bob` (Rangers co-owners — fix misnamed `simpson-ray` slug in registry).
- Special-case batch (need a small schema discussion before YAML write): `liberty-media-group` (Braves corporate parent — track John Malone personally and/or Liberty Media PAC), `rogers-communications` (Blue Jays Canadian parent).
- Calibration round across batch 3 owners — particularly johnson-greg and sherman-john.

### 2026-05-23 — INGESTION

- **run_id**: `1d30f6d7`
- **entity_slug**: `seidler-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["John Seidler", "Seidler, John"]`
- **api_calls_made**: `4`
- **records_fetched**: `1`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-23T18-47-44Z__1d30f6d7.db`
- **notes**: skipped(no-name-match)=0 · states=['CA']

### 2026-05-23 — INGESTION

- **run_id**: `3a271fed`
- **entity_slug**: `zalupski-patrick`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Patrick O Zalupski", "Patrick O. Zalupski", "Patrick Zalupski", "Zalupski, Patrick", "Zalupski, Patrick O", "Zalupski, Patrick O."]`
- **api_calls_made**: `6`
- **records_fetched**: `20`
- **confirmed_count**: `15`
- **probable_count**: `0`
- **uncertain_count**: `5`
- **snapshot_path**: `data/snapshots/2026-05-23T18-47-42Z__3a271fed.db`
- **notes**: skipped(no-name-match)=0 · states=['FL']

### 2026-05-23 — INGESTION

- **run_id**: `8181a08f`
- **entity_slug**: `feliciano-jose`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Jose E Feliciano", "Jose E. Feliciano", "Jose Feliciano", "Jos\u00e9 E. Feliciano", "Feliciano, Jose", "Feliciano, Jose E", "Feliciano, Jose E."]`
- **api_calls_made**: `10`
- **records_fetched**: `63`
- **confirmed_count**: `23`
- **probable_count**: `1`
- **uncertain_count**: `15`
- **snapshot_path**: `data/snapshots/2026-05-23T18-48-38Z__8181a08f.db`
- **notes**: skipped(no-name-match)=24 · states=['CA']

### 2026-05-23 — INGESTION

- **run_id**: `54191a41`
- **entity_slug**: `simpson-bob`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Bob R Simpson", "Bob R. Simpson", "Simpson, Bob R", "Simpson, Bob R."]`
- **api_calls_made**: `4`
- **records_fetched**: `8`
- **confirmed_count**: `0`
- **probable_count**: `8`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-23T18-48-39Z__54191a41.db`
- **notes**: skipped(no-name-match)=0 · states=['TX']

### 2026-05-23 — INGESTION

- **run_id**: `74728f10`
- **entity_slug**: `davis-ray`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Ray C Davis", "Ray C. Davis", "Ray Davis", "Davis, Ray", "Davis, Ray C", "Davis, Ray C."]`
- **api_calls_made**: `0`
- **records_fetched**: `318`
- **confirmed_count**: `52`
- **probable_count**: `29`
- **uncertain_count**: `13`
- **snapshot_path**: `data/snapshots/2026-05-23T19-08-33Z__74728f10.db`
- **notes**: skipped(no-name-match)=224 · FROM-RAW

### 2026-05-23 — NOTE — Phase 2 batch 4 summary

Batch 4 focused on **post-sale-closure principal transitions** plus fixing the misnamed `simpson-ray` placeholder. Five new pilots ingested; two registry placeholder slugs retired.

#### Registry changes

**Retired (placeholder slugs replaced by concrete entries):**

- **`seidler-family`** — anticipatory placeholder for the Padres ownership during the interim Eric Kutsenda / Peter Seidler estate period (Nov 2023 - Feb 2025). Never had a YAML file and was never ingested. Superseded by two concrete slugs: `seidler-john` (current MLB-approved control person since Feb 2025) and `feliciano-jose` (incoming control person under the May 2, 2026 sale agreement).
- **`simpson-ray`** — misnamed combined entry that conflated two distinct billionaires (Ray C. Davis and Bob R. Simpson) under one slug. Never had a YAML file. Superseded by two concrete slugs: `davis-ray` (formal MLB-designated control person, Dallas-based) and `simpson-bob` (co-chairman, Fort Worth-based) — entirely different signal stacks.

**Status updates:**

- **`sternberg-stuart`** — added `tenure_end_date: 2025-09-30`, role updated to "Principal owner (FORMER)", notes updated to reflect that Zalupski took over (Sternberg retained ~10% minority stake). Per project policy (TRACK ONLY current/active and future-confirmed owners; angelos-john-p pattern), NOT ingested in this batch. A lightweight historical YAML (analogous to `owners/angelos-john-p.yaml`) is a future task.

**New pilots added:**

| slug              | team                 | status | tenure                       |
|-------------------|----------------------|--------|------------------------------|
| `seidler-john`    | San Diego Padres     | pilot  | 2025-02-15 → null (mid-2026 close pending) |
| `feliciano-jose`  | San Diego Padres     | pilot  | 2026-05-02 → null (PRE-APPROVAL, MLB vote pending June 2026) |
| `zalupski-patrick`| Tampa Bay Rays       | pilot  | 2025-09-30 → null |
| `davis-ray`       | Texas Rangers        | pilot  | 2010-08-12 → null |
| `simpson-bob`     | Texas Rangers        | pilot  | 2010-08-12 → null |

The Padres dual-pilot is a deliberate user-approved exception: the sale to Feliciano/Jones at $3.9B (a new MLB-sale record) was announced 2026-05-02 but MLB approval is expected at the June 2026 owners meeting with close pre-2026 All-Star Break. Both Seidler (current) and Feliciano (future-confirmed) are tracked as pilots per the briefing's "future-confirmed owners" wording. When MLB approves and the deal closes, `seidler-john.yaml`'s `tenure_end_date` and `feliciano-jose.yaml`'s `tenure_start_date` should both be updated to the close date.

#### Per-owner batch-4 outcomes

| slug              | states  | C / P / U      | API | Notes                                                     |
|-------------------|---------|----------------|-----|-----------------------------------------------------------|
| `davis-ray`       | TX      |   52 / 29 / 13 |   0 | FROM-RAW — initial fetch timed out on `Davis, Ray C` variant mid-pagination (`last_index=4072620131194550873`). Classified from 13 saved raw payloads (318 unique records; 224 name-skipped — many other TX Ray Davises). |
| `feliciano-jose`  | CA      |   23 /  1 / 15 |  10 | Singer José Feliciano (b. 1945, CT) doppelganger filtered by CA state + pre-emptive negative_signals.employers (Musician/Singer/Entertainer/Polydor/JF Recording). 24 name-skipped. |
| `seidler-john`    | CA      |    1 /  0 /  0 |   4 | Tiny donor footprint (family-office runner). Sale pending — tenure_end will be set when Feliciano closes. |
| `simpson-bob`     | TX      |    0 /  8 /  0 |   4 | All 8 PROBABLE are genuinely him — name "SIMPSON, BOB R. MR." + Fort Worth city + empty/"INFORMATION REQUESTED" employer (PAC reports without donor-employer due diligence). Strict "R." middle initial requirement worked as designed. |
| `zalupski-patrick`| FL      |   15 /  0 /  5 |   6 | Clean run. Dream Finders Homes / DFH / DF Capital strong_signals fired. Rare Polish surname keeps doppelganger risk low. |
| **TOTAL**         |         | **91 / 38 / 33** | **24** |                                                       |

Cumulative archive after batch 4: **30 owners ingested** (counting `angelos-john-p` as 0/0/0 historical record; `sternberg-stuart` not yet a YAML), ~2,634 CONFIRMED records. Aggregate CSV: 469 confirmed-only rows, 687 with-probable rows.

#### Operational note

FEC `/schedules/schedule_a/` timed out again — this time on `davis-ray` variant `"Davis, Ray C"` state=['TX'] mid-pagination. The timeouts on common-name + populous-state combinations have now hit `sherman-bruce` (FL), `pohlad-joe` (MN), and `davis-ray` (TX). All three were resolvable via `--from-raw` against the partial saved payloads, but a more durable fix may be worth investigating in a future session (e.g., shorter `last_contribution_receipt_date` window per query, retry with backoff longer than 120s, or splitting deep pagination into date-bounded sub-fetches).

#### Calibration targets

1. **`simpson-bob`** — 8 PROBABLE records would promote to CONFIRMED with a Fort Worth strong-ZIP signal. Could be added if his Tarrant County property records identify a specific ZIP from public sources.
2. **`feliciano-jose`** — 15 UNCERTAINs likely other Jose Felicianos in CA. Once MLB approves and the deal closes, can revisit with broader Tier 2 sources from the closing.
3. **`davis-ray`** — 224 name-skipped (other Ray Davises in TX without our signals) is expected; spot-check 29 PROBABLEs for one-signal-only patterns.

#### Family / separate-slug candidates surfaced (not added, per GOVERNANCE.md §1.7)

- `jones-kwanza` (Kwanza Jones — co-leader of the Feliciano Padres bid; Princeton '93; JD Cardozo Law; independent professional / philanthropic identity).
- `seidler-peter` (Peter Seidler, deceased 2023-11-14) — historical record only.
- Other Seidler siblings (Robert, Matthew = Peter trust trustees; Sheel Seidler = Peter's widow, in litigation with brothers).
- Spouses: Leah Zalupski, Linda Davis, Janice Simpson — none documented as politically active in Tier 2.

#### What's next per the project briefing

- **Special-case batch**: `liberty-media-group` (Braves corporate parent — track John Malone personally and/or Liberty Media PAC?) and `rogers-communications` (Blue Jays Canadian parent — minimal FEC footprint expected). Needs a small schema discussion before YAMLs.
- **Historical-only YAMLs** for `sternberg-stuart` and `seidler-peter` (deceased), modeled on `owners/angelos-john-p.yaml`.
- **Calibration round** — johnson-greg (2,328 UNCERTAINs from batch 3) is the highest-priority calibration target across the archive; sherman-john (305 UNCERTAINs) second.
- **Phil Castellini Reds YAML** (`castellini-phil`), Tom Pohlad Twins YAML (`pohlad-tom`), and other separate-slug candidates from earlier batches.

The archive now covers 30 of 30 MLB principal owners (current or recent), with two historical placeholders queued (Sternberg/Seidler) and the Braves/Blue Jays corporate-parent cases pending the special-case discussion.

### 2026-05-23 — INGESTION

- **run_id**: `8d5ad9a6`
- **entity_slug**: `mcguirk-terry`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Terence F McGuirk", "Terence F. McGuirk", "Terence McGuirk", "Terry McGuirk", "Terry F. McGuirk", "Terry F McGuirk", "McGuirk, Terence", "McGuirk, Terence F", "McGuirk, Terence F.", "McGuirk, Terry"]`
- **api_calls_made**: `13`
- **records_fetched**: `47`
- **confirmed_count**: `39`
- **probable_count**: `8`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-23T19-32-44Z__8d5ad9a6.db`
- **notes**: skipped(no-name-match)=0 · states=['GA']

### 2026-05-23 — INGESTION

- **run_id**: `2986bac0`
- **entity_slug**: `pohlad-tom`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Thomas Pohlad", "Tom Pohlad", "Pohlad, Thomas", "Pohlad, Tom"]`
- **api_calls_made**: `10`
- **records_fetched**: `152`
- **confirmed_count**: `71`
- **probable_count**: `24`
- **uncertain_count**: `24`
- **snapshot_path**: `data/snapshots/2026-05-23T19-56-15Z__2986bac0.db`
- **notes**: skipped(no-name-match)=33 · states=['MN']

### 2026-05-23 — INGESTION

- **run_id**: `8efb714f`
- **entity_slug**: `malone-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["John C Malone", "John C. Malone", "John Malone", "John Carl Malone", "Malone, John", "Malone, John C", "Malone, John C."]`
- **api_calls_made**: `0`
- **records_fetched**: `57`
- **confirmed_count**: `41`
- **probable_count**: `7`
- **uncertain_count**: `7`
- **snapshot_path**: `data/snapshots/2026-05-23T19-59-24Z__8efb714f.db`
- **notes**: skipped(no-name-match)=2 · FROM-RAW

### 2026-05-23 — INGESTION

- **run_id**: `c68572b4`
- **entity_slug**: `castellini-phil`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Phillip J Castellini", "Phillip J. Castellini", "Phillip Castellini", "Phil Castellini", "Phil J. Castellini", "Phil J Castellini", "Castellini, Phillip", "Castellini, Phillip J", "Castellini, Phillip J.", "Castellini, Phil"]`
- **api_calls_made**: `14`
- **records_fetched**: `49`
- **confirmed_count**: `41`
- **probable_count**: `6`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-23T19-56-16Z__c68572b4.db`
- **notes**: skipped(no-name-match)=2 · states=['OH', 'KY']

### 2026-05-23 — NOTE — Phase 2 special-case batch + owner-roster completion

Two related work blocks completed in one session:

1. **Special-case batch** — corporate-parent ownership cases (Braves, Blue Jays) that required a schema discussion before YAML write.
2. **Owner-roster completion** — closed the last two intra-family-transition gaps (Tom Pohlad replacing Joe at Twins; Phil Castellini replacing Bob at Reds).

After this batch, **the archive covers 30/30 current MLB principal-owner equivalents**, with 1 paused placeholder (Blue Jays / Rogers — Canadian, no FEC scope), 2 historical-only FORMER entries with completed giving history (`castellini-bob`, `pohlad-joe`), and 1 historical-no-YAML placeholder (`sternberg-stuart`, registry-only).

#### Registry changes

**Retired (placeholder slugs replaced):**

- **`liberty-media-group`** — anticipatory placeholder for Atlanta Braves corporate-parent attribution. Made obsolete by the 2023-07-18 split-off of Atlanta Braves Holdings (NASDAQ: BATRA/BATRK) from Liberty Media. Never had a YAML file. Replaced by two co-principal slugs (user picked Davis-Simpson-style co-principal model): `mcguirk-terry` (MLB Control Person + day-to-day Chairman/President/CEO) and `malone-john` (largest economic + voting shareholder; Liberty Media chairman emeritus from Jan 1, 2026). Liberty Media Corporation PAC (FEC C00508101) is TERMINATED so no related-entity entry needed.
- **`rogers-communications`** — anticipatory placeholder for Toronto Blue Jays Canadian-corporate-parent attribution. Never had a YAML file. Replaced by `rogers-edward-iii` (Executive Chair Rogers Communications since 2024-08-14; Chair Rogers Control Trust holding ~97.52% of voting shares; Chairman Rogers Blue Jays Baseball Partnership) with status `paused` per user direction. Reasoning: Edward Rogers III is Canadian with no documented US residence; 52 U.S.C. § 30121 bars non-citizen federal donations; no Rogers Communications-affiliated FEC PAC exists. Two "Rogers"-named PACs in FEC are unrelated (Rogers Group Inc. = Nashville TN aggregates company; Team Rogers = Mike Rogers MI Senate JFC).

**Status updates (FORMER tracking on intra-family transitions):**

- **`castellini-bob`** — role updated to "Principal owner (FORMER)" to match the FORMER-pattern used for Pohlad-joe. tenure_end_date 2026-02-12 unchanged. Phil Castellini (son) tracked separately under `castellini-phil`.
- **`pohlad-joe`** — already marked FORMER in batch 3. Tom Pohlad (brother) now tracked separately under `pohlad-tom`.

**New pilots added (5):**

| slug                  | team              | status | tenure                       |
|-----------------------|-------------------|--------|------------------------------|
| `mcguirk-terry`       | Atlanta Braves    | pilot  | 2007-01-01 → null            |
| `malone-john`         | Atlanta Braves    | pilot  | 2007-05-16 → null            |
| `rogers-edward-iii`   | Toronto Blue Jays | paused | 2024-08-14 → null (NOT INGESTED) |
| `pohlad-tom`          | Minnesota Twins   | pilot  | 2025-12-17 → null            |
| `castellini-phil`     | Cincinnati Reds   | pilot  | 2026-02-12 → null            |

#### Per-owner outcomes

| slug              | states  | C / P / U      | API | Notes                                                       |
|-------------------|---------|----------------|-----|-------------------------------------------------------------|
| `mcguirk-terry`   | GA      |   39 /  8 /  0 |  13 | Cleanest run — Atlanta + Braves/Turner/Time Warner employers worked perfectly; 0 UNCERTAIN. |
| `malone-john`     | CO      |   40 /  7 /  7 |   0 | FROM-RAW after BOTH attempts timed out mid-pagination on "John C Malone" CO (~13 minutes deep pagination on each retry). 1 saved raw page; 57 records. Fisher-precedent partial first pass — most recent records captured well, deep history needs a date-chunked fetch strategy. Calibration target. |
| `rogers-edward-iii` | (paused) |   — / — / — |   — | NOT INGESTED. Paused placeholder documenting Canadian-citizen / no-FEC-scope structural gap. Full signal block populated for future re-activation. |
| `pohlad-tom`      | MN      |   71 / 24 / 24 |  10 | PaR Systems + Carousel Motor Group strong_signals fired; 33 name-skipped (other Pohlads filtered by strict "Tom/Thomas" name_variants). |
| `castellini-phil` | OH, KY  |   41 /  6 /  0 |  14 | Clean — Merchants Cold Storage + Castellini Group / Castellini Company strong_signals fired; 0 UNCERTAIN. |
| **TOTAL**         |         | **191 / 45 / 31** | **37** | (Rogers excluded — paused.)                              |

Cumulative archive after this batch: **30/30 current MLB principal-owner equivalents covered**. Status table shows 35 slugs in registry (including 1 paused, 1 angelos-historical-no-ingest, 2 FORMER with full history). Aggregate CSV: 529 confirmed-only rows, 763 with-probable rows. Total CONFIRMED records across all ingested slugs: ~2,825.

#### Operational gotchas

- **malone-john FEC timeout**: Both attempts (initial + retry) timed out on the FIRST variant `"John C Malone"` state=['CO'], mid-pagination after ~13 minutes. Only 1 raw page (57 records) saved. The common-name + populous-state combination plus 26 years of pagination depth (DESC sort going back to 2000) is too deep for a single connection. Pattern observed previously on sherman-bruce, pohlad-joe, davis-ray — all resolved via `--from-raw`. Future-session work: implement date-window chunking (e.g., 2-3 year sub-fetches) to reduce per-query pagination depth for common-name owners.

#### Schema-level decisions captured

- **Corporate-parent ownership cases** (post-spinoff Braves, Canadian Blue Jays) handled within the existing OWNER_SCHEMA without modification. Pattern: track individual principal(s) at the corporate parent; corporate-affiliated PACs go in `related_entities` when applicable; if no US-citizen principal exists, use `status: paused` with full signal block populated for future re-activation.
- **Co-principal model** (Davis-Simpson on Rangers, McGuirk-Malone on Braves): when two individuals jointly hold ownership-equivalent stake/control, both get separate pilot slugs with distinct signal stacks. Name-variants disambiguate at the FEC matcher; cross-attribution is prevented by different first names.
- **Editorial caveat for Malone-style cases**: Malone's ~$2M of 2024-cycle Republican federal giving is overwhelmingly driven by his Liberty Media identity, NOT his Braves stake. Reports aggregating "MLB owner political giving" should tag accordingly. This is a known-but-tracked attribution quirk of the co-principal model in this archive.

#### Coverage map (post-batch)

All 30 MLB teams have at least one tracked owner equivalent:

| Team status | Slugs |
|---|---|
| **Single principal pilot** (24 teams) | attanasio-mark, cohen-steven, crane-jim, dewitt-bill, dolan-paul, fisher-john, henry-john, ilitch-chris, johnson-greg, kendrick-ken, lerner-mark, middleton-john, monfort-dick, moreno-arte, nutting-bob, ricketts-tom, rubenstein-david, sherman-bruce, sherman-john, stanton-john, steinbrenner-hal, walter-mark, zalupski-patrick, reinsdorf-jerry |
| **Co-principals or transitional dual-pilot** (3 teams) | Braves (mcguirk-terry + malone-john) · Padres (seidler-john + feliciano-jose) · Rangers (davis-ray + simpson-bob) |
| **FORMER + current pair** (2 teams) | Reds (castellini-bob FORMER + castellini-phil current) · Twins (pohlad-joe FORMER + pohlad-tom current) |
| **Paused placeholder** (1 team) | Blue Jays (rogers-edward-iii — Canadian, no FEC scope) |
| **Historical-only no-ingestion + new principal** (1 team, partially) | Orioles (angelos-john-p historical-no-YAML-ingest + rubenstein-david current) |
| **Outgoing historical placeholders pending YAMLs** | sternberg-stuart (Rays predecessor; registry-only) |

#### Calibration targets (priority order)

1. `johnson-greg` — 2,328 UNCERTAINs (largest pile in archive).
2. `cohen-steven` — 2,785 UNCERTAINs but already calibration-round-1 done; revisit only if a target donation surfaces.
3. `sherman-john` — 305 UNCERTAINs (KC + KS attorney doppelganger flagged).
4. `lerner-mark` — 163 UNCERTAINs (other Mark Lerners in MD; psychologist doppelganger).
5. `sherman-bruce` — 131 UNCERTAINs (other Bruce Shermans in FL).
6. `malone-john` — partial fetch coverage; needs date-windowed fetch strategy for deep history.

#### Family / separate-slug candidates surfaced (still queued, not added per GOVERNANCE.md §1.7)

- `johnson-charles` (Greg Johnson's father, LARGEST Giants individual shareholder ~26%, Palm Beach FL; major Republican donor).
- `ilitch-marian` (Chris Ilitch's mother; MotorCity Casino principal; documented major political donor).
- `castellini-britt` (Phil Castellini's spouse; documented politically active — co-hosted Oct 2022 KY-04 Democratic fundraiser).
- `jones-kwanza` (Jose Feliciano's spouse / co-leader of Padres bid).
- `dolan-larry` (Paul Dolan's father, d. 2025-02-23).
- `dolan-matthew` (Paul Dolan's brother, OH State Senator + 2x US Senate primary candidate).
- `monfort-charlie` (Dick Monfort's brother; Rockies co-owner/GP).
- `pohlad-bob` (Tom/Joe Pohlad's father; PepsiAmericas former CEO).
- Lerner family cluster: `lerner-ted` (deceased 2023), `cohen-edward`, `tanenbaum-robert` (Nationals VCs), `lerner-annette`, `lerner-judy`, `lerner-marla`, `lerner-debra`.

#### What's next

- **Calibration sweep** — johnson-greg first (worst UNCERTAIN pile); date-windowed fetch retry for malone-john.
- **Historical-only YAMLs** — `sternberg-stuart` and optionally `seidler-peter` modeled on angelos-john-p.yaml.
- **Family separate-slug expansions** — case-by-case, in priority order; each requires a deliberate scope-expansion decision per GOVERNANCE.md §1.7.
- **Pipeline durability** — implement date-window chunking for common-name owners to address the FEC timeout pattern.

### 2026-05-25 — DELETION — reclassify rubenstein-david

- **entity_slug**: `rubenstein-david`
- **reason**: Tier-A calibration round 1: added negative_signals.employers: Georgetown University to demote 1 doppelgänger PROBABLE record (2008 Giuliani filing under 'V.P. OF FINANCIAL PLANNING & ANALYSIS')
- **rows_deleted_donations**: `8`
- **rows_deleted_review_queue**: `23` (of which 0 had resolutions)
- **snapshot_path**: `data/snapshots/2026-05-25T03-46-29Z__pre-reclassify-rubenstein-david.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/rubenstein-david/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `3989d9b9`
- **entity_slug**: `rubenstein-david`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-01-09`
- **name_variants_queried**: `["David Rubenstein", "David M. Rubenstein", "David M Rubenstein", "Rubenstein, David", "Rubenstein, David M", "Rubenstein, David M."]`
- **api_calls_made**: `0`
- **records_fetched**: `31`
- **confirmed_count**: `2`
- **probable_count**: `5`
- **uncertain_count**: `24`
- **snapshot_path**: `data/snapshots/2026-05-25T03-46-29Z__3989d9b9.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify fisher-john

- **entity_slug**: `fisher-john`
- **reason**: Tier-A round 1: 6 negative_signals.employers (WSP/Parsons/Sky Oak/SKS/DFJ/Draper Fisher Jurvetson) to demote 4 distinct doppelgänger clusters
- **rows_deleted_donations**: `393`
- **rows_deleted_review_queue**: `1267` (of which 0 had resolutions)
- **snapshot_path**: `data/snapshots/2026-05-25T03-50-05Z__pre-reclassify-fisher-john.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/fisher-john/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `6aa2f908`
- **entity_slug**: `fisher-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-30`
- **name_variants_queried**: `["John Fisher", "John J. Fisher", "John J Fisher", "John Joseph Fisher", "Fisher, John", "Fisher, John J", "Fisher, John J."]`
- **api_calls_made**: `0`
- **records_fetched**: `1698`
- **confirmed_count**: `138`
- **probable_count**: `43`
- **uncertain_count**: `1479`
- **snapshot_path**: `data/snapshots/2026-05-25T03-50-05Z__6aa2f908.db`
- **notes**: skipped(no-name-match)=38 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify middleton-john

- **entity_slug**: `middleton-john`
- **reason**: Tier-A round 1: added John P. Middleton (son) as related_entity (kind: child); added Branford Holdings + Mc Intosh Inns typo variants to verifying_signals.employers
- **rows_deleted_donations**: `71`
- **rows_deleted_review_queue**: `19` (of which 0 had resolutions)
- **include_related**: `True`
- **snapshot_path**: `data/snapshots/2026-05-25T04-02-44Z__pre-reclassify-middleton-john.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/middleton-john/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `d5bea8f9`
- **entity_slug**: `middleton-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-29`
- **name_variants_queried**: `["John Middleton", "John S. Middleton", "John S Middleton", "John Staubus Middleton", "Middleton, John", "Middleton, John S", "Middleton, John S."]`
- **api_calls_made**: `0`
- **records_fetched**: `100`
- **confirmed_count**: `53`
- **probable_count**: `18`
- **uncertain_count**: `19`
- **snapshot_path**: `data/snapshots/2026-05-25T04-02-44Z__d5bea8f9.db`
- **notes**: skipped(no-name-match)=10 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify middleton-john

- **entity_slug**: `middleton-john`
- **reason**: Tier-A round 1 (revised): Vertigo negative_signal + Branford/Mc Intosh typo variants. Reverted from related_entity approach due to classifier middle-initial limitation — see YAML change_log for details
- **rows_deleted_donations**: `71`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `True`
- **snapshot_path**: `data/snapshots/2026-05-25T04-05-04Z__pre-reclassify-middleton-john.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/middleton-john/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `e74158c2`
- **entity_slug**: `middleton-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-29`
- **name_variants_queried**: `["John Middleton", "John S. Middleton", "John S Middleton", "John Staubus Middleton", "Middleton, John", "Middleton, John S", "Middleton, John S."]`
- **api_calls_made**: `0`
- **records_fetched**: `100`
- **confirmed_count**: `35`
- **probable_count**: `23`
- **uncertain_count**: `32`
- **snapshot_path**: `data/snapshots/2026-05-25T04-05-04Z__e74158c2.db`
- **notes**: skipped(no-name-match)=10 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — middleton-john-p (orphaned)

- **entity_slug**: `middleton-john-p`
- **reason**: Orphaned rows from the reverted related_entity experiment (see middleton-john.yaml change_log 2026-05-25). The middleton-john-p entity was added to the YAML, used for one reclassify run, then removed when the related_entity approach was reverted due to classifier middle-initial limitation. Donations under entity_slug=middleton-john-p were already cleaned by the subsequent reclassify (parent_owner_slug=middleton-john deletion); review_queue rows were missed by that SQL and are now cleaned up here.
- **rows_deleted_review_queue**: `19`
- **note**: Pre-deletion snapshot exists at data/snapshots/2026-05-25T04-05-04Z__pre-reclassify-middleton-john.db (taken before the corrective reclassify).

### 2026-05-25 — DELETION — reclassify monfort-dick

- **entity_slug**: `monfort-dick`
- **reason**: Tier-A round 1: added 'eaton' + 'greenley' to verifying_signals.cities (ZIP 80615 spans both labels; all 17 UNCERTAINs verified as Dick Monfort)
- **rows_deleted_donations**: `85`
- **rows_deleted_review_queue**: `17` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T04-12-40Z__pre-reclassify-monfort-dick.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/monfort-dick/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `0d3086ad`
- **entity_slug**: `monfort-dick`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-06-03`
- **name_variants_queried**: `["Richard L Monfort", "Richard L. Monfort", "Richard Monfort", "Dick Monfort", "Monfort, Richard", "Monfort, Richard L", "Monfort, Richard L.", "Monfort, Dick"]`
- **api_calls_made**: `0`
- **records_fetched**: `106`
- **confirmed_count**: `60`
- **probable_count**: `42`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-25T04-12-40Z__0d3086ad.db`
- **notes**: skipped(no-name-match)=4 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify reinsdorf-jerry

- **entity_slug**: `reinsdorf-jerry`
- **reason**: Tier-A round 1 Option B: promoted Bojer Financial to strong_signals.employers; promoted ZIPs 60616 + 606163621 to strong_signals.zip_codes
- **rows_deleted_donations**: `422`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T04-15-04Z__pre-reclassify-reinsdorf-jerry.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/reinsdorf-jerry/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `81b1167d`
- **entity_slug**: `reinsdorf-jerry`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-02-04`
- **name_variants_queried**: `["Jerry Reinsdorf", "Jerry M Reinsdorf", "Jerry M. Reinsdorf", "Jerry Michael Reinsdorf", "Reinsdorf, Jerry", "Reinsdorf, Jerry M", "Reinsdorf, Jerry M."]`
- **api_calls_made**: `0`
- **records_fetched**: `425`
- **confirmed_count**: `422`
- **probable_count**: `2`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-25T04-15-04Z__81b1167d.db`
- **notes**: skipped(no-name-match)=1 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify monfort-dick

- **entity_slug**: `monfort-dick`
- **reason**: Tier-B calibration: promoted 80631/80632/80615 to strong_signals.zip_codes (Greeley/Eaton Monfort family base ZIPs); follow-up to Tier-A round 1
- **rows_deleted_donations**: `102`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T04-27-50Z__pre-reclassify-monfort-dick.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/monfort-dick/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `042a1431`
- **entity_slug**: `monfort-dick`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-06-03`
- **name_variants_queried**: `["Richard L Monfort", "Richard L. Monfort", "Richard Monfort", "Dick Monfort", "Monfort, Richard", "Monfort, Richard L", "Monfort, Richard L.", "Monfort, Dick"]`
- **api_calls_made**: `0`
- **records_fetched**: `106`
- **confirmed_count**: `101`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-25T04-27-50Z__042a1431.db`
- **notes**: skipped(no-name-match)=4 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify davis-ray

- **entity_slug**: `davis-ray`
- **reason**: Tier-B calibration: added 75225 (Highland Park / Avatar Investments Sherry Lane office) to strong_signals.zip_codes
- **rows_deleted_donations**: `81`
- **rows_deleted_review_queue**: `13` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T04-32-38Z__pre-reclassify-davis-ray.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/davis-ray/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `5cebeea6`
- **entity_slug**: `davis-ray`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-01-23`
- **name_variants_queried**: `["Ray C Davis", "Ray C. Davis", "Ray Davis", "Davis, Ray", "Davis, Ray C", "Davis, Ray C."]`
- **api_calls_made**: `0`
- **records_fetched**: `318`
- **confirmed_count**: `71`
- **probable_count**: `10`
- **uncertain_count**: `13`
- **snapshot_path**: `data/snapshots/2026-05-25T04-32-38Z__5cebeea6.db`
- **notes**: skipped(no-name-match)=224 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify moreno-arte

- **entity_slug**: `moreno-arte`
- **reason**: Tier-B: added 85016 + 85018 (Biltmore Estates / Arcadia Phoenix residence ZIPs) to strong_signals.zip_codes
- **rows_deleted_donations**: `73`
- **rows_deleted_review_queue**: `6` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T04-38-53Z__pre-reclassify-moreno-arte.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/moreno-arte/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `892671fc`
- **entity_slug**: `moreno-arte`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-06-03`
- **name_variants_queried**: `["Arturo Moreno", "Arte Moreno", "Arturo R. Moreno", "Arturo R Moreno", "Moreno, Arturo", "Moreno, Arte", "Moreno, Arturo R"]`
- **api_calls_made**: `0`
- **records_fetched**: `79`
- **confirmed_count**: `73`
- **probable_count**: `0`
- **uncertain_count**: `6`
- **snapshot_path**: `data/snapshots/2026-05-25T04-38-53Z__892671fc.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify pohlad-tom

- **entity_slug**: `pohlad-tom`
- **reason**: Tier-B: added Twin Cities Automotive/Inver Grove Volkswagen employers + Lake Minnetonka suburb cities (Excelsior/Deephaven/Shorewood); middle initial O identified via cross-period FEC match
- **rows_deleted_donations**: `95`
- **rows_deleted_review_queue**: `24` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T04-45-43Z__pre-reclassify-pohlad-tom.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/pohlad-tom/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `7afb021a`
- **entity_slug**: `pohlad-tom`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2024-09-24`
- **name_variants_queried**: `["Thomas Pohlad", "Tom Pohlad", "Pohlad, Thomas", "Pohlad, Tom"]`
- **api_calls_made**: `0`
- **records_fetched**: `152`
- **confirmed_count**: `111`
- **probable_count**: `8`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-25T04-45-43Z__7afb021a.db`
- **notes**: skipped(no-name-match)=33 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify pohlad-tom

- **entity_slug**: `pohlad-tom`
- **reason**: Tier-B re-reclassify after restoring inadvertently-dropped occupations block
- **rows_deleted_donations**: `118`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T04-46-32Z__pre-reclassify-pohlad-tom.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/pohlad-tom/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `a04736c9`
- **entity_slug**: `pohlad-tom`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2024-09-24`
- **name_variants_queried**: `["Thomas Pohlad", "Tom Pohlad", "Pohlad, Thomas", "Pohlad, Tom"]`
- **api_calls_made**: `0`
- **records_fetched**: `152`
- **confirmed_count**: `118`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-25T04-46-32Z__a04736c9.db`
- **notes**: skipped(no-name-match)=33 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify ricketts-tom

- **entity_slug**: `ricketts-tom`
- **reason**: Tier-B: added Incapitol/EnCapital Holdings/RAM Investment/RAM Investments/Capitol Building employer variants (alex-verified via 531 Laurel Ave shared address)
- **rows_deleted_donations**: `62`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T05-02-45Z__pre-reclassify-ricketts-tom.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/ricketts-tom/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `a9d60a8c`
- **entity_slug**: `ricketts-tom`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-25`
- **name_variants_queried**: `["Tom Ricketts", "Thomas Ricketts", "Thomas S Ricketts", "Thomas S. Ricketts", "Thomas Stuart Ricketts", "Ricketts, Tom", "Ricketts, Thomas", "Ricketts, Thomas S", "Ricketts, Thomas S."]`
- **api_calls_made**: `0`
- **records_fetched**: `63`
- **confirmed_count**: `61`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-25T05-02-45Z__a9d60a8c.db`
- **notes**: skipped(no-name-match)=1 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify stanton-john

- **entity_slug**: `stanton-john`
- **reason**: Tier-B: added medina/west-medina cities + Trilogy Partners/Triology employer variants
- **rows_deleted_donations**: `87`
- **rows_deleted_review_queue**: `96` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T05-07-37Z__pre-reclassify-stanton-john.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/stanton-john/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `a1f9711f`
- **entity_slug**: `stanton-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-08-12`
- **name_variants_queried**: `["John W Stanton", "John W. Stanton", "John Stanton", "Stanton, John", "Stanton, John W", "Stanton, John W."]`
- **api_calls_made**: `0`
- **records_fetched**: `326`
- **confirmed_count**: `159`
- **probable_count**: `20`
- **uncertain_count**: `4`
- **snapshot_path**: `data/snapshots/2026-05-25T05-07-37Z__a1f9711f.db`
- **notes**: skipped(no-name-match)=143 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify stanton-john

- **entity_slug**: `stanton-john`
- **reason**: Tier-B follow-up: added VoiceStream (one-word) to catch VOICESTREAM/VOICESTREAM COMMUNICATIONS variants
- **rows_deleted_donations**: `179`
- **rows_deleted_review_queue**: `4` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T05-08-25Z__pre-reclassify-stanton-john.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/stanton-john/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `d7c136ab`
- **entity_slug**: `stanton-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-08-12`
- **name_variants_queried**: `["John W Stanton", "John W. Stanton", "John Stanton", "Stanton, John", "Stanton, John W", "Stanton, John W."]`
- **api_calls_made**: `0`
- **records_fetched**: `326`
- **confirmed_count**: `165`
- **probable_count**: `14`
- **uncertain_count**: `4`
- **snapshot_path**: `data/snapshots/2026-05-25T05-08-25Z__d7c136ab.db`
- **notes**: skipped(no-name-match)=143 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify sherman-bruce

- **entity_slug**: `sherman-bruce`
- **reason**: Tier-B: added Boca Raton city + MAIMI MARLINS typo + Vistakon/PWC negative_signals
- **rows_deleted_donations**: `65`
- **rows_deleted_review_queue**: `131` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T05-13-44Z__pre-reclassify-sherman-bruce.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/sherman-bruce/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `a7911b4c`
- **entity_slug**: `sherman-bruce`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-31`
- **name_variants_queried**: `["Bruce S Sherman", "Bruce S. Sherman", "Bruce Sherman", "Sherman, Bruce", "Sherman, Bruce S", "Sherman, Bruce S."]`
- **api_calls_made**: `0`
- **records_fetched**: `196`
- **confirmed_count**: `57`
- **probable_count**: `19`
- **uncertain_count**: `120`
- **snapshot_path**: `data/snapshots/2026-05-25T05-13-44Z__a7911b4c.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify lerner-mark

- **entity_slug**: `lerner-mark`
- **reason**: Tier-B: added Kensington/Stevensville/Potomac cities + Lerner Corp employer + Chesapeake Partners doppelganger negative_signals
- **rows_deleted_donations**: `75`
- **rows_deleted_review_queue**: `163` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T05-18-21Z__pre-reclassify-lerner-mark.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/lerner-mark/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `20b98f28`
- **entity_slug**: `lerner-mark`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-17`
- **name_variants_queried**: `["Mark D Lerner", "Mark D. Lerner", "Mark Lerner", "Lerner, Mark", "Lerner, Mark D", "Lerner, Mark D."]`
- **api_calls_made**: `0`
- **records_fetched**: `240`
- **confirmed_count**: `68`
- **probable_count**: `10`
- **uncertain_count**: `160`
- **snapshot_path**: `data/snapshots/2026-05-25T05-18-21Z__20b98f28.db`
- **notes**: skipped(no-name-match)=2 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify mcguirk-terry

- **entity_slug**: `mcguirk-terry`
- **reason**: Tier-B: added 30327 Atlanta Buckhead to strong-zip
- **rows_deleted_donations**: `47`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T05-22-40Z__pre-reclassify-mcguirk-terry.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/mcguirk-terry/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `f57b26d0`
- **entity_slug**: `mcguirk-terry`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-05-20`
- **name_variants_queried**: `["Terence F McGuirk", "Terence F. McGuirk", "Terence McGuirk", "Terry McGuirk", "Terry F. McGuirk", "Terry F McGuirk", "McGuirk, Terence", "McGuirk, Terence F", "McGuirk, Terence F.", "McGuirk, Terry"]`
- **api_calls_made**: `0`
- **records_fetched**: `47`
- **confirmed_count**: `43`
- **probable_count**: `4`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-25T05-22-40Z__f57b26d0.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify simpson-bob

- **entity_slug**: `simpson-bob`
- **reason**: Tier-B: added 76102 office ZIP to strong-zip (Fort Worth XTO/TXO HQ); resolves 0 CONFIRMED / 8 PROBABLE anomaly
- **rows_deleted_donations**: `8`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T05-30-56Z__pre-reclassify-simpson-bob.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/simpson-bob/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `e1a2bec9`
- **entity_slug**: `simpson-bob`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2012-09-07`
- **name_variants_queried**: `["Bob R Simpson", "Bob R. Simpson", "Simpson, Bob R", "Simpson, Bob R."]`
- **api_calls_made**: `0`
- **records_fetched**: `8`
- **confirmed_count**: `8`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-25T05-30-56Z__e1a2bec9.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify henry-john

- **entity_slug**: `henry-john`
- **reason**: Tier-B: added 33496 Le Lac strong-zip + CarGurus negative_signal
- **rows_deleted_donations**: `19`
- **rows_deleted_review_queue**: `247` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T05-34-30Z__pre-reclassify-henry-john.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/henry-john/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `93a2a0eb`
- **entity_slug**: `henry-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-31`
- **name_variants_queried**: `["John W. Henry", "John W Henry", "John William Henry", "John William Henry II", "John Henry", "Henry, John W", "Henry, John W.", "Henry, John"]`
- **api_calls_made**: `0`
- **records_fetched**: `1142`
- **confirmed_count**: `15`
- **probable_count**: `3`
- **uncertain_count**: `248`
- **snapshot_path**: `data/snapshots/2026-05-25T05-34-30Z__93a2a0eb.db`
- **notes**: skipped(no-name-match)=876 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify dewitt-bill

- **entity_slug**: `dewitt-bill`
- **reason**: Tier-B batch: added 45243 Indian Hill strong-zip + Reynolds DeWitt typo variants + cincinatti city typo
- **rows_deleted_donations**: `221`
- **rows_deleted_review_queue**: `124` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T05-40-11Z__pre-reclassify-dewitt-bill.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/dewitt-bill/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `598ab7b9`
- **entity_slug**: `dewitt-bill`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-31`
- **name_variants_queried**: `["William O DeWitt Jr", "William O. DeWitt Jr.", "William O. DeWitt Jr", "William DeWitt Jr", "William DeWitt Jr.", "Bill DeWitt Jr", "Bill DeWitt Jr.", "William DeWitt", "Bill DeWitt", "DeWitt, William", "DeWitt, William O", "DeWitt, William O Jr", "DeWitt, Bill"]`
- **api_calls_made**: `0`
- **records_fetched**: `359`
- **confirmed_count**: `235`
- **probable_count**: `6`
- **uncertain_count**: `104`
- **snapshot_path**: `data/snapshots/2026-05-25T05-40-11Z__598ab7b9.db`
- **notes**: skipped(no-name-match)=14 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify malone-john

- **entity_slug**: `malone-john`
- **reason**: Tier-B batch: added 80107 + 80112 to strong_signals.zip_codes (Elizabeth ranch + Englewood Liberty office)
- **rows_deleted_donations**: `47`
- **rows_deleted_review_queue**: `7` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T05-40-12Z__pre-reclassify-malone-john.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/malone-john/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `54b0ee9a`
- **entity_slug**: `malone-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-01`
- **name_variants_queried**: `["John C Malone", "John C. Malone", "John Malone", "John Carl Malone", "Malone, John", "Malone, John C", "Malone, John C."]`
- **api_calls_made**: `0`
- **records_fetched**: `57`
- **confirmed_count**: `48`
- **probable_count**: `0`
- **uncertain_count**: `7`
- **snapshot_path**: `data/snapshots/2026-05-25T05-40-12Z__54b0ee9a.db`
- **notes**: skipped(no-name-match)=2 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify pohlad-joe

- **entity_slug**: `pohlad-joe`
- **reason**: Tier-B batch: added 55436+55424 Edina strong-zips + Mail Holdings employer + edna city typo
- **rows_deleted_donations**: `106`
- **rows_deleted_review_queue**: `1` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T05-40-12Z__pre-reclassify-pohlad-joe.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/pohlad-joe/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `9531c521`
- **entity_slug**: `pohlad-joe`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-02-21`
- **name_variants_queried**: `["Joseph C Pohlad", "Joseph C. Pohlad", "Joseph Pohlad", "Joe Pohlad", "Pohlad, Joseph", "Pohlad, Joseph C", "Pohlad, Joseph C.", "Pohlad, Joe"]`
- **api_calls_made**: `0`
- **records_fetched**: `171`
- **confirmed_count**: `107`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-25T05-40-12Z__9531c521.db`
- **notes**: skipped(no-name-match)=64 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify attanasio-mark

- **entity_slug**: `attanasio-mark`
- **reason**: Tier-C calibration
- **rows_deleted_donations**: `30`
- **rows_deleted_review_queue**: `28` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T05-51-33Z__pre-reclassify-attanasio-mark.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/attanasio-mark/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `c5743e13`
- **entity_slug**: `attanasio-mark`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-31`
- **name_variants_queried**: `["Mark Attanasio", "Mark L Attanasio", "Mark L. Attanasio", "Attanasio, Mark", "Attanasio, Mark L", "Attanasio, Mark L."]`
- **api_calls_made**: `0`
- **records_fetched**: `58`
- **confirmed_count**: `28`
- **probable_count**: `2`
- **uncertain_count**: `28`
- **snapshot_path**: `data/snapshots/2026-05-25T05-51-33Z__c5743e13.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify dolan-paul

- **entity_slug**: `dolan-paul`
- **reason**: Tier-C calibration
- **rows_deleted_donations**: `67`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T05-51-33Z__pre-reclassify-dolan-paul.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/dolan-paul/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `925776a3`
- **entity_slug**: `dolan-paul`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-05-19`
- **name_variants_queried**: `["Paul J Dolan", "Paul J. Dolan", "Paul Dolan", "Paul Joseph Dolan", "Dolan, Paul", "Dolan, Paul J", "Dolan, Paul J."]`
- **api_calls_made**: `0`
- **records_fetched**: `68`
- **confirmed_count**: `67`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-25T05-51-33Z__925776a3.db`
- **notes**: skipped(no-name-match)=1 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify johnson-greg

- **entity_slug**: `johnson-greg`
- **reason**: Tier-C calibration
- **rows_deleted_donations**: `107`
- **rows_deleted_review_queue**: `2328` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T05-51-34Z__pre-reclassify-johnson-greg.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/johnson-greg/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `01d82f45`
- **entity_slug**: `johnson-greg`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-04-17`
- **name_variants_queried**: `["Gregory E Johnson", "Gregory E. Johnson", "Gregory Eugene Johnson", "Greg E Johnson", "Greg E. Johnson", "Greg Johnson", "Johnson, Gregory", "Johnson, Gregory E", "Johnson, Gregory E.", "Johnson, Greg"]`
- **api_calls_made**: `0`
- **records_fetched**: `2497`
- **confirmed_count**: `104`
- **probable_count**: `3`
- **uncertain_count**: `2328`
- **snapshot_path**: `data/snapshots/2026-05-25T05-51-34Z__01d82f45.db`
- **notes**: skipped(no-name-match)=62 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify castellini-phil

- **entity_slug**: `castellini-phil`
- **reason**: Tier-C calibration
- **rows_deleted_donations**: `47`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-25T05-51-34Z__pre-reclassify-castellini-phil.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/castellini-phil/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-25 — INGESTION

- **run_id**: `4dee96e5`
- **entity_slug**: `castellini-phil`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2020-10-20`
- **name_variants_queried**: `["Phillip J Castellini", "Phillip J. Castellini", "Phillip Castellini", "Phil Castellini", "Phil J. Castellini", "Phil J Castellini", "Castellini, Phillip", "Castellini, Phillip J", "Castellini, Phillip J.", "Castellini, Phil"]`
- **api_calls_made**: `0`
- **records_fetched**: `49`
- **confirmed_count**: `47`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-25T05-51-34Z__4dee96e5.db`
- **notes**: skipped(no-name-match)=2 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — NOTE — raw-payload integrity event (GOVERNANCE.md §1.4)

While triaging broken donation-card links on the dashboard, discovered that **755 of 3,618 CONFIRMED+PROBABLE donations** have a `raw_payload_path` pointing at an on-disk JSON file that does not contain their `transaction_id`. The transactions are correctly recorded in `data/master.db` — but the raw FEC payloads that produced those rows have been silently overwritten and are gone from `data/raw/`.

- **discovered_at**: `2026-05-25` (after the 2026-05-23 manual refresh)
- **affected_donations**: `755` of `3618` (~21%)
- **distinct_filings_affected**: `346`
- **affected_with_filing_id**: `520`
- **affected_with_null_filing_id**: `235`
- **distinct_owners_affected**: `28`
- **worst_owners** (by count of orphaned txns):
  - `kendrick-ken`: 155
  - `reinsdorf-jerry`: 133
  - `castellini-bob`: 69
  - `dewitt-bill`: 63
  - `cohen-steven`: 50
  - `stanton-john`: 47
  - `monfort-dick`: 45
  - `johnson-greg`: 27

#### Root cause

`scripts/fetch_fec.py:_utc_now_filename` produced raw-payload filenames with **second-level resolution** (`%Y-%m-%dT%H-%M-%SZ`). When two `_persist_raw` calls happened within the same second — common when FEC returned warm cache hits at the start of a paginate session, or when two name variants ran back-to-back — the second `write_text()` silently clobbered the first file. The DB rows extracted from the first page had already been stamped with that filename and live on as forensic references to a file that holds different content.

This is a **GOVERNANCE.md §1.4 violation** (raw payloads must be preserved forever; the project must be reconstructible from `data/raw/` alone). For these 755 transactions, reconstruction from raw is no longer possible without a re-fetch.

#### Mitigation applied

1. **Prospective fix.** `_utc_now_filename` now uses **microsecond resolution** (`%Y-%m-%dT%H-%M-%S-%fZ`). Same-second collisions are no longer possible. Verified with a 2,000-call uniqueness test.
2. **Indexer hardening.** `mockup/build_data.py:load_raw_payload_index` now walks every JSON file under each owner's `data/raw/<slug>/` directory rather than only the donations' stamped paths. For any future near-miss where a transaction is present in a sibling payload, this surfaces it; for the existing 755, a full directory scan confirmed that the data is **not** present anywhere on disk (recovery from sibling payloads = 0).
3. **Filing PDF URL fix (unrelated but co-shipped).** `mockup/build_data.py:filing_pdf_url` switched from the gated `docquery.fec.gov/pdf/<shard>/<filing_id>/<filing_id>.pdf` pattern (HTTP 403 across all filings) to the canonical `fecfile.fec.gov/pdf/<filing_id>.pdf` (HTTP 200 verified).

#### Recovery plan (not done in this entry)

The 755 transactions need their raw payloads re-fetched from FEC to restore §1.4 compliance. Sketch:
1. For each affected owner (28 of them), identify the affected `two_year_transaction_period` cycles by looking up the cycle of each orphan txn.
2. Run `python -m scripts.cli ingest <slug> --full-refetch --chunk-by-cycle` for those owners, restricted to those cycles, against the now-microsecond-resolution filename code.
3. The new raw payloads will be written without collision; the widened indexer will pick them up automatically; re-running `python mockup/build_data.py` will resolve their `pdf_url` from the new files.
4. Append a follow-up NOTE entry recording the recovered count.

This recovery is out of scope for the current PR (it requires hitting the FEC API at scale; should run after the matrix workflow is validated).

### 2026-05-25 — NOTE — refresh workflow restructured to GHA matrix

The previous weekly refresh ran 35 active owners sequentially in a single GHA job. The 2026-05-23 manual run hit the GitHub-hosted runner's 6h cap mid-iteration and was cancelled, leaving some owners unprocessed. Restructured the workflow:

- `.github/workflows/refresh.yml` now fans out into **4 parallel matrix jobs**, each handling ~9 owners selected by `scripts/refresh.py:select_bucket`. Bucketing is balanced by raw-payload weight so the four heaviest owners (kendrick-ken / cohen-steven / johnson-greg / sherman-john) land in different buckets.
- Per-job `timeout-minutes: 330` (5.5h) so a runaway fails fast with logs instead of being silently killed at exactly 6h.
- A new `consolidate` job runs after the matrix: downloads each bucket's `master.db` + YAML + log artifacts, runs `scripts/merge_buckets.py` (per-slug DB row replace from the disjoint bucket DBs) and `scripts/finalize_matrix.py` (YAML adoption + append-only log concat against the pre-refresh snapshot), rebuilds `mockup/data.json` once, and commits.
- `scripts/fetch_fec.py:MIN_REQUEST_INTERVAL_S` bumped from 1.2 → 4.0 so 4 parallel workers sharing one FEC API key stay under the 1,000 req/hour cap.
- New CLI flag `--bucket N/M` in `scripts/cli.py refresh` is the workflow's entry point; mutually exclusive with `--only`. When `--bucket` is passed, `mockup/data.json` regeneration is skipped — the consolidate job does it once after merge.

### 2026-05-25 — NOTE — correction to earlier Bug A fix (filing link)

The earlier Bug A fix changed `filing_pdf_url` to `https://fecfile.fec.gov/pdf/<file_number>.pdf`. Browser testing confirmed the URL **redirects to the FECfile+ login wall** — my pre-commit verification used `curl -I`, which returned 200 because the SPA shell at `fecfile.fec.gov` answers any path; the JS in the body then routes unauthenticated visitors to `/login`. The verification was inadequate.

#### What's actually true

- `https://fecfile.fec.gov/pdf/<id>.pdf` is the FECfile+ filer-portal SPA, not a public PDF host.
- The canonical public PDF lives at `https://docquery.fec.gov/pdf/<last_3_digits_of_image_number>/<image_number>/<image_number>.pdf` — shard and path are keyed on the **filing's `image_number`** (18 digits), not the `file_number` (7 digits) we store. Verified one returns a real `application/pdf`.
- The filing's `image_number` is not in our DB; it's returned by FEC's `/v1/filings/?file_number=<id>` endpoint. Populating it for all 2,218 distinct `filing_id`s in the archive is a follow-up enrichment job (~2.5h at the new 4s throttle).

#### Correction applied

- `mockup/build_data.py:filing_pdf_url` renamed → `filing_page_url`, now returns `https://www.fec.gov/data/filings/?file_number=<id>`. That's a public HTML page on FEC.gov with the filing's record and a link to FEC's own PDF when available. Verified 200 across 5 filings.
- Field on each donation in `mockup/data.json` renamed `filing_pdf_url` → `filing_page_url`.
- `mockup/index.html` drawer-source label changed from "Full filing PDF" → "Filing on FEC.gov"; subtitle changed from "All transactions in filing X" → "Full filing record · #X".

#### Follow-up

Direct-PDF support is queued behind a data-enrichment task: extend the schema with a per-filing `image_number` (or a new `filings` table), backfill via `/v1/filings/`, and switch the URL builder to the docquery `/pdf/<shard>/...` pattern. Will be its own PR.

### 2026-05-25 — SCHEMA_MIGRATION — v1 → v2 (committees + committee_totals)

Added two tables to back the recipient-page Identity + Scale enrichment. See `CHARTER.md` Phase 1 scope addition for what these surfaces are for, and the design constraints (FEC primary-source, never editorial — GOVERNANCE.md §1.4 / §3 / §6).

- **committees** — one row per FEC committee_id that received an attributed donation. Identity fields: designation, committee_type, party, organization_type, affiliated_committee_name, treasurer, address, filing dates, termination flag. Optional `external_link*` columns for hand-curated Wikipedia/Ballotpedia pointers (catalog/committee_external_links.yaml). Indices on `party` and `committee_type`.
- **committee_totals** — composite PK `(committee_id, cycle)`. Per-cycle scale: receipts, disbursements, cash_on_hand_end_period, individual_contributions, other_political_committee_contributions, independent_expenditures, coverage start/end dates. Indexed on `cycle`.
- **schema_version** — new row at v2.

The migration is idempotent (`CREATE TABLE IF NOT EXISTS`). `scripts.db.init` records the v2 row only when a v1 DB is upgraded. New ingest code:

- `scripts/fetch_committees.py` — wraps `FECClient` for `/committee/<id>/` and `/committee/<id>/totals/`. Persists raw payloads to `data/raw/_committees/<id>/<UTC>__<endpoint>.json` (underscored dir to distinguish from per-owner slugs).
- `scripts/ingest_committees.py` — orchestrator with 30-day freshness gate, snapshots master.db before first row write (§1.6), preserves curated `external_link*` columns across FEC re-fetches.
- `scripts/apply_committee_external_links.py` — reads `catalog/committee_external_links.yaml` and writes the curated columns. Re-runnable.
- CLI: `python -m scripts.cli ingest-committees [--only ID,ID --force-refresh --max N]`, `python -m scripts.cli apply-committee-external-links`.

Workflow integration: `.github/workflows/refresh.yml` grows a new `committees_refresh` job after `consolidate` (90-min timeout). Steady-state ~30 min/week — only committees outside the 30-day freshness window are re-fetched.

The first full backfill across all ~925 distinct recipient committees runs after this PR merges (see follow-up entry).

### 2026-05-25 — NOTE — matrix workflow robustness fix

While validating the Phase 1 committee enrichment via a `workflow_dispatch` bucket-0 dry run (run #26413887828), discovered that a single FEC `Read timed out` failure in `refresh_all` would: cause `scripts/cli.py refresh` to exit 1 → cause the GHA step to fail → skip the artifact upload → block the consolidate job → throw away the 7 (of 9) successfully-fetched owners' work.

Per GOVERNANCE.md §1.9, per-owner failures are isolated and already surface in the run summary JSON. Treating them as catastrophic in the workflow contradicts that design.

Patch in this PR's `.github/workflows/refresh.yml`:
- `Run refresh bucket` step now has `continue-on-error: true` — the bucket job's overall conclusion isn't dragged down by a per-owner timeout.
- `Upload bucket artifact` step now has `if: always() && steps.gate.outputs.run == 'true'` — uploads whatever progress the bucket made, even after a partial failure.

The consolidate job's existing `needs: refresh` semantics keep working because continue-on-error masks the step failure at the job level. The summary JSON still records the failed owners; user-visible behavior of `cli refresh` (exit 1 on partial failure) is unchanged.

This fix is part of the same Phase 1 commit; it would otherwise have caused next Monday's cron to lose work on any FEC timeout.

### 2026-05-25 — SETUP — committee enrichment first backfill

Initial local backfill across every recipient committee in the donations table.

- **started_at**: `2026-05-25T20:08:46Z`
- **completed_at**: `2026-05-25T23:57:50Z`
- **wall_clock**: ~3h49m (first pass) + 45s (re-run of 6 failures after fix)
- **committees attempted**: `925`
- **fetched**: `915` first pass + `6` re-run = `921` total fresh fetches
- **skipped (already fresh from earlier smoke tests)**: `4` first pass + `0` re-run
- **first-pass failures**: `6` — all six were `UNIQUE constraint failed: committee_totals.committee_id, committee_totals.cycle` on candidate committees where FEC's `/totals/` returns one row per election round per cycle (primary + general). Fixed by switching the INSERT to `INSERT OR REPLACE` keyed on `(committee_id, cycle)`. Last row per cycle survives. Re-ran the 6 affected committees clean. Regression test added in `tests/test_ingest_committees.py::TestIngestCommittee::test_duplicate_cycle_rows_dont_blow_up`.
- **committee_totals rows written**: `5,660`
- **snapshots**: `data/snapshots/2026-05-25T20-08-46Z__committees_ingest_2026-05-25T20-08-46Z.db` (pre-backfill) and `data/snapshots/2026-05-25T23-57-05Z__committees_ingest_2026-05-25T23-57-05Z.db` (pre-recovery).
- **raw payloads**: persisted under `data/raw/_committees/<committee_id>/` (gitignored per usual). Project remains rebuildable from raw per GOVERNANCE.md §1.4.

Resulting `mockup/data.json`: 925/925 recipients enriched, 918 committee_scale blocks (7 committees have no FEC-reported financial activity, e.g. nonfederal-only accounts).

### 2026-05-26 — INGESTION

- **run_id**: `e6d9cbed`
- **entity_slug**: `angelos-john-p`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-10`
- **name_variants_queried**: `["John P Angelos", "John P. Angelos", "John Angelos", "Angelos, John", "Angelos, John P", "Angelos, John P."]`
- **api_calls_made**: `8`
- **records_fetched**: `86`
- **confirmed_count**: `51`
- **probable_count**: `1`
- **uncertain_count**: `8`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T00-31-31Z__e6d9cbed.db`
- **notes**: skipped(no-name-match)=26 · min_date=default (no prior ingestion) · states=['TN', 'MD']

### 2026-05-26 — INGESTION

- **run_id**: `1df121bf`
- **entity_slug**: `cohen-steven`
- **dry_run**: `0`
- **period_start**: `2026-05-22`
- **period_end**: `None`
- **name_variants_queried**: `["Steven A Cohen", "Steven A. Cohen", "Steven Cohen", "Steve Cohen", "Cohen, Steven", "Cohen, Steven A", "Cohen, Steven A."]`
- **api_calls_made**: `5`
- **records_fetched**: `0`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T00-38-51Z__1df121bf.db`
- **notes**: skipped(no-name-match)=0 · min_date=audit.last_ingestion · states=['CT', 'NY']

### 2026-05-26 — INGESTION

- **run_id**: `ff096481`
- **entity_slug**: `dolan-paul`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-05-19`
- **name_variants_queried**: `["Paul J Dolan", "Paul J. Dolan", "Paul Dolan", "Paul Joseph Dolan", "Dolan, Paul", "Dolan, Paul J", "Dolan, Paul J."]`
- **api_calls_made**: `9`
- **records_fetched**: `68`
- **confirmed_count**: `67`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T00-43-09Z__ff096481.db`
- **notes**: skipped(no-name-match)=1 · min_date=default (no prior ingestion) · states=['OH']

### 2026-05-26 — INGESTION

- **run_id**: `8086632a`
- **entity_slug**: `ilitch-chris`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-26`
- **name_variants_queried**: `["Christopher P Ilitch", "Christopher P. Ilitch", "Christopher Ilitch", "Chris Ilitch", "Christopher Paul Ilitch", "Ilitch, Christopher", "Ilitch, Christopher P", "Ilitch, Christopher P.", "Ilitch, Chris"]`
- **api_calls_made**: `11`
- **records_fetched**: `30`
- **confirmed_count**: `12`
- **probable_count**: `0`
- **uncertain_count**: `18`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T00-50-55Z__8086632a.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · states=['MI']

### 2026-05-26 — INGESTION

- **run_id**: `21042dc4`
- **entity_slug**: `moreno-arte`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-06-03`
- **name_variants_queried**: `["Arturo Moreno", "Arte Moreno", "Arturo R. Moreno", "Arturo R Moreno", "Moreno, Arturo", "Moreno, Arte", "Moreno, Arturo R"]`
- **api_calls_made**: `12`
- **records_fetched**: `79`
- **confirmed_count**: `73`
- **probable_count**: `0`
- **uncertain_count**: `6`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T01-11-34Z__21042dc4.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · states=['AZ', 'CA']

### 2026-05-26 — INGESTION

- **run_id**: `c27e51e2`
- **entity_slug**: `reinsdorf-jerry`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-02-04`
- **name_variants_queried**: `["Jerry Reinsdorf", "Jerry M Reinsdorf", "Jerry M. Reinsdorf", "Jerry Michael Reinsdorf", "Reinsdorf, Jerry", "Reinsdorf, Jerry M", "Reinsdorf, Jerry M."]`
- **api_calls_made**: `19`
- **records_fetched**: `425`
- **confirmed_count**: `422`
- **probable_count**: `2`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T01-22-55Z__c27e51e2.db`
- **notes**: skipped(no-name-match)=1 · min_date=default (no prior ingestion) · states=['IL']

### 2026-05-26 — INGESTION

- **run_id**: `f4ce419d`
- **entity_slug**: `steinbrenner-hal`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-22`
- **name_variants_queried**: `["Harold Z Steinbrenner", "Harold Z. Steinbrenner", "Harold Steinbrenner", "Hal Steinbrenner", "Steinbrenner, Harold", "Steinbrenner, Harold Z", "Steinbrenner, Harold Z.", "Steinbrenner, Hal"]`
- **api_calls_made**: `10`
- **records_fetched**: `15`
- **confirmed_count**: `13`
- **probable_count**: `2`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T02-08-43Z__f4ce419d.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · states=['FL']

### 2026-05-26 — REFRESH RUN 0f3684f4

- **started_at**: `2026-05-26T00:31:30Z`
- **completed_at**: `2026-05-26T02:09:39Z`
- **dry_run**: `0`
- **owners_attempted**: `9`
- **owners_succeeded**: `7`
- **owners_failed**: `2`
- **total_records_fetched**: `703`
- **data_json_regenerated**: `False`
- **failed_owners**: `['malone-john', 'sherman-bruce']`

### 2026-05-26 — INGESTION

- **run_id**: `e83b975b`
- **entity_slug**: `attanasio-mark`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-31`
- **name_variants_queried**: `["Mark Attanasio", "Mark L Attanasio", "Mark L. Attanasio", "Attanasio, Mark", "Attanasio, Mark L", "Attanasio, Mark L."]`
- **api_calls_made**: `8`
- **records_fetched**: `58`
- **confirmed_count**: `28`
- **probable_count**: `2`
- **uncertain_count**: `28`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T00-31-35Z__e83b975b.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · states=['CA', 'WI']

### 2026-05-26 — INGESTION

- **run_id**: `32b0e450`
- **entity_slug**: `crane-jim`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-09-26`
- **name_variants_queried**: `["James R Crane", "James R. Crane", "James Crane", "Jim Crane", "Crane, James", "Crane, James R", "Crane, James R."]`
- **api_calls_made**: `10`
- **records_fetched**: `51`
- **confirmed_count**: `34`
- **probable_count**: `2`
- **uncertain_count**: `14`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T00-38-23Z__32b0e450.db`
- **notes**: skipped(no-name-match)=1 · min_date=default (no prior ingestion) · states=['TX']

### 2026-05-26 — INGESTION

- **run_id**: `914a5241`
- **entity_slug**: `feliciano-jose`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-22`
- **name_variants_queried**: `["Jose E Feliciano", "Jose E. Feliciano", "Jose Feliciano", "Jos\u00e9 E. Feliciano", "Feliciano, Jose", "Feliciano, Jose E", "Feliciano, Jose E."]`
- **api_calls_made**: `10`
- **records_fetched**: `63`
- **confirmed_count**: `23`
- **probable_count**: `1`
- **uncertain_count**: `15`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T00-42-31Z__914a5241.db`
- **notes**: skipped(no-name-match)=24 · min_date=default (no prior ingestion) · states=['CA']

### 2026-05-26 — INGESTION

- **run_id**: `25bb4069`
- **entity_slug**: `johnson-greg`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-04-17`
- **name_variants_queried**: `["Gregory E Johnson", "Gregory E. Johnson", "Gregory Eugene Johnson", "Greg E Johnson", "Greg E. Johnson", "Greg Johnson", "Johnson, Gregory", "Johnson, Gregory E", "Johnson, Gregory E.", "Johnson, Greg"]`
- **api_calls_made**: `120`
- **records_fetched**: `2497`
- **confirmed_count**: `104`
- **probable_count**: `3`
- **uncertain_count**: `2328`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T00-46-28Z__25bb4069.db`
- **notes**: skipped(no-name-match)=62 · min_date=default (no prior ingestion) · states=['CA']

### 2026-05-26 — INGESTION

- **run_id**: `326e664a`
- **entity_slug**: `mcguirk-terry`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-05-20`
- **name_variants_queried**: `["Terence F McGuirk", "Terence F. McGuirk", "Terence McGuirk", "Terry McGuirk", "Terry F. McGuirk", "Terry F McGuirk", "McGuirk, Terence", "McGuirk, Terence F", "McGuirk, Terence F.", "McGuirk, Terry"]`
- **api_calls_made**: `13`
- **records_fetched**: `47`
- **confirmed_count**: `43`
- **probable_count**: `4`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T01-13-03Z__326e664a.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · states=['GA']

### 2026-05-26 — INGESTION

- **run_id**: `50d5dbfd`
- **entity_slug**: `nutting-bob`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-08-12`
- **name_variants_queried**: `["Robert Nutting", "Bob Nutting", "Nutting, Robert", "Nutting, Bob"]`
- **api_calls_made**: `6`
- **records_fetched**: `24`
- **confirmed_count**: `23`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T01-16-07Z__50d5dbfd.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · states=['WV', 'PA']

### 2026-05-26 — INGESTION

- **run_id**: `6a12e0f3`
- **entity_slug**: `ricketts-tom`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-25`
- **name_variants_queried**: `["Tom Ricketts", "Thomas Ricketts", "Thomas S Ricketts", "Thomas S. Ricketts", "Thomas Stuart Ricketts", "Ricketts, Tom", "Ricketts, Thomas", "Ricketts, Thomas S", "Ricketts, Thomas S."]`
- **api_calls_made**: `13`
- **records_fetched**: `63`
- **confirmed_count**: `61`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T01-16-40Z__6a12e0f3.db`
- **notes**: skipped(no-name-match)=1 · min_date=default (no prior ingestion) · states=['IL']

### 2026-05-26 — INGESTION

- **run_id**: `c25830ba`
- **entity_slug**: `sherman-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-01-15`
- **name_variants_queried**: `["John J Sherman", "John J. Sherman", "John Sherman", "Sherman, John", "Sherman, John J", "Sherman, John J."]`
- **api_calls_made**: `28`
- **records_fetched**: `526`
- **confirmed_count**: `34`
- **probable_count**: `6`
- **uncertain_count**: `305`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T01-30-16Z__c25830ba.db`
- **notes**: skipped(no-name-match)=181 · min_date=default (no prior ingestion) · states=['MO', 'KS', 'FL']

### 2026-05-26 — INGESTION

- **run_id**: `2e9d227f`
- **entity_slug**: `walter-mark`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-07-05`
- **name_variants_queried**: `["Mark Walter", "Mark R. Walter", "Mark R Walter", "Mark Richard Walter", "Walter, Mark", "Walter, Mark R", "Walter, Mark R."]`
- **api_calls_made**: `19`
- **records_fetched**: `579`
- **confirmed_count**: `20`
- **probable_count**: `0`
- **uncertain_count**: `41`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T01-58-28Z__2e9d227f.db`
- **notes**: skipped(no-name-match)=518 · min_date=default (no prior ingestion) · states=['IL']

### 2026-05-26 — REFRESH RUN 797721c7

- **started_at**: `2026-05-26T00:31:34Z`
- **completed_at**: `2026-05-26T02:18:24Z`
- **dry_run**: `0`
- **owners_attempted**: `9`
- **owners_succeeded**: `9`
- **owners_failed**: `0`
- **total_records_fetched**: `3908`
- **data_json_regenerated**: `False`

### 2026-05-26 — INGESTION

- **run_id**: `c172dbfb`
- **entity_slug**: `castellini-bob`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-31`
- **name_variants_queried**: `["Robert H Castellini", "Robert H. Castellini", "Robert Castellini", "Bob Castellini", "Castellini, Robert", "Castellini, Robert H", "Castellini, Robert H.", "Castellini, Bob"]`
- **api_calls_made**: `24`
- **records_fetched**: `461`
- **confirmed_count**: `405`
- **probable_count**: `34`
- **uncertain_count**: `14`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T00-31-29Z__c172dbfb.db`
- **notes**: skipped(no-name-match)=8 · min_date=default (no prior ingestion) · states=['OH']

### 2026-05-26 — INGESTION

- **run_id**: `d1fb6e0a`
- **entity_slug**: `fisher-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-30`
- **name_variants_queried**: `["John Fisher", "John J. Fisher", "John J Fisher", "John Joseph Fisher", "Fisher, John", "Fisher, John J", "Fisher, John J."]`
- **api_calls_made**: `245`
- **records_fetched**: `3921`
- **confirmed_count**: `569`
- **probable_count**: `99`
- **uncertain_count**: `3166`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T01-06-34Z__d1fb6e0a.db`
- **notes**: skipped(no-name-match)=87 · min_date=default (no prior ingestion) · states=['CA']

### 2026-05-26 — INGESTION

- **run_id**: `8844e7b8`
- **entity_slug**: `kendrick-ken`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-31`
- **name_variants_queried**: `["Ken Kendrick", "Earl G. Kendrick", "Earl G Kendrick", "Earl G. Kendrick Jr.", "Earl G. Kendrick, Jr.", "Earl Kendrick", "E. G. Kendrick", "E.G. Kendrick", "E G Kendrick", "Earl Gentry Kendrick", "Kendrick, Ken", "Kendrick, Earl G", "Kendrick, Earl G.", "Kendrick, Earl G., Jr."]`
- **api_calls_made**: `136`
- **records_fetched**: `3473`
- **confirmed_count**: `639`
- **probable_count**: `60`
- **uncertain_count**: `9`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T01-57-19Z__8844e7b8.db`
- **notes**: skipped(no-name-match)=2765 · min_date=default (no prior ingestion) · states=['AZ']

### 2026-05-26 — INGESTION

- **run_id**: `42c84da4`
- **entity_slug**: `rubenstein-david`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-01-09`
- **name_variants_queried**: `["David Rubenstein", "David M. Rubenstein", "David M Rubenstein", "Rubenstein, David", "Rubenstein, David M", "Rubenstein, David M."]`
- **api_calls_made**: `8`
- **records_fetched**: `31`
- **confirmed_count**: `2`
- **probable_count**: `5`
- **uncertain_count**: `24`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T03-10-06Z__42c84da4.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · states=['MD', 'DC', 'MA']

### 2026-05-26 — INGESTION

- **run_id**: `4ff6799b`
- **entity_slug**: `simpson-bob`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2012-09-07`
- **name_variants_queried**: `["Bob R Simpson", "Bob R. Simpson", "Simpson, Bob R", "Simpson, Bob R."]`
- **api_calls_made**: `4`
- **records_fetched**: `8`
- **confirmed_count**: `8`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T03-30-48Z__4ff6799b.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · states=['TX']

### 2026-05-26 — INGESTION

- **run_id**: `f8e14ac2`
- **entity_slug**: `zalupski-patrick`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-22`
- **name_variants_queried**: `["Patrick O Zalupski", "Patrick O. Zalupski", "Patrick Zalupski", "Zalupski, Patrick", "Zalupski, Patrick O", "Zalupski, Patrick O."]`
- **api_calls_made**: `6`
- **records_fetched**: `20`
- **confirmed_count**: `15`
- **probable_count**: `0`
- **uncertain_count**: `5`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T03-36-22Z__f8e14ac2.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · states=['FL']

### 2026-05-26 — REFRESH RUN e2147cca

- **started_at**: `2026-05-26T00:31:29Z`
- **completed_at**: `2026-05-26T03:38:27Z`
- **dry_run**: `0`
- **owners_attempted**: `9`
- **owners_succeeded**: `6`
- **owners_failed**: `3`
- **total_records_fetched**: `7914`
- **data_json_regenerated**: `False`
- **failed_owners**: `['davis-ray', 'middleton-john', 'pohlad-joe']`

### 2026-05-26 — INGESTION

- **run_id**: `f7155a9a`
- **entity_slug**: `castellini-phil`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2020-10-20`
- **name_variants_queried**: `["Phillip J Castellini", "Phillip J. Castellini", "Phillip Castellini", "Phil Castellini", "Phil J. Castellini", "Phil J Castellini", "Castellini, Phillip", "Castellini, Phillip J", "Castellini, Phillip J.", "Castellini, Phil"]`
- **api_calls_made**: `14`
- **records_fetched**: `49`
- **confirmed_count**: `47`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T00-31-39Z__f7155a9a.db`
- **notes**: skipped(no-name-match)=2 · min_date=default (no prior ingestion) · states=['OH', 'KY']

### 2026-05-26 — INGESTION

- **run_id**: `4b0e8b32`
- **entity_slug**: `dewitt-bill`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-31`
- **name_variants_queried**: `["William O DeWitt Jr", "William O. DeWitt Jr.", "William O. DeWitt Jr", "William DeWitt Jr", "William DeWitt Jr.", "Bill DeWitt Jr", "Bill DeWitt Jr.", "William DeWitt", "Bill DeWitt", "DeWitt, William", "DeWitt, William O", "DeWitt, William O Jr", "DeWitt, Bill"]`
- **api_calls_made**: `28`
- **records_fetched**: `359`
- **confirmed_count**: `235`
- **probable_count**: `6`
- **uncertain_count**: `104`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T00-43-08Z__4b0e8b32.db`
- **notes**: skipped(no-name-match)=14 · min_date=default (no prior ingestion) · states=['OH', 'MO']

### 2026-05-26 — INGESTION

- **run_id**: `40fb71d2`
- **entity_slug**: `henry-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-31`
- **name_variants_queried**: `["John W. Henry", "John W Henry", "John William Henry", "John William Henry II", "John Henry", "Henry, John W", "Henry, John W.", "Henry, John"]`
- **api_calls_made**: `32`
- **records_fetched**: `1142`
- **confirmed_count**: `15`
- **probable_count**: `3`
- **uncertain_count**: `248`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T00-48-12Z__40fb71d2.db`
- **notes**: skipped(no-name-match)=876 · min_date=default (no prior ingestion) · states=['FL', 'MA']

### 2026-05-26 — INGESTION

- **run_id**: `ded077d0`
- **entity_slug**: `lerner-mark`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-17`
- **name_variants_queried**: `["Mark D Lerner", "Mark D. Lerner", "Mark Lerner", "Lerner, Mark", "Lerner, Mark D", "Lerner, Mark D."]`
- **api_calls_made**: `14`
- **records_fetched**: `240`
- **confirmed_count**: `68`
- **probable_count**: `10`
- **uncertain_count**: `160`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T01-14-22Z__ded077d0.db`
- **notes**: skipped(no-name-match)=2 · min_date=default (no prior ingestion) · states=['MD']

### 2026-05-26 — INGESTION

- **run_id**: `7ad972e1`
- **entity_slug**: `monfort-dick`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-06-03`
- **name_variants_queried**: `["Richard L Monfort", "Richard L. Monfort", "Richard Monfort", "Dick Monfort", "Monfort, Richard", "Monfort, Richard L", "Monfort, Richard L.", "Monfort, Dick"]`
- **api_calls_made**: `12`
- **records_fetched**: `106`
- **confirmed_count**: `101`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T01-26-53Z__7ad972e1.db`
- **notes**: skipped(no-name-match)=4 · min_date=default (no prior ingestion) · states=['CO']

### 2026-05-26 — INGESTION

- **run_id**: `59aa2c5a`
- **entity_slug**: `pohlad-tom`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2024-09-24`
- **name_variants_queried**: `["Thomas Pohlad", "Tom Pohlad", "Pohlad, Thomas", "Pohlad, Tom"]`
- **api_calls_made**: `10`
- **records_fetched**: `152`
- **confirmed_count**: `118`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T01-38-33Z__59aa2c5a.db`
- **notes**: skipped(no-name-match)=33 · min_date=default (no prior ingestion) · states=['MN']

### 2026-05-26 — INGESTION

- **run_id**: `c76fff27`
- **entity_slug**: `seidler-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-05-11`
- **name_variants_queried**: `["John Seidler", "Seidler, John"]`
- **api_calls_made**: `4`
- **records_fetched**: `1`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T01-39-20Z__c76fff27.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · states=['CA']

### 2026-05-26 — INGESTION

- **run_id**: `ade6714f`
- **entity_slug**: `stanton-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-08-12`
- **name_variants_queried**: `["John W Stanton", "John W. Stanton", "John Stanton", "Stanton, John", "Stanton, John W", "Stanton, John W."]`
- **api_calls_made**: `14`
- **records_fetched**: `326`
- **confirmed_count**: `165`
- **probable_count**: `14`
- **uncertain_count**: `4`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-26T01-39-53Z__ade6714f.db`
- **notes**: skipped(no-name-match)=143 · min_date=default (no prior ingestion) · states=['WA']

### 2026-05-26 — REFRESH RUN 69c9da0f

- **started_at**: `2026-05-26T00:31:38Z`
- **completed_at**: `2026-05-26T01:46:40Z`
- **dry_run**: `0`
- **owners_attempted**: `8`
- **owners_succeeded**: `8`
- **owners_failed**: `0`
- **total_records_fetched**: `2375`
- **data_json_regenerated**: `False`

### 2026-05-26 — SCHEMA_MIGRATION — v2 → v3 (donations gets 6 per-transaction columns)

Lifted six FEC per-transaction fields off the raw-payload lookup at build_data.py time and onto the `donations` row itself: `image_number`, `pdf_url`, `filing_form`, `line_number`, `receipt_type_full`, `recipient_committee_type`.

#### Why

Yesterday's matrix-validation refresh exposed a structural flaw: `data/raw/` is gitignored and not included in the bucket artifacts (size-prohibitive), so any donation ingested by a GHA matrix bucket has a `raw_payload_path` pointing at a file that lived only on the now-destroyed runner. The dashboard's donation card was reading per-transaction FEC fields via `mockup/build_data.py:load_raw_payload_index`, which walks `data/raw/<slug>/*.json` *locally* — and locally, those files no longer existed. Result: image-link coverage dropped from 79% (3618 donations baseline) → 69% (4158 donations, with the 540 new ones all NULL because their payloads were ephemeral).

#### What changed

- `scripts/db.py`: `SCHEMA_VERSION = 3`. `init()` runs `ALTER TABLE donations ADD COLUMN` gated by `PRAGMA table_info` (same pattern as the existing `family_tenure_start_date` migration on `entities`).
- `scripts/ingest.py:_record_to_donation_row` extracts the six fields from each FEC record and includes them in the insert dict. New helper `_committee_type_of` resolves the recipient committee type with the prefer-top-level / fall-back-to-nested precedence.
- `scripts/db.insert_donation` accepts the new columns; tolerant of legacy callers (defaults to NULL).
- `mockup/build_data.py`: per-donation lookup is DB-first; the raw-payload index only loads for rows where `image_number IS NULL`. Legacy fallback path stays as a safety net for any pre-v3 row whose raw payload happens to still be on disk.
- `scripts/backfill_donation_image_fields.py` + `cli backfill-donation-image-fields`: one-shot that scans local `data/raw/<slug>/*.json` per owner and UPDATEs rows missing the new columns. Idempotent. Snapshots master.db before writing.

#### Backfill results

- **rows updated**: 2,864 (rehydrated from local raw payloads)
- **rows unrecoverable**: 1,294 (raw payload destroyed with the GHA runner; need a `cli ingest --full-refetch` per affected owner to recover from FEC)
- **txn_index size scanned**: 15,077 transactions across 35 owner directories

Coverage in `mockup/data.json` after rebuild: 2,864 from the DB, 1 via legacy raw-payload fallback, 1,293 still NULL. Same user-visible coverage as before the rebuild but **now persisted in the DB** — survives any future GHA matrix re-fetch, no longer depends on whether raw payloads happen to be locally present.

#### Follow-up

To recover the 1,294 unrecoverable rows, run `cli ingest <slug> --full-refetch --chunk-by-cycle` locally per affected owner. The ingest will fetch fresh raw payloads (microsecond-resolution filenames, no collisions) and populate the v3 columns at insert time. Approximate scope: ~30 affected owners, ~3-4h of FEC API time. Separate session.

Tests covering the migration, insert, backfill, build precedence: `tests/test_donation_image_fields.py` (10 cases). Total suite: 169 green.

### 2026-05-27 — INGESTION

- **run_id**: `edfd6703`
- **entity_slug**: `angelos-john-p`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-10`
- **name_variants_queried**: `["John P Angelos", "John P. Angelos", "John Angelos", "Angelos, John", "Angelos, John P", "Angelos, John P."]`
- **api_calls_made**: `84`
- **records_fetched**: `86`
- **confirmed_count**: `51`
- **probable_count**: `1`
- **uncertain_count**: `8`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T01-39-10Z__edfd6703.db`
- **notes**: skipped(no-name-match)=26 · min_date=--full-refetch · states=['TN', 'MD'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `659c73e4`
- **entity_slug**: `dolan-paul`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-05-19`
- **name_variants_queried**: `["Paul J Dolan", "Paul J. Dolan", "Paul Dolan", "Paul Joseph Dolan", "Dolan, Paul", "Dolan, Paul J", "Dolan, Paul J."]`
- **api_calls_made**: `136`
- **records_fetched**: `68`
- **confirmed_count**: `67`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T02-23-57Z__659c73e4.db`
- **notes**: skipped(no-name-match)=1 · min_date=--full-refetch · states=['OH'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `526d2773`
- **entity_slug**: `ilitch-chris`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-26`
- **name_variants_queried**: `["Christopher P Ilitch", "Christopher P. Ilitch", "Christopher Ilitch", "Chris Ilitch", "Christopher Paul Ilitch", "Ilitch, Christopher", "Ilitch, Christopher P", "Ilitch, Christopher P.", "Ilitch, Chris"]`
- **api_calls_made**: `132`
- **records_fetched**: `30`
- **confirmed_count**: `12`
- **probable_count**: `0`
- **uncertain_count**: `18`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T03-20-06Z__526d2773.db`
- **notes**: skipped(no-name-match)=0 · min_date=--full-refetch · states=['MI'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `68719827`
- **entity_slug**: `malone-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-31`
- **name_variants_queried**: `["John C Malone", "John C. Malone", "John Malone", "John Carl Malone", "Malone, John", "Malone, John C", "Malone, John C."]`
- **api_calls_made**: `135`
- **records_fetched**: `152`
- **confirmed_count**: `102`
- **probable_count**: `0`
- **uncertain_count**: `23`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T03-33-15Z__68719827.db`
- **notes**: skipped(no-name-match)=27 · min_date=--full-refetch · states=['CO'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `3da9961f`
- **entity_slug**: `moreno-arte`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-06-03`
- **name_variants_queried**: `["Arturo Moreno", "Arte Moreno", "Arturo R. Moreno", "Arturo R Moreno", "Moreno, Arturo", "Moreno, Arte", "Moreno, Arturo R"]`
- **api_calls_made**: `151`
- **records_fetched**: `79`
- **confirmed_count**: `73`
- **probable_count**: `0`
- **uncertain_count**: `6`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T04-25-59Z__3da9961f.db`
- **notes**: skipped(no-name-match)=0 · min_date=--full-refetch · states=['AZ', 'CA'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `266388b6`
- **entity_slug**: `reinsdorf-jerry`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-02-04`
- **name_variants_queried**: `["Jerry Reinsdorf", "Jerry M Reinsdorf", "Jerry M. Reinsdorf", "Jerry Michael Reinsdorf", "Reinsdorf, Jerry", "Reinsdorf, Jerry M", "Reinsdorf, Jerry M."]`
- **api_calls_made**: `126`
- **records_fetched**: `425`
- **confirmed_count**: `422`
- **probable_count**: `2`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T05-07-41Z__266388b6.db`
- **notes**: skipped(no-name-match)=1 · min_date=--full-refetch · states=['IL'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `3a55273c`
- **entity_slug**: `sherman-bruce`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-31`
- **name_variants_queried**: `["Bruce S Sherman", "Bruce S. Sherman", "Bruce Sherman", "Sherman, Bruce", "Sherman, Bruce S", "Sherman, Bruce S."]`
- **api_calls_made**: `104`
- **records_fetched**: `196`
- **confirmed_count**: `57`
- **probable_count**: `19`
- **uncertain_count**: `120`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T05-27-24Z__3a55273c.db`
- **notes**: skipped(no-name-match)=0 · min_date=--full-refetch · states=['FL'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `a07de4a1`
- **entity_slug**: `steinbrenner-hal`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-22`
- **name_variants_queried**: `["Harold Z Steinbrenner", "Harold Z. Steinbrenner", "Harold Steinbrenner", "Hal Steinbrenner", "Steinbrenner, Harold", "Steinbrenner, Harold Z", "Steinbrenner, Harold Z.", "Steinbrenner, Hal"]`
- **api_calls_made**: `104`
- **records_fetched**: `15`
- **confirmed_count**: `13`
- **probable_count**: `2`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T05-42-59Z__a07de4a1.db`
- **notes**: skipped(no-name-match)=0 · min_date=--full-refetch · states=['FL'] · chunk-by-cycle

### 2026-05-27 — REFRESH RUN d22b008a

- **started_at**: `2026-05-27T01:39:10Z`
- **completed_at**: `2026-05-27T05:51:54Z`
- **dry_run**: `0`
- **owners_attempted**: `9`
- **owners_succeeded**: `8`
- **owners_failed**: `1`
- **total_records_fetched**: `1051`
- **data_json_regenerated**: `False`
- **failed_owners**: `['cohen-steven']`

### 2026-05-27 — INGESTION

- **run_id**: `5bbfd468`
- **entity_slug**: `attanasio-mark`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-31`
- **name_variants_queried**: `["Mark Attanasio", "Mark L Attanasio", "Mark L. Attanasio", "Attanasio, Mark", "Attanasio, Mark L", "Attanasio, Mark L."]`
- **api_calls_made**: `96`
- **records_fetched**: `58`
- **confirmed_count**: `28`
- **probable_count**: `2`
- **uncertain_count**: `28`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T01-39-14Z__5bbfd468.db`
- **notes**: skipped(no-name-match)=0 · min_date=--full-refetch · states=['CA', 'WI'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `db180558`
- **entity_slug**: `crane-jim`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-09-26`
- **name_variants_queried**: `["James R Crane", "James R. Crane", "James Crane", "Jim Crane", "Crane, James", "Crane, James R", "Crane, James R."]`
- **api_calls_made**: `118`
- **records_fetched**: `51`
- **confirmed_count**: `34`
- **probable_count**: `2`
- **uncertain_count**: `14`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T01-47-59Z__db180558.db`
- **notes**: skipped(no-name-match)=1 · min_date=--full-refetch · states=['TX'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `6498d2b4`
- **entity_slug**: `feliciano-jose`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-22`
- **name_variants_queried**: `["Jose E Feliciano", "Jose E. Feliciano", "Jose Feliciano", "Jos\u00e9 E. Feliciano", "Feliciano, Jose", "Feliciano, Jose E", "Feliciano, Jose E."]`
- **api_calls_made**: `85`
- **records_fetched**: `63`
- **confirmed_count**: `23`
- **probable_count**: `1`
- **uncertain_count**: `15`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T02-01-59Z__6498d2b4.db`
- **notes**: skipped(no-name-match)=24 · min_date=--full-refetch · states=['CA'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `87d56dfa`
- **entity_slug**: `mcguirk-terry`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-05-20`
- **name_variants_queried**: `["Terence F McGuirk", "Terence F. McGuirk", "Terence McGuirk", "Terry McGuirk", "Terry F. McGuirk", "Terry F McGuirk", "McGuirk, Terence", "McGuirk, Terence F", "McGuirk, Terence F.", "McGuirk, Terry"]`
- **api_calls_made**: `148`
- **records_fetched**: `47`
- **confirmed_count**: `43`
- **probable_count**: `4`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T03-13-23Z__87d56dfa.db`
- **notes**: skipped(no-name-match)=0 · min_date=--full-refetch · states=['GA'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `609a234f`
- **entity_slug**: `nutting-bob`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-08-12`
- **name_variants_queried**: `["Robert Nutting", "Bob Nutting", "Nutting, Robert", "Nutting, Bob"]`
- **api_calls_made**: `88`
- **records_fetched**: `24`
- **confirmed_count**: `23`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T03-34-03Z__609a234f.db`
- **notes**: skipped(no-name-match)=0 · min_date=--full-refetch · states=['WV', 'PA'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `90d0f364`
- **entity_slug**: `ricketts-tom`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-25`
- **name_variants_queried**: `["Tom Ricketts", "Thomas Ricketts", "Thomas S Ricketts", "Thomas S. Ricketts", "Thomas Stuart Ricketts", "Ricketts, Tom", "Ricketts, Thomas", "Ricketts, Thomas S", "Ricketts, Thomas S."]`
- **api_calls_made**: `143`
- **records_fetched**: `63`
- **confirmed_count**: `61`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T03-55-21Z__90d0f364.db`
- **notes**: skipped(no-name-match)=1 · min_date=--full-refetch · states=['IL'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `6f8a8c14`
- **entity_slug**: `walter-mark`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-07-05`
- **name_variants_queried**: `["Mark Walter", "Mark R. Walter", "Mark R Walter", "Mark Richard Walter", "Walter, Mark", "Walter, Mark R", "Walter, Mark R."]`
- **api_calls_made**: `106`
- **records_fetched**: `579`
- **confirmed_count**: `20`
- **probable_count**: `0`
- **uncertain_count**: `41`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T04-37-54Z__6f8a8c14.db`
- **notes**: skipped(no-name-match)=518 · min_date=--full-refetch · states=['IL'] · chunk-by-cycle

### 2026-05-27 — REFRESH RUN c3ab9707

- **started_at**: `2026-05-27T01:39:13Z`
- **completed_at**: `2026-05-27T05:07:35Z`
- **dry_run**: `0`
- **owners_attempted**: `9`
- **owners_succeeded**: `7`
- **owners_failed**: `2`
- **total_records_fetched**: `885`
- **data_json_regenerated**: `False`
- **failed_owners**: `['johnson-greg', 'sherman-john']`

### 2026-05-27 — INGESTION

- **run_id**: `16355e46`
- **entity_slug**: `castellini-bob`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-31`
- **name_variants_queried**: `["Robert H Castellini", "Robert H. Castellini", "Robert Castellini", "Bob Castellini", "Castellini, Robert", "Castellini, Robert H", "Castellini, Robert H.", "Castellini, Bob"]`
- **api_calls_made**: `148`
- **records_fetched**: `461`
- **confirmed_count**: `405`
- **probable_count**: `34`
- **uncertain_count**: `14`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T01-39-11Z__16355e46.db`
- **notes**: skipped(no-name-match)=8 · min_date=--full-refetch · states=['OH'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `605259cf`
- **entity_slug**: `davis-ray`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-01-23`
- **name_variants_queried**: `["Ray C Davis", "Ray C. Davis", "Ray Davis", "Davis, Ray", "Davis, Ray C", "Davis, Ray C."]`
- **api_calls_made**: `106`
- **records_fetched**: `318`
- **confirmed_count**: `71`
- **probable_count**: `10`
- **uncertain_count**: `13`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T02-02-33Z__605259cf.db`
- **notes**: skipped(no-name-match)=224 · min_date=--full-refetch · states=['TX'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `e8212d10`
- **entity_slug**: `fisher-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-30`
- **name_variants_queried**: `["John Fisher", "John J. Fisher", "John J Fisher", "John Joseph Fisher", "Fisher, John", "Fisher, John J", "Fisher, John J."]`
- **api_calls_made**: `280`
- **records_fetched**: `3921`
- **confirmed_count**: `569`
- **probable_count**: `99`
- **uncertain_count**: `3166`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T02-34-41Z__e8212d10.db`
- **notes**: skipped(no-name-match)=87 · min_date=--full-refetch · states=['CA'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `3c9a734c`
- **entity_slug**: `kendrick-ken`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-31`
- **name_variants_queried**: `["Ken Kendrick", "Earl G. Kendrick", "Earl G Kendrick", "Earl G. Kendrick Jr.", "Earl G. Kendrick, Jr.", "Earl Kendrick", "E. G. Kendrick", "E.G. Kendrick", "E G Kendrick", "Earl Gentry Kendrick", "Kendrick, Ken", "Kendrick, Earl G", "Kendrick, Earl G.", "Kendrick, Earl G., Jr."]`
- **api_calls_made**: `296`
- **records_fetched**: `3473`
- **confirmed_count**: `639`
- **probable_count**: `60`
- **uncertain_count**: `9`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T04-24-44Z__3c9a734c.db`
- **notes**: skipped(no-name-match)=2765 · min_date=--full-refetch · states=['AZ'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `f9ae641a`
- **entity_slug**: `middleton-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-29`
- **name_variants_queried**: `["John Middleton", "John S. Middleton", "John S Middleton", "John Staubus Middleton", "Middleton, John", "Middleton, John S", "Middleton, John S."]`
- **api_calls_made**: `108`
- **records_fetched**: `100`
- **confirmed_count**: `35`
- **probable_count**: `23`
- **uncertain_count**: `32`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T05-28-22Z__f9ae641a.db`
- **notes**: skipped(no-name-match)=10 · min_date=--full-refetch · states=['PA'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `fc6815d4`
- **entity_slug**: `pohlad-joe`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-02-21`
- **name_variants_queried**: `["Joseph C Pohlad", "Joseph C. Pohlad", "Joseph Pohlad", "Joe Pohlad", "Pohlad, Joseph", "Pohlad, Joseph C", "Pohlad, Joseph C.", "Pohlad, Joe"]`
- **api_calls_made**: `122`
- **records_fetched**: `171`
- **confirmed_count**: `107`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T05-42-09Z__fc6815d4.db`
- **notes**: skipped(no-name-match)=64 · min_date=--full-refetch · states=['MN'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `83217410`
- **entity_slug**: `rubenstein-david`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-01-09`
- **name_variants_queried**: `["David Rubenstein", "David M. Rubenstein", "David M Rubenstein", "Rubenstein, David", "Rubenstein, David M", "Rubenstein, David M."]`
- **api_calls_made**: `72`
- **records_fetched**: `31`
- **confirmed_count**: `2`
- **probable_count**: `5`
- **uncertain_count**: `24`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T06-00-06Z__83217410.db`
- **notes**: skipped(no-name-match)=0 · min_date=--full-refetch · states=['MD', 'DC', 'MA'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `6665c032`
- **entity_slug**: `simpson-bob`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2012-09-07`
- **name_variants_queried**: `["Bob R Simpson", "Bob R. Simpson", "Simpson, Bob R", "Simpson, Bob R."]`
- **api_calls_made**: `30`
- **records_fetched**: `8`
- **confirmed_count**: `8`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T06-10-50Z__6665c032.db`
- **notes**: skipped(no-name-match)=0 · min_date=--full-refetch · states=['TX'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `2d792810`
- **entity_slug**: `zalupski-patrick`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-22`
- **name_variants_queried**: `["Patrick O Zalupski", "Patrick O. Zalupski", "Patrick Zalupski", "Zalupski, Patrick", "Zalupski, Patrick O", "Zalupski, Patrick O."]`
- **api_calls_made**: `70`
- **records_fetched**: `20`
- **confirmed_count**: `15`
- **probable_count**: `0`
- **uncertain_count**: `5`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T06-16-44Z__2d792810.db`
- **notes**: skipped(no-name-match)=0 · min_date=--full-refetch · states=['FL'] · chunk-by-cycle

### 2026-05-27 — REFRESH RUN f222c7a3

- **started_at**: `2026-05-27T01:39:10Z`
- **completed_at**: `2026-05-27T06:21:38Z`
- **dry_run**: `0`
- **owners_attempted**: `9`
- **owners_succeeded**: `9`
- **owners_failed**: `0`
- **total_records_fetched**: `8503`
- **data_json_regenerated**: `False`

### 2026-05-27 — INGESTION

- **run_id**: `96d84b1e`
- **entity_slug**: `castellini-phil`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2020-10-20`
- **name_variants_queried**: `["Phillip J Castellini", "Phillip J. Castellini", "Phillip Castellini", "Phil Castellini", "Phil J. Castellini", "Phil J Castellini", "Castellini, Phillip", "Castellini, Phillip J", "Castellini, Phillip J.", "Castellini, Phil"]`
- **api_calls_made**: `158`
- **records_fetched**: `49`
- **confirmed_count**: `47`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T01-39-11Z__96d84b1e.db`
- **notes**: skipped(no-name-match)=2 · min_date=--full-refetch · states=['OH', 'KY'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `11bcc25d`
- **entity_slug**: `dewitt-bill`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-12-31`
- **name_variants_queried**: `["William O DeWitt Jr", "William O. DeWitt Jr.", "William O. DeWitt Jr", "William DeWitt Jr", "William DeWitt Jr.", "Bill DeWitt Jr", "Bill DeWitt Jr.", "William DeWitt", "Bill DeWitt", "DeWitt, William", "DeWitt, William O", "DeWitt, William O Jr", "DeWitt, Bill"]`
- **api_calls_made**: `230`
- **records_fetched**: `359`
- **confirmed_count**: `235`
- **probable_count**: `6`
- **uncertain_count**: `104`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T01-52-49Z__11bcc25d.db`
- **notes**: skipped(no-name-match)=14 · min_date=--full-refetch · states=['OH', 'MO'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `1c081980`
- **entity_slug**: `lerner-mark`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-03-17`
- **name_variants_queried**: `["Mark D Lerner", "Mark D. Lerner", "Mark Lerner", "Lerner, Mark", "Lerner, Mark D", "Lerner, Mark D."]`
- **api_calls_made**: `112`
- **records_fetched**: `240`
- **confirmed_count**: `68`
- **probable_count**: `10`
- **uncertain_count**: `160`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T03-17-03Z__1c081980.db`
- **notes**: skipped(no-name-match)=2 · min_date=--full-refetch · states=['MD'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `da9d6d9f`
- **entity_slug**: `monfort-dick`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-06-03`
- **name_variants_queried**: `["Richard L Monfort", "Richard L. Monfort", "Richard Monfort", "Dick Monfort", "Monfort, Richard", "Monfort, Richard L", "Monfort, Richard L.", "Monfort, Dick"]`
- **api_calls_made**: `158`
- **records_fetched**: `106`
- **confirmed_count**: `101`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T03-29-41Z__da9d6d9f.db`
- **notes**: skipped(no-name-match)=4 · min_date=--full-refetch · states=['CO'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `62a9659b`
- **entity_slug**: `pohlad-tom`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2024-09-24`
- **name_variants_queried**: `["Thomas Pohlad", "Tom Pohlad", "Pohlad, Thomas", "Pohlad, Tom"]`
- **api_calls_made**: `85`
- **records_fetched**: `152`
- **confirmed_count**: `118`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T04-06-19Z__62a9659b.db`
- **notes**: skipped(no-name-match)=33 · min_date=--full-refetch · states=['MN'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `8b9f6700`
- **entity_slug**: `seidler-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-05-11`
- **name_variants_queried**: `["John Seidler", "Seidler, John"]`
- **api_calls_made**: `30`
- **records_fetched**: `1`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T04-18-09Z__8b9f6700.db`
- **notes**: skipped(no-name-match)=0 · min_date=--full-refetch · states=['CA'] · chunk-by-cycle

### 2026-05-27 — INGESTION

- **run_id**: `6b7c1b79`
- **entity_slug**: `stanton-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2025-08-12`
- **name_variants_queried**: `["John W Stanton", "John W. Stanton", "John Stanton", "Stanton, John", "Stanton, John W", "Stanton, John W."]`
- **api_calls_made**: `116`
- **records_fetched**: `326`
- **confirmed_count**: `165`
- **probable_count**: `14`
- **uncertain_count**: `4`
- **snapshot_path**: `/home/runner/work/checkswing/checkswing/data/snapshots/2026-05-27T04-21-00Z__6b7c1b79.db`
- **notes**: skipped(no-name-match)=143 · min_date=--full-refetch · states=['WA'] · chunk-by-cycle

### 2026-05-27 — REFRESH RUN 6a003969

- **started_at**: `2026-05-27T01:39:11Z`
- **completed_at**: `2026-05-27T05:00:16Z`
- **dry_run**: `0`
- **owners_attempted**: `8`
- **owners_succeeded**: `7`
- **owners_failed**: `1`
- **total_records_fetched**: `1233`
- **data_json_regenerated**: `False`
- **failed_owners**: `['henry-john']`

### 2026-05-27 — NOTE — overnight v3 image-fields recovery: partial

Triggered the matrix workflow with `full_refetch=true` (workflow_dispatch run #26485532692) to recover the 1,294 NULL `image_number` rows the local backfill couldn't reach. The workflow ran cleanly end-to-end (all 4 buckets, consolidate, committees_refresh, 2 commits to main: ede9dae + 18d32e6). Per-bucket backfill step worked as designed.

#### Outcome

- **before**: 2,865 / 4,158 with `image_number` (68.9%)
- **after**: 3,276 / 4,212 with `image_number` (**77.8%**)
- **recovered**: 358 rows
- **still NULL**: 936 rows
- bucket 2 did the heaviest lifting (310 rows updated; mostly fisher-john +310), bucket 0 updated 48, buckets 1 and 3 updated 0 each

#### Why partial — not an architecture bug

Spot-checking the 155 still-NULL kendrick-ken rows: all from year 2000, donor names like `"KENDRICK, E G MR"` (note `E G` vs the modern `Earl G` we use in name variants), and **empty `filing_id`**. The `--full-refetch` fetched 3,473 unique records for kendrick (284 raw-payload files persisted on the bucket runner) — but the specific 155 transaction_ids didn't appear in the fresh fetch.

Likely cause: FEC's contributor_name search tokenization or backend indexing has shifted for ancient records. Modern queries using current name variants don't return these specific transaction_ids. The donations themselves are correctly attributed (status=CONFIRMED, owner-side donor signals match) — but the per-transaction image-link payload field can't be rehydrated from current FEC.

#### What this means going forward

- 77.8% image-link coverage is the realistic ceiling without per-record FEC archeology (e.g., querying by image_number / transaction_id directly via /schedules/schedule_a/ search params). Not pursued here.
- Architecture works: the v3 schema + ingest population + per-bucket backfill step does the right thing whenever FEC returns matching data. Next Monday's incremental refresh will continue to populate fields on new donations.
- The 936 stuck rows render as "Image link not available in this payload" on the donation card — same UX as before, but now correctly persisted as NULL rather than failing build-time lookups.

#### Worst-case remaining counts by owner

```
fisher-john:    177 NULL
kendrick-ken:   155
reinsdorf-jerry:133
castellini-bob:  69
dewitt-bill:     63
cohen-steven:    50
stanton-john:    47
monfort-dick:    45
johnson-greg:    27
dolan-paul:      25
```

Open path forward if we ever revisit: an alternative recovery script that queries FEC's `/schedules/schedule_a/?contributor_id=<id>` or per-transaction lookups per affected row. Documented but not built.

### 2026-05-27 — SCHEMA_MIGRATION — v3 → v4 (filings table + real PDF links)

Replaces the donation card's "Filing on FEC.gov" HTML-page stopgap with the
real per-filing PDF, sourced from OpenFEC `/v1/filings/?file_number=<id>`.

#### Background

The previous fix (commit d2d7ee0) routed the donation card's filing link to
`https://www.fec.gov/data/filings/?file_number=<id>` — a public HTML page,
not a direct PDF. FEC's actual per-filing PDF lives at
`https://docquery.fec.gov/pdf/<shard>/<image_number>/<image_number>.pdf`,
where `image_number` is the filing's 18-digit image identifier (different
from per-transaction image numbers). We didn't have that field locally, so
we couldn't construct the URL.

#### Schema

New table `filings`:

```
file_number TEXT PRIMARY KEY,
pdf_url, form_type, document_type, document_type_full,
filed_date, receipt_date, coverage_start_date, coverage_end_date,
committee_id, committee_name, is_amended, amendment_chain, cycle,
raw_payload_path, fetched_at, refreshed_at
```

#### Implementation

- `scripts/fetch_filings.py` — wraps FECClient with `/v1/filings/?file_number=<id>` calls, batching up to 50 file_numbers per request. FEC accepts multi-valued `file_number` query params; one request returns 50 filings on one page in the common case. Raw payloads at `data/raw/_filings/<UTC>__<batch>.json`.
- `scripts/ingest_filings.py` — orchestrator with 30-day freshness gate. Walks `SELECT DISTINCT filing_id FROM donations`, filters to stale ids, batches the fetch. INSERT OR REPLACE keyed on `file_number`.
- CLI: `cli ingest-filings [--only IDs --force-refresh --max N]`.
- `mockup/build_data.py` reads the filings table; each donation gains a `filing_pdf_url` field when its filing has been enriched.
- `mockup/index.html` (`renderDrawer`): donation card prefers `filing_pdf_url` (label "Full filing PDF" → real docquery PDF) over `filing_page_url` (label "Filing on FEC.gov" → HTML fallback). CSV export gains both columns.
- `.github/workflows/refresh.yml` `committees_refresh` job grows an `ingest-filings` step. Steady-state cost: ~10s/week (only newly-stale filings get re-fetched).

#### Backfill results

```
candidates:        2,581 distinct filing_ids
stale_to_fetch:    2,581
fetched:           2,576
upserted:          2,576
missing_from_fec:  5     (ancient filings the /v1/filings/ endpoint doesn't return)
wall-clock:        ~8 min  (51 batches × 50 file_numbers × 4s throttle ≈ ~200s + FEC response time)
```

Coverage in `mockup/data.json`: 2,576/2,581 distinct filings have a real `pdf_url`. The 5 missing leave their donations with the HTML-page fallback (same UX as before this PR, just for a much smaller set). 259 donations have NULL `filing_id` and stay link-less either way.

#### Verification

Spot-checked one URL with curl: `https://docquery.fec.gov/pdf/193/202604159862338193/202604159862338193.pdf` returns `200 OK`, `content-type: application/pdf`, 164KB PDF. Donation card visually confirmed showing "Full filing PDF" with the correct link.

Tests: 13 new cases across `test_fetch_filings.py` and `test_ingest_filings.py`. Total suite: 182 green.

#### Status of the open queue, after this PR

- ~Real PDF filing links~ — done (this entry)
- Phase 2 (Beneficiary view: committee → recipients) — still queued
- 936 NULL image_number rows on donations (year-2000 ancients) — accepted as the realistic ceiling; documented above

### 2026-05-28 — SCHEMA_MIGRATION — v4 → v5 (committee_disbursements_by_recipient)

Adds Phase 2's "Who this committee funded" data layer. For any Phase-1-enriched committee, we now record the top-N (default 200) recipients per cycle as reported by OpenFEC Schedule B `by_recipient`. See `CHARTER.md` Phase 1b sub-bullet for the active-scope statement and GOVERNANCE.md §6 for the editorial guardrail (names + amounts only; no cross-referencing to votes/legislation/policy outcomes — that's Phase 3).

#### What changed

- `scripts/db.py`: `SCHEMA_VERSION = 5`. New `committee_disbursements_by_recipient` table — PK `(committee_id, cycle, recipient_id, recipient_kind)`, columns: recipient_name, recipient_party, recipient_office, total_amount, n_transactions, raw_payload_path, fetched_at. Index `idx_cdbr_committee_cycle`. Idempotent `CREATE TABLE IF NOT EXISTS`.
- `scripts/fetch_committee_disbursements.py` — wraps `FECClient` for `/schedules/schedule_b/by_recipient/?committee_id=<id>&cycle=<c>`. Paginates up to a `top_n` cap (default 200). Persists raw payloads to `data/raw/_committee_disbursements/<id>/<UTC>__by_recipient_cycle_<N>_p<page>.json` (underscored dir matches the `_committees` and `_filings` conventions). Endpoint primary path validated via live smoke test.
- `scripts/ingest_committee_disbursements.py` — orchestrator with 30-day freshness gate per `(committee, cycle)`. DELETE+INSERT per cycle so retracted/amended recipients don't ghost-survive a re-fetch. Snapshots `master.db` before first row write (§1.6). Lock at `data/.committee_disbursements_ingest.lock`. Per-committee failures recorded but don't abort the batch (§1.9).
- CLI: `python -m scripts.cli ingest-committee-beneficiaries [--only IDS --cycles 2022,2024 --force-refresh --top 200 --max N]`.
- `mockup/build_data.py`: new top-level `committee_beneficiaries: dict[committee_id, dict[cycle_str, list[recipient]]]`. Pre-sorted desc by amount, sliced to top 25 per cycle. Tolerant of a pre-v5 DB.
- `mockup/index.html:renderCommittee`: new "Who this committee funded" section after the donations table — cycle selector (defaults to most-recent), table with rank / recipient (party chip + office for candidates) / total amount / # transactions. Plain-text recipients in Phase 2 (candidate-detail routing would be Phase 3).
- `.github/workflows/refresh.yml`: new `beneficiaries: bool` workflow_dispatch input. `committees_refresh` job grows an `ingest-committee-beneficiaries` step after `ingest-filings` (with `continue-on-error: true`). `timeout-minutes` bumped from 90 → 330 to accommodate the initial backfill (which still completes incrementally across multiple weekly runs because the 30-day freshness gate skips already-fresh pairs).

#### Live smoke test

Two small committees, run locally to validate the endpoint + plumbing end-to-end:

```
committees:        2 (C00356279 CAMPBELL VICTORY COMMITTEE, C00457846 ROGER WILLIAMS FOR US SENATE)
cycles_fetched:    5 total (2 + 3)
rows_written:      203 (3 + 200 — top_n cap hit on the Roger Williams cycle)
snapshot:          data/snapshots/2026-05-28T02-49-07Z__beneficiaries_ingest_2026-05-28T02-49-07Z.db
```

Spot-check: C00356279 cycle 2000 reports $804K to NRSC + $69K to CAMPBELL FOR SENATE = ~$873K of the $966K total disbursements recorded on `committee_totals` for that cycle. The remaining $93K is the long-tail Schedule B disbursements below the top 200.

Re-running on the same committee inside the freshness window correctly returns `cycles_skipped_fresh=2, fetched=0` (no FEC calls).

The full ~1054-committee backfill runs via `workflow_dispatch beneficiaries=true` against the new GHA step — not run locally (5-17h of FEC API time at the 4s throttle, completes incrementally over multiple weekly runs as each run skips already-fresh pairs).

#### Tests

- `tests/test_fetch_committee_disbursements.py`: 10 cases (endpoint URL, raw-payload envelope, pagination to `top_n`, candidate vs committee recipient routing, missing-id fallback, missing-total tolerance).
- `tests/test_ingest_committee_disbursements.py`: 14 cases (cycle enumeration, freshness gate, idempotent retraction-aware re-runs, `--cycles` override, per-committee failure isolation, `--only`, `--max`, snapshot creation).
- `tests/test_build_data_committees.py`: 3 new cases (beneficiary join, party normalization, pre-v5 fallback to empty map).

Total suite: 209 green.

### 2026-05-30 — BACKFILL — filing_id sentinel (H3)

- **rows_updated**: `259`
- **sentinel**: `FEC-PRE2006-NOID`
- **snapshot_path**: `data/snapshots/2026-05-30T03-25-45Z__pre-filing-id-sentinel.db`
- **sample_txns**: `['1070820110006620625', '3061920110007582202', '3061920110009108639', '2072320041040551613', '3061920110008116721']`
- **note**: Pre-2006 paper filings with no FEC file number; the sentinel makes the gap explicit (GOVERNANCE.md §1.3). Rows retain raw_payload_path.

### 2026-05-30 — DELETION — reclassify castellini-bob

- **entity_slug**: `castellini-bob`
- **reason**: B2a: apply H4 suffix + M2 city/state classifier fixes (audit remediation)
- **rows_deleted_donations**: `439`
- **rows_deleted_review_queue**: `14` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-55Z__pre-reclassify-castellini-bob.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/castellini-bob/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `cb44cdd9`
- **entity_slug**: `castellini-bob`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2026-03-31`
- **name_variants_queried**: `["Robert H Castellini", "Robert H. Castellini", "Robert Castellini", "Bob Castellini", "Castellini, Robert", "Castellini, Robert H", "Castellini, Robert H.", "Castellini, Bob"]`
- **api_calls_made**: `0`
- **records_fetched**: `461`
- **confirmed_count**: `398`
- **probable_count**: `31`
- **uncertain_count**: `25`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-55Z__cb44cdd9.db`
- **notes**: skipped(no-name-match)=7 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — DELETION — reclassify castellini-phil

- **entity_slug**: `castellini-phil`
- **reason**: B2a: apply H4 suffix + M2 city/state classifier fixes (audit remediation)
- **rows_deleted_donations**: `47`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-55Z__pre-reclassify-castellini-phil.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/castellini-phil/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `4e89f818`
- **entity_slug**: `castellini-phil`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2020-10-20`
- **name_variants_queried**: `["Phillip J Castellini", "Phillip J. Castellini", "Phillip Castellini", "Phil Castellini", "Phil J. Castellini", "Phil J Castellini", "Castellini, Phillip", "Castellini, Phillip J", "Castellini, Phillip J.", "Castellini, Phil"]`
- **api_calls_made**: `0`
- **records_fetched**: `49`
- **confirmed_count**: `47`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-55Z__4e89f818.db`
- **notes**: skipped(no-name-match)=2 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — DELETION — reclassify dewitt-bill

- **entity_slug**: `dewitt-bill`
- **reason**: B2a: apply H4 suffix + M2 city/state classifier fixes (audit remediation)
- **rows_deleted_donations**: `241`
- **rows_deleted_review_queue**: `104` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-55Z__pre-reclassify-dewitt-bill.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/dewitt-bill/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `710d3910`
- **entity_slug**: `dewitt-bill`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2025-12-31`
- **name_variants_queried**: `["William O DeWitt Jr", "William O. DeWitt Jr.", "William O. DeWitt Jr", "William DeWitt Jr", "William DeWitt Jr.", "Bill DeWitt Jr", "Bill DeWitt Jr.", "William DeWitt", "Bill DeWitt", "DeWitt, William", "DeWitt, William O", "DeWitt, William O Jr", "DeWitt, Bill"]`
- **api_calls_made**: `0`
- **records_fetched**: `359`
- **confirmed_count**: `224`
- **probable_count**: `6`
- **uncertain_count**: `126`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-55Z__710d3910.db`
- **notes**: skipped(no-name-match)=3 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — DELETION — reclassify henry-john

- **entity_slug**: `henry-john`
- **reason**: B2a: apply H4 suffix + M2 city/state classifier fixes (audit remediation)
- **rows_deleted_donations**: `18`
- **rows_deleted_review_queue**: `248` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-56Z__pre-reclassify-henry-john.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/henry-john/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `37a4619d`
- **entity_slug**: `henry-john`
- **dry_run**: `0`
- **period_start**: `2024-11-22`
- **period_end**: `2026-03-31`
- **name_variants_queried**: `["John W. Henry", "John W Henry", "John William Henry", "John William Henry II", "John Henry", "Henry, John W", "Henry, John W.", "Henry, John"]`
- **api_calls_made**: `0`
- **records_fetched**: `1142`
- **confirmed_count**: `15`
- **probable_count**: `3`
- **uncertain_count**: `252`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-56Z__37a4619d.db`
- **notes**: skipped(no-name-match)=872 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — DELETION — reclassify ilitch-chris

- **entity_slug**: `ilitch-chris`
- **reason**: B2a: apply H4 suffix + M2 city/state classifier fixes (audit remediation)
- **rows_deleted_donations**: `12`
- **rows_deleted_review_queue**: `18` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-56Z__pre-reclassify-ilitch-chris.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/ilitch-chris/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `f2c83a79`
- **entity_slug**: `ilitch-chris`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2025-12-26`
- **name_variants_queried**: `["Christopher P Ilitch", "Christopher P. Ilitch", "Christopher Ilitch", "Chris Ilitch", "Christopher Paul Ilitch", "Ilitch, Christopher", "Ilitch, Christopher P", "Ilitch, Christopher P.", "Ilitch, Chris"]`
- **api_calls_made**: `0`
- **records_fetched**: `30`
- **confirmed_count**: `12`
- **probable_count**: `0`
- **uncertain_count**: `18`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-56Z__f2c83a79.db`
- **notes**: skipped(no-name-match)=0 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — DELETION — reclassify johnson-greg

- **entity_slug**: `johnson-greg`
- **reason**: B2a: apply H4 suffix + M2 city/state classifier fixes (audit remediation)
- **rows_deleted_donations**: `107`
- **rows_deleted_review_queue**: `2328` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-56Z__pre-reclassify-johnson-greg.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/johnson-greg/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `ee86cab1`
- **entity_slug**: `johnson-greg`
- **dry_run**: `0`
- **period_start**: `2024-11-22`
- **period_end**: `2026-04-17`
- **name_variants_queried**: `["Gregory E Johnson", "Gregory E. Johnson", "Gregory Eugene Johnson", "Greg E Johnson", "Greg E. Johnson", "Greg Johnson", "Johnson, Gregory", "Johnson, Gregory E", "Johnson, Gregory E.", "Johnson, Greg"]`
- **api_calls_made**: `0`
- **records_fetched**: `2497`
- **confirmed_count**: `104`
- **probable_count**: `3`
- **uncertain_count**: `2328`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-57Z__ee86cab1.db`
- **notes**: skipped(no-name-match)=62 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — DELETION — reclassify middleton-john

- **entity_slug**: `middleton-john`
- **reason**: B2a: apply H4 suffix + M2 city/state classifier fixes (audit remediation)
- **rows_deleted_donations**: `58`
- **rows_deleted_review_queue**: `32` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-57Z__pre-reclassify-middleton-john.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/middleton-john/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `560a894f`
- **entity_slug**: `middleton-john`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2025-12-29`
- **name_variants_queried**: `["John Middleton", "John S. Middleton", "John S Middleton", "John Staubus Middleton", "Middleton, John", "Middleton, John S", "Middleton, John S."]`
- **api_calls_made**: `0`
- **records_fetched**: `100`
- **confirmed_count**: `33`
- **probable_count**: `23`
- **uncertain_count**: `44`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-57Z__560a894f.db`
- **notes**: skipped(no-name-match)=0 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — DELETION — reclassify rogers-edward-iii

- **entity_slug**: `rogers-edward-iii`
- **reason**: B2a: apply H4 suffix + M2 city/state classifier fixes (audit remediation)
- **rows_deleted_donations**: `0`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-57Z__pre-reclassify-rogers-edward-iii.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/rogers-edward-iii/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `57ccf096`
- **entity_slug**: `rogers-edward-iii`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `None`
- **name_variants_queried**: `["Edward S Rogers III", "Edward S. Rogers III", "Edward Rogers III", "Edward Samuel Rogers III", "Edward Rogers", "Ed Rogers", "Rogers, Edward", "Rogers, Edward S", "Rogers, Edward S.", "Rogers, Edward III"]`
- **api_calls_made**: `0`
- **records_fetched**: `0`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-57Z__57ccf096.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-30 — DELETION — reclassify seidler-john

- **entity_slug**: `seidler-john`
- **reason**: B2a: apply H4 suffix + M2 city/state classifier fixes (audit remediation)
- **rows_deleted_donations**: `1`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-57Z__pre-reclassify-seidler-john.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/seidler-john/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `4094534e`
- **entity_slug**: `seidler-john`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2025-05-11`
- **name_variants_queried**: `["John Seidler", "Seidler, John"]`
- **api_calls_made**: `0`
- **records_fetched**: `1`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-58Z__4094534e.db`
- **notes**: skipped(no-name-match)=0 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — DELETION — reclassify sherman-john

- **entity_slug**: `sherman-john`
- **reason**: B2a: apply H4 suffix + M2 city/state classifier fixes (audit remediation)
- **rows_deleted_donations**: `40`
- **rows_deleted_review_queue**: `305` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-58Z__pre-reclassify-sherman-john.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/sherman-john/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `23e1d179`
- **entity_slug**: `sherman-john`
- **dry_run**: `0`
- **period_start**: `2024-11-22`
- **period_end**: `2026-01-15`
- **name_variants_queried**: `["John J Sherman", "John J. Sherman", "John Sherman", "Sherman, John", "Sherman, John J", "Sherman, John J."]`
- **api_calls_made**: `0`
- **records_fetched**: `526`
- **confirmed_count**: `34`
- **probable_count**: `6`
- **uncertain_count**: `305`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-58Z__23e1d179.db`
- **notes**: skipped(no-name-match)=181 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — DELETION — reclassify steinbrenner-hal

- **entity_slug**: `steinbrenner-hal`
- **reason**: B2a: apply H4 suffix + M2 city/state classifier fixes (audit remediation)
- **rows_deleted_donations**: `15`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-58Z__pre-reclassify-steinbrenner-hal.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/steinbrenner-hal/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `b0906593`
- **entity_slug**: `steinbrenner-hal`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2025-12-22`
- **name_variants_queried**: `["Harold Z Steinbrenner", "Harold Z. Steinbrenner", "Harold Steinbrenner", "Hal Steinbrenner", "Steinbrenner, Harold", "Steinbrenner, Harold Z", "Steinbrenner, Harold Z.", "Steinbrenner, Hal"]`
- **api_calls_made**: `0`
- **records_fetched**: `15`
- **confirmed_count**: `13`
- **probable_count**: `2`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-05-30T03-55-58Z__b0906593.db`
- **notes**: skipped(no-name-match)=0 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — NOTE — B2b re-fetch attempt for raw-blocked owners (no change applied)

- **owners**: angelos-john-p, malone-john, fisher-john, kendrick-ken
- **goal**: restore missing raw payloads from FEC, then reclassify under the H4 suffix / M2 city+state fixes (the four owners the raw-coverage guard blocked from the B2a pass).
- **outcome**: NOT APPLIED. After a full FEC re-fetch, attributed rows still lacked raw on disk (angelos: 48 missing; fisher: 310; kendrick: 1; malone: fetch timed out). FEC's current API no longer returns the specific historical transactions these rows were sourced from. `reclassify` was skipped for all four — the raw-coverage guard held, not `--force`, so no attributed rows were dropped.
- **data effect**: none. The partial re-fetch writes were rolled back to the pre-B2b committed state; these four owners remain on their prior classification. A minority of raw payloads were restored to disk and are retained for future re-verification.
- **implication**: these four cannot be cleanly reclassified until their raw is otherwise recovered. Of the four, only kendrick-ken has suffix name-variants (the only one whose classification the H4 fix would change); the others are non-suffix / non-cross-state, so the current classifier yields the same result as before.

### 2026-05-30 — RESOLUTION — bulk-discard review-queue items

- **reason_like**: `city/state outside documented residences%`
- **scope**: `all owners`
- **items_discarded**: `8411`
- **per_owner**: cohen-steven=2683, fisher-john=2439, johnson-greg=2328, sherman-john=305, henry-john=241, dewitt-bill=97, sherman-bruce=77, walter-mark=41, attanasio-mark=28, malone-john=23, rubenstein-david=23, lerner-mark=23, ilitch-chris=18, feliciano-jose=15, middleton-john=15, crane-jim=14, davis-ray=13, angelos-john-p=8, moreno-arte=6, kendrick-ken=5, zalupski-patrick=5, stanton-john=4
- **open_queue_remaining**: `1107`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T17-00-03Z__pre-bulk-discard.db`
- **note**: Standing DISCARDED verdicts recorded in review_resolutions (survive reclassify). Attribution unaffected (GOVERNANCE.md §2.5). Reversible via `unresolve`.

### 2026-05-30 — RESOLUTION — bulk-discard review-queue items

- **reason_like**: `matches negative employer signal%`
- **scope**: `all owners`
- **items_discarded**: `1039`
- **per_owner**: fisher-john=725, lerner-mark=137, cohen-steven=101, sherman-bruce=43, middleton-john=17, castellini-bob=14, rubenstein-david=1, henry-john=1
- **open_queue_remaining**: `68`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T17-00-03Z__pre-bulk-discard.db`
- **note**: Standing DISCARDED verdicts recorded in review_resolutions (survive reclassify). Attribution unaffected (GOVERNANCE.md §2.5). Reversible via `unresolve`.

### 2026-05-30 — RESOLUTION — bulk-discard review-queue items

- **reason_like**: `name match only%`
- **scope**: `all owners`
- **items_discarded**: `1`
- **per_owner**: cohen-steven=1
- **open_queue_remaining**: `67`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T17-00-03Z__pre-bulk-discard.db`
- **note**: Standing DISCARDED verdicts recorded in review_resolutions (survive reclassify). Attribution unaffected (GOVERNANCE.md §2.5). Reversible via `unresolve`.

### 2026-05-30 — DELETION — reclassify dewitt-bill

- **entity_slug**: `dewitt-bill`
- **reason**: probe: verify discard suppression survives reclassify
- **rows_deleted_donations**: `230`
- **rows_deleted_review_queue**: `126` (of which 97 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T17-00-03Z__pre-reclassify-dewitt-bill.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/dewitt-bill/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `4c506293`
- **entity_slug**: `dewitt-bill`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2025-12-31`
- **name_variants_queried**: `["William O DeWitt Jr", "William O. DeWitt Jr.", "William O. DeWitt Jr", "William DeWitt Jr", "William DeWitt Jr.", "Bill DeWitt Jr", "Bill DeWitt Jr.", "William DeWitt", "Bill DeWitt", "DeWitt, William", "DeWitt, William O", "DeWitt, William O Jr", "DeWitt, Bill"]`
- **api_calls_made**: `0`
- **records_fetched**: `359`
- **confirmed_count**: `224`
- **probable_count**: `6`
- **uncertain_count**: `126`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T17-00-04Z__4c506293.db`
- **notes**: skipped(no-name-match)=3 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — RESOLUTION — bulk-discard review-queue items

- **reason_like**: `suffix mismatch%`
- **scope**: `dewitt-bill`
- **items_discarded**: `29`
- **per_owner**: dewitt-bill=29
- **open_queue_remaining**: `38`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T17-12-52Z__pre-bulk-discard.db`
- **note**: Standing DISCARDED verdicts recorded in review_resolutions (survive reclassify). Attribution unaffected (GOVERNANCE.md §2.5). Reversible via `unresolve`.

### 2026-05-30 — MANUAL_ATTRIBUTION — dewitt-bill (Cluster A, 6 records)

- **transaction_ids**: ['SA11AI.9241', 'SA11AI.9473', 'SA11AI.9745', 'SA11AI.9791', 'SA11AI.9980', 'SA11AI.10338']
- **entity_slug**: dewitt-bill
- **forced_status**: CONFIRMED
- **count**: 6 · **total_amount**: $30,000 (6 × $5,000, 2019-2025)
- **reason**: Bill DeWitt Jr donations misfiled under name "DEWITT III, WILLIAM O JR."; every signal matches the principal owner (zip 45243 = his documented residence, employer ST. LOUIS CARDINALS, occupation CHAIRMAN AND CEO). The literal III is a filer data-entry error, not the son: the son (William DeWitt III, Cardinals President) files from St. Louis MO as President/General Partner. Six annual $5,000 contributions 2019-2025.
- **source**: FEC Schedule A raw payloads vs owners/dewitt-bill.yaml signal block; son disambiguated by city/title (St. Louis/President vs Cincinnati/Chairman&CEO).
- **snapshot_path**: /Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T17-21-36Z__pre-manual-attribute-dewitt-clusterA.db
- **note**: Override recorded in manual_attributions (survives reclassify), bypassing the two-signal rule by documented human decision (GOVERNANCE.md §1.1). The son's 23 same-named records remain out of scope (verified: 0 wrongly attributed). Reversible via unattribute. Reclassification applied below.

### 2026-05-30 — DELETION — reclassify dewitt-bill

- **entity_slug**: `dewitt-bill`
- **reason**: apply 6 Cluster-A manual attributions
- **rows_deleted_donations**: `230`
- **rows_deleted_review_queue**: `29` (of which 29 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T17-21-36Z__pre-reclassify-dewitt-bill.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/dewitt-bill/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `70eb5612`
- **entity_slug**: `dewitt-bill`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2025-12-31`
- **name_variants_queried**: `["William O DeWitt Jr", "William O. DeWitt Jr.", "William O. DeWitt Jr", "William DeWitt Jr", "William DeWitt Jr.", "Bill DeWitt Jr", "Bill DeWitt Jr.", "William DeWitt", "Bill DeWitt", "DeWitt, William", "DeWitt, William O", "DeWitt, William O Jr", "DeWitt, Bill"]`
- **api_calls_made**: `0`
- **records_fetched**: `359`
- **confirmed_count**: `230`
- **probable_count**: `6`
- **uncertain_count**: `120`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T17-21-37Z__70eb5612.db`
- **notes**: skipped(no-name-match)=3 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — MANUAL_ATTRIBUTION — castellini-bob

- **transaction_id**: `SA11A.29970534`
- **entity_slug**: `castellini-bob`
- **forced_status**: `CONFIRMED`
- **reason**: Robert H. Castellini Sr. donation misfiled with stray suffix 'SR.' (name 'CASTELLINI, ROBERT H. MR. SR.') that no name_variant can capture without also matching same-named relatives. All signals match the principal owner: middle initial H, employer CASTELLINI COMPANY (strong_signal), occupation CHAIRMAN, Cincinnati OH, ZIP 452022728 (151 CONFIRMED records share this ZIP). Disambiguated from two JR doppelgangers in the same queue: Robert Castellini Jr (President & CEO, no middle H) and Bob S. Castellini Jr (Wells Fargo, Managing Partner — documented financial-advisor doppelganger).
- **source**: FEC Schedule A raw payload data/raw/castellini-bob/2026-05-22T23-43-05Z__schedule_a.json vs owners/castellini-bob.yaml strong_signals.employers; SR. = father (owner), JR. variants are separate individuals.
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T18-24-14Z__pre-manual-attribute.db`
- **note**: Override recorded in manual_attributions (survives reclassify). Bypasses the two-signal rule by documented human decision (GOVERNANCE.md §1.1). Reversible via `unattribute`. Reclassification follows below.

### 2026-05-30 — DELETION — reclassify castellini-bob

- **entity_slug**: `castellini-bob`
- **reason**: apply manual attribution of SA11A.29970534
- **rows_deleted_donations**: `429`
- **rows_deleted_review_queue**: `25` (of which 14 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T18-24-14Z__pre-reclassify-castellini-bob.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/castellini-bob/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `f9fe9778`
- **entity_slug**: `castellini-bob`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2026-03-31`
- **name_variants_queried**: `["Robert H Castellini", "Robert H. Castellini", "Robert Castellini", "Bob Castellini", "Castellini, Robert", "Castellini, Robert H", "Castellini, Robert H.", "Castellini, Bob"]`
- **api_calls_made**: `0`
- **records_fetched**: `461`
- **confirmed_count**: `399`
- **probable_count**: `31`
- **uncertain_count**: `24`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T18-24-14Z__f9fe9778.db`
- **notes**: skipped(no-name-match)=7 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — MANUAL_ATTRIBUTION — castellini-bob

- **transaction_id**: `SA11A.33810`
- **entity_slug**: `castellini-bob`
- **forced_status**: `CONFIRMED`
- **reason**: Robert H. Castellini Sr. donation misfiled with stray suffix 'SR.' (name 'CASTELLINI, ROBERT H. MR. SR.') that no name_variant can capture without also matching same-named relatives. All signals match the principal owner: middle initial H, employer CASTELLINI COMPANY (strong_signal), occupation CHAIRMAN, Cincinnati OH, ZIP 452022728/452022739 (both heavy CONFIRMED clusters for this owner). Disambiguated from two JR doppelgangers in the same queue: Robert Castellini Jr (President & CEO, no middle H) and Bob S. Castellini Jr (Wells Fargo, Managing Partner — documented financial-advisor doppelganger).
- **source**: FEC Schedule A raw payload data/raw/castellini-bob/2026-05-22T23-43-05Z__schedule_a.json vs owners/castellini-bob.yaml strong_signals.employers; SR. = father (owner), JR. variants are separate individuals.
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T18-24-33Z__pre-manual-attribute.db`
- **note**: Override recorded in manual_attributions (survives reclassify). Bypasses the two-signal rule by documented human decision (GOVERNANCE.md §1.1). Reversible via `unattribute`. Reclassification follows below.

### 2026-05-30 — DELETION — reclassify castellini-bob

- **entity_slug**: `castellini-bob`
- **reason**: apply manual attribution of SA11A.33810
- **rows_deleted_donations**: `430`
- **rows_deleted_review_queue**: `10` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T18-24-33Z__pre-reclassify-castellini-bob.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/castellini-bob/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `28b60b03`
- **entity_slug**: `castellini-bob`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2026-03-31`
- **name_variants_queried**: `["Robert H Castellini", "Robert H. Castellini", "Robert Castellini", "Bob Castellini", "Castellini, Robert", "Castellini, Robert H", "Castellini, Robert H.", "Castellini, Bob"]`
- **api_calls_made**: `0`
- **records_fetched**: `461`
- **confirmed_count**: `400`
- **probable_count**: `31`
- **uncertain_count**: `23`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T18-24-33Z__28b60b03.db`
- **notes**: skipped(no-name-match)=7 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — MANUAL_ATTRIBUTION — castellini-bob

- **transaction_id**: `SA.33810.2.TM10`
- **entity_slug**: `castellini-bob`
- **forced_status**: `CONFIRMED`
- **reason**: Robert H. Castellini Sr. donation misfiled with stray suffix 'SR.' (name 'CASTELLINI, ROBERT H. MR. SR.') that no name_variant can capture without also matching same-named relatives. All signals match the principal owner: middle initial H, employer CASTELLINI COMPANY (strong_signal), occupation CHAIRMAN, Cincinnati OH, ZIP 452022728/452022739 (both heavy CONFIRMED clusters for this owner). Disambiguated from two JR doppelgangers in the same queue: Robert Castellini Jr (President & CEO, no middle H) and Bob S. Castellini Jr (Wells Fargo, Managing Partner — documented financial-advisor doppelganger).
- **source**: FEC Schedule A raw payload data/raw/castellini-bob/2026-05-22T23-43-05Z__schedule_a.json vs owners/castellini-bob.yaml strong_signals.employers; SR. = father (owner), JR. variants are separate individuals.
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T18-24-33Z__pre-manual-attribute.db`
- **note**: Override recorded in manual_attributions (survives reclassify). Bypasses the two-signal rule by documented human decision (GOVERNANCE.md §1.1). Reversible via `unattribute`. Reclassification follows below.

### 2026-05-30 — DELETION — reclassify castellini-bob

- **entity_slug**: `castellini-bob`
- **reason**: apply manual attribution of SA.33810.2.TM10
- **rows_deleted_donations**: `431`
- **rows_deleted_review_queue**: `9` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T18-24-33Z__pre-reclassify-castellini-bob.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/castellini-bob/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `001f8c07`
- **entity_slug**: `castellini-bob`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2026-03-31`
- **name_variants_queried**: `["Robert H Castellini", "Robert H. Castellini", "Robert Castellini", "Bob Castellini", "Castellini, Robert", "Castellini, Robert H", "Castellini, Robert H.", "Castellini, Bob"]`
- **api_calls_made**: `0`
- **records_fetched**: `461`
- **confirmed_count**: `401`
- **probable_count**: `31`
- **uncertain_count**: `22`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T18-24-34Z__001f8c07.db`
- **notes**: skipped(no-name-match)=7 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — MANUAL_ATTRIBUTION — castellini-bob

- **transaction_id**: `SA.33810.1.TM10`
- **entity_slug**: `castellini-bob`
- **forced_status**: `CONFIRMED`
- **reason**: Robert H. Castellini Sr. donation misfiled with stray suffix 'SR.' (name 'CASTELLINI, ROBERT H. MR. SR.') that no name_variant can capture without also matching same-named relatives. All signals match the principal owner: middle initial H, employer CASTELLINI COMPANY (strong_signal), occupation CHAIRMAN, Cincinnati OH, ZIP 452022728/452022739 (both heavy CONFIRMED clusters for this owner). Disambiguated from two JR doppelgangers in the same queue: Robert Castellini Jr (President & CEO, no middle H) and Bob S. Castellini Jr (Wells Fargo, Managing Partner — documented financial-advisor doppelganger).
- **source**: FEC Schedule A raw payload data/raw/castellini-bob/2026-05-22T23-43-05Z__schedule_a.json vs owners/castellini-bob.yaml strong_signals.employers; SR. = father (owner), JR. variants are separate individuals.
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T18-24-34Z__pre-manual-attribute.db`
- **note**: Override recorded in manual_attributions (survives reclassify). Bypasses the two-signal rule by documented human decision (GOVERNANCE.md §1.1). Reversible via `unattribute`. Reclassification follows below.

### 2026-05-30 — DELETION — reclassify castellini-bob

- **entity_slug**: `castellini-bob`
- **reason**: apply manual attribution of SA.33810.1.TM10
- **rows_deleted_donations**: `432`
- **rows_deleted_review_queue**: `8` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T18-24-34Z__pre-reclassify-castellini-bob.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/castellini-bob/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `a66c1a33`
- **entity_slug**: `castellini-bob`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2026-03-31`
- **name_variants_queried**: `["Robert H Castellini", "Robert H. Castellini", "Robert Castellini", "Bob Castellini", "Castellini, Robert", "Castellini, Robert H", "Castellini, Robert H.", "Castellini, Bob"]`
- **api_calls_made**: `0`
- **records_fetched**: `461`
- **confirmed_count**: `402`
- **probable_count**: `31`
- **uncertain_count**: `21`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T18-24-34Z__a66c1a33.db`
- **notes**: skipped(no-name-match)=7 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — MANUAL_ATTRIBUTION — castellini-bob

- **transaction_id**: `SA12.25708975`
- **entity_slug**: `castellini-bob`
- **forced_status**: `CONFIRMED`
- **reason**: Robert H. Castellini Sr. donation misfiled with stray suffix 'SR.' (name 'CASTELLINI, ROBERT H. MR. SR.') that no name_variant can capture without also matching same-named relatives. All signals match the principal owner: middle initial H, employer CASTELLINI COMPANY (strong_signal), occupation CHAIRMAN, Cincinnati OH, ZIP 452022728/452022739 (both heavy CONFIRMED clusters for this owner). Disambiguated from two JR doppelgangers in the same queue: Robert Castellini Jr (President & CEO, no middle H) and Bob S. Castellini Jr (Wells Fargo, Managing Partner — documented financial-advisor doppelganger).
- **source**: FEC Schedule A raw payload data/raw/castellini-bob/2026-05-22T23-43-05Z__schedule_a.json vs owners/castellini-bob.yaml strong_signals.employers; SR. = father (owner), JR. variants are separate individuals.
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T18-24-34Z__pre-manual-attribute.db`
- **note**: Override recorded in manual_attributions (survives reclassify). Bypasses the two-signal rule by documented human decision (GOVERNANCE.md §1.1). Reversible via `unattribute`. Reclassification follows below.

### 2026-05-30 — DELETION — reclassify castellini-bob

- **entity_slug**: `castellini-bob`
- **reason**: apply manual attribution of SA12.25708975
- **rows_deleted_donations**: `433`
- **rows_deleted_review_queue**: `7` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T18-24-34Z__pre-reclassify-castellini-bob.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/castellini-bob/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `df3d1586`
- **entity_slug**: `castellini-bob`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2026-03-31`
- **name_variants_queried**: `["Robert H Castellini", "Robert H. Castellini", "Robert Castellini", "Bob Castellini", "Castellini, Robert", "Castellini, Robert H", "Castellini, Robert H.", "Castellini, Bob"]`
- **api_calls_made**: `0`
- **records_fetched**: `461`
- **confirmed_count**: `403`
- **probable_count**: `31`
- **uncertain_count**: `20`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T18-24-35Z__df3d1586.db`
- **notes**: skipped(no-name-match)=7 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — RESOLUTION — bulk-discard review-queue items

- **reason_like**: `suffix mismatch%`
- **scope**: `castellini-bob`
- **items_discarded**: `6`
- **per_owner**: castellini-bob=6
- **open_queue_remaining**: `27`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T18-25-31Z__pre-bulk-discard.db`
- **note**: Standing DISCARDED verdicts recorded in review_resolutions (survive reclassify). Attribution unaffected (GOVERNANCE.md §2.5). Reversible via `unresolve`.

### 2026-05-30 — RESOLUTION — bulk-discard review-queue items

- **reason_like**: `suffix mismatch%`
- **scope**: `henry-john`
- **items_discarded**: `10`
- **per_owner**: henry-john=10
- **open_queue_remaining**: `17`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T19-13-14Z__pre-bulk-discard.db`
- **note**: Standing DISCARDED verdicts recorded in review_resolutions (survive reclassify). Attribution unaffected (GOVERNANCE.md §2.5). Reversible via `unresolve`.

### 2026-05-30 — DELETION — reclassify kendrick-ken

- **entity_slug**: `kendrick-ken`
- **reason**: Calibration round 3: add Ken/E.G.+Jr name_variants (heals 14 divergent stored Ken rows, confirms 3 queued Ken records, ATTORNEY->PROBABLE) and correct the Randy Kendrick misattribution (§1.10, §2.5a)
- **rows_deleted_donations**: `701`
- **rows_deleted_review_queue**: `9` (of which 5 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T22-07-12Z__pre-reclassify-kendrick-ken.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/kendrick-ken/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `0c390333`
- **entity_slug**: `kendrick-ken`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2025-12-31`
- **name_variants_queried**: `["Ken Kendrick", "Earl G. Kendrick", "Earl G Kendrick", "Earl G. Kendrick Jr.", "Earl G. Kendrick, Jr.", "Earl Kendrick", "E. G. Kendrick", "E.G. Kendrick", "E G Kendrick", "Earl Gentry Kendrick", "Kendrick, Ken", "Kendrick, Earl G", "Kendrick, Earl G.", "Kendrick, Earl G., Jr.", "Ken Kendrick Jr.", "Kendrick, Ken, Jr.", "Kendrick, Ken Jr.", "E.G. Kendrick Jr.", "E. G. Kendrick Jr.", "Kendrick, E.G., Jr.", "Kendrick, E. G., Jr."]`
- **api_calls_made**: `0`
- **records_fetched**: `3475`
- **confirmed_count**: `663`
- **probable_count**: `68`
- **uncertain_count**: `7`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T22-07-12Z__0c390333.db`
- **notes**: skipped(no-name-match)=2737 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — DELETION — reclassify kendrick-ken

- **entity_slug**: `kendrick-ken`
- **reason**: Calibration round 3 (cont'd): add city variant 'paradise vly' to close the 2 residual E.G.-Jr/Diamondbacks items
- **rows_deleted_donations**: `731`
- **rows_deleted_review_queue**: `2` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T22-10-42Z__pre-reclassify-kendrick-ken.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/kendrick-ken/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `bb72f4b5`
- **entity_slug**: `kendrick-ken`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2025-12-31`
- **name_variants_queried**: `["Ken Kendrick", "Earl G. Kendrick", "Earl G Kendrick", "Earl G. Kendrick Jr.", "Earl G. Kendrick, Jr.", "Earl Kendrick", "E. G. Kendrick", "E.G. Kendrick", "E G Kendrick", "Earl Gentry Kendrick", "Kendrick, Ken", "Kendrick, Earl G", "Kendrick, Earl G.", "Kendrick, Earl G., Jr.", "Ken Kendrick Jr.", "Kendrick, Ken, Jr.", "Kendrick, Ken Jr.", "E.G. Kendrick Jr.", "E. G. Kendrick Jr.", "Kendrick, E.G., Jr.", "Kendrick, E. G., Jr."]`
- **api_calls_made**: `0`
- **records_fetched**: `3475`
- **confirmed_count**: `665`
- **probable_count**: `68`
- **uncertain_count**: `5`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T22-10-42Z__bb72f4b5.db`
- **notes**: skipped(no-name-match)=2737 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — CORRECTION (§1.10 misattribution) — kendrick-ken

- **transaction_id**: `SA11AI.4306`
- **entity_slug**: `kendrick-ken`
- **prior_status**: `CONFIRMED` (stale — predated the current classifier)
- **action**: Removed from kendrick-ken during the calibration-round-3 reclassify above. The record is `KENDRICK, RANDY` / RETIRED / Paradise Valley AZ / $56,000 — **Randy Kendrick, the owner's wife**, an independent donor the owner YAML explicitly forbids attributing to Ken (she is name-no-match under his name_variants). The row was a latent misattribution carried over from an earlier classifier state and surfaced when the reclassify rebuilt kendrick-ken from raw.
- **recoverable_from**: the pre-reclassify snapshots listed above + `data/raw/kendrick-ken/`. This corrects the owner attribution only; it is NOT a deletion of the underlying FEC fact. Randy remains a documented future `kendrick-randy.yaml` candidate (GOVERNANCE.md §1.7).

### 2026-05-30 — RESOLUTION — bulk-discard review-queue items

- **reason_like**: `suffix mismatch%`
- **scope**: `fisher-john`
- **items_discarded**: `1`
- **per_owner**: fisher-john=1
- **open_queue_remaining**: `12`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T22-26-51Z__pre-bulk-discard.db`
- **note**: Standing DISCARDED verdicts recorded in review_resolutions (survive reclassify). Attribution unaffected (GOVERNANCE.md §2.5). Reversible via `unresolve`.

### 2026-05-30 — MANUAL_ATTRIBUTION (batch heal) — reinsdorf-jerry, mcguirk-terry

- **records**: `1070520180036861433` + `1070520180036861432` (reinsdorf-jerry); `2072220141218266778` (mcguirk-terry)
- **forced_status**: `CONFIRMED`
- **why batched**: reinsdorf-jerry has 2 divergent rows; the divergence guard (PR #9) would block a one-at-a-time `attribute` (the 2nd row still divergent during the 1st's reclassify), so both overrides are inserted before reclassifying.
- **reinsdorf reason**: Jerry M. Reinsdorf (principal owner, Chicago White Sox / Bulls) donation misfiled with the junk suffix token 'OTHER' (name 'REINSDORF, JERRY MR OTHER'), which breaks the canonical name-match entirely (name-no-match) so NO name_variant can capture it. All other signals match the owner: SELF EMPLOYED / BUSINESSMAN, Chicago IL, ZIP 606163621 (a documented strong_signal zip for this owner). Stored CONFIRMED in master.db; force-CONFIRM via override so a from-raw reclassify preserves it (it would otherwise be dropped as name-no-match). Two FEC transactions, same date/committee ($2,700 each, 2018-03-31, C00436386).
- **mcguirk reason**: Terence F. 'Terry' McGuirk (principal owner / Chairman & CEO, Atlanta Braves) donation misfiled with the wrong generational suffix 'III' (name 'MCGUIRK, TERRY MR III'), routing to UNCERTAIN suffix-mismatch (owner is Terence F., not a III). All signals match the owner: employer ATLANTA BRAVES, occupation CHAIRMAN & CEO, Atlanta GA, ZIP 30327 (a documented strong_signal zip). Stored CONFIRMED in master.db; force-CONFIRM via override so a from-raw reclassify preserves it. $1,000, 2013-09-25, C00547570.
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T22-53-46Z__pre-heal-reinsdorf-mcguirk.db`
- **note**: Overrides recorded in manual_attributions (survive reclassify; §1.1 documented human decision). These rows were CONFIRMED in master.db but not reproducible by the current classifier (the divergence the PR #9 guard now flags). Reversible via `unattribute`. Reclassify entries follow.

### 2026-05-30 — DELETION — reclassify reinsdorf-jerry

- **entity_slug**: `reinsdorf-jerry`
- **reason**: apply manual attribution heal of misfiled-suffix owner record(s) for reinsdorf-jerry
- **rows_deleted_donations**: `422`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T22-53-46Z__pre-reclassify-reinsdorf-jerry.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/reinsdorf-jerry/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `cbdcdb40`
- **entity_slug**: `reinsdorf-jerry`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2026-02-04`
- **name_variants_queried**: `["Jerry Reinsdorf", "Jerry M Reinsdorf", "Jerry M. Reinsdorf", "Jerry Michael Reinsdorf", "Reinsdorf, Jerry", "Reinsdorf, Jerry M", "Reinsdorf, Jerry M."]`
- **api_calls_made**: `0`
- **records_fetched**: `425`
- **confirmed_count**: `422`
- **probable_count**: `2`
- **uncertain_count**: `0`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T22-53-46Z__cbdcdb40.db`
- **notes**: skipped(no-name-match)=1 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-30 — SUPERSESSION — run cbdcdb40

- `SA11AI.4319` (reinsdorf-jerry): FEC restatement: amount, date, recipient_committee_id, filing_id, image_number
- `SA11AI.4164` (reinsdorf-jerry): FEC restatement: date, recipient_committee_id, filing_id, image_number

### 2026-05-30 — DELETION — reclassify mcguirk-terry

- **entity_slug**: `mcguirk-terry`
- **reason**: apply manual attribution heal of misfiled-suffix owner record(s) for mcguirk-terry
- **rows_deleted_donations**: `47`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T22-53-46Z__pre-reclassify-mcguirk-terry.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/mcguirk-terry/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-30 — INGESTION

- **run_id**: `c911f038`
- **entity_slug**: `mcguirk-terry`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2025-05-20`
- **name_variants_queried**: `["Terence F McGuirk", "Terence F. McGuirk", "Terence McGuirk", "Terry McGuirk", "Terry F. McGuirk", "Terry F McGuirk", "McGuirk, Terence", "McGuirk, Terence F", "McGuirk, Terence F.", "McGuirk, Terry"]`
- **api_calls_made**: `0`
- **records_fetched**: `47`
- **confirmed_count**: `43`
- **probable_count**: `4`
- **uncertain_count**: `0`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-30T22-53-46Z__c911f038.db`
- **notes**: skipped(no-name-match)=0 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-31 — MANUAL_ATTRIBUTION + MANUAL_EXCLUSION (batch) — middleton-john

- **attributed (CONFIRMED, John S. Staubus = owner)**: 6 txns — `MIDDLETON, JOHN S. MR. SR.` / Bradford Holdings (strong signal); queued only on the SR suffix. ['2011YEL11AI08426', '1121520090002805373', 'SA11.1653083', '49428662', 'SA18.1294499', 'SA11.1294499']
- **excluded (John P. Powers = son)**: 14 txns — `MIDDLETON, JOHN P. ...` (1 was CONFIRMED, 7 PROBABLE, 6 queued). Dropped from john-s. ['2011M03L11AI01333', 'SA11AI.6390', '1020220110005296882', '2009M12L11AI08886', '1010720100003825407', '1010720100003825406', '1010720100003825405', '2009M02L11AI01711', '1121520090003627754', 'SA11.1142039', 'SA11.1004545', 'SA17A.880045', 'SA17.804881', 'SA17.804880']
- **attribute reason**: John S. (Staubus) Middleton — principal owner / managing partner of the Philadelphia Phillies — donation misfiled with the 'SR.' suffix ('MIDDLETON, JOHN S. MR. SR.') that no name_variant captures. Employer BRADFORD HOLDINGS INC. is his strong_signal holding company; queued only on the suffix. Middle initial S (Staubus) distinguishes him from his son John Powers Middleton (P).
- **exclude reason**: John Powers Middleton (middle initial P), SON of owner John S. Middleton — a film producer (Vertigo Entertainment; Manchester by the Sea) AND a documented independent major federal political donor (Wikipedia 'John Powers Middleton'; Media Matters reporting on his political giving). He lives at the same Bryn Mawr address, and the classifier drops middle initials, so 'John P.' cannot be separated from 'John S.' by name. These records carry middle initial P (Powers) — they are the SON's donations, NOT the owner's — regardless of employer (some list 'PHILADELPHIA PHILLIES' loosely). EXCLUDED from john-s by documented human decision (GOVERNANCE.md §1.1/§1.9). John P. is a future separate slug (middleton-john-p) pending a classifier middle-initial fix.
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-31T01-02-28Z__pre-middleton-triage.db`
- **note**: Disambiguated by middle initial (S=Staubus owner, P=Powers son). The classifier drops middle initials so name alone can't separate father/son at the shared Bryn Mawr address; the Vertigo negative_signal only caught Vertigo-employer records. These txn-keyed overrides survive reclassify. John P. (son) is a documented independent major donor and a future middleton-john-p slug. Reversible via unattribute/unexclude. Reclassify follows.

### 2026-05-31 — DELETION — reclassify middleton-john

- **entity_slug**: `middleton-john`
- **reason**: round-2 triage: attribute 6 John S Sr/Bradford queued records; exclude 14 John P (Powers, son) records via new EXCLUDE feature
- **rows_deleted_donations**: `56`
- **rows_deleted_review_queue**: `44` (of which 32 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-31T01-02-28Z__pre-reclassify-middleton-john.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/middleton-john/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-31 — INGESTION

- **run_id**: `f23a17e3`
- **entity_slug**: `middleton-john`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2025-12-29`
- **name_variants_queried**: `["John Middleton", "John S. Middleton", "John S Middleton", "John Staubus Middleton", "Middleton, John", "Middleton, John S", "Middleton, John S."]`
- **api_calls_made**: `0`
- **records_fetched**: `100`
- **confirmed_count**: `38`
- **probable_count**: `16`
- **uncertain_count**: `32`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-31T01-02-28Z__f23a17e3.db`
- **notes**: skipped(no-name-match)=0 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-31 — BENEFICIARY-INGEST (full backfill) — committee_disbursements_by_recipient

- **operation**: `ingest-committee-beneficiaries` (full backfill, top-200 recipients per committee per cycle from OpenFEC Schedule B by_recipient)
- **scope**: every committee with totals — `attempted=1047`, `fetched=1024`, `skipped_no_fresh_cycles=23` (already fresh from the 2026-05-28 smoke run), `failed=0`
- **rows_written**: `350044` this run; table total now `395509` recipient-rows across `6237` (committee, cycle) pairs and `1047` committees
- **empty_cycles**: `~308` (committee, cycle) pairs in committee_totals returned zero by_recipient rows from FEC — legitimately empty, not failures
- **run_window**: `2026-05-31T03:48:26Z → 2026-05-31T10:49:08Z` (~7h, single worker at MIN_REQUEST_INTERVAL_S=4.0)
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-31T03-48-26Z__beneficiaries_ingest_2026-05-31T03-48-26Z.db`
- **raw_payloads**: `data/raw/_committee_disbursements/<committee_id>/` (gitignored; one envelope per cycle/page, GOVERNANCE.md §1.4)
- **idempotency**: per-(committee, cycle) 30-day freshness gate; DELETE-then-INSERT per cycle so FEC retroactive amendments supersede (§1.5)
- **data notes**: all rows `recipient_kind=committee` (FEC's by_recipient response is committee-keyed; no candidate_id populated) · `2780` negative `total_amount` rows = refunds/returned disbursements (legitimate FEC net aggregates), retained as-is · names + amounts only, NO editorial/legislative linkage (GOVERNANCE.md §6 — that is Phase 3)
- **provenance note**: the backfill ran via a local self-healing wrapper (`caffeinate` + auto-lock-clear + retry); completed in 1 clean pass (rc=0). The wrapper is an operational helper, not part of the committed pipeline.

### 2026-05-31 — DELETION — reclassify middleton-john

- **entity_slug**: `middleton-john`
- **reason**: Dock Street residue: confirm John S. Middleton donations via Dock Street Capital (his family office) + King of Prussia signals
- **rows_deleted_donations**: `54`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-31T16-43-16Z__pre-reclassify-middleton-john.db`
- **note**: Rows are recoverable from the snapshot above and from data/raw/middleton-john/ payloads. Re-classification follows in the next INGESTION entry.

### 2026-05-31 — INGESTION

- **run_id**: `438126ab`
- **entity_slug**: `middleton-john`
- **dry_run**: `0`
- **period_start**: `2024-11-23`
- **period_end**: `2025-12-29`
- **name_variants_queried**: `["John Middleton", "John S. Middleton", "John S Middleton", "John Staubus Middleton", "Middleton, John", "Middleton, John S", "Middleton, John S."]`
- **api_calls_made**: `0`
- **records_fetched**: `100`
- **confirmed_count**: `51`
- **probable_count**: `9`
- **uncertain_count**: `26`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-31T16-43-16Z__438126ab.db`
- **notes**: skipped(no-name-match)=0 · min_date=audit.last_ingestion (−trailing window) · FROM-RAW

### 2026-05-31 — SETUP (Phase 3 scaffolding)

Phase 3 (CHARTER.md §Phase 3 — cross-referencing donations to MLB-relevant
federal legislation, votes, and regulatory actions) is now active. This entry
records the scaffolding only; no donation data in `master.db` was read or
mutated.

- **New DB**: `data/legislation.db` (`scripts/legislation_db.py`, leg schema v1) —
  a SEPARATE SQLite database from `master.db`, deliberately committed as a normal
  (non-LFS) git blob (`.gitattributes` LFS-tracks only `master.db`). Verified
  `git check-attr filter data/legislation.db` → `unspecified`. Holds the neutral,
  sourced index: `legislators` + `legislator_fec_ids` + `legislator_terms`
  (the FEC-candidate-id → Bioguide crosswalk), `bills` + `bill_sponsors`,
  `votes` + `vote_positions`, `policy_events`.
- **Neutrality boundary (GOVERNANCE.md §6, project CLAUDE.md §2)**: the index
  stores neutral facts in a law-librarian's tone; `bills.relevance_basis` is a
  sourced factual reason a bill is indexed, never editorial framing. Interpretation
  lives only in `reports/`.
- **Sources adopted** (SOURCES.md Phase-3 addendum): Tier-1 Congress.gov API +
  House Clerk / Senate roll-call XML + OpenFEC `/candidate/`; Tier-2
  `unitedstates/congress-legislators` crosswalk; Tier-3 GovTrack/OpenSecrets.
  ProPublica Congress API (sunset 2024) not used.
- **Files**: `scripts/legislation_db.py`, `scripts/paths.py` (legislation paths),
  `scripts/cli.py` (`init-legislation`), `.env.example` (`CONGRESS_API_KEY`),
  `.gitignore` (legislation.db journal/WAL), `tests/test_legislation_db.py` (+8).
- **Gates**: `validate` 36 OK / 0 failed / 0 warnings; `pytest` 283 passed.

### 2026-05-31 — INGESTION (legislators crosswalk)

- **source**: `unitedstates/congress-legislators` (https://unitedstates.github.io/congress-legislators/legislators-current.yaml + https://unitedstates.github.io/congress-legislators/legislators-historical.yaml)
- **fetched_at**: `2026-05-31T21:04:02Z`
- **legislators**: `1529`
- **fec_id_links**: `1713`
- **terms**: `9048`
- **only_with_fec**: `True`
- **include_historical**: `True`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-31T21-04-02Z__pre-ingest-legislators.db`
- **note**: Tier-2 entity identification (SOURCES.md Phase-3 addendum). Crosswalk tables are a pure projection of the upstream source — idempotent wipe-and-rebuild. Raw payloads persisted under data/raw/legislation/.

### 2026-05-31 — NOTE — legislator crosswalk coverage (de-risking probe)

Read-only `legislation-coverage` against the freshly-ingested crosswalk. Of **76**
distinct `recipient_candidate_id`s on CONFIRMED+PROBABLE donations in master.db,
**39 (51.3%)** resolve to a legislator via `legislator_fec_ids`; 37 do not.

The unresolved set was Tier-1 cross-checked against OpenFEC `/candidate/<id>/` and
is expected, not a defect — it is dominated by recipients who never cast a
congressional vote our index could join to:

- **Never-seated candidates** (e.g. H8MN08068 Radinovich, H8MN01279 Feehan,
  S2NV00324 Laxalt, H6NY10176 Lander) — lost their races; no Bioguide id, no votes.
- **Presidential committees** (e.g. P80000722 Biden) — no congressional roll calls.
- **Sitting members' campaigns for a *different* office** (e.g. S8TX00285 O'Rourke's
  2018 Senate run, S6MN00499 Craig's 2026 Senate run) — the person served, but this
  candidate id funded a race for an office where they cast no votes. Conservatively
  left unresolved (donation→legislator is matched only on the candidate id actually
  filed, not by person-across-all-ids).

The resolved 39 are exactly the donations to people who held office and could vote —
the joinable universe Phase 3 needs. **Known limitation (documented, not yet acted
on):** person-level resolution across a legislator's multiple FEC ids (House id vs.
Senate-run id) would lift coverage but risks attributing a campaign-for-office-X
donation as influence over office-Y votes; deferred deliberately.

### 2026-05-31 — INGESTION (bills)

- **source**: `congress.gov` (api.congress.gov v3)
- **fetched_at**: `2026-05-31T21:14:10Z`
- **curated_bills_in_set**: `3`
- **bills_enriched**: `3`
- **sponsor_rows**: `47`
- **errors**: `[]`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-31T21-14-10Z__pre-ingest-bills.db`
- **note**: Curated fields (mlb_issue_area, relevance_basis, carried_by_bill_id) sourced from legislation/bills/*.yaml; identity/sponsors/action from Congress.gov (Tier-1). Raw payloads under data/raw/legislation/.

### 2026-05-31 — INGESTION (legislators crosswalk)

- **source**: `unitedstates/congress-legislators` (https://unitedstates.github.io/congress-legislators/legislators-current.yaml + https://unitedstates.github.io/congress-legislators/legislators-historical.yaml)
- **fetched_at**: `2026-05-31T21:23:27Z`
- **legislators**: `1529`
- **fec_id_links**: `1713`
- **terms**: `9048`
- **only_with_fec**: `True`
- **include_historical**: `True`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-31T21-23-27Z__pre-ingest-legislators.db`
- **note**: Tier-2 entity identification (SOURCES.md Phase-3 addendum). Crosswalk tables are a pure projection of the upstream source — idempotent wipe-and-rebuild. Raw payloads persisted under data/raw/legislation/.

### 2026-05-31 — INGESTION (votes)

- **source**: `clerk.house.gov` (EVS XML) + `senate.gov` (LIS XML) — Tier-1 source of record
- **fetched_at**: `2026-05-31T21:23:47Z`
- **roll_calls_in_set**: `2`
- **votes_ingested**: `2`
- **vote_positions**: `530`
- **senate_unmapped (no FEC-crosswalk lis_id)**: `0`
- **errors**: `[]`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-31T21-23-47Z__pre-ingest-votes.db`
- **note**: Vote positions are FEC-neutral facts (who voted Yea/Nay). Senate LIS ids mapped to Bioguide via legislators.lis_id. Raw XML under data/raw/legislation/.

### 2026-05-31 — NOTE — Phase 3 exit criterion met (first published brief)

The Phase 3 exit criterion (CHARTER.md §Phase 3: "at least one publishable brief
generated end-to-end from the joined data") is satisfied.

- **Brief**: `reports/2026-05-31_save-americas-pastime-act.md` — MLB owner donations
  joined to the 2018 roll-call votes that carried the Save America's Pastime Act.
- **Built end-to-end from the join**: owners→donations (master.db) → FEC→Bioguide
  crosswalk → bills+sponsors (Congress.gov) → roll-call positions (Clerk/Senate XML)
  → the neutral query output in `reports/data/save-americas-pastime-act{,-sponsors}.{csv,json}`
  (`policy-join`, read-only).
- **Neutrality preserved**: the brief is labeled interpretation and lives only in
  `reports/`; the data layer carries no framing (GOVERNANCE.md §6). The brief
  reports the honest finding — the join does NOT show a quid-pro-quo (1 of 41
  donations predates the 2018 vote; owner money splits ~50/50 Yea/Nay; zero owner
  donations to SAPA's authors Guthrie/Bustos) — rather than overclaiming.
- master.db untouched throughout Phase 3; legislation.db is a separate non-LFS DB.

### 2026-06-04 — STATE_INGESTION

- **run_id**: `377dcaee`
- **entity_slug**: `angelos-john-p`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `153`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `3`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-10Z__377dcaee.db`
- **notes**: scanned=153

### 2026-06-04 — STATE_INGESTION

- **run_id**: `0d9c32cc`
- **entity_slug**: `attanasio-mark`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `65`
- **confirmed_count**: `7`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-10Z__0d9c32cc.db`
- **notes**: scanned=65

### 2026-06-04 — STATE_INGESTION

- **run_id**: `6d7d3ed1`
- **entity_slug**: `castellini-bob`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `50`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-10Z__6d7d3ed1.db`
- **notes**: scanned=50

### 2026-06-04 — STATE_INGESTION

- **run_id**: `f61821e2`
- **entity_slug**: `castellini-phil`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `50`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-10Z__f61821e2.db`
- **notes**: scanned=50

### 2026-06-04 — STATE_INGESTION

- **run_id**: `6bebb459`
- **entity_slug**: `cohen-steven`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `9343`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `127`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-10Z__6bebb459.db`
- **notes**: scanned=9343

### 2026-06-04 — STATE_INGESTION

- **run_id**: `793b24a8`
- **entity_slug**: `crane-jim`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `3615`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `24`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-11Z__793b24a8.db`
- **notes**: scanned=3615

### 2026-06-04 — STATE_INGESTION

- **run_id**: `efd7a735`
- **entity_slug**: `davis-ray`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `30921`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-11Z__efd7a735.db`
- **notes**: scanned=30921

### 2026-06-04 — STATE_INGESTION

- **run_id**: `e01de070`
- **entity_slug**: `dewitt-bill`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `76067`
- **confirmed_count**: `2`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-12Z__e01de070.db`
- **notes**: scanned=76067

### 2026-06-04 — STATE_INGESTION

- **run_id**: `76a92da0`
- **entity_slug**: `dolan-paul`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `2329`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `20`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-16Z__76a92da0.db`
- **notes**: scanned=2329

### 2026-06-04 — STATE_INGESTION

- **run_id**: `785b0dfd`
- **entity_slug**: `feliciano-jose`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `467`
- **confirmed_count**: `0`
- **probable_count**: `5`
- **uncertain_count**: `5`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-16Z__785b0dfd.db`
- **notes**: scanned=467

### 2026-06-04 — STATE_INGESTION

- **run_id**: `3e754b6c`
- **entity_slug**: `fisher-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `10688`
- **confirmed_count**: `259`
- **probable_count**: `6`
- **uncertain_count**: `151`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-16Z__3e754b6c.db`
- **notes**: scanned=10688

### 2026-06-04 — STATE_INGESTION

- **run_id**: `0e78995f`
- **entity_slug**: `henry-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `39138`
- **confirmed_count**: `2`
- **probable_count**: `0`
- **uncertain_count**: `131`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-16Z__0e78995f.db`
- **notes**: scanned=39138

### 2026-06-04 — STATE_INGESTION

- **run_id**: `84516695`
- **entity_slug**: `ilitch-chris`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `16`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-17Z__84516695.db`
- **notes**: scanned=16

### 2026-06-04 — STATE_INGESTION

- **run_id**: `065cee7b`
- **entity_slug**: `johnson-greg`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `57853`
- **confirmed_count**: `8`
- **probable_count**: `0`
- **uncertain_count**: `467`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-18Z__065cee7b.db`
- **notes**: scanned=57853

### 2026-06-04 — STATE_INGESTION

- **run_id**: `737363c1`
- **entity_slug**: `kendrick-ken`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `30120`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-20Z__737363c1.db`
- **notes**: scanned=30120

### 2026-06-04 — STATE_INGESTION

- **run_id**: `f5bc1dae`
- **entity_slug**: `lerner-mark`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `1029`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `28`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-22Z__f5bc1dae.db`
- **notes**: scanned=1029

### 2026-06-04 — STATE_INGESTION

- **run_id**: `4e6c453b`
- **entity_slug**: `malone-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `3211`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `9`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-22Z__4e6c453b.db`
- **notes**: scanned=3211

### 2026-06-04 — STATE_INGESTION

- **run_id**: `a53474f8`
- **entity_slug**: `mcguirk-terry`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `21`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-22Z__a53474f8.db`
- **notes**: scanned=21

### 2026-06-04 — STATE_INGESTION

- **run_id**: `51a6023a`
- **entity_slug**: `middleton-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `1918`
- **confirmed_count**: `1`
- **probable_count**: `1`
- **uncertain_count**: `14`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-22Z__51a6023a.db`
- **notes**: scanned=1918

### 2026-06-04 — STATE_INGESTION

- **run_id**: `9e6f7341`
- **entity_slug**: `monfort-dick`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `189`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-22Z__9e6f7341.db`
- **notes**: scanned=189

### 2026-06-04 — STATE_INGESTION

- **run_id**: `18e92112`
- **entity_slug**: `moreno-arte`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `9146`
- **confirmed_count**: `2`
- **probable_count**: `0`
- **uncertain_count**: `48`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-23Z__18e92112.db`
- **notes**: scanned=9146

### 2026-06-04 — STATE_INGESTION

- **run_id**: `ddf58ece`
- **entity_slug**: `nutting-bob`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `123`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `31`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-23Z__ddf58ece.db`
- **notes**: scanned=123

### 2026-06-04 — STATE_INGESTION

- **run_id**: `c1a7724a`
- **entity_slug**: `pohlad-joe`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-23Z__c1a7724a.db`
- **notes**: scanned=2

### 2026-06-04 — STATE_INGESTION

- **run_id**: `e65dce04`
- **entity_slug**: `pohlad-tom`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-23Z__e65dce04.db`
- **notes**: scanned=2

### 2026-06-04 — STATE_INGESTION

- **run_id**: `417da937`
- **entity_slug**: `reinsdorf-jerry`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `3`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-23Z__417da937.db`
- **notes**: scanned=3

### 2026-06-04 — STATE_INGESTION

- **run_id**: `960e50cd`
- **entity_slug**: `ricketts-tom`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `163`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `3`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-23Z__960e50cd.db`
- **notes**: scanned=163

### 2026-06-04 — STATE_INGESTION

- **run_id**: `df31fbd7`
- **entity_slug**: `rubenstein-david`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `551`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `4`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-23Z__df31fbd7.db`
- **notes**: scanned=551

### 2026-06-04 — STATE_INGESTION

- **run_id**: `af42fefd`
- **entity_slug**: `seidler-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `100`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-23Z__af42fefd.db`
- **notes**: scanned=100

### 2026-06-04 — STATE_INGESTION

- **run_id**: `1dfc1a1d`
- **entity_slug**: `sherman-bruce`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `4301`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-23Z__1dfc1a1d.db`
- **notes**: scanned=4301

### 2026-06-04 — STATE_INGESTION

- **run_id**: `7894c098`
- **entity_slug**: `sherman-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `4301`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `76`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-23Z__7894c098.db`
- **notes**: scanned=4301

### 2026-06-04 — STATE_INGESTION

- **run_id**: `47d6249f`
- **entity_slug**: `simpson-bob`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `5998`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-23Z__47d6249f.db`
- **notes**: scanned=5998

### 2026-06-04 — STATE_INGESTION

- **run_id**: `601ca75c`
- **entity_slug**: `stanton-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `1656`
- **confirmed_count**: `2`
- **probable_count**: `0`
- **uncertain_count**: `62`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-23Z__601ca75c.db`
- **notes**: scanned=1656

### 2026-06-04 — STATE_INGESTION

- **run_id**: `1f6af79c`
- **entity_slug**: `steinbrenner-hal`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `19`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-23Z__1f6af79c.db`
- **notes**: scanned=19

### 2026-06-04 — STATE_INGESTION

- **run_id**: `a6aa57dd`
- **entity_slug**: `walter-mark`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `7377`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-04T02-17-23Z__a6aa57dd.db`
- **notes**: scanned=7377

### 2026-06-04 — STATE_INGESTION

- **run_id**: `987631f6`
- **entity_slug**: `feliciano-jose`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `reclassify: Phase-4 CA calibration: added Tennenbaum/Special Value employer signals`
- **records_scanned**: `467`
- **confirmed_count**: `5`
- **probable_count**: `0`
- **uncertain_count**: `5`
- **snapshot_path**: `data/snapshots/2026-06-04T03-39-16Z__987631f6.db`
- **notes**: scanned=467

### 2026-06-04 — STATE_INGESTION

- **run_id**: `04806aaa`
- **entity_slug**: `angelos-john-p`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `153`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `3`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-07Z__04806aaa.db`
- **notes**: scanned=153

### 2026-06-04 — STATE_INGESTION

- **run_id**: `66dee004`
- **entity_slug**: `attanasio-mark`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `65`
- **confirmed_count**: `7`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-07Z__66dee004.db`
- **notes**: scanned=65

### 2026-06-04 — STATE_INGESTION

- **run_id**: `1d69d393`
- **entity_slug**: `castellini-bob`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `50`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-08Z__1d69d393.db`
- **notes**: scanned=50

### 2026-06-04 — STATE_INGESTION

- **run_id**: `3afde5b8`
- **entity_slug**: `castellini-phil`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `50`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-08Z__3afde5b8.db`
- **notes**: scanned=50

### 2026-06-04 — STATE_INGESTION

- **run_id**: `94fc22da`
- **entity_slug**: `cohen-steven`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `9343`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `127`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-08Z__94fc22da.db`
- **notes**: scanned=9343

### 2026-06-04 — STATE_INGESTION

- **run_id**: `ef54a623`
- **entity_slug**: `crane-jim`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `3615`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `24`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-09Z__ef54a623.db`
- **notes**: scanned=3615

### 2026-06-04 — STATE_INGESTION

- **run_id**: `b89e2bed`
- **entity_slug**: `davis-ray`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `30921`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-09Z__b89e2bed.db`
- **notes**: scanned=30921

### 2026-06-04 — STATE_INGESTION

- **run_id**: `9e8c829e`
- **entity_slug**: `dewitt-bill`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `76067`
- **confirmed_count**: `2`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-11Z__9e8c829e.db`
- **notes**: scanned=76067

### 2026-06-04 — STATE_INGESTION

- **run_id**: `56c0933e`
- **entity_slug**: `dolan-paul`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `2329`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `20`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-18Z__56c0933e.db`
- **notes**: scanned=2329

### 2026-06-04 — STATE_INGESTION

- **run_id**: `851613ec`
- **entity_slug**: `feliciano-jose`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `467`
- **confirmed_count**: `5`
- **probable_count**: `0`
- **uncertain_count**: `5`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-18Z__851613ec.db`
- **notes**: scanned=467

### 2026-06-04 — STATE_INGESTION

- **run_id**: `e2bbd7f4`
- **entity_slug**: `fisher-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `10688`
- **confirmed_count**: `259`
- **probable_count**: `6`
- **uncertain_count**: `151`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-18Z__e2bbd7f4.db`
- **notes**: scanned=10688

### 2026-06-04 — STATE_INGESTION

- **run_id**: `930c7774`
- **entity_slug**: `henry-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `39138`
- **confirmed_count**: `2`
- **probable_count**: `0`
- **uncertain_count**: `131`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-19Z__930c7774.db`
- **notes**: scanned=39138

### 2026-06-04 — STATE_INGESTION

- **run_id**: `a356ddca`
- **entity_slug**: `ilitch-chris`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `16`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-22Z__a356ddca.db`
- **notes**: scanned=16

### 2026-06-04 — STATE_INGESTION

- **run_id**: `b4cd1b9e`
- **entity_slug**: `johnson-greg`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `57853`
- **confirmed_count**: `8`
- **probable_count**: `0`
- **uncertain_count**: `467`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-22Z__b4cd1b9e.db`
- **notes**: scanned=57853

### 2026-06-04 — STATE_INGESTION

- **run_id**: `54659fb9`
- **entity_slug**: `kendrick-ken`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `30120`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-27Z__54659fb9.db`
- **notes**: scanned=30120

### 2026-06-04 — STATE_INGESTION

- **run_id**: `b9c13c50`
- **entity_slug**: `lerner-mark`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `1029`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `28`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-32Z__b9c13c50.db`
- **notes**: scanned=1029

### 2026-06-04 — STATE_INGESTION

- **run_id**: `34d68b37`
- **entity_slug**: `malone-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `3211`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `9`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-32Z__34d68b37.db`
- **notes**: scanned=3211

### 2026-06-04 — STATE_INGESTION

- **run_id**: `6a75a5a2`
- **entity_slug**: `mcguirk-terry`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `21`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-32Z__6a75a5a2.db`
- **notes**: scanned=21

### 2026-06-04 — STATE_INGESTION

- **run_id**: `fdc98a8e`
- **entity_slug**: `middleton-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `1918`
- **confirmed_count**: `1`
- **probable_count**: `1`
- **uncertain_count**: `14`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-32Z__fdc98a8e.db`
- **notes**: scanned=1918

### 2026-06-04 — STATE_INGESTION

- **run_id**: `8941142d`
- **entity_slug**: `monfort-dick`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `189`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-32Z__8941142d.db`
- **notes**: scanned=189

### 2026-06-04 — STATE_INGESTION

- **run_id**: `9b113d2b`
- **entity_slug**: `moreno-arte`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `9146`
- **confirmed_count**: `2`
- **probable_count**: `0`
- **uncertain_count**: `48`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-32Z__9b113d2b.db`
- **notes**: scanned=9146

### 2026-06-04 — STATE_INGESTION

- **run_id**: `683908f0`
- **entity_slug**: `nutting-bob`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `123`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `31`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-33Z__683908f0.db`
- **notes**: scanned=123

### 2026-06-04 — STATE_INGESTION

- **run_id**: `71bb513c`
- **entity_slug**: `pohlad-joe`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-33Z__71bb513c.db`
- **notes**: scanned=2

### 2026-06-04 — STATE_INGESTION

- **run_id**: `ec2492da`
- **entity_slug**: `pohlad-tom`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-33Z__ec2492da.db`
- **notes**: scanned=2

### 2026-06-04 — STATE_INGESTION

- **run_id**: `b333c630`
- **entity_slug**: `reinsdorf-jerry`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `3`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-33Z__b333c630.db`
- **notes**: scanned=3

### 2026-06-04 — STATE_INGESTION

- **run_id**: `9196cdf4`
- **entity_slug**: `ricketts-tom`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `163`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `3`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-33Z__9196cdf4.db`
- **notes**: scanned=163

### 2026-06-04 — STATE_INGESTION

- **run_id**: `92d12fb2`
- **entity_slug**: `rubenstein-david`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `551`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `4`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-33Z__92d12fb2.db`
- **notes**: scanned=551

### 2026-06-04 — STATE_INGESTION

- **run_id**: `043415d1`
- **entity_slug**: `seidler-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `100`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-33Z__043415d1.db`
- **notes**: scanned=100

### 2026-06-04 — STATE_INGESTION

- **run_id**: `73be9cde`
- **entity_slug**: `sherman-bruce`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `4301`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-33Z__73be9cde.db`
- **notes**: scanned=4301

### 2026-06-04 — STATE_INGESTION

- **run_id**: `08c35ed1`
- **entity_slug**: `sherman-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `4301`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `76`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-33Z__08c35ed1.db`
- **notes**: scanned=4301

### 2026-06-04 — STATE_INGESTION

- **run_id**: `ee180cb0`
- **entity_slug**: `simpson-bob`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `5998`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-33Z__ee180cb0.db`
- **notes**: scanned=5998

### 2026-06-04 — STATE_INGESTION

- **run_id**: `3af365e6`
- **entity_slug**: `stanton-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `1656`
- **confirmed_count**: `2`
- **probable_count**: `0`
- **uncertain_count**: `62`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-34Z__3af365e6.db`
- **notes**: scanned=1656

### 2026-06-04 — STATE_INGESTION

- **run_id**: `05db00dc`
- **entity_slug**: `steinbrenner-hal`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `19`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-34Z__05db00dc.db`
- **notes**: scanned=19

### 2026-06-04 — STATE_INGESTION

- **run_id**: `bf8db5a5`
- **entity_slug**: `walter-mark`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `7377`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-04T04-15-34Z__bf8db5a5.db`
- **notes**: scanned=7377

### 2026-06-06 — STATE_INGESTION

- **run_id**: `ceebd5ab`
- **entity_slug**: `cohen-steven`
- **jurisdiction**: `NY`
- **source**: `NYSBOE`
- **extract_label**: `https://data.ny.gov/resource/4j2b-6a2j.json`
- **records_scanned**: `583`
- **confirmed_count**: `42`
- **probable_count**: `0`
- **uncertain_count**: `541`
- **snapshot_path**: `data/snapshots/2026-06-06T18-39-37Z__ceebd5ab.db`
- **notes**: scanned=583

### 2026-06-06 — STATE_INGESTION

- **run_id**: `b3c29881`
- **entity_slug**: `steinbrenner-hal`
- **jurisdiction**: `NY`
- **source**: `NYSBOE`
- **extract_label**: `https://data.ny.gov/resource/4j2b-6a2j.json`
- **records_scanned**: `3`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `3`
- **snapshot_path**: `data/snapshots/2026-06-06T18-39-37Z__b3c29881.db`
- **notes**: scanned=3

### 2026-06-06 — STATE_INGESTION

- **run_id**: `c85f9ce8`
- **entity_slug**: `angelos-john-p`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `388`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-34Z__c85f9ce8.db`
- **notes**: scanned=388

### 2026-06-06 — STATE_INGESTION

- **run_id**: `e5c3263d`
- **entity_slug**: `attanasio-mark`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `36`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-34Z__e5c3263d.db`
- **notes**: scanned=36

### 2026-06-06 — STATE_INGESTION

- **run_id**: `afedacac`
- **entity_slug**: `castellini-bob`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `3`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-34Z__afedacac.db`
- **notes**: scanned=3

### 2026-06-06 — STATE_INGESTION

- **run_id**: `07932218`
- **entity_slug**: `castellini-phil`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `3`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-34Z__07932218.db`
- **notes**: scanned=3

### 2026-06-06 — STATE_INGESTION

- **run_id**: `9a997904`
- **entity_slug**: `cohen-steven`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `14554`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `270`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-34Z__9a997904.db`
- **notes**: scanned=14554

### 2026-06-06 — STATE_INGESTION

- **run_id**: `a4667684`
- **entity_slug**: `crane-jim`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `6785`
- **confirmed_count**: `12`
- **probable_count**: `7`
- **uncertain_count**: `37`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-35Z__a4667684.db`
- **notes**: scanned=6785

### 2026-06-06 — STATE_INGESTION

- **run_id**: `605103b0`
- **entity_slug**: `davis-ray`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `143683`
- **confirmed_count**: `77`
- **probable_count**: `13`
- **uncertain_count**: `203`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-35Z__605103b0.db`
- **notes**: scanned=143683

### 2026-06-06 — STATE_INGESTION

- **run_id**: `e7e83e81`
- **entity_slug**: `dewitt-bill`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `130080`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `3`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-39Z__e7e83e81.db`
- **notes**: scanned=130080

### 2026-06-06 — STATE_INGESTION

- **run_id**: `2c0767a7`
- **entity_slug**: `dolan-paul`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `2566`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `10`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-45Z__2c0767a7.db`
- **notes**: scanned=2566

### 2026-06-06 — STATE_INGESTION

- **run_id**: `29b4c43c`
- **entity_slug**: `feliciano-jose`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `401`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `15`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-45Z__29b4c43c.db`
- **notes**: scanned=401

### 2026-06-06 — STATE_INGESTION

- **run_id**: `9c17178a`
- **entity_slug**: `fisher-john`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `29473`
- **confirmed_count**: `1`
- **probable_count**: `1`
- **uncertain_count**: `582`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-45Z__9c17178a.db`
- **notes**: scanned=29473

### 2026-06-06 — STATE_INGESTION

- **run_id**: `b8c5340a`
- **entity_slug**: `henry-john`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `77788`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `762`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-46Z__b8c5340a.db`
- **notes**: scanned=77788

### 2026-06-06 — STATE_INGESTION

- **run_id**: `94c49344`
- **entity_slug**: `ilitch-chris`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-49Z__94c49344.db`
- **notes**: scanned=2

### 2026-06-06 — STATE_INGESTION

- **run_id**: `faa5eb7f`
- **entity_slug**: `johnson-greg`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `201552`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `879`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-49Z__faa5eb7f.db`
- **notes**: scanned=201552

### 2026-06-06 — STATE_INGESTION

- **run_id**: `b83d3a89`
- **entity_slug**: `kendrick-ken`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `42306`
- **confirmed_count**: `1`
- **probable_count**: `1`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-56Z__b83d3a89.db`
- **notes**: scanned=42306

### 2026-06-06 — STATE_INGESTION

- **run_id**: `132f56d1`
- **entity_slug**: `lerner-mark`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `1346`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `11`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-59Z__132f56d1.db`
- **notes**: scanned=1346

### 2026-06-06 — STATE_INGESTION

- **run_id**: `05893a08`
- **entity_slug**: `malone-john`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `16747`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `44`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-59Z__05893a08.db`
- **notes**: scanned=16747

### 2026-06-06 — STATE_INGESTION

- **run_id**: `e49fe2a7`
- **entity_slug**: `mcguirk-terry`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `88`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-59Z__e49fe2a7.db`
- **notes**: scanned=88

### 2026-06-06 — STATE_INGESTION

- **run_id**: `e3ac863b`
- **entity_slug**: `middleton-john`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `7263`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `181`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-59Z__e3ac863b.db`
- **notes**: scanned=7263

### 2026-06-06 — STATE_INGESTION

- **run_id**: `7e517491`
- **entity_slug**: `monfort-dick`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `149`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T20-58-59Z__7e517491.db`
- **notes**: scanned=149

### 2026-06-06 — STATE_INGESTION

- **run_id**: `32de36a3`
- **entity_slug**: `moreno-arte`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `24727`
- **confirmed_count**: `0`
- **probable_count**: `1`
- **uncertain_count**: `182`
- **snapshot_path**: `data/snapshots/2026-06-06T20-59-00Z__32de36a3.db`
- **notes**: scanned=24727

### 2026-06-06 — STATE_INGESTION

- **run_id**: `4f9b1b13`
- **entity_slug**: `nutting-bob`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `233`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `24`
- **snapshot_path**: `data/snapshots/2026-06-06T20-59-00Z__4f9b1b13.db`
- **notes**: scanned=233

### 2026-06-06 — STATE_INGESTION

- **run_id**: `d219cfcc`
- **entity_slug**: `pohlad-joe`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `12`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T20-59-00Z__d219cfcc.db`
- **notes**: scanned=12

### 2026-06-06 — STATE_INGESTION

- **run_id**: `847d343f`
- **entity_slug**: `pohlad-tom`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `12`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T20-59-00Z__847d343f.db`
- **notes**: scanned=12

### 2026-06-06 — STATE_INGESTION

- **run_id**: `bc6067af`
- **entity_slug**: `reinsdorf-jerry`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `4`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T20-59-00Z__bc6067af.db`
- **notes**: scanned=4

### 2026-06-06 — STATE_INGESTION

- **run_id**: `d5ecccf6`
- **entity_slug**: `ricketts-tom`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `1807`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T20-59-00Z__d5ecccf6.db`
- **notes**: scanned=1807

### 2026-06-06 — STATE_INGESTION

- **run_id**: `ba2fb2de`
- **entity_slug**: `rubenstein-david`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `867`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `26`
- **snapshot_path**: `data/snapshots/2026-06-06T20-59-00Z__ba2fb2de.db`
- **notes**: scanned=867

### 2026-06-06 — STATE_INGESTION

- **run_id**: `1353b171`
- **entity_slug**: `seidler-john`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `443`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T20-59-00Z__1353b171.db`
- **notes**: scanned=443

### 2026-06-06 — STATE_INGESTION

- **run_id**: `3b347f28`
- **entity_slug**: `sherman-bruce`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `10853`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `10`
- **snapshot_path**: `data/snapshots/2026-06-06T20-59-00Z__3b347f28.db`
- **notes**: scanned=10853

### 2026-06-06 — STATE_INGESTION

- **run_id**: `c9fd5a3b`
- **entity_slug**: `sherman-john`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `10853`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `86`
- **snapshot_path**: `data/snapshots/2026-06-06T20-59-01Z__c9fd5a3b.db`
- **notes**: scanned=10853

### 2026-06-06 — STATE_INGESTION

- **run_id**: `33d56d78`
- **entity_slug**: `simpson-bob`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `22438`
- **confirmed_count**: `1`
- **probable_count**: `1`
- **uncertain_count**: `6`
- **snapshot_path**: `data/snapshots/2026-06-06T20-59-01Z__33d56d78.db`
- **notes**: scanned=22438

### 2026-06-06 — STATE_INGESTION

- **run_id**: `c8553c36`
- **entity_slug**: `stanton-john`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `2741`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `8`
- **snapshot_path**: `data/snapshots/2026-06-06T20-59-01Z__c8553c36.db`
- **notes**: scanned=2741

### 2026-06-06 — STATE_INGESTION

- **run_id**: `e79375b9`
- **entity_slug**: `steinbrenner-hal`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `6`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T20-59-01Z__e79375b9.db`
- **notes**: scanned=6

### 2026-06-06 — STATE_INGESTION

- **run_id**: `4c267d39`
- **entity_slug**: `walter-mark`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `19970`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `6`
- **snapshot_path**: `data/snapshots/2026-06-06T20-59-01Z__4c267d39.db`
- **notes**: scanned=19970

### 2026-06-06 — STATE_INGESTION

- **run_id**: `729f4b79`
- **entity_slug**: `angelos-john-p`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `153`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `3`
- **snapshot_path**: `data/snapshots/2026-06-06T21-17-36Z__729f4b79.db`
- **notes**: scanned=153

### 2026-06-06 — STATE_INGESTION

- **run_id**: `c56c3e11`
- **entity_slug**: `attanasio-mark`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `65`
- **confirmed_count**: `7`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-06T21-17-36Z__c56c3e11.db`
- **notes**: scanned=65

### 2026-06-06 — STATE_INGESTION

- **run_id**: `a3f25c0d`
- **entity_slug**: `castellini-bob`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `50`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-17-36Z__a3f25c0d.db`
- **notes**: scanned=50

### 2026-06-06 — STATE_INGESTION

- **run_id**: `63df78d4`
- **entity_slug**: `castellini-phil`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `50`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-17-36Z__63df78d4.db`
- **notes**: scanned=50

### 2026-06-06 — STATE_INGESTION

- **run_id**: `32d31103`
- **entity_slug**: `cohen-steven`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `9343`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `127`
- **snapshot_path**: `data/snapshots/2026-06-06T21-17-36Z__32d31103.db`
- **notes**: scanned=9343

### 2026-06-06 — STATE_INGESTION

- **run_id**: `8660e2a2`
- **entity_slug**: `crane-jim`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `3615`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `24`
- **snapshot_path**: `data/snapshots/2026-06-06T21-17-37Z__8660e2a2.db`
- **notes**: scanned=3615

### 2026-06-06 — STATE_INGESTION

- **run_id**: `c3d9da3b`
- **entity_slug**: `davis-ray`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `30922`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-17-37Z__c3d9da3b.db`
- **notes**: scanned=30922

### 2026-06-06 — STATE_INGESTION

- **run_id**: `2979e087`
- **entity_slug**: `dewitt-bill`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `76067`
- **confirmed_count**: `2`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-06T21-17-39Z__2979e087.db`
- **notes**: scanned=76067

### 2026-06-06 — STATE_INGESTION

- **run_id**: `4f539a6e`
- **entity_slug**: `dolan-paul`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `2329`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `20`
- **snapshot_path**: `data/snapshots/2026-06-06T21-17-46Z__4f539a6e.db`
- **notes**: scanned=2329

### 2026-06-06 — STATE_INGESTION

- **run_id**: `d0b7d68f`
- **entity_slug**: `feliciano-jose`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `467`
- **confirmed_count**: `5`
- **probable_count**: `0`
- **uncertain_count**: `5`
- **snapshot_path**: `data/snapshots/2026-06-06T21-17-47Z__d0b7d68f.db`
- **notes**: scanned=467

### 2026-06-06 — STATE_INGESTION

- **run_id**: `04dd3513`
- **entity_slug**: `fisher-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `10688`
- **confirmed_count**: `259`
- **probable_count**: `6`
- **uncertain_count**: `151`
- **snapshot_path**: `data/snapshots/2026-06-06T21-17-47Z__04dd3513.db`
- **notes**: scanned=10688

### 2026-06-06 — STATE_INGESTION

- **run_id**: `4fbc0606`
- **entity_slug**: `henry-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `39138`
- **confirmed_count**: `2`
- **probable_count**: `0`
- **uncertain_count**: `131`
- **snapshot_path**: `data/snapshots/2026-06-06T21-17-48Z__4fbc0606.db`
- **notes**: scanned=39138

### 2026-06-06 — STATE_INGESTION

- **run_id**: `4380a51f`
- **entity_slug**: `ilitch-chris`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `16`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-17-50Z__4380a51f.db`
- **notes**: scanned=16

### 2026-06-06 — STATE_INGESTION

- **run_id**: `2085486d`
- **entity_slug**: `johnson-greg`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `57853`
- **confirmed_count**: `8`
- **probable_count**: `0`
- **uncertain_count**: `467`
- **snapshot_path**: `data/snapshots/2026-06-06T21-17-50Z__2085486d.db`
- **notes**: scanned=57853

### 2026-06-06 — STATE_INGESTION

- **run_id**: `d7336c87`
- **entity_slug**: `kendrick-ken`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `30120`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-17-55Z__d7336c87.db`
- **notes**: scanned=30120

### 2026-06-06 — STATE_INGESTION

- **run_id**: `c87816e0`
- **entity_slug**: `lerner-mark`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `1029`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `28`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-00Z__c87816e0.db`
- **notes**: scanned=1029

### 2026-06-06 — STATE_INGESTION

- **run_id**: `f30e001f`
- **entity_slug**: `malone-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `3211`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `9`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-00Z__f30e001f.db`
- **notes**: scanned=3211

### 2026-06-06 — STATE_INGESTION

- **run_id**: `be078fac`
- **entity_slug**: `mcguirk-terry`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `21`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-00Z__be078fac.db`
- **notes**: scanned=21

### 2026-06-06 — STATE_INGESTION

- **run_id**: `4106a882`
- **entity_slug**: `middleton-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `1918`
- **confirmed_count**: `1`
- **probable_count**: `1`
- **uncertain_count**: `14`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-00Z__4106a882.db`
- **notes**: scanned=1918

### 2026-06-06 — STATE_INGESTION

- **run_id**: `434b0993`
- **entity_slug**: `monfort-dick`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `189`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-00Z__434b0993.db`
- **notes**: scanned=189

### 2026-06-06 — STATE_INGESTION

- **run_id**: `78135799`
- **entity_slug**: `moreno-arte`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `9146`
- **confirmed_count**: `2`
- **probable_count**: `0`
- **uncertain_count**: `48`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-00Z__78135799.db`
- **notes**: scanned=9146

### 2026-06-06 — STATE_INGESTION

- **run_id**: `88e93d94`
- **entity_slug**: `nutting-bob`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `123`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `31`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-01Z__88e93d94.db`
- **notes**: scanned=123

### 2026-06-06 — STATE_INGESTION

- **run_id**: `6531e1a3`
- **entity_slug**: `pohlad-joe`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-01Z__6531e1a3.db`
- **notes**: scanned=2

### 2026-06-06 — STATE_INGESTION

- **run_id**: `f7dc9a87`
- **entity_slug**: `pohlad-tom`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-01Z__f7dc9a87.db`
- **notes**: scanned=2

### 2026-06-06 — STATE_INGESTION

- **run_id**: `a06cc581`
- **entity_slug**: `reinsdorf-jerry`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `3`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-01Z__a06cc581.db`
- **notes**: scanned=3

### 2026-06-06 — STATE_INGESTION

- **run_id**: `3dc7d192`
- **entity_slug**: `ricketts-tom`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `163`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `3`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-01Z__3dc7d192.db`
- **notes**: scanned=163

### 2026-06-06 — STATE_INGESTION

- **run_id**: `974aefea`
- **entity_slug**: `rubenstein-david`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `551`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `4`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-01Z__974aefea.db`
- **notes**: scanned=551

### 2026-06-06 — STATE_INGESTION

- **run_id**: `14184f4b`
- **entity_slug**: `seidler-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `100`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-01Z__14184f4b.db`
- **notes**: scanned=100

### 2026-06-06 — STATE_INGESTION

- **run_id**: `dfcabca5`
- **entity_slug**: `sherman-bruce`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `4301`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-01Z__dfcabca5.db`
- **notes**: scanned=4301

### 2026-06-06 — STATE_INGESTION

- **run_id**: `e00f5dc6`
- **entity_slug**: `sherman-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `4301`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `76`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-01Z__e00f5dc6.db`
- **notes**: scanned=4301

### 2026-06-06 — STATE_INGESTION

- **run_id**: `e3862355`
- **entity_slug**: `simpson-bob`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `5998`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-02Z__e3862355.db`
- **notes**: scanned=5998

### 2026-06-06 — STATE_INGESTION

- **run_id**: `a62f79a5`
- **entity_slug**: `stanton-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `1656`
- **confirmed_count**: `2`
- **probable_count**: `0`
- **uncertain_count**: `62`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-02Z__a62f79a5.db`
- **notes**: scanned=1656

### 2026-06-06 — STATE_INGESTION

- **run_id**: `129b525b`
- **entity_slug**: `steinbrenner-hal`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `19`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-02Z__129b525b.db`
- **notes**: scanned=19

### 2026-06-06 — STATE_INGESTION

- **run_id**: `c48ab0a6`
- **entity_slug**: `walter-mark`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `dbwebexport.zip`
- **records_scanned**: `7377`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-06T21-18-02Z__c48ab0a6.db`
- **notes**: scanned=7377

### 2026-06-06 — STATE_INGESTION

- **run_id**: `e86ee8fc`
- **entity_slug**: `angelos-john-p`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `388`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-28-48Z__e86ee8fc.db`
- **notes**: scanned=388

### 2026-06-06 — STATE_INGESTION

- **run_id**: `f5e03e80`
- **entity_slug**: `attanasio-mark`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `36`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-28-48Z__f5e03e80.db`
- **notes**: scanned=36

### 2026-06-06 — STATE_INGESTION

- **run_id**: `1992cb18`
- **entity_slug**: `castellini-bob`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `3`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-28-48Z__1992cb18.db`
- **notes**: scanned=3

### 2026-06-06 — STATE_INGESTION

- **run_id**: `cd49737a`
- **entity_slug**: `castellini-phil`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `3`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-28-48Z__cd49737a.db`
- **notes**: scanned=3

### 2026-06-06 — STATE_INGESTION

- **run_id**: `228e12ab`
- **entity_slug**: `cohen-steven`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `14554`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `270`
- **snapshot_path**: `data/snapshots/2026-06-06T21-28-48Z__228e12ab.db`
- **notes**: scanned=14554

### 2026-06-06 — STATE_INGESTION

- **run_id**: `5d8fb93f`
- **entity_slug**: `crane-jim`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `6785`
- **confirmed_count**: `12`
- **probable_count**: `7`
- **uncertain_count**: `37`
- **snapshot_path**: `data/snapshots/2026-06-06T21-28-49Z__5d8fb93f.db`
- **notes**: scanned=6785

### 2026-06-06 — STATE_INGESTION

- **run_id**: `7d138492`
- **entity_slug**: `davis-ray`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `143683`
- **confirmed_count**: `77`
- **probable_count**: `13`
- **uncertain_count**: `203`
- **snapshot_path**: `data/snapshots/2026-06-06T21-28-50Z__7d138492.db`
- **notes**: scanned=143683

### 2026-06-06 — STATE_INGESTION

- **run_id**: `a9020f69`
- **entity_slug**: `dewitt-bill`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `130080`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `3`
- **snapshot_path**: `data/snapshots/2026-06-06T21-28-57Z__a9020f69.db`
- **notes**: scanned=130080

### 2026-06-06 — STATE_INGESTION

- **run_id**: `53d6296e`
- **entity_slug**: `dolan-paul`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `2566`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `10`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-10Z__53d6296e.db`
- **notes**: scanned=2566

### 2026-06-06 — STATE_INGESTION

- **run_id**: `70d570a8`
- **entity_slug**: `feliciano-jose`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `401`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `15`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-10Z__70d570a8.db`
- **notes**: scanned=401

### 2026-06-06 — STATE_INGESTION

- **run_id**: `0e7eb62b`
- **entity_slug**: `fisher-john`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `29473`
- **confirmed_count**: `1`
- **probable_count**: `1`
- **uncertain_count**: `582`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-10Z__0e7eb62b.db`
- **notes**: scanned=29473

### 2026-06-06 — STATE_INGESTION

- **run_id**: `24e178d7`
- **entity_slug**: `henry-john`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `77788`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `762`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-12Z__24e178d7.db`
- **notes**: scanned=77788

### 2026-06-06 — STATE_INGESTION

- **run_id**: `661506f1`
- **entity_slug**: `ilitch-chris`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-17Z__661506f1.db`
- **notes**: scanned=2

### 2026-06-06 — STATE_INGESTION

- **run_id**: `b753a8fa`
- **entity_slug**: `johnson-greg`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `201552`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `879`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-17Z__b753a8fa.db`
- **notes**: scanned=201552

### 2026-06-06 — STATE_INGESTION

- **run_id**: `acb4f776`
- **entity_slug**: `kendrick-ken`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `42306`
- **confirmed_count**: `1`
- **probable_count**: `1`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-34Z__acb4f776.db`
- **notes**: scanned=42306

### 2026-06-06 — STATE_INGESTION

- **run_id**: `86fb0c44`
- **entity_slug**: `lerner-mark`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `1346`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `11`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-41Z__86fb0c44.db`
- **notes**: scanned=1346

### 2026-06-06 — STATE_INGESTION

- **run_id**: `237a2797`
- **entity_slug**: `malone-john`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `16747`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `44`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-41Z__237a2797.db`
- **notes**: scanned=16747

### 2026-06-06 — STATE_INGESTION

- **run_id**: `d2c6b098`
- **entity_slug**: `mcguirk-terry`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `88`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-42Z__d2c6b098.db`
- **notes**: scanned=88

### 2026-06-06 — STATE_INGESTION

- **run_id**: `dd89d573`
- **entity_slug**: `middleton-john`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `7263`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `181`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-42Z__dd89d573.db`
- **notes**: scanned=7263

### 2026-06-06 — STATE_INGESTION

- **run_id**: `72eab47f`
- **entity_slug**: `monfort-dick`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `149`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-42Z__72eab47f.db`
- **notes**: scanned=149

### 2026-06-06 — STATE_INGESTION

- **run_id**: `b9f6bea1`
- **entity_slug**: `moreno-arte`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `24727`
- **confirmed_count**: `0`
- **probable_count**: `1`
- **uncertain_count**: `182`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-42Z__b9f6bea1.db`
- **notes**: scanned=24727

### 2026-06-06 — STATE_INGESTION

- **run_id**: `71b4cc56`
- **entity_slug**: `nutting-bob`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `233`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `24`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-44Z__71b4cc56.db`
- **notes**: scanned=233

### 2026-06-06 — STATE_INGESTION

- **run_id**: `497510ad`
- **entity_slug**: `pohlad-joe`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `12`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-44Z__497510ad.db`
- **notes**: scanned=12

### 2026-06-06 — STATE_INGESTION

- **run_id**: `0e4d39d4`
- **entity_slug**: `pohlad-tom`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `12`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-44Z__0e4d39d4.db`
- **notes**: scanned=12

### 2026-06-06 — STATE_INGESTION

- **run_id**: `9783775e`
- **entity_slug**: `reinsdorf-jerry`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `4`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-44Z__9783775e.db`
- **notes**: scanned=4

### 2026-06-06 — STATE_INGESTION

- **run_id**: `24df9f8d`
- **entity_slug**: `ricketts-tom`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `1807`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-44Z__24df9f8d.db`
- **notes**: scanned=1807

### 2026-06-06 — STATE_INGESTION

- **run_id**: `2fc7fcce`
- **entity_slug**: `rubenstein-david`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `867`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `26`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-44Z__2fc7fcce.db`
- **notes**: scanned=867

### 2026-06-06 — STATE_INGESTION

- **run_id**: `88bd8980`
- **entity_slug**: `seidler-john`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `443`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-44Z__88bd8980.db`
- **notes**: scanned=443

### 2026-06-06 — STATE_INGESTION

- **run_id**: `7371a24a`
- **entity_slug**: `sherman-bruce`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `10853`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `10`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-44Z__7371a24a.db`
- **notes**: scanned=10853

### 2026-06-06 — STATE_INGESTION

- **run_id**: `4d0fd7ba`
- **entity_slug**: `sherman-john`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `10853`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `86`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-44Z__4d0fd7ba.db`
- **notes**: scanned=10853

### 2026-06-06 — STATE_INGESTION

- **run_id**: `82e5a238`
- **entity_slug**: `simpson-bob`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `22438`
- **confirmed_count**: `1`
- **probable_count**: `1`
- **uncertain_count**: `6`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-45Z__82e5a238.db`
- **notes**: scanned=22438

### 2026-06-06 — STATE_INGESTION

- **run_id**: `e0b4574b`
- **entity_slug**: `stanton-john`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `2741`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `8`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-46Z__e0b4574b.db`
- **notes**: scanned=2741

### 2026-06-06 — STATE_INGESTION

- **run_id**: `f447e0d9`
- **entity_slug**: `steinbrenner-hal`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `6`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-46Z__f447e0d9.db`
- **notes**: scanned=6

### 2026-06-06 — STATE_INGESTION

- **run_id**: `b3446483`
- **entity_slug**: `walter-mark`
- **jurisdiction**: `TX`
- **source**: `TEC`
- **extract_label**: `TEC_CF_CSV.zip`
- **records_scanned**: `19970`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `6`
- **snapshot_path**: `data/snapshots/2026-06-06T21-29-46Z__b3446483.db`
- **notes**: scanned=19970

### 2026-06-06 — STATE_INGESTION

- **run_id**: `7b035ecb`
- **entity_slug**: `fisher-john`
- **jurisdiction**: `CA`
- **source**: `CAL-ACCESS`
- **extract_label**: `reclassify: calibration: city_state_alone_insufficient flag (kill SF one-signal PROBABLE leak)`
- **records_scanned**: `10688`
- **confirmed_count**: `259`
- **probable_count**: `0`
- **uncertain_count**: `157`
- **snapshot_path**: `data/snapshots/2026-06-06T21-57-45Z__7b035ecb.db`
- **notes**: scanned=10688

### 2026-06-06 — INGESTION

- **run_id**: `edf7e1da`
- **entity_slug**: `fisher-john`
- **dry_run**: `0`
- **period_start**: `2000-01-01`
- **period_end**: `2026-04-30`
- **name_variants_queried**: `["John Fisher", "John J. Fisher", "John J Fisher", "John Joseph Fisher", "Fisher, John", "Fisher, John J", "Fisher, John J."]`
- **api_calls_made**: `245`
- **records_fetched**: `3946`
- **confirmed_count**: `569`
- **probable_count**: `0`
- **uncertain_count**: `3284`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-06-06T21-58-14Z__edf7e1da.db`
- **notes**: skipped(no-name-match)=93 · min_date=--full-refetch · states=['CA']

### 2026-06-06 — SUPERSESSION — run edf7e1da

- `3061920110008177836` (fisher-john): FEC restatement: image_number
- `3061920110008066611` (fisher-john): FEC restatement: image_number
- `3061920110007453564` (fisher-john): FEC restatement: image_number
- `3061920110007426764` (fisher-john): FEC restatement: image_number
- `3061920110009701334` (fisher-john): FEC restatement: image_number
- `3061920110009640306` (fisher-john): FEC restatement: image_number
- `3061920110009037030` (fisher-john): FEC restatement: image_number
- `3061920110008614631` (fisher-john): FEC restatement: image_number
- `2110820041045290008` (fisher-john): FEC restatement: image_number
- `2051820071077390483` (fisher-john): FEC restatement: image_number
- `1070820110007402579` (fisher-john): FEC restatement: image_number
- `1070820110006782411` (fisher-john): FEC restatement: image_number
- `2061220071077937941` (fisher-john): FEC restatement: image_number
- `2081020041041240137` (fisher-john): FEC restatement: image_number
- `2050720041038194732` (fisher-john): FEC restatement: image_number
- `1070820110008033357` (fisher-john): FEC restatement: image_number
- `1070820110008002870` (fisher-john): FEC restatement: image_number
- `1070820110007995349` (fisher-john): FEC restatement: image_number
- `1070820110006110802` (fisher-john): FEC restatement: image_number
- `2121920061073117280` (fisher-john): FEC restatement: image_number
- `1070820110008120530` (fisher-john): FEC restatement: image_number
- `1070820110007461441` (fisher-john): FEC restatement: image_number
- `1070820110006099311` (fisher-john): FEC restatement: image_number
- `1070820110007774903` (fisher-john): FEC restatement: image_number
- `2103120061071456669` (fisher-john): FEC restatement: image_number
- `1070820110006266172` (fisher-john): FEC restatement: image_number
- `1070820110008320901` (fisher-john): FEC restatement: image_number
- `2030620091110212145` (fisher-john): FEC restatement: image_number
- `1121520090001216709` (fisher-john): FEC restatement: image_number
- `2121520081102434288` (fisher-john): FEC restatement: image_number
- `2012220091104671005` (fisher-john): FEC restatement: image_number
- `1121520090001976513` (fisher-john): FEC restatement: image_number
- `2021220091107613820` (fisher-john): FEC restatement: image_number
- `1121520090001979369` (fisher-john): FEC restatement: image_number
- `2110720081100568757` (fisher-john): FEC restatement: image_number
- `1121520090001983382` (fisher-john): FEC restatement: image_number
- `1121520090001854090` (fisher-john): FEC restatement: image_number
- `2103020081099864178` (fisher-john): FEC restatement: image_number
- `2103020081099864177` (fisher-john): FEC restatement: image_number
- `1121520090001797049` (fisher-john): FEC restatement: image_number
- `2102320081098411785` (fisher-john): FEC restatement: image_number
- `2030520091108962333` (fisher-john): FEC restatement: image_number
- `1121520090001827886` (fisher-john): FEC restatement: image_number
- `1121520090001736705` (fisher-john): FEC restatement: image_number
- `2103020081099864176` (fisher-john): FEC restatement: image_number
- `2012220091104665234` (fisher-john): FEC restatement: image_number
- `1121520090001640205` (fisher-john): FEC restatement: image_number
- `1121520090001199865` (fisher-john): FEC restatement: image_number
- `2021920081084897921` (fisher-john): FEC restatement: image_number
- `1121520090001802400` (fisher-john): FEC restatement: image_number
- `1042620110005724347` (fisher-john): FEC restatement: image_number
- `1062820110005889474` (fisher-john): FEC restatement: image_number
- `2051920111140062250` (fisher-john): FEC restatement: image_number
- `2120120101132065393` (fisher-john): FEC restatement: image_number
- `2120120101132065041` (fisher-john): FEC restatement: image_number
- `2120120101132062894` (fisher-john): FEC restatement: image_number
- `2041320121154794787` (fisher-john): FEC restatement: image_number
- `1021520110005349830` (fisher-john): FEC restatement: image_number
- `2040720111138010165` (fisher-john): FEC restatement: image_number
- `2020720111135397231` (fisher-john): FEC restatement: image_number
- `1032220120009535877` (fisher-john): FEC restatement: image_number
- `1111520100004858083` (fisher-john): FEC restatement: image_number
- `2082420111142417335` (fisher-john): FEC restatement: image_number
- `2042820101125887741` (fisher-john): FEC restatement: image_number
- `2042820101125887740` (fisher-john): FEC restatement: image_number
- `2051320101126316579` (fisher-john): FEC restatement: image_number
- `2041120111138036394` (fisher-john): FEC restatement: image_number
- `2041120111138036393` (fisher-john): FEC restatement: image_number
- `2021820101123494253` (fisher-john): FEC restatement: image_number
- `2103020091120295543` (fisher-john): FEC restatement: image_number
- `1021220100003889540` (fisher-john): FEC restatement: image_number
- `1112420200264123760` (fisher-john): FEC restatement: image_number
- `2102420111144112692` (fisher-john): FEC restatement: image_number
- `2072720111141740939` (fisher-john): FEC restatement: image_number
- `2072720111141740938` (fisher-john): FEC restatement: image_number
- `2061520111140811844` (fisher-john): FEC restatement: image_number
- `2050420111139653845` (fisher-john): FEC restatement: image_number
- `1120720170036045193` (fisher-john): FEC restatement: image_number
- `1120720170036040138` (fisher-john): FEC restatement: image_number
- `2122220141233554716` (fisher-john): FEC restatement: image_number
- `2061020151245491565` (fisher-john): FEC restatement: image_number
- `2012920151237594011` (fisher-john): FEC restatement: image_number
- `1121920140016514492` (fisher-john): FEC restatement: image_number
- `1052220150017579853` (fisher-john): FEC restatement: image_number
- `1050120150017430994` (fisher-john): FEC restatement: image_number
- `2110620141226866451` (fisher-john): FEC restatement: image_number
- `2091120141221587528` (fisher-john): FEC restatement: image_number
- `2081320141220833373` (fisher-john): FEC restatement: image_number
- `2081320141220833369` (fisher-john): FEC restatement: image_number
- `2081120141220582992` (fisher-john): FEC restatement: image_number
- `2081120141220582991` (fisher-john): FEC restatement: image_number
- `2081120141220582990` (fisher-john): FEC restatement: image_number
- `1010620150016546138` (fisher-john): FEC restatement: image_number
- `2081820141220946468` (fisher-john): FEC restatement: image_number
- `2060220141213970757` (fisher-john): FEC restatement: image_number
- `2050620141212323129` (fisher-john): FEC restatement: image_number
- `2050620141212323083` (fisher-john): FEC restatement: image_number
- `2022720141206113961` (fisher-john): FEC restatement: image_number
- `2022720141206113960` (fisher-john): FEC restatement: image_number
- `2030420141206290068` (fisher-john): FEC restatement: image_number
- `1022520140015221348` (fisher-john): FEC restatement: image_number
- `1022520140015221347` (fisher-john): FEC restatement: image_number
- `2100920141225912546` (fisher-john): FEC restatement: image_number
- `2041620151241955135` (fisher-john): FEC restatement: image_number
- `2111920131199147663` (fisher-john): FEC restatement: image_number
- `1112420200263888944` (fisher-john): FEC restatement: image_number
- `1103120160032410532` (fisher-john): FEC restatement: image_number
- `1103120160032407596` (fisher-john): FEC restatement: image_number
- `1103120160032407595` (fisher-john): FEC restatement: image_number
- `2120220161356604367` (fisher-john): FEC restatement: image_number
- `1110720160032608293` (fisher-john): FEC restatement: image_number
- `2052620161293506618` (fisher-john): FEC restatement: image_number
- `2052620161293505250` (fisher-john): FEC restatement: image_number
- `2050420161291363642` (fisher-john): FEC restatement: image_number
- `2051120161292586269` (fisher-john): FEC restatement: image_number
- `2051620161292826623` (fisher-john): FEC restatement: image_number
- `2031220161276386766` (fisher-john): FEC restatement: image_number
- `2032520161277057028` (fisher-john): FEC restatement: image_number
- `2021320161262948745` (fisher-john): FEC restatement: image_number
- `2062220161300060797` (fisher-john): FEC restatement: image_number
- `2022520161272894574` (fisher-john): FEC restatement: image_number
- `2022520161272894573` (fisher-john): FEC restatement: image_number
- `2022520161272894572` (fisher-john): FEC restatement: image_number
- `1042820160018641815` (fisher-john): FEC restatement: image_number
- `1042820160018639555` (fisher-john): FEC restatement: image_number
- `2022520161272892048` (fisher-john): FEC restatement: image_number
- `2021820161272526720` (fisher-john): FEC restatement: image_number
- `2021120161262926389` (fisher-john): FEC restatement: image_number
- `1072120160028290475` (fisher-john): FEC restatement: image_number
- `2102920151256717078` (fisher-john): FEC restatement: image_number
- `2110220151256749686` (fisher-john): FEC restatement: image_number
- `1012920160018295010` (fisher-john): FEC restatement: image_number
- `2072420151247911226` (fisher-john): FEC restatement: image_number
- `1022520160018429066` (fisher-john): FEC restatement: image_number
- `2072420151247911668` (fisher-john): FEC restatement: image_number
- `2111520151257165497` (fisher-john): FEC restatement: image_number
- `2101420151255588064` (fisher-john): FEC restatement: image_number
- `2073020151248067240` (fisher-john): FEC restatement: image_number
- `2072920151248020358` (fisher-john): FEC restatement: image_number
- `1101920150017958937` (fisher-john): FEC restatement: image_number
- `1112420200263995295` (fisher-john): FEC restatement: image_number
- `1110720180037418737` (fisher-john): FEC restatement: image_number
- `1110720180037418446` (fisher-john): FEC restatement: image_number
- `2072020181576526656` (fisher-john): FEC restatement: image_number
- `2071920181576442315` (fisher-john): FEC restatement: image_number
- `2051020181552120881` (fisher-john): FEC restatement: image_number
- `2051020181552119660` (fisher-john): FEC restatement: image_number
- `2032220181519806962` (fisher-john): FEC restatement: image_number
- `2032220181519805785` (fisher-john): FEC restatement: image_number
- `2080720171442831525` (fisher-john): FEC restatement: image_number
- `2080720171442831524` (fisher-john): FEC restatement: image_number
- `2080520171442763729` (fisher-john): FEC restatement: image_number
- `2080520171442763728` (fisher-john): FEC restatement: image_number
- `2080320171442549631` (fisher-john): FEC restatement: image_number
- `2080320171442549630` (fisher-john): FEC restatement: image_number
- `2080220171442481096` (fisher-john): FEC restatement: image_number
- `2080220171442480944` (fisher-john): FEC restatement: image_number
- `2072820171442304586` (fisher-john): FEC restatement: image_number
- `2072820171442304476` (fisher-john): FEC restatement: image_number
- `1072120170035559028` (fisher-john): FEC restatement: image_number
- `2102720171461394668` (fisher-john): FEC restatement: image_number
- `2071920171426271820` (fisher-john): FEC restatement: image_number
- `2071920171426271819` (fisher-john): FEC restatement: image_number
- `2042620171400504254` (fisher-john): FEC restatement: image_number
- `2042620171400504253` (fisher-john): FEC restatement: image_number
- `2042620171400504252` (fisher-john): FEC restatement: image_number
- `1071720170035539512` (fisher-john): FEC restatement: image_number
- `1071720170035539511` (fisher-john): FEC restatement: image_number
- `2042620171400509007` (fisher-john): FEC restatement: image_number
- `2042620171400509006` (fisher-john): FEC restatement: image_number
- `2022220171372036337` (fisher-john): FEC restatement: image_number
- `IA13` (fisher-john): FEC restatement: image_number
- `IA19456` (fisher-john): FEC restatement: image_number

### 2026-06-06 — RECLASSIFY-IN-PLACE — fisher-john

- **entity_slug**: `fisher-john`
- **reason**: apply city_state_alone_insufficient flag (back-apply; raw incomplete so from-raw reclassify unsafe)
- **rows_scored**: `668`
- **updated**: `0` · **demoted→queue**: `99` · **forced**: `0` · **excluded**: `0` · **unchanged**: `569`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-06-06T23-01-26Z__pre-reclassify-inplace-fisher-john.db`
- **note**: in-place re-score from stored donations columns (no raw read, no delete-rebuild); rows recoverable from the snapshot above.

### 2026-06-07 — STATE_INGESTION

- **run_id**: `73aa8ddb`
- **entity_slug**: `middleton-john`
- **jurisdiction**: `PA`
- **source**: `PA-DOS`
- **extract_label**: `pa`
- **records_scanned**: `2753`
- **confirmed_count**: `24`
- **probable_count**: `0`
- **uncertain_count**: `22`
- **snapshot_path**: `data/snapshots/2026-06-07T00-36-52Z__73aa8ddb.db`
- **notes**: scanned=2753, skipped_no_date=56

### 2026-06-07 — STATE_INGESTION

- **run_id**: `6d25c6b1`
- **entity_slug**: `nutting-bob`
- **jurisdiction**: `PA`
- **source**: `PA-DOS`
- **extract_label**: `pa`
- **records_scanned**: `39`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-07T00-36-52Z__6d25c6b1.db`
- **notes**: scanned=39, skipped_no_date=4

### 2026-06-07 — STATE_INGESTION

- **run_id**: `41770954`
- **entity_slug**: `stanton-john`
- **jurisdiction**: `WA`
- **source**: `WA-PDC`
- **extract_label**: `https://data.wa.gov/resource/kv7h-kjye.json`
- **records_scanned**: `398`
- **confirmed_count**: `194`
- **probable_count**: `9`
- **uncertain_count**: `193`
- **snapshot_path**: `data/snapshots/2026-06-07T02-22-59Z__41770954.db`
- **notes**: scanned=398

### 2026-06-07 — STATE_INGESTION

- **run_id**: `93aa05c2`
- **entity_slug**: `stanton-john`
- **jurisdiction**: `WA`
- **source**: `WA-PDC`
- **extract_label**: `https://data.wa.gov/resource/kv7h-kjye.json`
- **records_scanned**: `398`
- **confirmed_count**: `194`
- **probable_count**: `9`
- **uncertain_count**: `193`
- **snapshot_path**: `data/snapshots/2026-06-07T02-33-48Z__93aa05c2.db`
- **notes**: scanned=398

### 2026-06-08 — STATE_INGESTION

- **run_id**: `b0a5dbb5`
- **entity_slug**: `angelos-john-p`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `89`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-40Z__b0a5dbb5.db`
- **notes**: scanned=89

### 2026-06-08 — STATE_INGESTION

- **run_id**: `9a7fc115`
- **entity_slug**: `attanasio-mark`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `11`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-40Z__9a7fc115.db`
- **notes**: scanned=11

### 2026-06-08 — STATE_INGESTION

- **run_id**: `744ec82b`
- **entity_slug**: `castellini-bob`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `3`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-40Z__744ec82b.db`
- **notes**: scanned=3

### 2026-06-08 — STATE_INGESTION

- **run_id**: `b1a49983`
- **entity_slug**: `castellini-phil`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `3`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-40Z__b1a49983.db`
- **notes**: scanned=3

### 2026-06-08 — STATE_INGESTION

- **run_id**: `807fe4e0`
- **entity_slug**: `cohen-steven`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `2815`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `91`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-40Z__807fe4e0.db`
- **notes**: scanned=2815

### 2026-06-08 — STATE_INGESTION

- **run_id**: `e2a256d3`
- **entity_slug**: `crane-jim`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `4206`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `12`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-41Z__e2a256d3.db`
- **notes**: scanned=4206

### 2026-06-08 — STATE_INGESTION

- **run_id**: `8e2cb3b3`
- **entity_slug**: `davis-ray`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `11179`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-41Z__8e2cb3b3.db`
- **notes**: scanned=11179

### 2026-06-08 — STATE_INGESTION

- **run_id**: `0d4ffda3`
- **entity_slug**: `dewitt-bill`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `41135`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-41Z__0d4ffda3.db`
- **notes**: scanned=41135

### 2026-06-08 — STATE_INGESTION

- **run_id**: `b4f00a92`
- **entity_slug**: `dolan-paul`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `1164`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-43Z__b4f00a92.db`
- **notes**: scanned=1164

### 2026-06-08 — STATE_INGESTION

- **run_id**: `189e87a0`
- **entity_slug**: `feliciano-jose`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `38`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `7`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-43Z__189e87a0.db`
- **notes**: scanned=38

### 2026-06-08 — STATE_INGESTION

- **run_id**: `c7a01c62`
- **entity_slug**: `fisher-john`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `2237`
- **confirmed_count**: `18`
- **probable_count**: `0`
- **uncertain_count**: `88`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-43Z__c7a01c62.db`
- **notes**: scanned=2237

### 2026-06-08 — STATE_INGESTION

- **run_id**: `67bf7c60`
- **entity_slug**: `henry-john`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `27594`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `18`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-43Z__67bf7c60.db`
- **notes**: scanned=27594

### 2026-06-08 — STATE_INGESTION

- **run_id**: `faa83ba3`
- **entity_slug**: `johnson-greg`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `20126`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `23`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-44Z__faa83ba3.db`
- **notes**: scanned=20126

### 2026-06-08 — STATE_INGESTION

- **run_id**: `42f69296`
- **entity_slug**: `kendrick-ken`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `31024`
- **confirmed_count**: `4`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-44Z__42f69296.db`
- **notes**: scanned=31024

### 2026-06-08 — STATE_INGESTION

- **run_id**: `191a30b6`
- **entity_slug**: `lerner-mark`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `1082`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__191a30b6.db`
- **notes**: scanned=1082

### 2026-06-08 — STATE_INGESTION

- **run_id**: `354a1cb5`
- **entity_slug**: `malone-john`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `2440`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `12`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__354a1cb5.db`
- **notes**: scanned=2440

### 2026-06-08 — STATE_INGESTION

- **run_id**: `5a38adc8`
- **entity_slug**: `mcguirk-terry`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `19`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__5a38adc8.db`
- **notes**: scanned=19

### 2026-06-08 — STATE_INGESTION

- **run_id**: `94eb6a77`
- **entity_slug**: `middleton-john`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `265`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__94eb6a77.db`
- **notes**: scanned=265

### 2026-06-08 — STATE_INGESTION

- **run_id**: `f592c01a`
- **entity_slug**: `monfort-dick`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `12`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__f592c01a.db`
- **notes**: scanned=12

### 2026-06-08 — STATE_INGESTION

- **run_id**: `7be5fa3e`
- **entity_slug**: `moreno-arte`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `1151`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__7be5fa3e.db`
- **notes**: scanned=1151

### 2026-06-08 — STATE_INGESTION

- **run_id**: `1ed4d9ed`
- **entity_slug**: `nutting-bob`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__1ed4d9ed.db`
- **notes**: scanned=2

### 2026-06-08 — STATE_INGESTION

- **run_id**: `aed12e4f`
- **entity_slug**: `reinsdorf-jerry`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `145`
- **confirmed_count**: `72`
- **probable_count**: `11`
- **uncertain_count**: `11`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__aed12e4f.db`
- **notes**: scanned=145

### 2026-06-08 — STATE_INGESTION

- **run_id**: `176f0710`
- **entity_slug**: `ricketts-tom`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `646`
- **confirmed_count**: `24`
- **probable_count**: `11`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__176f0710.db`
- **notes**: scanned=646

### 2026-06-08 — STATE_INGESTION

- **run_id**: `12f2a22f`
- **entity_slug**: `rubenstein-david`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `215`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__12f2a22f.db`
- **notes**: scanned=215

### 2026-06-08 — STATE_INGESTION

- **run_id**: `64532deb`
- **entity_slug**: `seidler-john`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `26`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__64532deb.db`
- **notes**: scanned=26

### 2026-06-08 — STATE_INGESTION

- **run_id**: `946ec38e`
- **entity_slug**: `sherman-bruce`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `835`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__946ec38e.db`
- **notes**: scanned=835

### 2026-06-08 — STATE_INGESTION

- **run_id**: `84bfde8f`
- **entity_slug**: `sherman-john`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `835`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__84bfde8f.db`
- **notes**: scanned=835

### 2026-06-08 — STATE_INGESTION

- **run_id**: `39596f0e`
- **entity_slug**: `simpson-bob`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `2316`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__39596f0e.db`
- **notes**: scanned=2316

### 2026-06-08 — STATE_INGESTION

- **run_id**: `2c5cdad9`
- **entity_slug**: `stanton-john`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `590`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `3`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__2c5cdad9.db`
- **notes**: scanned=590

### 2026-06-08 — STATE_INGESTION

- **run_id**: `4c35e6b4`
- **entity_slug**: `steinbrenner-hal`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `4`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__4c35e6b4.db`
- **notes**: scanned=4

### 2026-06-08 — STATE_INGESTION

- **run_id**: `3982d585`
- **entity_slug**: `walter-mark`
- **jurisdiction**: `IL`
- **source**: `ISBE`
- **extract_label**: `il`
- **records_scanned**: `2229`
- **confirmed_count**: `2`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-22-47Z__3982d585.db`
- **notes**: scanned=2229

### 2026-06-08 — STATE_INGESTION

- **run_id**: `d3ad79de`
- **entity_slug**: `angelos-john-p`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `199`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-15Z__d3ad79de.db`
- **notes**: scanned=199

### 2026-06-08 — STATE_INGESTION

- **run_id**: `18e1c456`
- **entity_slug**: `attanasio-mark`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `1`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-15Z__18e1c456.db`
- **notes**: scanned=1

### 2026-06-08 — STATE_INGESTION

- **run_id**: `4e288c6d`
- **entity_slug**: `cohen-steven`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `2423`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `145`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-15Z__4e288c6d.db`
- **notes**: scanned=2423

### 2026-06-08 — STATE_INGESTION

- **run_id**: `85306588`
- **entity_slug**: `crane-jim`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `780`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `11`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-15Z__85306588.db`
- **notes**: scanned=780

### 2026-06-08 — STATE_INGESTION

- **run_id**: `2577f675`
- **entity_slug**: `davis-ray`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `10916`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `3`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-15Z__2577f675.db`
- **notes**: scanned=10916

### 2026-06-08 — STATE_INGESTION

- **run_id**: `a116137f`
- **entity_slug**: `dewitt-bill`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `3766`
- **confirmed_count**: `9`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-15Z__a116137f.db`
- **notes**: scanned=3766

### 2026-06-08 — STATE_INGESTION

- **run_id**: `99dbc794`
- **entity_slug**: `dolan-paul`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `678`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `5`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-16Z__99dbc794.db`
- **notes**: scanned=678

### 2026-06-08 — STATE_INGESTION

- **run_id**: `db1cd105`
- **entity_slug**: `feliciano-jose`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `146`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-16Z__db1cd105.db`
- **notes**: scanned=146

### 2026-06-08 — STATE_INGESTION

- **run_id**: `4f192579`
- **entity_slug**: `fisher-john`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `2438`
- **confirmed_count**: `2`
- **probable_count**: `0`
- **uncertain_count**: `18`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-16Z__4f192579.db`
- **notes**: scanned=2438

### 2026-06-08 — STATE_INGESTION

- **run_id**: `bfbb4b65`
- **entity_slug**: `henry-john`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `5045`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `42`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-16Z__bfbb4b65.db`
- **notes**: scanned=5045

### 2026-06-08 — STATE_INGESTION

- **run_id**: `537c95da`
- **entity_slug**: `ilitch-chris`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `1`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-16Z__537c95da.db`
- **notes**: scanned=1

### 2026-06-08 — STATE_INGESTION

- **run_id**: `512efb0d`
- **entity_slug**: `johnson-greg`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `18566`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `69`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-16Z__512efb0d.db`
- **notes**: scanned=18566

### 2026-06-08 — STATE_INGESTION

- **run_id**: `da380f73`
- **entity_slug**: `kendrick-ken`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `2190`
- **confirmed_count**: `2`
- **probable_count**: `5`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__da380f73.db`
- **notes**: scanned=2190

### 2026-06-08 — STATE_INGESTION

- **run_id**: `382d9bf6`
- **entity_slug**: `lerner-mark`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `97`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__382d9bf6.db`
- **notes**: scanned=97

### 2026-06-08 — STATE_INGESTION

- **run_id**: `3b4143ff`
- **entity_slug**: `malone-john`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `1431`
- **confirmed_count**: `4`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__3b4143ff.db`
- **notes**: scanned=1431

### 2026-06-08 — STATE_INGESTION

- **run_id**: `93af59b2`
- **entity_slug**: `mcguirk-terry`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `23`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__93af59b2.db`
- **notes**: scanned=23

### 2026-06-08 — STATE_INGESTION

- **run_id**: `f6ee0978`
- **entity_slug**: `middleton-john`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `783`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `3`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__f6ee0978.db`
- **notes**: scanned=783

### 2026-06-08 — STATE_INGESTION

- **run_id**: `8df30e81`
- **entity_slug**: `monfort-dick`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `123`
- **confirmed_count**: `34`
- **probable_count**: `2`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__8df30e81.db`
- **notes**: scanned=123

### 2026-06-08 — STATE_INGESTION

- **run_id**: `a81c9ec1`
- **entity_slug**: `moreno-arte`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `906`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__a81c9ec1.db`
- **notes**: scanned=906

### 2026-06-08 — STATE_INGESTION

- **run_id**: `0deaabb2`
- **entity_slug**: `nutting-bob`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `182`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__0deaabb2.db`
- **notes**: scanned=182

### 2026-06-08 — STATE_INGESTION

- **run_id**: `f4b3ee19`
- **entity_slug**: `pohlad-joe`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `1`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__f4b3ee19.db`
- **notes**: scanned=1

### 2026-06-08 — STATE_INGESTION

- **run_id**: `58af5727`
- **entity_slug**: `pohlad-tom`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `1`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__58af5727.db`
- **notes**: scanned=1

### 2026-06-08 — STATE_INGESTION

- **run_id**: `13313d2c`
- **entity_slug**: `ricketts-tom`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `171`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__13313d2c.db`
- **notes**: scanned=171

### 2026-06-08 — STATE_INGESTION

- **run_id**: `e1d39e01`
- **entity_slug**: `rubenstein-david`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `94`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__e1d39e01.db`
- **notes**: scanned=94

### 2026-06-08 — STATE_INGESTION

- **run_id**: `f509b9ce`
- **entity_slug**: `seidler-john`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `77`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__f509b9ce.db`
- **notes**: scanned=77

### 2026-06-08 — STATE_INGESTION

- **run_id**: `bd94a32f`
- **entity_slug**: `sherman-bruce`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `1293`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__bd94a32f.db`
- **notes**: scanned=1293

### 2026-06-08 — STATE_INGESTION

- **run_id**: `8fa34db4`
- **entity_slug**: `sherman-john`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `1293`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `9`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__8fa34db4.db`
- **notes**: scanned=1293

### 2026-06-08 — STATE_INGESTION

- **run_id**: `3e52de47`
- **entity_slug**: `simpson-bob`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `1413`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__3e52de47.db`
- **notes**: scanned=1413

### 2026-06-08 — STATE_INGESTION

- **run_id**: `305a97ae`
- **entity_slug**: `stanton-john`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `240`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `3`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__305a97ae.db`
- **notes**: scanned=240

### 2026-06-08 — STATE_INGESTION

- **run_id**: `38a7a3c8`
- **entity_slug**: `steinbrenner-hal`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `1`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__38a7a3c8.db`
- **notes**: scanned=1

### 2026-06-08 — STATE_INGESTION

- **run_id**: `3e6564a4`
- **entity_slug**: `walter-mark`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `1635`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__3e6564a4.db`
- **notes**: scanned=1635

### 2026-06-08 — STATE_INGESTION

- **run_id**: `3c177db0`
- **entity_slug**: `zalupski-patrick`
- **jurisdiction**: `CO`
- **source**: `CO-TRACER`
- **extract_label**: `co`
- **records_scanned**: `1`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-08T04-41-17Z__3c177db0.db`
- **notes**: scanned=1

### 2026-06-08 — STATE_INGESTION

- **run_id**: `7d93c982`
- **entity_slug**: `castellini-bob`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `19`
- **confirmed_count**: `6`
- **probable_count**: `0`
- **uncertain_count**: `13`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-14Z__7d93c982.db`
- **notes**: scanned=19

### 2026-06-08 — STATE_INGESTION

- **run_id**: `fbcd8662`
- **entity_slug**: `cohen-steven`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `1`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-14Z__fbcd8662.db`
- **notes**: scanned=1

### 2026-06-08 — STATE_INGESTION

- **run_id**: `6fce78a7`
- **entity_slug**: `crane-jim`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `36`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `9`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-14Z__6fce78a7.db`
- **notes**: scanned=36

### 2026-06-08 — STATE_INGESTION

- **run_id**: `78e61794`
- **entity_slug**: `davis-ray`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `72`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-14Z__78e61794.db`
- **notes**: scanned=72

### 2026-06-08 — STATE_INGESTION

- **run_id**: `94099e13`
- **entity_slug**: `dewitt-bill`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `4`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-14Z__94099e13.db`
- **notes**: scanned=4

### 2026-06-08 — STATE_INGESTION

- **run_id**: `1cac3150`
- **entity_slug**: `dolan-paul`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-14Z__1cac3150.db`
- **notes**: scanned=2

### 2026-06-08 — STATE_INGESTION

- **run_id**: `ea000287`
- **entity_slug**: `kendrick-ken`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `79`
- **confirmed_count**: `74`
- **probable_count**: `5`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-14Z__ea000287.db`
- **notes**: scanned=79

### 2026-06-08 — STATE_INGESTION

- **run_id**: `4ea02ff8`
- **entity_slug**: `lerner-mark`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-14Z__4ea02ff8.db`
- **notes**: scanned=2

### 2026-06-08 — STATE_INGESTION

- **run_id**: `9e51bbe4`
- **entity_slug**: `malone-john`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `37`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `37`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-14Z__9e51bbe4.db`
- **notes**: scanned=37

### 2026-06-08 — STATE_INGESTION

- **run_id**: `6ee21ac4`
- **entity_slug**: `middleton-john`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `35`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `34`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-14Z__6ee21ac4.db`
- **notes**: scanned=35

### 2026-06-08 — STATE_INGESTION

- **run_id**: `d139a6f1`
- **entity_slug**: `moreno-arte`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `31`
- **confirmed_count**: `9`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-14Z__d139a6f1.db`
- **notes**: scanned=31

### 2026-06-08 — STATE_INGESTION

- **run_id**: `126510da`
- **entity_slug**: `reinsdorf-jerry`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `11`
- **confirmed_count**: `11`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-14Z__126510da.db`
- **notes**: scanned=11

### 2026-06-08 — STATE_INGESTION

- **run_id**: `ae893aa5`
- **entity_slug**: `rubenstein-david`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-14Z__ae893aa5.db`
- **notes**: scanned=2

### 2026-06-08 — STATE_INGESTION

- **run_id**: `6764a7b9`
- **entity_slug**: `sherman-bruce`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `12`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `12`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-14Z__6764a7b9.db`
- **notes**: scanned=12

### 2026-06-08 — STATE_INGESTION

- **run_id**: `cc473375`
- **entity_slug**: `sherman-john`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `413`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `384`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-14Z__cc473375.db`
- **notes**: scanned=413

### 2026-06-08 — STATE_INGESTION

- **run_id**: `546e1bb4`
- **entity_slug**: `simpson-bob`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-14Z__546e1bb4.db`
- **notes**: scanned=2

### 2026-06-08 — STATE_INGESTION

- **run_id**: `a002fea6`
- **entity_slug**: `stanton-john`
- **jurisdiction**: `AZ`
- **source**: `AZ-SOS`
- **extract_label**: `https://seethemoney.az.gov/Reporting/GetNEWDetailedTableData (Page=80, Individuals)`
- **records_scanned**: `71`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `13`
- **snapshot_path**: `data/snapshots/2026-06-08T05-26-15Z__a002fea6.db`
- **notes**: scanned=71

### 2026-06-08 — STATE_INGESTION

- **run_id**: `717c82e7`
- **entity_slug**: `angelos-john-p`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `7`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__717c82e7.db`
- **notes**: scanned=7

### 2026-06-08 — STATE_INGESTION

- **run_id**: `48403a6a`
- **entity_slug**: `cohen-steven`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `146`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__48403a6a.db`
- **notes**: scanned=146

### 2026-06-08 — STATE_INGESTION

- **run_id**: `9cade3c6`
- **entity_slug**: `crane-jim`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `34`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__9cade3c6.db`
- **notes**: scanned=34

### 2026-06-08 — STATE_INGESTION

- **run_id**: `c48eedc1`
- **entity_slug**: `davis-ray`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `828`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__c48eedc1.db`
- **notes**: scanned=828

### 2026-06-08 — STATE_INGESTION

- **run_id**: `e80279e7`
- **entity_slug**: `dewitt-bill`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `152`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__e80279e7.db`
- **notes**: scanned=152

### 2026-06-08 — STATE_INGESTION

- **run_id**: `3b4cb4d8`
- **entity_slug**: `dolan-paul`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `128`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__3b4cb4d8.db`
- **notes**: scanned=128

### 2026-06-08 — STATE_INGESTION

- **run_id**: `659a7a67`
- **entity_slug**: `fisher-john`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `156`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__659a7a67.db`
- **notes**: scanned=156

### 2026-06-08 — STATE_INGESTION

- **run_id**: `77c2127b`
- **entity_slug**: `henry-john`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `498`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__77c2127b.db`
- **notes**: scanned=498

### 2026-06-08 — STATE_INGESTION

- **run_id**: `07c5ab4d`
- **entity_slug**: `johnson-greg`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `3714`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `21`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__07c5ab4d.db`
- **notes**: scanned=3714

### 2026-06-08 — STATE_INGESTION

- **run_id**: `33dc529e`
- **entity_slug**: `kendrick-ken`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `3`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__33dc529e.db`
- **notes**: scanned=3

### 2026-06-08 — STATE_INGESTION

- **run_id**: `0c5f6ff0`
- **entity_slug**: `lerner-mark`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `21`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__0c5f6ff0.db`
- **notes**: scanned=21

### 2026-06-08 — STATE_INGESTION

- **run_id**: `8b6fa65f`
- **entity_slug**: `malone-john`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `116`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__8b6fa65f.db`
- **notes**: scanned=116

### 2026-06-08 — STATE_INGESTION

- **run_id**: `ce82ec1b`
- **entity_slug**: `middleton-john`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `32`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__ce82ec1b.db`
- **notes**: scanned=32

### 2026-06-08 — STATE_INGESTION

- **run_id**: `9f6bf938`
- **entity_slug**: `moreno-arte`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `6`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__9f6bf938.db`
- **notes**: scanned=6

### 2026-06-08 — STATE_INGESTION

- **run_id**: `bd95a721`
- **entity_slug**: `nutting-bob`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__bd95a721.db`
- **notes**: scanned=2

### 2026-06-08 — STATE_INGESTION

- **run_id**: `36aa1ef6`
- **entity_slug**: `pohlad-joe`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `105`
- **confirmed_count**: `7`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__36aa1ef6.db`
- **notes**: scanned=105

### 2026-06-08 — STATE_INGESTION

- **run_id**: `ecaf209c`
- **entity_slug**: `pohlad-tom`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `105`
- **confirmed_count**: `4`
- **probable_count**: `1`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__ecaf209c.db`
- **notes**: scanned=105

### 2026-06-08 — STATE_INGESTION

- **run_id**: `1885d8dc`
- **entity_slug**: `rubenstein-david`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `1`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__1885d8dc.db`
- **notes**: scanned=1

### 2026-06-08 — STATE_INGESTION

- **run_id**: `0ebb6651`
- **entity_slug**: `sherman-bruce`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `110`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__0ebb6651.db`
- **notes**: scanned=110

### 2026-06-08 — STATE_INGESTION

- **run_id**: `c24426af`
- **entity_slug**: `sherman-john`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `110`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__c24426af.db`
- **notes**: scanned=110

### 2026-06-08 — STATE_INGESTION

- **run_id**: `0120c3ed`
- **entity_slug**: `simpson-bob`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `63`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__0120c3ed.db`
- **notes**: scanned=63

### 2026-06-08 — STATE_INGESTION

- **run_id**: `f6b644ff`
- **entity_slug**: `stanton-john`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `39`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__f6b644ff.db`
- **notes**: scanned=39

### 2026-06-08 — STATE_INGESTION

- **run_id**: `cc6d1560`
- **entity_slug**: `steinbrenner-hal`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `14`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__cc6d1560.db`
- **notes**: scanned=14

### 2026-06-08 — STATE_INGESTION

- **run_id**: `d754a4ed`
- **entity_slug**: `walter-mark`
- **jurisdiction**: `MN`
- **source**: `MN-CFB`
- **extract_label**: `mn`
- **records_scanned**: `257`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-08T23-50-37Z__d754a4ed.db`
- **notes**: scanned=257

### 2026-06-09 — STATE_INGESTION

- **run_id**: `599c4e43`
- **entity_slug**: `angelos-john-p`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `33`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-56Z__599c4e43.db`
- **notes**: scanned=33

### 2026-06-09 — STATE_INGESTION

- **run_id**: `84cd510c`
- **entity_slug**: `attanasio-mark`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `18`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-56Z__84cd510c.db`
- **notes**: scanned=18

### 2026-06-09 — STATE_INGESTION

- **run_id**: `5d855e8c`
- **entity_slug**: `castellini-bob`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-56Z__5d855e8c.db`
- **notes**: scanned=2

### 2026-06-09 — STATE_INGESTION

- **run_id**: `0026b8f1`
- **entity_slug**: `castellini-phil`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-56Z__0026b8f1.db`
- **notes**: scanned=2

### 2026-06-09 — STATE_INGESTION

- **run_id**: `310cb7ca`
- **entity_slug**: `cohen-steven`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `4786`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `84`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-56Z__310cb7ca.db`
- **notes**: scanned=4786

### 2026-06-09 — STATE_INGESTION

- **run_id**: `37cae73d`
- **entity_slug**: `crane-jim`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `972`
- **confirmed_count**: `0`
- **probable_count**: `1`
- **uncertain_count**: `44`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-56Z__37cae73d.db`
- **notes**: scanned=972

### 2026-06-09 — STATE_INGESTION

- **run_id**: `60476543`
- **entity_slug**: `davis-ray`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `4568`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-56Z__60476543.db`
- **notes**: scanned=4568

### 2026-06-09 — STATE_INGESTION

- **run_id**: `6840e1f3`
- **entity_slug**: `dewitt-bill`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `457`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__6840e1f3.db`
- **notes**: scanned=457

### 2026-06-09 — STATE_INGESTION

- **run_id**: `c0e9bc2c`
- **entity_slug**: `dolan-paul`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `643`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `3`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__c0e9bc2c.db`
- **notes**: scanned=643

### 2026-06-09 — STATE_INGESTION

- **run_id**: `a5d00da3`
- **entity_slug**: `feliciano-jose`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `115`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__a5d00da3.db`
- **notes**: scanned=115

### 2026-06-09 — STATE_INGESTION

- **run_id**: `1f713a7e`
- **entity_slug**: `fisher-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `3357`
- **confirmed_count**: `27`
- **probable_count**: `0`
- **uncertain_count**: `65`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__1f713a7e.db`
- **notes**: scanned=3357

### 2026-06-09 — STATE_INGESTION

- **run_id**: `811610b1`
- **entity_slug**: `henry-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `2568`
- **confirmed_count**: `0`
- **probable_count**: `2`
- **uncertain_count**: `14`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__811610b1.db`
- **notes**: scanned=2568

### 2026-06-09 — STATE_INGESTION

- **run_id**: `9c400126`
- **entity_slug**: `ilitch-chris`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `1`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__9c400126.db`
- **notes**: scanned=1

### 2026-06-09 — STATE_INGESTION

- **run_id**: `1556431d`
- **entity_slug**: `johnson-greg`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `4521`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__1556431d.db`
- **notes**: scanned=4521

### 2026-06-09 — STATE_INGESTION

- **run_id**: `1c631dd2`
- **entity_slug**: `kendrick-ken`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `375`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__1c631dd2.db`
- **notes**: scanned=375

### 2026-06-09 — STATE_INGESTION

- **run_id**: `12b5985b`
- **entity_slug**: `lerner-mark`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `752`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `4`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__12b5985b.db`
- **notes**: scanned=752

### 2026-06-09 — STATE_INGESTION

- **run_id**: `bc80bf77`
- **entity_slug**: `malone-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `1348`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `13`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__bc80bf77.db`
- **notes**: scanned=1348

### 2026-06-09 — STATE_INGESTION

- **run_id**: `a785dd78`
- **entity_slug**: `mcguirk-terry`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `193`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__a785dd78.db`
- **notes**: scanned=193

### 2026-06-09 — STATE_INGESTION

- **run_id**: `97ab0d3b`
- **entity_slug**: `middleton-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `450`
- **confirmed_count**: `0`
- **probable_count**: `1`
- **uncertain_count**: `26`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__97ab0d3b.db`
- **notes**: scanned=450

### 2026-06-09 — STATE_INGESTION

- **run_id**: `dfc94021`
- **entity_slug**: `monfort-dick`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `40`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__dfc94021.db`
- **notes**: scanned=40

### 2026-06-09 — STATE_INGESTION

- **run_id**: `c1e235b4`
- **entity_slug**: `moreno-arte`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `491`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__c1e235b4.db`
- **notes**: scanned=491

### 2026-06-09 — STATE_INGESTION

- **run_id**: `8906d141`
- **entity_slug**: `nutting-bob`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `28`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `6`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__8906d141.db`
- **notes**: scanned=28

### 2026-06-09 — STATE_INGESTION

- **run_id**: `4cd5d005`
- **entity_slug**: `ricketts-tom`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `110`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__4cd5d005.db`
- **notes**: scanned=110

### 2026-06-09 — STATE_INGESTION

- **run_id**: `0b9006c0`
- **entity_slug**: `rubenstein-david`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `825`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__0b9006c0.db`
- **notes**: scanned=825

### 2026-06-09 — STATE_INGESTION

- **run_id**: `4192a1ef`
- **entity_slug**: `seidler-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `45`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__4192a1ef.db`
- **notes**: scanned=45

### 2026-06-09 — STATE_INGESTION

- **run_id**: `cadf034c`
- **entity_slug**: `sherman-bruce`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `1551`
- **confirmed_count**: `2`
- **probable_count**: `1`
- **uncertain_count**: `5`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__cadf034c.db`
- **notes**: scanned=1551

### 2026-06-09 — STATE_INGESTION

- **run_id**: `2f625923`
- **entity_slug**: `sherman-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `1551`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `10`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__2f625923.db`
- **notes**: scanned=1551

### 2026-06-09 — STATE_INGESTION

- **run_id**: `2e82c4fe`
- **entity_slug**: `simpson-bob`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `1753`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__2e82c4fe.db`
- **notes**: scanned=1753

### 2026-06-09 — STATE_INGESTION

- **run_id**: `8684536a`
- **entity_slug**: `stanton-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `653`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `47`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__8684536a.db`
- **notes**: scanned=653

### 2026-06-09 — STATE_INGESTION

- **run_id**: `3ec521d0`
- **entity_slug**: `steinbrenner-hal`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `46`
- **confirmed_count**: `2`
- **probable_count**: `2`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__3ec521d0.db`
- **notes**: scanned=46

### 2026-06-09 — STATE_INGESTION

- **run_id**: `95dad988`
- **entity_slug**: `walter-mark`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `2569`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__95dad988.db`
- **notes**: scanned=2569

### 2026-06-09 — STATE_INGESTION

- **run_id**: `6697ec63`
- **entity_slug**: `zalupski-patrick`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `6`
- **confirmed_count**: `3`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-23-57Z__6697ec63.db`
- **notes**: scanned=6

### 2026-06-09 — STATE_INGESTION

- **run_id**: `b1416608`
- **entity_slug**: `angelos-john-p`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `33`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-45Z__b1416608.db`
- **notes**: scanned=33

### 2026-06-09 — STATE_INGESTION

- **run_id**: `09ba66c0`
- **entity_slug**: `attanasio-mark`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `18`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-45Z__09ba66c0.db`
- **notes**: scanned=18

### 2026-06-09 — STATE_INGESTION

- **run_id**: `b909517e`
- **entity_slug**: `castellini-bob`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-45Z__b909517e.db`
- **notes**: scanned=2

### 2026-06-09 — STATE_INGESTION

- **run_id**: `146b52ff`
- **entity_slug**: `castellini-phil`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-45Z__146b52ff.db`
- **notes**: scanned=2

### 2026-06-09 — STATE_INGESTION

- **run_id**: `4b3195a8`
- **entity_slug**: `cohen-steven`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `4786`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `84`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-45Z__4b3195a8.db`
- **notes**: scanned=4786

### 2026-06-09 — STATE_INGESTION

- **run_id**: `77ca9515`
- **entity_slug**: `crane-jim`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `972`
- **confirmed_count**: `0`
- **probable_count**: `1`
- **uncertain_count**: `44`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__77ca9515.db`
- **notes**: scanned=972

### 2026-06-09 — STATE_INGESTION

- **run_id**: `a7d21229`
- **entity_slug**: `davis-ray`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `4568`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__a7d21229.db`
- **notes**: scanned=4568

### 2026-06-09 — STATE_INGESTION

- **run_id**: `5d1c8405`
- **entity_slug**: `dewitt-bill`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `457`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__5d1c8405.db`
- **notes**: scanned=457

### 2026-06-09 — STATE_INGESTION

- **run_id**: `5975277d`
- **entity_slug**: `dolan-paul`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `643`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `3`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__5975277d.db`
- **notes**: scanned=643

### 2026-06-09 — STATE_INGESTION

- **run_id**: `bceb240b`
- **entity_slug**: `feliciano-jose`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `115`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__bceb240b.db`
- **notes**: scanned=115

### 2026-06-09 — STATE_INGESTION

- **run_id**: `0c0e7ad4`
- **entity_slug**: `henry-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `2568`
- **confirmed_count**: `0`
- **probable_count**: `2`
- **uncertain_count**: `14`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__0c0e7ad4.db`
- **notes**: scanned=2568

### 2026-06-09 — STATE_INGESTION

- **run_id**: `9e8db903`
- **entity_slug**: `ilitch-chris`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `1`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__9e8db903.db`
- **notes**: scanned=1

### 2026-06-09 — STATE_INGESTION

- **run_id**: `fc6271fb`
- **entity_slug**: `johnson-greg`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `4521`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__fc6271fb.db`
- **notes**: scanned=4521

### 2026-06-09 — STATE_INGESTION

- **run_id**: `81e06780`
- **entity_slug**: `kendrick-ken`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `375`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__81e06780.db`
- **notes**: scanned=375

### 2026-06-09 — STATE_INGESTION

- **run_id**: `a8b46d1e`
- **entity_slug**: `lerner-mark`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `752`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `4`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__a8b46d1e.db`
- **notes**: scanned=752

### 2026-06-09 — STATE_INGESTION

- **run_id**: `b6e75eef`
- **entity_slug**: `malone-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `1348`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `13`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__b6e75eef.db`
- **notes**: scanned=1348

### 2026-06-09 — STATE_INGESTION

- **run_id**: `3b0a0b9e`
- **entity_slug**: `mcguirk-terry`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `193`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__3b0a0b9e.db`
- **notes**: scanned=193

### 2026-06-09 — STATE_INGESTION

- **run_id**: `d173af3a`
- **entity_slug**: `middleton-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `450`
- **confirmed_count**: `0`
- **probable_count**: `1`
- **uncertain_count**: `26`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__d173af3a.db`
- **notes**: scanned=450

### 2026-06-09 — STATE_INGESTION

- **run_id**: `26aee8b8`
- **entity_slug**: `monfort-dick`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `40`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__26aee8b8.db`
- **notes**: scanned=40

### 2026-06-09 — STATE_INGESTION

- **run_id**: `4d1b144f`
- **entity_slug**: `moreno-arte`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `491`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__4d1b144f.db`
- **notes**: scanned=491

### 2026-06-09 — STATE_INGESTION

- **run_id**: `c8d5a8ab`
- **entity_slug**: `nutting-bob`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `28`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `6`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__c8d5a8ab.db`
- **notes**: scanned=28

### 2026-06-09 — STATE_INGESTION

- **run_id**: `09383232`
- **entity_slug**: `ricketts-tom`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `110`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__09383232.db`
- **notes**: scanned=110

### 2026-06-09 — STATE_INGESTION

- **run_id**: `87c1ab56`
- **entity_slug**: `rubenstein-david`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `825`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__87c1ab56.db`
- **notes**: scanned=825

### 2026-06-09 — STATE_INGESTION

- **run_id**: `128a2765`
- **entity_slug**: `seidler-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `45`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__128a2765.db`
- **notes**: scanned=45

### 2026-06-09 — STATE_INGESTION

- **run_id**: `efe51413`
- **entity_slug**: `sherman-bruce`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `1551`
- **confirmed_count**: `2`
- **probable_count**: `1`
- **uncertain_count**: `5`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__efe51413.db`
- **notes**: scanned=1551

### 2026-06-09 — STATE_INGESTION

- **run_id**: `33dfdc9e`
- **entity_slug**: `sherman-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `1551`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `10`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__33dfdc9e.db`
- **notes**: scanned=1551

### 2026-06-09 — STATE_INGESTION

- **run_id**: `4841637d`
- **entity_slug**: `simpson-bob`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `1753`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__4841637d.db`
- **notes**: scanned=1753

### 2026-06-09 — STATE_INGESTION

- **run_id**: `9774895b`
- **entity_slug**: `stanton-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `653`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `47`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__9774895b.db`
- **notes**: scanned=653

### 2026-06-09 — STATE_INGESTION

- **run_id**: `7016ef4e`
- **entity_slug**: `steinbrenner-hal`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `46`
- **confirmed_count**: `2`
- **probable_count**: `2`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__7016ef4e.db`
- **notes**: scanned=46

### 2026-06-09 — STATE_INGESTION

- **run_id**: `72ebe3fa`
- **entity_slug**: `walter-mark`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `2569`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__72ebe3fa.db`
- **notes**: scanned=2569

### 2026-06-09 — STATE_INGESTION

- **run_id**: `232c0109`
- **entity_slug**: `zalupski-patrick`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `6`
- **confirmed_count**: `3`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-30-46Z__232c0109.db`
- **notes**: scanned=6

### 2026-06-09 — STATE_INGESTION

- **run_id**: `5d96bbe9`
- **entity_slug**: `angelos-john-p`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `33`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-49Z__5d96bbe9.db`
- **notes**: scanned=33

### 2026-06-09 — STATE_INGESTION

- **run_id**: `6d9aead5`
- **entity_slug**: `attanasio-mark`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `18`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-49Z__6d9aead5.db`
- **notes**: scanned=18

### 2026-06-09 — STATE_INGESTION

- **run_id**: `9250c919`
- **entity_slug**: `castellini-bob`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-49Z__9250c919.db`
- **notes**: scanned=2

### 2026-06-09 — STATE_INGESTION

- **run_id**: `8cd0fd30`
- **entity_slug**: `castellini-phil`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `2`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-49Z__8cd0fd30.db`
- **notes**: scanned=2

### 2026-06-09 — STATE_INGESTION

- **run_id**: `23f27fe0`
- **entity_slug**: `cohen-steven`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `4786`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `84`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-49Z__23f27fe0.db`
- **notes**: scanned=4786

### 2026-06-09 — STATE_INGESTION

- **run_id**: `f66b230d`
- **entity_slug**: `crane-jim`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `972`
- **confirmed_count**: `0`
- **probable_count**: `1`
- **uncertain_count**: `44`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__f66b230d.db`
- **notes**: scanned=972

### 2026-06-09 — STATE_INGESTION

- **run_id**: `8bedb994`
- **entity_slug**: `davis-ray`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `4568`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__8bedb994.db`
- **notes**: scanned=4568

### 2026-06-09 — STATE_INGESTION

- **run_id**: `6f0eafc2`
- **entity_slug**: `dewitt-bill`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `457`
- **confirmed_count**: `1`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__6f0eafc2.db`
- **notes**: scanned=457

### 2026-06-09 — STATE_INGESTION

- **run_id**: `575605cf`
- **entity_slug**: `dolan-paul`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `643`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `3`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__575605cf.db`
- **notes**: scanned=643

### 2026-06-09 — STATE_INGESTION

- **run_id**: `786dc2c0`
- **entity_slug**: `feliciano-jose`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `115`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__786dc2c0.db`
- **notes**: scanned=115

### 2026-06-09 — STATE_INGESTION

- **run_id**: `d447eaaf`
- **entity_slug**: `ilitch-chris`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `1`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__d447eaaf.db`
- **notes**: scanned=1

### 2026-06-09 — STATE_INGESTION

- **run_id**: `68833f1e`
- **entity_slug**: `johnson-greg`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `4521`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__68833f1e.db`
- **notes**: scanned=4521

### 2026-06-09 — STATE_INGESTION

- **run_id**: `d107a27b`
- **entity_slug**: `kendrick-ken`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `375`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__d107a27b.db`
- **notes**: scanned=375

### 2026-06-09 — STATE_INGESTION

- **run_id**: `e6469d9b`
- **entity_slug**: `lerner-mark`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `752`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `4`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__e6469d9b.db`
- **notes**: scanned=752

### 2026-06-09 — STATE_INGESTION

- **run_id**: `6757c517`
- **entity_slug**: `malone-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `1348`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `13`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__6757c517.db`
- **notes**: scanned=1348

### 2026-06-09 — STATE_INGESTION

- **run_id**: `d1b12cfd`
- **entity_slug**: `mcguirk-terry`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `193`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__d1b12cfd.db`
- **notes**: scanned=193

### 2026-06-09 — STATE_INGESTION

- **run_id**: `e34e568c`
- **entity_slug**: `middleton-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `450`
- **confirmed_count**: `0`
- **probable_count**: `1`
- **uncertain_count**: `26`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__e34e568c.db`
- **notes**: scanned=450

### 2026-06-09 — STATE_INGESTION

- **run_id**: `7cc33000`
- **entity_slug**: `monfort-dick`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `40`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__7cc33000.db`
- **notes**: scanned=40

### 2026-06-09 — STATE_INGESTION

- **run_id**: `dba0ba88`
- **entity_slug**: `moreno-arte`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `491`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__dba0ba88.db`
- **notes**: scanned=491

### 2026-06-09 — STATE_INGESTION

- **run_id**: `e56e36ab`
- **entity_slug**: `nutting-bob`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `28`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `6`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__e56e36ab.db`
- **notes**: scanned=28

### 2026-06-09 — STATE_INGESTION

- **run_id**: `8d56d9f1`
- **entity_slug**: `ricketts-tom`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `110`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__8d56d9f1.db`
- **notes**: scanned=110

### 2026-06-09 — STATE_INGESTION

- **run_id**: `d77f7aff`
- **entity_slug**: `rubenstein-david`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `825`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__d77f7aff.db`
- **notes**: scanned=825

### 2026-06-09 — STATE_INGESTION

- **run_id**: `bee99335`
- **entity_slug**: `seidler-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `45`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__bee99335.db`
- **notes**: scanned=45

### 2026-06-09 — STATE_INGESTION

- **run_id**: `d0e0d42a`
- **entity_slug**: `sherman-bruce`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `1551`
- **confirmed_count**: `2`
- **probable_count**: `1`
- **uncertain_count**: `5`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__d0e0d42a.db`
- **notes**: scanned=1551

### 2026-06-09 — STATE_INGESTION

- **run_id**: `89dc6b36`
- **entity_slug**: `sherman-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `1551`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `10`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__89dc6b36.db`
- **notes**: scanned=1551

### 2026-06-09 — STATE_INGESTION

- **run_id**: `df161b7a`
- **entity_slug**: `simpson-bob`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `1753`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `2`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__df161b7a.db`
- **notes**: scanned=1753

### 2026-06-09 — STATE_INGESTION

- **run_id**: `37923139`
- **entity_slug**: `stanton-john`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `653`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `47`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__37923139.db`
- **notes**: scanned=653

### 2026-06-09 — STATE_INGESTION

- **run_id**: `0c11c214`
- **entity_slug**: `steinbrenner-hal`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `46`
- **confirmed_count**: `2`
- **probable_count**: `2`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__0c11c214.db`
- **notes**: scanned=46

### 2026-06-09 — STATE_INGESTION

- **run_id**: `05d6c092`
- **entity_slug**: `walter-mark`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `2569`
- **confirmed_count**: `0`
- **probable_count**: `0`
- **uncertain_count**: `1`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__05d6c092.db`
- **notes**: scanned=2569

### 2026-06-09 — STATE_INGESTION

- **run_id**: `13503866`
- **entity_slug**: `zalupski-patrick`
- **jurisdiction**: `FL`
- **source**: `FL-DOE`
- **extract_label**: `https://dos.elections.myflorida.com/campaign-finance/contributions/`
- **records_scanned**: `6`
- **confirmed_count**: `3`
- **probable_count**: `1`
- **uncertain_count**: `0`
- **snapshot_path**: `data/snapshots/2026-06-09T00-34-50Z__13503866.db`
- **notes**: scanned=6
