# CheckSwing

A self-contained, static, single-page prototype for **CheckSwing** —
a public-facing dashboard layered over the MLB Owner FEC Donations Archive.

This is a **presentation layer**, not canonical data — the database and
per-owner CSV exports remain the source of truth (see GOVERNANCE.md §6).

## What's here

```
mockup/
├── build_data.py    # exports data/master.db → data.json, parses catalog/PROVENANCE_LOG.md → provenance.json
├── data.json        # baked donations + owners + runs snapshot (regenerated each build)
├── provenance.json  # parsed audit log (regenerated each build); lazy-fetched by /#/changelog
├── index.html       # single-file SPA: HTML + CSS + JS inline
├── serve.sh         # one-line local server
├── assets/          # favicon, OG image, hero photo, etc.
└── README.md        # this file
```

## Run it

```bash
./mockup/serve.sh
# → http://localhost:8000
```

Or any static server pointed at `mockup/`. The page fetches `data.json` at
load, so `file://` won't work in most browsers.

## Brand assets

The mockup expects these files in `mockup/assets/`:

| File | What it is | Used by |
|---|---|---|
| `hero-ballpark.webp` | The grayscale ballpark / luxury-suite hero photo (WebP preferred for size; JPG fallback acceptable — update the `background-image` URL in `index.html` to match) | League index hero |
| `checkswing-icon.svg` | Brand mark (crimson tile + cream serif "C" + accent dot). Source-of-truth vector. | Browser favicon (`<link rel="icon" type="image/svg+xml">`) |
| `checkswing-icon.png` | 512×512 rasterization of the SVG mark. | Apple-touch-icon, masthead `<img>`, PNG favicon fallback |
| `og-image.svg` | 1200×630 social-share card — wordmark, deck, stat strip, URL footer. Source-of-truth vector. | Editing source; regenerated to PNG via `scripts/gen_brand_pngs.py` |
| `og-image.png` | 1200×630 PNG rasterization. | `og:image` / `twitter:image` meta tags (PNG wins compatibility vs SVG) |

If `hero-ballpark.webp` is missing, the hero falls back to a dark warm
background so the headline stays readable.

To regenerate the brand PNGs from the SVG sources (matched visually via PIL):

```bash
python3 scripts/gen_brand_pngs.py
```

## Refresh the data

After an ingestion run, regenerate the snapshot:

```bash
python3 mockup/build_data.py
```

`data.json` is reproducible — it's a derivative of `data/master.db` and the
owner YAMLs. It includes only CONFIRMED + PROBABLE donations (matches the
export policy in DONATION_SCHEMA.md). UNCERTAIN records stay in the review
queue and never appear here.

## Design intent

The verification rubric is the editorial backbone of the project — so it's
the editorial backbone of the UI:

- **Every donation row carries a status chip.** PROBABLE is shown by default,
  never hidden behind a toggle. Hiding it would suggest we're embarrassed
  by it; we're transparent about it.
- **Every donation drawer shows the matched signals.** The "Why this is
  confirmed" block is the trust anchor of the record view.
- **Methodology is one click from every page.** The masthead link, the
  footer, and the verification cards on owner pages all surface it.
- **Color is reserved for meaning.** Party colors (DEM blue, REP red, OTH
  grey) carry data. Deep crimson (`--brand`) is reserved for the CheckSwing
  identity itself. Burnt sienna (`--accent`) carries interactive UI (links,
  CSV buttons, focus states). The two halves of the project read as a pair:
  the **Federal** level keeps the crimson (`--federal`), the **State** level
  takes a deep teal-green (`--state`) — distinct from both the brand and the
  party colors, so the level you're in is always legible at a glance.
- **Display type.** Headlines and large numerals are set in **Fraunces**
  (`--display`); running serif text stays in Source Serif 4 (`--serif`); UI
  and labels in Inter (`--sans`); figures in JetBrains Mono (`--mono`).
- **Tabular figures everywhere.** Money columns align on the decimal point
  in tables, sparklines, the timeline chart, the drawer.

