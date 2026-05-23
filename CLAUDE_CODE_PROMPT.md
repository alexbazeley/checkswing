# Claude Code Starter Prompt

This file contains the prompt to paste into Claude Code to bootstrap the implementation. The prompt is self-contained — it assumes Claude Code has no prior context on this project.

Open this folder in Claude Code (`cd "/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive"`), then paste everything between the `---` markers below into Claude Code.

---

I want your help building the MLB Owner FEC Donations Archive. The scope, rules, and architecture are already defined — your job is to implement the pipeline correctly.

## First: read these files, in order, in full. Do not skip any.

1. `./CLAUDE.md` — operating rules and non-negotiables. The §1 rules are not flexible.
2. `./CHARTER.md` — what's in scope and what isn't.
3. `./VERIFICATION.md` — three-tier classification rules. This is the spec your classifier must implement exactly.
4. `./OWNER_SCHEMA.md` — schema for owner entity YAMLs.
5. `./DONATION_SCHEMA.md` — SQLite schema your ingestion writes to.
6. `./SOURCES.md` — what counts as an authoritative source.
7. `./NAMING.md` — file naming conventions.
8. `./owners/_registry.yaml` — pilot and queued owners.
9. `./owners/_template.yaml` — owner YAML template.
10. `./owners/cohen-steven.yaml` — the worked pilot example. This is what every owner YAML will eventually look like.
11. `./catalog/PROVENANCE_LOG.md` and `./catalog/REVIEW_QUEUE.md` — the audit logs you'll append to.

After reading, respond with a five-bullet summary covering:
- The attribution standard (when a donation can be marked CONFIRMED vs PROBABLE vs UNCERTAIN)
- The three "never" rules from CLAUDE.md that you find most important
- The directory structure you'll create
- The pilot scope (which owner you start with)
- The primary data source

If you misunderstand anything in those files, ask before writing code. I'd rather pause for ten minutes than ingest a thousand misattributed records.

## Phase 0 tasks (do these once aligned)

1. **FEC API key.** Ask me if I have one. If not, point me at https://api.data.gov/signup/ — it's free and instant. Once I have it, store it in a `.env` file (`FEC_API_KEY=...`) and add `.env` to `.gitignore`.

2. **Python environment.** Create `requirements.txt`. My preferred dependencies:
   - `requests` — HTTP client for OpenFEC API
   - `pyyaml` — owner YAML parsing
   - `python-dotenv` — env var loading
   - `click` — CLI
   - `rapidfuzz` — fuzzy matching, used only for discovery aid (not classification)
   - `tabulate` — terminal output
   - `pytest` — testing
   
   No exotic deps. No ORMs — `sqlite3` from stdlib is fine for this scale.

3. **Directory structure.** Create:
   ```
   data/
     master.db                  (empty SQLite, created by db.py init)
     raw/<owner-slug>/          (raw API JSON responses)
     snapshots/                 (pre-run DB snapshots)
     donations/<owner-slug>/    (CSV exports)
     donations/_aggregate/      (cross-owner aggregates)
   scripts/                     (Python source)
   reviews/                     (working files for resolving review-queue items)
   reports/                     (rendered narrative summaries — Phase 3+)
   tests/                       (unit tests)
   ```

4. **Git.** `git init` if not already. First commit is the current state of base files. `.gitignore` includes `.env`, `data/master.db`, `data/snapshots/`, `data/raw/`, `__pycache__/`, `.venv/`, but NOT the YAMLs (those are version-controlled).

## Phase 1 deliverables — Cohen pilot

Build the minimum end-to-end pipeline, against `cohen-steven` only. Show me results before applying to anyone else.

### Module breakdown

**`scripts/db.py`** — SQLite schema and migrations.
- Implement the tables defined in `DONATION_SCHEMA.md`: `donations`, `ingestion_runs`, `entities`, `review_queue`.
- A `db.init()` function creates the schema idempotently.
- A `db.snapshot()` function copies `master.db` to `data/snapshots/<UTC-timestamp>__<run-id>.db`.
- A `db.refresh_entities()` function reads all `owners/*.yaml` and rebuilds the `entities` table.

**`scripts/validate_owners.py`** — validates every YAML in `owners/` against `OWNER_SCHEMA.md` rules. Run at the start of every ingestion. Fail loudly on schema violations.

**`scripts/fetch_fec.py`** — OpenFEC client and ingestion.
- Loads an owner YAML.
- Queries `/schedules/schedule_a/` for each name variant.
- Persists every API response raw to `data/raw/<slug>/<timestamp>__schedule_a.json` BEFORE parsing (this is CLAUDE.md §1.4 — non-negotiable).
- Respects rate limits (1000/hour default — be polite).
- Returns a list of unique raw records (deduplicated by `transaction_id`).
- Idempotent: re-running the same query should not duplicate rows in the DB.

