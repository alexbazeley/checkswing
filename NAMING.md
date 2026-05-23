# NAMING — File and Slug Conventions

## Owner slugs

Format: `lastname-firstname` — lowercase, hyphen-separated, filename-safe.

Examples:
- `cohen-steven`
- `henry-john`
- `steinbrenner-hal`

For owners with common surnames where ambiguity with another tracked owner is possible, append a disambiguator: `angelos-john-p` vs `angelos-louis`. For now, no MLB owners require this; revisit if needed.

For related entities (spouses, children, business entities): use the same `lastname-firstname` pattern for individuals, and a hyphenated company name for businesses.

Examples:
- `cohen-alexandra` (Steven Cohen's spouse)
- `point72-pac`
- `liberty-media-pac`

## Owner YAML filenames

`owners/<slug>.yaml`. The slug inside the file must match the filename.

The two reserved files in `owners/`:
- `_registry.yaml` — master list of all tracked owners and their statuses
- `_template.yaml` — template for new owner entries

## Raw payload filenames

`data/raw/<slug>/<UTC-timestamp>__<endpoint>.json`

- Timestamp format: `YYYY-MM-DDTHH-MM-SSZ` (filename-safe ISO 8601 — colons replaced with hyphens because Windows hates them).
- Endpoint: a slug for the FEC endpoint (`schedule_a`, `schedule_e`, `committees`, etc.).

Examples:
- `data/raw/cohen-steven/2026-05-22T18-30-15Z__schedule_a.json`
- `data/raw/cohen-steven/2026-05-22T18-32-08Z__committees.json`

## Snapshot filenames

`data/snapshots/<UTC-timestamp>__<run-id>.db`

Examples:
- `data/snapshots/2026-05-22T18-30-00Z__a1b2c3d4.db`

## CSV exports

- `data/donations/<slug>/all.csv` — full per-entity export
- `data/donations/<slug>/by_cycle/<cycle>.csv` — partitioned by election cycle
- `data/donations/_aggregate/by_owner.csv` — top-level aggregate
- `data/donations/_aggregate/by_owner_with_probable.csv` — variant including PROBABLE

## Reports

`reports/<slug>__<YYYY-MM-DD>.md` for per-entity narrative summaries.

`reports/_episode-briefs/<episode-slug>__<YYYY-MM-DD>.md` for episode-specific cross-cuts.

Reports are interpretation built on top of the data; the data files are not allowed to carry interpretation (CLAUDE.md §1.8).

## Catalog files

- `catalog/PROVENANCE_LOG.md` — append-only log of ingestion runs, signal changes, status promotions, review-queue resolutions.
- `catalog/REVIEW_QUEUE.md` — current open UNCERTAIN records awaiting adjudication.
- `catalog/CHANGES.md` — Phase-level milestones (Phase 1 complete, Phase 2 started, etc.). Append-only.

## Slugs in code

Code (Python) references slugs as strings. Never hardcode an owner-specific string anywhere except the owner YAML. The pipeline iterates `owners/*.yaml` to discover what to track — no list of owners lives in code.
