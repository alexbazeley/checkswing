# CheckSwing — design decision record

The GROUNDRULES.md Phase A–C artifacts for the CheckSwing front-end
(`mockup/index.html`). GROUNDRULES §7.1 makes this file the first item of the
exit checklist: *"Phase A–C artifacts exist in writing … No writeup → not done."*

This is a **decision record**, not a style guide. It exists so that the next
person to touch the CSS can tell which values were chosen and why, and which
would be a regression to change.

---

## Phase A — ground in the subject

**Subject.** 5,092 itemized political contributions made by the 36 principal
owners of Major League Baseball's 30 franchises, each one traced to a specific
FEC or state campaign-finance filing and classified by how strongly its signals
tie it to that owner.

**Audience.** Baseball readers and reporters who know the sport and do not know
campaign-finance forms. Secondarily the hosts of *Tipping Pitches*, who need a
number and a source they can say out loud on a podcast without hedging.

**Job.** Get a reader to pick a level (federal or state) and land on one owner's
record with its provenance visible — the amount, the recipient, and the filing
it came from.

**The subject's own world.** This matters because §1 Phase A says distinctive
design comes from the subject's materials, instruments, and documents. This
archive is made of one specific document: the **FEC Form 3/3X itemized
Schedule A line**. Its native visual properties are:

- Fixed-column ledger rules, not boxes. Disclosure schedules are ruled, not carded.
- Form coordinates on every row — `filing_form`, `line_number` (`11AI`),
  a transaction ID that encodes the schedule (`SA11A.141624567`), an
  `image_number` pointing at the scanned filing.
- Field-label-over-value, in the flat register of a government form
  ("As filed with FEC").
- Tabular lining figures. Money in a filing always aligns on the decimal.
- The filing image as the court of last resort — the thing you click when you
  don't believe the row.

Every one of those properties is already present in `donations.json` and was,
before this pass, mostly thrown away at render time.

---

## Phase B — the committed direction

**One sentence:** *the site is an itemized disclosure schedule you can read* —
the FEC Schedule A line is the atomic unit, and pages are built from ledger
rules and form coordinates rather than from cards.

### Palette (locked)

Kept from the previous pass; it was already sound. Warm paper, one dominant
crimson, one teal for the state level, semantic party and status colors held
outside the brand hues.

| Token | Value | Role |
|---|---|---|
| `--bg` | `#FBFAF5` | dominant surface — warm off-white, never `#FFF` |
| `--bg-sunk` | `#F1EFE6` | recessed surface (the 3–5% shift §3 asks for) |
| `--ink` / `--ink-2` / `--ink-3` | `#17150F` / `#46433A` / `#5C574B` | text ramp |
| `--brand` (federal) | `#8B1B2C` | dominant accent |
| `--state` | `#1C5C56` | the second level's accent |
| `--accent` | `#A44C26` | link/action |

Party (`--dem`/`--rep`/`--oth`) and status (`--ok`/`--warn`/`--flag`) are
**semantic**, not brand, and live outside the ≤3-hue budget per §1B.

`--ink-3`, `--accent`, and the party text colors were re-derived in this pass
from measured APCA values — see "Contrast" below. They are shades of the
existing hues, not new hues.

### Type (chosen, with a reason)

| Role | Face | Why |
|---|---|---|
| Display | **Libre Caslon Display** | Caslon is the typeface of American public records and legal printing. A political-disclosure archive set in Caslon is an argument about what the thing *is*. |
| Serif text | **Libre Caslon Text** | Same family at text optical size for decks and long prose. |
| UI / body | **Inter** | Does UI and dense-table work, where a neutral grotesque is genuinely correct. §2 blocks Inter as a *headline* face; it is not one here. |
| Data | **JetBrains Mono** | Load-bearing, not decoration: filing IDs, image numbers, dates, and tabular money. The filing vocabulary depends on it. |

**What changed and why.** The previous pass used **Fraunces** for display and
**Source Serif 4** for text. GROUNDRULES §2 names Fraunces explicitly as a
current default-stack reflex face — "fine when *chosen*; tells when defaulted" —
and it was defaulted. Source Serif 4 was a fourth family doing work Libre Caslon
Text now does, so the stack went 4 families → 3.

All four files are **self-hosted** in `assets/fonts/` (156 KB total; Inter and
JetBrains Mono are variable fonts covering every weight in one file each). The
Google Fonts `<link>` and both preconnects are gone — three render-blocking
cross-origin round-trips removed, per §6's performance clause.

**Scale.** One ratio, ~1.25, snapped to whole pixels: 12 · 13 · 16 · 20 · 25 ·
31 · 39 · 56. Body prose is **16px** (§1B floor; it was 13–15px before). Measure
is capped at `--measure: 68ch` and is now actually applied to prose (§1B asks
60–80ch; the previous build applied it to exactly one selector and ran to 213ch).

### Space

