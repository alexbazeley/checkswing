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
- `CLAUDE.md`
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

Pipeline code written by claude-code-session:
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

Decision (with user approval): abort the broad fetch and switch to a state-filtered fetch. When an owner YAML has `verifying_signals.states` populated, the fetch will pass those as `contributor_state` filters to FEC. Cohen's states (CT, NY) narrow the result set ~10-20x while remaining name-anchored (so it does NOT violate CLAUDE.md §3's prohibition on employer-only aggregated queries).

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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-22T20-51-24Z__3ea399e7.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-22T21-06-20Z__cae911f9.db`
- **notes**: skipped(no-name-match)=153 · FROM-RAW

### 2026-05-22 — DELETION — reclassify cohen-steven

- **entity_slug**: `cohen-steven`
- **reason**: smoke test of new command — should be a no-op
- **rows_deleted_donations**: `112`
- **rows_deleted_review_queue**: `2696` (of which 0 had resolutions)
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-22T21-18-23Z__pre-reclassify-cohen-steven.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-22T21-18-23Z__9fb78c4b.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-22T21-52-51Z__1f5cbf21.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-22T23-05-08Z__dbcd04e1.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-22T23-41-41Z__3b72871b.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-22T23-55-13Z__8baaf48b.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-04-55Z__pre-reclassify-cohen-steven.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-04-55Z__2ec4b4c2.db`
- **notes**: skipped(no-name-match)=153 · FROM-RAW

### 2026-05-23 — DELETION — reclassify crane-jim

- **entity_slug**: `crane-jim`
- **reason**: Calibration round 1 (cross-pilot): _norm() period-strip + YAML signal additions
- **rows_deleted_donations**: `35`
- **rows_deleted_review_queue**: `14` (of which 0 had resolutions)
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-04-56Z__pre-reclassify-crane-jim.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-04-56Z__7408f814.db`
- **notes**: skipped(no-name-match)=2 · FROM-RAW

### 2026-05-23 — DELETION — reclassify henry-john

- **entity_slug**: `henry-john`
- **reason**: Calibration round 1 (cross-pilot): _norm() period-strip + YAML signal additions
- **rows_deleted_donations**: `12`
- **rows_deleted_review_queue**: `218` (of which 0 had resolutions)
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-04-56Z__pre-reclassify-henry-john.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-04-56Z__a8588d7b.db`
- **notes**: skipped(no-name-match)=912 · FROM-RAW

### 2026-05-23 — DELETION — reclassify castellini-bob

- **entity_slug**: `castellini-bob`
- **reason**: Calibration round 1 (cross-pilot): _norm() period-strip + YAML signal additions
- **rows_deleted_donations**: `302`
- **rows_deleted_review_queue**: `6` (of which 0 had resolutions)
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-04-56Z__pre-reclassify-castellini-bob.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-04-56Z__01937262.db`
- **notes**: skipped(no-name-match)=153 · FROM-RAW

### 2026-05-23 — DELETION — reclassify steinbrenner-hal

- **entity_slug**: `steinbrenner-hal`
- **reason**: Calibration round 1 (cross-pilot): _norm() period-strip + YAML signal additions
- **rows_deleted_donations**: `12`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-04-56Z__pre-reclassify-steinbrenner-hal.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-04-56Z__3c5fac88.db`
- **notes**: skipped(no-name-match)=3 · FROM-RAW

### 2026-05-23 — DELETION — reclassify cohen-steven

- **entity_slug**: `cohen-steven`
- **reason**: Calibration round 1 (honorific strip + structured-fields fallback)
- **rows_deleted_donations**: `112`
- **rows_deleted_review_queue**: `2696` (of which 0 had resolutions)
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-08-30Z__pre-reclassify-cohen-steven.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-08-30Z__5af14960.db`
- **notes**: skipped(no-name-match)=40 · FROM-RAW

### 2026-05-23 — DELETION — reclassify crane-jim

- **entity_slug**: `crane-jim`
- **reason**: Calibration round 1 (honorific strip + structured-fields fallback)
- **rows_deleted_donations**: `35`
- **rows_deleted_review_queue**: `14` (of which 0 had resolutions)
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-08-31Z__pre-reclassify-crane-jim.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-08-31Z__6ccf16dd.db`
- **notes**: skipped(no-name-match)=1 · FROM-RAW

