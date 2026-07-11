# Contributing

This is a provenance-tracked research archive; **data integrity is the product.**
Read [GOVERNANCE.md](GOVERNANCE.md) (the non-negotiable rules), [CHARTER.md](CHARTER.md)
(scope), and [VERIFICATION.md](VERIFICATION.md) (the classification spec) before
changing anything that touches data, classification, or provenance.
[ARCHITECTURE.md](ARCHITECTURE.md) is the layout map.

## Setup

```bash
python3.11 -m venv .venv && source .venv/bin/activate   # 3.11 — see .python-version
pip install -r requirements.txt
git config core.hooksPath .githooks                     # enable the pre-commit gate (§2.7)
```

FEC API access needs `FEC_API_KEY` in `.env` (see `.env.example`). Never commit
secrets.

## The gates (before every commit)

```bash
python -m scripts.cli validate      # owner YAMLs + legislation must pass
python -m pytest -q                 # the full suite must pass
```

The `.githooks/pre-commit` hook runs `validate` automatically once enabled; CI
runs `pytest` on every PR as the backstop.

## Conventions

- **One change per PR**, smallest reviewable unit; branch `fix/...` or `feat/...`;
  PRs target `main` (never push directly).
- **Every data mutation** snapshots the DB first and appends to
  `catalog/PROVENANCE_LOG.md` — use the gated CLI ops (`reclassify`, `attribute`,
  `exclude`, the `backfill-*` / `dedupe-*` commands), don't hand-roll SQL.
- **Schema changes** go through the `schema_version` migration pattern in
  `scripts/db.py` (or `state_db.py`) with tests — never ad-hoc `ALTER`s.
- **Neutral register.** Metadata, docs, and dashboard copy stay in a flat
  law-librarian voice. Interpretation lives only in `reports/`, never in a row,
  node, or catalog field.
- **When a fix changes published totals,** restate the affected dashboard/README
  numbers in the same PR, log the correction in `PROVENANCE_LOG.md`, and add a
  plain-language methodology-correction note where the site documents methodology.

## The binary-DB PR gotcha

`master.db` (LFS) and `state.db` / `legislation.db` (plain blobs) are binary.
**Never merge a PR whose binary DB has diverged from `main`** — a binary can't be
3-way merged, so a stale committed DB silently clobbers the newer one. Instead
**rebase onto `main` and re-run the ingest/migration** so the committed DB is
regenerated from the current data. Prefer keeping data mutations and code changes
in *separate* PRs; a code-only PR never carries a binary-DB diff.