8pt grid, 4px half-step, as a token ladder (`--s1` 4 → `--s10` 80). Every
padding and margin resolves to one of these. The previous build had 6px, 14px,
18px, 22px, 26px, 30px and 34px scattered through it, which is why only 5–59%
of spacing values landed on the grid.

Proximity is meaning: space inside a component < between components < between
sections. Section padding **varies** by role — the home level-choice section
breathes, dense data sections are tighter — so the page has rhythm rather than
uniformity (§3, §3b.5).

### Layout concept

A single-column reading measure for prose, opening out to full-bleed ruled
ledgers for data. The grid is the **schedule**: date · donor · recipient ·
amount, with amount right-aligned on tabular figures, and the form coordinates
set in mono beneath. Interior pages are a hero (eyebrow · h1 · deck), then
ledger sections separated by whitespace and background shift.

### Signature

**The itemized line.** Every donation renders the way FEC Schedule A renders
it, carrying its form coordinates — `F3X · Line 11AI · SA11A.141624567` — with
the filing image one click away. It is the one element this site should be
remembered by, and it is the reason the design could not be lifted onto a
product in another category.

Boldness is spent there. Everything around it stays quiet.

### Motion

Instant and mechanical. Nothing animates on scroll; nothing fades in on load.
Motion exists only to confirm a pointer or a keystroke landed: 80–140ms on
hover and focus, a 3px lift on genuinely elevated cards, nothing longer than
240ms. Under `prefers-reduced-motion` **all** of it is off — transitions,
transforms, and animations — not just the loading spinner.

---

## Phase C — the regression check

§1 calls this the highest-value step in the file: *if given a generically
similar brief, would I have landed on roughly this same plan?*

| Component | Verdict | What changed |
|---|---|---|
| **Palette** | **Kept — it survives the counterfactual.** Warm paper is a common "editorial data site" default, but the *specific* pairing of crimson-federal against teal-state is doing information work: the two levels are separate databases that are never merged, and the color carries that separation across every page. A generic brief would not have produced a two-level color system because a generic brief has one level. | nothing |
| **Type** | **Failed the check — revised.** Fraunces + Inter is close to *exactly* what a generic "make a data archive feel editorial" brief produces in 2026; §2 lists Fraunces by name for this reason. Replaced with Libre Caslon, argued from American public-records printing. | Fraunces → Libre Caslon Display; Source Serif 4 → Libre Caslon Text; 4 families → 3 |
| **Layout** | **Failed in part — revised.** Hero → statband → 3-up trust strip → card grid is the canned skeleton with the serial numbers filed off. The heroes and the level cards earn their place; the uniform "eyebrow → H2 → caption" applied to all 57 interior sections did not — it was a template pretending to be structure. | eyebrows cut where they only restated the H2; section rhythm now varies |
| **Signature** | **Failed — this is the substance of the pass.** The previous build's most distinctive element was a duotone stadium photo. A stock-photo treatment is not a signature; it is a mood. The itemized filing line is native to the subject and could not be transplanted. | filing vocabulary built; see §3b.3 below |

**The honest note.** Warm paper + serif display + 1px-bordered cards is roughly
what a generic brief yields. That is precisely why the type swap and the filing
vocabulary — not the contrast fixes — are the substance of this pass. The
contrast work was necessary and measurable, but it would have been necessary
for the generic version too.

---

## Measured baseline (before this pass)

`groundcheck` (the automated half of §7.10), run against a local build on
2026-07-19. Slop side / craft side:

| Route | Slop | Patterns triggered | Craft |
|---|---|---|---|
| `#/` | Low (10/100) | slop fonts, low contrast | 5/8 |
| `#/federal` | **Medium (15/100)** | slop fonts, hero font mix, low contrast | 4/8 |
| `#/states` | Low (10/100) | slop fonts, hero font mix | 4/8 |
| `#/owner/cohen-steven` | Low (10/100) | slop fonts, low contrast | 4/8 |
| `#/methodology` | Low (10/100) | slop fonts, accent stripe | 5/8 |
| `#/recipients` | Low (10/100) | slop fonts, low contrast | 4/8 |

Craft signals failing everywhere: **spacing grid** (5–59% on-grid), **type
scale** (up to 12 distinct sizes; body computed at 13px), **measure**
(median 187–213ch), **semantics** (skipped heading levels; every filter input
unlabeled). Passing everywhere: focus visible, reduced motion, hue discipline,
hover states.

Contrast, measured with APCA rather than the WCAG proxy groundcheck uses: 19
distinct text/background pairs below target (Lc ≥75 body, ≥45 large/bold). A
single token — `--ink-3` at `#7C7666` — accounted for ten of them.

Post-pass results are recorded at the bottom of this file.

---

## Results (after the pass)

`groundcheck`, 15 routes. Every route is **Clean** tier on the slop side.

