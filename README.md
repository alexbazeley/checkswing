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

- **Phase 0** (setup): files created 2026-05-22. No code or data yet.
- **Phase 1** (Cohen pilot): not started.
- **Phase 1.5** (4 more pilot owners): not started.
- **Phase 2** (remaining 25 owners): not started.
- **Phase 3** (legislative cross-reference): not started.
- **Phase 4** (state/local): not started.
- **Phase 5** (maintenance automation): not started.

See `CHARTER.md` for phase definitions and exit criteria.