## Routes

The site has a **Home / Federal / State** information architecture. `#/` is a
project-level front door that previews both levels; the federal FEC dashboard
lives at `#/federal`, and the state campaign-finance section at `#/states`.
Federal and State are parallel siblings, each with its own graphic hero and a
sticky section sub-nav exposing its sub-pages.

| Route | What it is |
|---|---|
| `#/` | Home — project front door: photo hero, combined federal+state toplines, two level-preview cards (Federal / State), three-tier verification trust strip |
| `#/federal` (alias `#/league`) | Federal dashboard — graphic hero + sub-nav, KPIs, recent-donations feed, league political map, cycle chart, heatmap, owners table |
| `#/states` (alias `#/state`) | State campaign-finance overview — graphic hero + sub-nav, toplines, by-jurisdiction cards, by-owner table, all state contributions |
| `#/owner/<slug>` | Owner detail — hero stats, log-scale timeline with tenure shading, top recipients, verification preview, complete donations table (federal + a state-finance panel) |
| `#/team/<slug>` | Team rollup — combined stats for teams with multiple tracked owners, multi-tenure timeline, combined recipients and donations |
| `#/cycle/<year>` | Election-cycle detail — biggest owner donors that cycle, top recipients, all-cycle donations table |
| `#/committee/<id>` | Committee detail — which MLB owners gave to this recipient, ranked, with all donations |
| `#/state/<code>` | One jurisdiction's full state record (e.g. `#/state/CA`) |
| `#/state-recipients` | Every distinct state recipient committee/candidate/ballot-measure — sortable/filterable |
| `#/state-recipient/<key>` | One state recipient's detail |
| `#/methodology` | The three-tier rubric, in/out of scope, reproducibility |
| `#/about` | Coverage snapshot |
| `#/whats-new` | Most recent refresh batch — new donations, owners affected, top 5 by amount, owner-activity rollup |
| `#/recipients` | Every distinct recipient committee — sortable / filterable table with type (Candidate / Party / PAC / Other), party, owner count, cycles active. Rows click through to `#/committee/<id>`. |
| `#/runs` | Pipeline → Ingestion runs tab — every FEC API session with counts |
| `#/changelog` | Pipeline → Audit log tab — every entry from `catalog/PROVENANCE_LOG.md`, filterable by TYPE + subject search |
| `?d=<txn_id>` | Drawer permalink — open any donation by transaction ID. Includes a "Copy permalink" button inside the drawer; falls back to a friendly "Donation not found" body for stale IDs. |

## Inflation toggle

The masthead pill (`$` / `$↑`) flips every dollar amount on every page
between **nominal** (as filed with the FEC) and **real** (CPI-adjusted to
the current dollar). State persists in `localStorage` under `cs.dollars`.

Implementation lives in `scripts/dollars.py`: hand-maintained BLS CPI-U
table (annual averages 2000–2024, monthly proxies for 2025–2026). When
new monthly CPI prints land, update the table and bump `CPI_LATEST_MONTH`
— the dashboard footnote pulls from there automatically.

`mockup/build_data.py` pre-bakes a parallel `*_2026` field next to every
nominal $ field at build time, so the frontend just picks which to read.
Per-donation `amount_2026`, per-owner `total_amount_2026` / `party_dollars_2026`
/ `cycle_dollars_2026` / `cycle_party_dollars_2026`, league-level mirrors,
and `recipients[].total_amount_2026` are all present.

## Cycle heatmap

The League page hosts a 36 owners × 14 cycles SVG grid below the cycle
chart. Cell brightness encodes log-banded dollars; cell hue blends from
DEM-blue → neutral → REP-red by party majority within that owner-cycle.
Row labels sort by total / name / team via the masthead-style pill group.
Click a cell or row label to jump to that owner's full page.

| Keyboard | Action |
|---|---|
| `⌘K` / `Ctrl+K` | Open search palette (owners, teams, committees) |
| `/` | Open search (when not in an input) |
| `↑↓` / `Enter` / `Esc` | Search nav / select / close |