**`scripts/resolve_entities.py`** — classifier. Implements VERIFICATION.md exactly.
- Inputs: a raw FEC record + an owner YAML (or related-entity block).
- Outputs: `(status, status_reason, signals_matched)` tuple.
- The classification rules in VERIFICATION.md are the spec. Do not deviate.
- Include unit tests in `tests/test_resolve_entities.py` covering:
  - Two confirming signals → CONFIRMED
  - One strong signal → CONFIRMED
  - One confirming signal → PROBABLE
  - Name only → UNCERTAIN
  - Suffix mismatch → UNCERTAIN regardless of other signals
  - Spouse name collision → routes to spouse entity, not owner
  - Address contradiction without documentation → UNCERTAIN

**`scripts/ingest.py`** — the orchestrator.
- Takes an entity slug.
- Validates owner YAMLs (calls `validate_owners`).
- Snapshots the DB (calls `db.snapshot`).
- Fetches via `fetch_fec`.
- Classifies via `resolve_entities`.
- Writes CONFIRMED + PROBABLE to `donations` table.
- Writes UNCERTAIN to `review_queue` table AND appends to `catalog/REVIEW_QUEUE.md`.
- Logs the run in `ingestion_runs` AND appends to `catalog/PROVENANCE_LOG.md`.

**`scripts/export.py`** — CSV generation.
- Per-entity export at `data/donations/<slug>/all.csv` (CONFIRMED + PROBABLE, status column always present).
- Per-cycle export at `data/donations/<slug>/by_cycle/<cycle>.csv`.

**`scripts/cli.py`** — typer/click CLI entry point. Commands:
- `validate` — runs `validate_owners`
- `init` — `db.init()`
- `ingest <slug>` — full pipeline for one entity
- `ingest --all-pilot` — runs for every entity marked `pilot` in `_registry.yaml`
- `export <slug>` — refresh CSVs
- `review` — list open review queue items
- `status` — show per-owner ingestion freshness

### After the first Cohen ingestion

Before running anything else:

1. Show me total counts at each tier: `CONFIRMED: N`, `PROBABLE: N`, `UNCERTAIN: N`.
2. Show me 5 random sample records from each tier so I can sanity-check the matching.
3. Identify any common reasons for UNCERTAIN (e.g., many records from a third city we didn't anticipate).
4. Compare the CONFIRMED total to OpenSecrets' public summary of Cohen — same order of magnitude? If off by 10x in either direction, the matching is wrong.

We resolve any matching surprises BEFORE expanding to the other pilot owners.

## Working principles

- The CLAUDE.md §1 rules are not optional. Re-read §1 before any architecturally significant decision.
- When uncertain about a match, route to UNCERTAIN. Never confirm an ambiguous record.
- Preserve raw API responses always. The DB is rebuildable from raw + YAMLs alone — verify this with a `reconstruct` test command if it's quick to add.
- Every ingestion run is logged. Every signal change is logged. Every status promotion is logged.
- No hardcoded owner-specific logic in Python code. The owner YAMLs are the spec; code iterates over them generically.

## What I do NOT want

- Don't add any owner past `cohen-steven` until I sign off on Cohen results.
- Don't try to handle state or local campaign finance — Phase 4.
- Don't fuzzy-match employer strings aggressively. Normalize whitespace and case; substring or exact match only; no stemming, no word removal.
- Don't write tests for hypothetical future features. Test what you're building now.
- Don't write any cross-referencing logic against legislation — Phase 3.
- Don't build a UI, website, or dashboard — the deliverable is the DB and CSVs.

## Open questions you can ask me at the start

- My FEC API key (or whether I need help getting one)
- Whether I want a `dry-run` mode that fetches but doesn't write to DB (probably yes — useful for testing)
- How to handle FEC's edge cases when we hit them (rather than guessing)

## Start

Step 1: read the files listed above. Step 2: respond with your five-bullet summary. Step 3: wait for me to confirm alignment before writing any code.

---

(End of prompt. Everything below this line is for human reference, not part of the prompt to Claude Code.)

## How to invoke

```
cd "/Users/abaze/Documents/Claude/Projects/Tipping Pitches/fec-donations-archive"
claude
```

Then paste the prompt above (between the `---` markers).

## After the first session

When Claude Code finishes the Cohen pilot, the artifacts you should see:
- `requirements.txt`, `.env.example`, `.gitignore`
- `scripts/*.py` files implementing the spec
- `tests/` with unit tests for the classifier
- `data/master.db` populated with Cohen records
- `data/donations/cohen-steven/all.csv` and `by_cycle/<cycle>.csv`
- `data/raw/cohen-steven/*.json` (the raw FEC payloads)
- New entries in `catalog/PROVENANCE_LOG.md` for the ingestion run
- Any UNCERTAIN records in `catalog/REVIEW_QUEUE.md`

You then sit down with the UNCERTAIN list and decide which to add new signals for vs which to discard. That review session is itself logged in PROVENANCE_LOG.

Once Cohen looks right, the prompt to expand to the other four pilots is essentially: "apply the same pipeline to crane-jim, henry-john, castellini-bob, and steinbrenner-hal — populate their YAMLs first using the same Tier 2 sources I've already approved in SOURCES.md, then ingest." Phase 2 expansion (the remaining 25 owners) is structurally identical work, mostly mechanical YAML population.
