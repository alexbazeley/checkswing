# MLB Owner FEC Donations Archive

A continuously-maintained, provenance-tracked archive of federal political
donations reported to the U.S. Federal Election Commission (FEC) by Major League
Baseball principal owners and their documented related entities.

Every record is matched against a defined, per-owner signal set before it is
attributed, carries a verifiable trail back to a specific FEC filing, and is
classified into one of three confidence tiers. The goal is **trustworthy
attribution**, not maximum volume.

The data powers the public **CheckSwing** dashboard (a static site on Cloudflare
Pages).

## What this is

- A SQLite database (`data/master.db`) plus per-owner CSV exports of donations attributed to MLB owners and their tracked related entities (spouses, family members, business PACs).
- Sourced exclusively from the [OpenFEC API](https://api.open.fec.gov/) (`api.open.fec.gov`).
- Conservatively attributed: a donor name match is necessary but never sufficient. Attribution additionally requires employer, occupation, or city+state corroboration (see [VERIFICATION.md](VERIFICATION.md)).
- Fully auditable: every ingestion, reclassification, and adjudication is logged in [`catalog/PROVENANCE_LOG.md`](catalog/PROVENANCE_LOG.md), and every row links to the raw FEC payload it came from.

## What this is not

- **Not** state or local campaign-finance data — federal only.
- **Not** "dark money" / 501(c)(4) giving — FEC-reported donations only.
- **Not** legislative cross-referencing — the archive records who gave to whom, never an interpretation of why or its policy effect.
- **Not** editorial. Any narrative analysis is built separately, on top of the data.
- **Not** a scraper of aggregator sites. OpenSecrets and the like can point to FEC records; they are never treated as the record itself.

## Confidence tiers

Every attributed record carries a status (full spec in [VERIFICATION.md](VERIFICATION.md)):

| Status | Criteria | In canonical export? |
|---|---|---|
| **CONFIRMED** | Name match + two confirming signals, or one uniquely-identifying signal | Yes |
| **PROBABLE** | Name match + one confirming signal | Yes, always labeled |
| **UNCERTAIN** | Name match alone, or contradicting signals | No — held in the review queue |

## Capabilities

- **Owner registry** — version-controlled YAML per owner (`owners/<slug>.yaml`) defining name variants and verifying signals; this is the attribution spec.
- **Incremental ingestion** — fetches new FEC filings per owner since the last run, with a trailing re-fetch window so late-filed contributions aren't missed; idempotent on FEC `transaction_id`, with FEC restatements recorded as `SUPERSEDED` rather than overwritten.
- **Committee enrichment** — recipient committees are enriched with identity and scale metadata, and linked to their real FEC filing PDFs.
- **Committee beneficiary view** — for enriched committees, the top recipients of that committee's Schedule B disbursements per cycle ("who this committee funded").
- **Review queue** — UNCERTAIN matches are routed to a queryable `review_queue` table for human adjudication; nothing ambiguous reaches the canonical export.
- **Provenance & safety** — raw FEC responses are persisted before parsing; `master.db` is snapshotted before every mutating run; reclassification is guarded against silently dropping rows whose raw payload is missing on disk.

## Repository layout

```
owners/            Per-owner YAML registry (the attribution spec)
scripts/           Ingestion, classification, enrichment, and CLI
data/master.db     SQLite source of truth (committed)
data/donations/    Per-owner CSV exports (regenerable)
catalog/           PROVENANCE_LOG.md and committee link catalog
mockup/            The CheckSwing static dashboard (Cloudflare Pages build root)
tests/             Test suite
```

Key documents: [GOVERNANCE.md](GOVERNANCE.md) (data-integrity rules),
[CHARTER.md](CHARTER.md) (scope and phases), [VERIFICATION.md](VERIFICATION.md)
(classification spec), [OWNER_SCHEMA.md](OWNER_SCHEMA.md) and
[DONATION_SCHEMA.md](DONATION_SCHEMA.md) (data schemas),
[SOURCES.md](SOURCES.md) (what counts as an authoritative source), and
[docs/CALIBRATION_PLAYBOOK.md](docs/CALIBRATION_PLAYBOOK.md) (how to tune an
owner's signal block).

## Setup

Requires Python 3.11.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then add your free FEC API key
```

Get a free FEC API key at <https://api.data.gov/signup/>.

## Usage

```bash
python -m scripts.cli validate                 # validate all owner YAMLs
python -m scripts.cli ingest <slug>            # incremental ingest for one owner
python -m scripts.cli ingest <slug> --full-refetch   # re-fetch full history
python -m scripts.cli audit <slug>             # read-only signal/classification audit
python -m scripts.cli reclassify <slug>        # re-apply classifier to existing raw (no FEC calls)
python -m scripts.cli raw-coverage             # report rows whose raw payload is missing on disk
python -m scripts.cli review-queue             # list open review-queue items
python -m pytest -q                            # run the test suite
```

`ingest` reads `audit.last_ingestion` and fetches only since that date; pass
`--full-refetch` to pull complete history (e.g., after a classifier change).

## Deployment

The dashboard ships as a static site on **Cloudflare Pages** via direct Git
integration. On every push to `main`, Cloudflare runs the build and deploys
`mockup/`:

- **Production branch:** `main`
- **Build command:** `python -m pip install -r requirements.txt && python mockup/build_data.py`
- **Build output directory:** `mockup`
- **Environment:** `PYTHON_VERSION = 3.11`

`mockup/build_data.py` regenerates `mockup/data.json` from `master.db` at build
time, so the generated JSON is not committed. `mockup/_headers` sets the
Content-Security-Policy, cache controls, and `X-Frame-Options: DENY`.

The only secret is a GitHub Actions secret `FEC_API_KEY`, used by the refresh
workflow. It never appears in the Cloudflare environment.

## Continuous integration & refresh

- **CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs `pytest` and `validate` on every pull request and push to `main`.
- **Refresh** ([`.github/workflows/refresh.yml`](.github/workflows/refresh.yml)) runs the self-maintaining federal refresh layer monthly (1st, and on-demand via `workflow_dispatch`): incremental fetch per owner → classify → rebuild `data.json` → push, which triggers a redeploy.
- **State refresh** ([`.github/workflows/refresh-state.yml`](.github/workflows/refresh-state.yml)) runs the Phase-4 state refresh monthly (2nd, staggered after the federal run; and on-demand): for each adopted state (CA · CAL-ACCESS, TX · TEC) download the official bulk export → stream + re-ingest → commit `data/state.db`. LFS-free (it never touches `master.db`), so it consumes no LFS bandwidth.

## Why `master.db` is committed

`data/master.db` is the project's **durable source of truth** for both the
archive and the dashboard. `mockup/data.json` is regenerated from it on every
build, so committing the JSON would only thrash the diff. Raw FEC payloads
(`data/raw/`) stay out of git — they are large and serve as best-effort ground
truth for re-verification, but are **not** a guaranteed backup (a minority of
historical rows reference raw files no longer on disk). The database is therefore
not assumed reconstructible from raw alone, and `reclassify` is guarded against
silently dropping rows whose raw is missing (`raw-coverage` audits the gap).

## Data, sources, and accuracy

All underlying donation data is public record published by the FEC. Attribution —
deciding which donations belong to which owner — is this project's own analysis,
governed by the rules in [GOVERNANCE.md](GOVERNANCE.md) and
[VERIFICATION.md](VERIFICATION.md). PROBABLE records are always labeled; UNCERTAIN
matches are never published. Corrections are tracked in
[`catalog/PROVENANCE_LOG.md`](catalog/PROVENANCE_LOG.md).

## Contributing

Read [GOVERNANCE.md](GOVERNANCE.md) before changing anything that touches data,
classification, or provenance — those rules are non-negotiable. `validate` and
`pytest` must pass before every commit; CI enforces both.
