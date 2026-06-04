# Contributor & agent guide

This repository maintains a provenance-tracked archive of FEC political
donations by MLB principal owners. Correct attribution is the product; data
integrity rules are non-negotiable.

Before changing anything that touches data, classification, or provenance, read:

- **[GOVERNANCE.md](GOVERNANCE.md)** — the data-integrity rules (non-negotiable).
- **[CHARTER.md](CHARTER.md)** — scope and project phases.
- **[VERIFICATION.md](VERIFICATION.md)** — the three-tier classification spec.
- **[OWNER_SCHEMA.md](OWNER_SCHEMA.md)** / **[DONATION_SCHEMA.md](DONATION_SCHEMA.md)** — data schemas (federal/FEC, `master.db`).
- **[STATE_DONATION_SCHEMA.md](STATE_DONATION_SCHEMA.md)** — Phase 4 state campaign-finance schema (`data/state.db`, CA/CAL-ACCESS pilot; separate from `master.db`).
- **[docs/CALIBRATION_PLAYBOOK.md](docs/CALIBRATION_PLAYBOOK.md)** — how to tune an owner's signal block (the `audit` → `reclassify` loop; applies to state data too).

Conventions:

- `python -m scripts.cli validate` must pass before any commit.
- `python -m pytest -q` must pass before any commit.
- Never commit secrets; FEC API access uses `FEC_API_KEY` (see `.env.example`).
- Data-mutating operations snapshot `master.db` first and log to `catalog/PROVENANCE_LOG.md`.
