# The Owner's Box

A self-contained, static, single-page prototype for **The Owner's Box** —
a public-facing dashboard layered over the MLB Owner FEC Donations Archive.

This is a **presentation layer**, not canonical data — the database and
per-owner CSV exports remain the source of truth (see CLAUDE.md §6).

## What's here

```
mockup/
├── build_data.py    # exports data/master.db → data.json
├── data.json        # baked snapshot (regenerate after ingestion)
├── index.html       # single-file SPA: HTML + CSS + JS inline
├── serve.sh         # one-line local server
├── assets/          # logo, hero photo, etc.
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
| `owners-box-icon.png` *(optional)* | Designed logo icon to replace the inline SVG in the masthead | Masthead — see the swap instructions in the `index.html` masthead block |

If `hero-ballpark.webp` is missing, the hero falls back to a dark warm
background so the headline stays readable. If the icon PNG is absent, the
inline SVG fallback in the masthead renders instead.

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
  grey) carry data. Deep crimson (`--brand`) is reserved for The Owner's Box
  identity itself. Burnt sienna (`--accent`) carries interactive UI (links,
  CSV buttons, focus states).
- **Tabular figures everywhere.** Money columns align on the decimal point
  in tables, sparklines, the timeline chart, the drawer.

## Routes

| Route | What it is |
|---|---|
| `#/` | League index — photo hero, KPIs, recent-donations feed, league political map, cycle chart, owners table |
| `#/owner/<slug>` | Owner detail — hero stats, log-scale timeline with tenure shading, top recipients, verification preview, complete donations table |
| `#/team/<slug>` | Team rollup — combined stats for teams with multiple tracked owners, multi-tenure timeline, combined recipients and donations |
| `#/cycle/<year>` | Election-cycle detail — biggest owner donors that cycle, top recipients, all-cycle donations table |
| `#/committee/<id>` | Committee detail — which MLB owners gave to this recipient, ranked, with all donations |
| `#/methodology` | The three-tier rubric, in/out of scope, reproducibility |
| `#/about` | Coverage snapshot |
| `?d=<txn_id>` | Drawer permalink — open any donation by transaction ID |

| Keyboard | Action |
|---|---|
| `⌘K` / `Ctrl+K` | Open search palette (owners, teams, committees) |
| `/` | Open search (when not in an input) |
| `↑↓` / `Enter` / `Esc` | Search nav / select / close |