### 2026-05-23 — DELETION — reclassify henry-john

- **entity_slug**: `henry-john`
- **reason**: Calibration round 1 (honorific strip + structured-fields fallback)
- **rows_deleted_donations**: `16`
- **rows_deleted_review_queue**: `214` (of which 0 had resolutions)
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-08-31Z__pre-reclassify-henry-john.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-08-31Z__a11f0175.db`
- **notes**: skipped(no-name-match)=876 · FROM-RAW

### 2026-05-23 — DELETION — reclassify castellini-bob

- **entity_slug**: `castellini-bob`
- **reason**: Calibration round 1 (honorific strip + structured-fields fallback)
- **rows_deleted_donations**: `300`
- **rows_deleted_review_queue**: `8` (of which 0 had resolutions)
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-08-31Z__pre-reclassify-castellini-bob.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-08-31Z__622b4090.db`
- **notes**: skipped(no-name-match)=8 · FROM-RAW

### 2026-05-23 — DELETION — reclassify steinbrenner-hal

- **entity_slug**: `steinbrenner-hal`
- **reason**: Calibration round 1 (honorific strip + structured-fields fallback)
- **rows_deleted_donations**: `12`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-08-32Z__pre-reclassify-steinbrenner-hal.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-08-32Z__241dc1d0.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-32-02Z__5f8213d7.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T00-52-44Z__8f092363.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T01-04-12Z__dc4e5575.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T01-09-22Z__864d1936.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T02-35-43Z__8c8cbacc.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T03-08-31Z__ef2a7eae.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T03-35-34Z__a5b90844.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T03-48-43Z__ad9d887a.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T04-37-35Z__155dc60e.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T04-37-50Z__fe188090.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T05-04-41Z__9392b8c0.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T16-57-53Z__aae57a83.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T16-57-54Z__2252e059.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T17-25-42Z__ba257fa3.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T17-25-40Z__a0b17326.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T17-37-10Z__ad15ab15.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T17-25-37Z__d87f0012.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T18-01-50Z__2f7503b4.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T17-37-08Z__cd0c46ce.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T17-44-31Z__7a4292ae.db`
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

Family / separate-slug candidates surfaced during this batch (not added, per CLAUDE.md §1.7; queued for future batches):
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T18-47-44Z__1d30f6d7.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T18-47-42Z__3a271fed.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T18-48-38Z__8181a08f.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T18-48-39Z__54191a41.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T19-08-33Z__74728f10.db`
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

#### Family / separate-slug candidates surfaced (not added, per CLAUDE.md §1.7)

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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T19-32-44Z__8d5ad9a6.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T19-56-15Z__2986bac0.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T19-59-24Z__8efb714f.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-23T19-56-16Z__c68572b4.db`
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

#### Family / separate-slug candidates surfaced (still queued, not added per CLAUDE.md §1.7)

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
- **Family separate-slug expansions** — case-by-case, in priority order; each requires a deliberate scope-expansion decision per CLAUDE.md §1.7.
- **Pipeline durability** — implement date-window chunking for common-name owners to address the FEC timeout pattern.

### 2026-05-25 — DELETION — reclassify rubenstein-david

- **entity_slug**: `rubenstein-david`
- **reason**: Tier-A calibration round 1: added negative_signals.employers: Georgetown University to demote 1 doppelgänger PROBABLE record (2008 Giuliani filing under 'V.P. OF FINANCIAL PLANNING & ANALYSIS')
- **rows_deleted_donations**: `8`
- **rows_deleted_review_queue**: `23` (of which 0 had resolutions)
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T03-46-29Z__pre-reclassify-rubenstein-david.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T03-46-29Z__3989d9b9.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify fisher-john

- **entity_slug**: `fisher-john`
- **reason**: Tier-A round 1: 6 negative_signals.employers (WSP/Parsons/Sky Oak/SKS/DFJ/Draper Fisher Jurvetson) to demote 4 distinct doppelgänger clusters
- **rows_deleted_donations**: `393`
- **rows_deleted_review_queue**: `1267` (of which 0 had resolutions)
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T03-50-05Z__pre-reclassify-fisher-john.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T03-50-05Z__6aa2f908.db`
- **notes**: skipped(no-name-match)=38 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify middleton-john