| | before | after |
|---|---|---|
| Routes at 0 slop patterns | — | **14 of 15** |
| Worst route | `#/federal`, Medium (3 patterns) | `#/runs`, Clean (1, justified below) |
| Routes at full craft | 0 of 6 sampled | **13 of 15** |
| APCA pairs below target | **19** | **0** |

Behaviour, measured headlessly:

- `prefers-reduced-motion`: 0 of 197 rendered elements still transition or
  animate; the card lift resolves to `transform: none`.
- Keyboard: 30 consecutive tab stops on `#/federal`, all with a visible focus
  ring. All 24 sortable table headers across the state and recipient tables are
  focusable, `role="button"`, and sort on Enter/Space (they were mouse-only).
- Mobile: no horizontal overflow at 375px on any route checked.
- Repo gates: `validate` 26 OK / 0 failed, `pytest` 653 passed, `node --test` 8
  passed.

### The two remaining §2 flags, and why they stand (§7.10)

§7.10 allows a blocklist hit that carries a written justification. Two do.

**1. `#/runs` — "stat banner row."** The pipeline page opens with four figures:
runs, records fetched, API calls, time since the last run. §2's objection is to
*trust-padding* — "10K+ users · 99.9% uptime" on a marketing page, numbers
chosen to reassure rather than inform. These four are the page's entire subject;
`#/runs` exists to answer "is the archive current, and what did it cost to keep
current." The row is also identical in component and markup to the ones on `#/`
and `#/federal`, which the detector passes — it clears them only because their
values start with `$`, which the detector reads as pricing. Adding a currency
symbol to make it pass would be gaming a bug, not fixing a design.

**2. `#/states`, `#/state-coverage` — "hue discipline," 4 families.** Two
separate things, both worth stating.

The first is a sampling artifact. The detector reads the first 800 elements in
DOM order; within that prefix the sienna accent falls below its 3-use floor,
which breaks the bin-0↔bin-11 wraparound merge that folds sienna and crimson
into one family. Running the detector's own algorithm over the *whole* page
gives bins `[0, 1, 4, 5, 11]` → **2 families**.

The second is that §1B's budget is about *brand* hues, and it says so: "Semantic
colors (success/warning/error/info) live outside the brand hues." The brand
hues here are exactly three — crimson (federal), teal (state), sienna
(interactive). Everything else the detector counts is semantic: green and amber
encode the verification tier, blue and red encode recipient party. Collapsing
those into the brand budget would mean giving up the encoding, which is the
opposite of what §3 asks for.

---

## §3b rubric — post-pass

*Answered against rendered full-page screenshots, per §7.14. Ships at ≥6 real yeses.*

1. **Focal point — yes.** On `#/`, the eye lands on "Where Major League
   Baseball's owners spend in politics." set 56px over the darkened ballpark
   duotone; nothing else on the first screen competes. That is what Phase A says
   the page is for. On interior pages the focal point is the page h1, and the
   statband deliberately sits *under* it rather than beside it.
2. **Subject vocabulary — yes.** Three choices that would make no sense on a
   product in another category: (a) the filing coordinate — `F3X · Line 11AI ·
   SA11A.141624567 · Image 202606209870932503` — set in mono directly beneath
   the amount, which is how an FEC schedule identifies its own line; (b) Libre
   Caslon, the typeface of American public records, carrying every heading;
   (c) the drawer's "AS FILED" register, which labels the contributor exactly as
   the filing does rather than as the archive's own tidied entity.
3. **Signature — yes, one.** The itemized line. It is the reason a reader can
   click any row and land on the scanned page it was read from.
4. **Type as personality — yes.** Cover the data and the type still says
   *records office*: a one-weight Caslon that never bolds, small caps used only
   where a form would use them, and mono reserved for the things a filing
   assigns numbers to.
5. **Hierarchy under blur — yes, with a caveat.** At thumbnail size the home
   page reads dark hero → floating white statband → open paper → recessed sunk
   band → footer, and the register genuinely alternates. The owner page varies
   through its top third and then settles into two long uniform tables that
   occupy most of its height. That is honest — the table *is* the content — but
   it is the weakest answer here.
6. **Earned density — yes.** The deletion test ran three times this pass and
   removed something each time: 30 eyebrows that restated the h2 beneath them;
   four provenance fields the new AS FILED strip already showed; and the tier
   cards' duplicate name, where a chip reading "Confirmed" sat directly on an h3
   reading "CONFIRMED".
7. **Craft floor — yes.** Measured rather than asserted: see Results above.

**7 of 7.**

## Accessory removed (§7.15)

The hero's accent word. On `#/federal` and `#/states` one word of the headline
was set in italic *and* in the level colour — while the eyebrow above it, the
sub-nav below it, and the level colouring throughout the page all already said
which level you were on. It was the fourth statement of the same fact, and the
most decorative. The italic went first, then the colour. The headline is now
just a headline.
