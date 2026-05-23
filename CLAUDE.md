# CLAUDE.md — Operating Instructions for the MLB Owner FEC Donations Archive

This file governs how any Claude session — Cowork, Claude Code, or any subagent — must behave when working inside `fec-donations-archive/`. These rules override default behaviors. Read this file before doing any work in this folder.

The premise of this archive is that Tipping Pitches, and eventually others, will cite individual donation records from it. The product is **trustworthy attribution** — every record being correctly tied to a real owner with a real, verifiable signal trail back to a real FEC filing. Speed is a distant second priority. If a tradeoff exists between "ingest more" and "get it right," Claude must choose "get it right."

## 1. The non-negotiables

These are hard rules, not preferences. Violating them is grounds for the work to be discarded.

### 1.1 No attribution without two verifying signals
A donor name matching an owner's name is **necessary but not sufficient**. A FEC record is attributed to an owner only when it matches the owner's `verifying_signals` block in `owners/<slug>.yaml`:

- **Required**: normalized name match
- **PLUS at least one of**: known employer string, known occupation, known city+state combination
- **CONFIRMED status** requires two or more confirming signals OR one `strong_signal` match (e.g., a uniquely-identifying employer string).

A name match alone is never enough. Ever. There are hundreds of John Smiths in the FEC database.

### 1.2 Three-tier verification status, recorded on every record
- **CONFIRMED** — Name match + two or more confirming signals (or one strong unique signal). Exported as canonical.
- **PROBABLE** — Name match + one confirming signal. Included in exports, **always flagged with status**. Never described as "Owner X donated" without the qualifier.
- **UNCERTAIN** — Name match alone, or contradicting signals. Lives in the review queue, **never** in the canonical export.

Status can be demoted but not silently promoted. Promotions must be logged in `catalog/PROVENANCE_LOG.md`.

### 1.3 Every record traces to a specific FEC filing
Every donation row in the database must carry: `transaction_id`, `committee_id` (recipient), `filing_id` (source FEC filing), `original_filing_date`, `ingested_at`, `raw_payload_path` (link to the preserved JSON in `data/raw/`). If any of these are missing, the row does not enter the database.

### 1.4 Raw FEC API responses are preserved forever
Every API call's response is saved verbatim to `data/raw/<owner-slug>/<UTC-timestamp>__<endpoint>.json` before any parsing. The database is a derivative; the raw payloads are the ground truth. The project must be reconstructible from `data/raw/` alone.

### 1.5 Idempotent ingestion
Re-running ingestion for the same period must not duplicate rows. FEC `transaction_id` is the primary key. If FEC restates a transaction, the new version is recorded with `status = superseded` on the old row — the old row is never deleted.

### 1.6 Snapshot before each ingestion run
Before each ingestion, snapshot the current `data/master.db` to `data/snapshots/YYYY-MM-DDTHH-MM-SSZ.db`. Enables rollback and reproducibility of past states.

### 1.7 No silent expansion of tracked entities
Adding a spouse, family member, business entity, or PAC to an owner's tracked-set is a **deliberate, version-controlled change** to that owner's YAML, with a `change_log` entry inside the YAML explaining the justification and source. Never widen attribution scope inside an ingestion run. Widening scope is itself an editorial act and gets a paper trail.

### 1.8 Editorial commentary is separated from the data
The database stores donations and their FEC-given attributes. It does not store editorial framing, narrative angles, or "what this means for labor." The `notes` field on records and the `notes` field on owner YAMLs are for narrow factual clarifications (e.g., "two donations on same day to same committee, possibly a reporting correction") — not interpretation.

Editorial analysis of donation patterns lives in `reports/`, clearly labeled as interpretation built **on top of** the data.

### 1.9 Conservative tie-breaks
When a match is ambiguous — partial signal overlap, contradicting employers across filings, unusual city — the record goes to UNCERTAIN and the review queue. Default to "don't attribute" rather than "probably them." The cost of a missed donation is far smaller than the cost of a misattribution that ends up cited on the show.

### 1.10 No deletion without record
If a donation is removed (FEC retracts, owner relationship reconsidered, misattribution discovered), the row is marked `superseded` with a reason in `catalog/PROVENANCE_LOG.md`. Never hard-delete a record that has ever existed in the database.

## 2. Workflow rules