- **entity_slug**: `middleton-john`
- **reason**: Tier-A round 1: added John P. Middleton (son) as related_entity (kind: child); added Branford Holdings + Mc Intosh Inns typo variants to verifying_signals.employers
- **rows_deleted_donations**: `71`
- **rows_deleted_review_queue**: `19` (of which 0 had resolutions)
- **include_related**: `True`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-02-44Z__pre-reclassify-middleton-john.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-02-44Z__d5bea8f9.db`
- **notes**: skipped(no-name-match)=10 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify middleton-john

- **entity_slug**: `middleton-john`
- **reason**: Tier-A round 1 (revised): Vertigo negative_signal + Branford/Mc Intosh typo variants. Reverted from related_entity approach due to classifier middle-initial limitation — see YAML change_log for details
- **rows_deleted_donations**: `71`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `True`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-05-04Z__pre-reclassify-middleton-john.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-05-04Z__e74158c2.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-12-40Z__pre-reclassify-monfort-dick.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-12-40Z__0d3086ad.db`
- **notes**: skipped(no-name-match)=4 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify reinsdorf-jerry

- **entity_slug**: `reinsdorf-jerry`
- **reason**: Tier-A round 1 Option B: promoted Bojer Financial to strong_signals.employers; promoted ZIPs 60616 + 606163621 to strong_signals.zip_codes
- **rows_deleted_donations**: `422`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-15-04Z__pre-reclassify-reinsdorf-jerry.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-15-04Z__81b1167d.db`
- **notes**: skipped(no-name-match)=1 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify monfort-dick

- **entity_slug**: `monfort-dick`
- **reason**: Tier-B calibration: promoted 80631/80632/80615 to strong_signals.zip_codes (Greeley/Eaton Monfort family base ZIPs); follow-up to Tier-A round 1
- **rows_deleted_donations**: `102`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-27-50Z__pre-reclassify-monfort-dick.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-27-50Z__042a1431.db`
- **notes**: skipped(no-name-match)=4 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify davis-ray

- **entity_slug**: `davis-ray`
- **reason**: Tier-B calibration: added 75225 (Highland Park / Avatar Investments Sherry Lane office) to strong_signals.zip_codes
- **rows_deleted_donations**: `81`
- **rows_deleted_review_queue**: `13` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-32-38Z__pre-reclassify-davis-ray.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-32-38Z__5cebeea6.db`
- **notes**: skipped(no-name-match)=224 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify moreno-arte

- **entity_slug**: `moreno-arte`
- **reason**: Tier-B: added 85016 + 85018 (Biltmore Estates / Arcadia Phoenix residence ZIPs) to strong_signals.zip_codes
- **rows_deleted_donations**: `73`
- **rows_deleted_review_queue**: `6` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-38-53Z__pre-reclassify-moreno-arte.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-38-53Z__892671fc.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify pohlad-tom

- **entity_slug**: `pohlad-tom`
- **reason**: Tier-B: added Twin Cities Automotive/Inver Grove Volkswagen employers + Lake Minnetonka suburb cities (Excelsior/Deephaven/Shorewood); middle initial O identified via cross-period FEC match
- **rows_deleted_donations**: `95`
- **rows_deleted_review_queue**: `24` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-45-43Z__pre-reclassify-pohlad-tom.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-45-43Z__7afb021a.db`
- **notes**: skipped(no-name-match)=33 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify pohlad-tom

