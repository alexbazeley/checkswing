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