### 2.1 Before adding a new owner to the registry
- Confirm they are a principal/majority owner per current public ownership records.
- Document the ownership relationship in the owner YAML's `sources` block.
- Populate `verifying_signals` from at least two independent public sources (e.g., a profile in major business press + an MLB-published ownership page + their corporate website).
- Mark `status: queued` until the YAML is fully populated and reviewed, then move to `pilot` or `active`.

### 2.2 Before each ingestion run
- Read the owner YAML's `name_variants` and `verifying_signals` — they are the spec.
- Confirm no pending review-queue items for this owner are unresolved that would change matching logic.
- Snapshot the master DB (rule 1.6).

### 2.3 During ingestion
- Persist every API response raw before parsing (rule 1.4).
- Score every record against the owner's signals.
- Insert CONFIRMED + PROBABLE into the DB; route UNCERTAIN to the review queue with full raw payload.
- Never modify the owner YAML mid-run.

### 2.4 After ingestion
- Append a run entry to `catalog/PROVENANCE_LOG.md` with: owner, period queried, raw API calls made, rows added per tier, anomalies.
- If any UNCERTAIN entries were created, surface the count.
- Refresh per-owner CSV exports in `data/donations/<slug>/`.

### 2.5 Resolving a review-queue item
- Inspect the raw payload.
- Either: (a) add a new confirming signal to the owner YAML with a `change_log` entry and re-score, or (b) mark UNCERTAIN as DISCARDED with reason. Never promote without strengthened evidence.
- Log the resolution in `catalog/PROVENANCE_LOG.md`.

### 2.6 When you're uncertain
- Use `UNCERTAIN` status honestly.
- Put a note in the owner YAML's `notes` field if a pattern is emerging.
- Flag it in `catalog/REVIEW_QUEUE.md` for a human pass.

## 3. Prohibited behaviors

- Attributing a donation to an owner because the name matches and "it's probably them."
- Inferring an owner's employer or city from training-data knowledge instead of from the owner YAML's documented signals.
- Adding new name variants, employer strings, or signals to an owner YAML mid-ingestion based on what the data "looks like."
- Treating OpenSecrets, news summaries, or aggregator sites as a primary source of donation facts. They can be pointers to FEC records; they are never the record itself.
- Auto-attributing donations from a PAC to an individual owner without an `ownership_link_documented` entry in the related_entities block.
- Aggregated employer-based queries against FEC (e.g., "everyone reporting employer = New York Mets") without a named-individual anchor. That is a different project, not this one.
- Smoothing or "cleaning" name variants in the canonical database. Store what FEC filed; normalize only at query time.
- Filling required fields with plausible guesses. Use `unknown` or `null`.
- Adding interpretive prose to the `notes` field of a donation or owner YAML.
- Deleting rows or files without a `PROVENANCE_LOG.md` entry.

## 4. Subagent and Claude Code delegation

When work is delegated to a subagent or a Claude Code session:
- The session must be given a reference to this CLAUDE.md and the relevant schema files (`OWNER_SCHEMA.md`, `DONATION_SCHEMA.md`, `VERIFICATION.md`).
- The session must return structured findings: what was fetched, what was classified at each tier, what landed in the review queue, what changed in the DB.
- The orchestrating session is responsible for any signal-set expansions or status promotions. Subagents propose; orchestrators promote.

## 5. When in doubt

Stop. Ask the user. The archive is supposed to outlast any individual session and any individual ingestion run. A clarifying question now is cheaper than purging contaminated data later.

Specifically, **always** stop and ask before:
- Adding a new tracked entity (spouse, family member, business entity, PAC) to an owner profile.
- Promoting an UNCERTAIN record to PROBABLE or CONFIRMED.
- Adopting a new signal source (e.g., starting to use a state campaign-finance database — that's a scope expansion).
- Re-classifying a previously CONFIRMED record.
- Backfilling history beyond the period the owner held the team (do we want pre-ownership giving? — case by case).

## 6. Scope drift signals

This project is federal-only, FEC-only, principal-owners-only. If a session finds itself doing any of the following, it has drifted and should stop:

- Cross-referencing donations with legislation, votes, or policy outcomes (that is Phase 3, not yet active).
- Pulling state or local campaign finance (that is Phase 4).
- Looking up team-affiliated charitable foundations (that is a separate project — IRS 990s).
- Writing narrative analysis of "what these donations mean."
- Building UI / a website / a publishable dashboard. The DB and CSVs are the deliverable; presentation is downstream.

Drift kills projects. When in doubt, return to CHARTER.md and verify the work is in scope.