- **entity_slug**: `pohlad-tom`
- **reason**: Tier-B re-reclassify after restoring inadvertently-dropped occupations block
- **rows_deleted_donations**: `118`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-46-32Z__pre-reclassify-pohlad-tom.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T04-46-32Z__a04736c9.db`
- **notes**: skipped(no-name-match)=33 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify ricketts-tom

- **entity_slug**: `ricketts-tom`
- **reason**: Tier-B: added Incapitol/EnCapital Holdings/RAM Investment/RAM Investments/Capitol Building employer variants (alex-verified via 531 Laurel Ave shared address)
- **rows_deleted_donations**: `62`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T05-02-45Z__pre-reclassify-ricketts-tom.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T05-02-45Z__a9d60a8c.db`
- **notes**: skipped(no-name-match)=1 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify stanton-john

- **entity_slug**: `stanton-john`
- **reason**: Tier-B: added medina/west-medina cities + Trilogy Partners/Triology employer variants
- **rows_deleted_donations**: `87`
- **rows_deleted_review_queue**: `96` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T05-07-37Z__pre-reclassify-stanton-john.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T05-07-37Z__a1f9711f.db`
- **notes**: skipped(no-name-match)=143 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify stanton-john

- **entity_slug**: `stanton-john`
- **reason**: Tier-B follow-up: added VoiceStream (one-word) to catch VOICESTREAM/VOICESTREAM COMMUNICATIONS variants
- **rows_deleted_donations**: `179`
- **rows_deleted_review_queue**: `4` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T05-08-25Z__pre-reclassify-stanton-john.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T05-08-25Z__d7c136ab.db`
- **notes**: skipped(no-name-match)=143 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify sherman-bruce

- **entity_slug**: `sherman-bruce`
- **reason**: Tier-B: added Boca Raton city + MAIMI MARLINS typo + Vistakon/PWC negative_signals
- **rows_deleted_donations**: `65`
- **rows_deleted_review_queue**: `131` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T05-13-44Z__pre-reclassify-sherman-bruce.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T05-13-44Z__a7911b4c.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify lerner-mark

- **entity_slug**: `lerner-mark`
- **reason**: Tier-B: added Kensington/Stevensville/Potomac cities + Lerner Corp employer + Chesapeake Partners doppelganger negative_signals
- **rows_deleted_donations**: `75`
- **rows_deleted_review_queue**: `163` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T05-18-21Z__pre-reclassify-lerner-mark.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T05-18-21Z__20b98f28.db`
- **notes**: skipped(no-name-match)=2 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify mcguirk-terry

- **entity_slug**: `mcguirk-terry`
- **reason**: Tier-B: added 30327 Atlanta Buckhead to strong-zip
- **rows_deleted_donations**: `47`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T05-22-40Z__pre-reclassify-mcguirk-terry.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T05-22-40Z__f57b26d0.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · FROM-RAW

### 2026-05-25 — DELETION — reclassify simpson-bob

- **entity_slug**: `simpson-bob`
- **reason**: Tier-B: added 76102 office ZIP to strong-zip (Fort Worth XTO/TXO HQ); resolves 0 CONFIRMED / 8 PROBABLE anomaly
- **rows_deleted_donations**: `8`
- **rows_deleted_review_queue**: `0` (of which 0 had resolutions)
- **include_related**: `False`
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T05-30-56Z__pre-reclassify-simpson-bob.db`
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
- **snapshot_path**: `/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive/data/snapshots/2026-05-25T05-30-56Z__e1a2bec9.db`
- **notes**: skipped(no-name-match)=0 · min_date=default (no prior ingestion) · FROM-RAW
