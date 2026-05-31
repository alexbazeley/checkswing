# Calibration Playbook

How to tune an owner's signal block so the classifier puts the right donations
in the right tier — without loosening the rules, misattributing a relative, or
silently dropping a verified row.

This is the operational companion to the classification **spec**
([VERIFICATION.md](../VERIFICATION.md)) and the data-integrity **rules**
([GOVERNANCE.md](../GOVERNANCE.md)). The spec says what each tier *means*; the
rules say what you're *allowed* to change. This playbook is the loop you run to
get there. It is the "Cohen-playbook" referenced by `scripts/audit.py` — named
for the [cohen-steven](../owners/cohen-steven.yaml) pilot it was first written
against.

Nothing here overrides GOVERNANCE.md. Where they appear to conflict, GOVERNANCE
wins.

---

## The loop

```
  audit <slug>          (read-only — see where the owner stands)
      │
      ▼
  interpret             (which UNCERTAIN/PROBABLE are really the owner's?)
      │
      ▼
  decide                (add a signal · negative-signal a doppelgänger ·
      │                  attribute/exclude a single txn · or leave UNCERTAIN)
      ▼
  edit owner YAML       (deliberate change + change_log entry — §1.7)
      │
      ▼
  reclassify <slug>     (re-score from stored raw; snapshots + logs)
      │
      ▼
  verify                (counts moved as predicted; no relative swept in;
                         no verified row silently dropped)
```

Every step except `interpret` and `decide` is a command. `audit` and the read
probes are read-only; `reclassify`, `attribute`, `exclude`, `resolve`,
`bulk-discard` all snapshot `data/master.db` and append to
[catalog/PROVENANCE_LOG.md](../catalog/PROVENANCE_LOG.md) before they touch
anything, and all are reversible.

---

## Step 1 — `audit <slug>` (read-only)

```
python -m scripts.cli audit <slug>
```

`audit` writes nothing. It prints, for one owner:

| Section | What it tells you | What to do with it |
|---|---|---|
| **Current signal block** | counts of name_variants / cities / employers / strong / negative / related | A near-empty block on an owner with many PROBABLE/UNCERTAIN rows is under-calibrated. |
| **Current classifications** | CONFIRMED / PROBABLE / UNCERTAIN(open) + **P/C ratio** | A high P/C ratio means lots of one-signal matches — either a missing strong signal, or genuine ambiguity. |
| **PROBABLE by employer** | the employer strings on one-signal records | Recurring real-employer strings are promotion candidates; unknown employers are doppelgänger candidates. |
| **PROBABLE by ZIP** | the ZIPs on one-signal records | A ZIP shared with CONFIRMED rows is a strong-signal candidate. |
| **CONFIRMED ZIPs** | ZIPs already proven tied to the owner | Seed for `strong_signals.zip_codes`. |
| **Review-queue reasons** | histogram of *why* rows are UNCERTAIN | "suffix mismatch" → a relative split (see §Disambiguation). "name match only" → a missing signal. |
| **UNCERTAIN sample** | 5 random open rows w/ txn id + raw path | Pull the raw payload to inspect the full contributor block. |
| **Suggestions** | heuristic checklist (see Step 3) | Candidates only — never directives. |

Read the raw payload behind any txn before acting on it:

```
python -m scripts.cli sample <slug> -n 20      # random records for eyeballing
python -m scripts.cli review                    # all open review-queue items
```

---

## Step 2 — interpret

The only question that matters per row: **is this transaction the owner's, a
known relative's, a same-named stranger's, or unknowable from the data?**

Answer it from the contributor block in the raw payload, not from the name
alone. The single most important lesson from the triage sessions:

> **Disambiguate by city + employer + title, never by name.** Same-named
> relatives (father/son, Jr./III) and doppelgängers all share the normalized
> name. The classifier strips middle initials and treats the name as a gate,
> not as proof — so the *signals* are what separate the people.

Worked cases from the archive (see PROVENANCE_LOG):

- **DeWitt** — "DeWitt III, William O Jr" rows were the *father* (Bill DeWitt
  Jr.) misfiled under the son's numeral. Cincinnati ZIP + Chairman&CEO title
  matched the father → attributed to him; the son's St. Louis / Cardinals-
  President rows were correctly left out.
- **Castellini** — split Robert H. **Sr.** (owner, CASTELLINI COMPANY,
  Cincinnati) from "Robert Jr." (President&CEO) and a Wells Fargo financial-
  advisor doppelgänger of the same name.
- **Middleton** — father John **S.** (Staubus, owner) vs son John **P.**
  (Powers, an independent major federal donor at Vertigo Entertainment). The
  records *carry* the middle initial; the classifier just doesn't preserve it.

---

## Step 3 — decide (and which tool fits)

There are five legitimate dispositions. Pick by *why* the row is where it is.

### (a) Genuine owner rows blocked by a missing signal → **add the signal**

The record really is the owner's and would satisfy the tier rules, but a string
is missing from the YAML (a name variant, a real employer, a documented
secondary-residence city, a private-compound ZIP).

This is the **preferred** fix — it's general, it survives reclassify, and it
fixes every matching row at once.

- A recurring **real employer** on PROBABLE rows → `verifying_signals.employers`,
  or promote to `strong_signals.employers` if the string is diagnostic of the
  owner alone (e.g. their named holding company).
- A **ZIP that CONFIRMED rows already share** → `strong_signals.zip_codes`.
- A **legitimate, documented secondary residence** → `verifying_signals.cities`
  (+ the state). Document the residence; don't add a city just because rows
  appear there — that's how a doppelgänger slips in.
- A **missing name spelling/variant** (FEC typo, maiden name, initial form) →
  `name_variants`.

> **The kendrick lesson:** a missing name_variant doesn't just *demote* rows —
> it can *drop* them entirely as name-no-match. A variant add can therefore
> recover far more than the divergent set you measured (kendrick: estimated +2,
> actual +24). Always check the **full reclassify delta**, not just the rows you
> were aiming at, and confirm no relative was swept in.

### (b) A confirmed same-named **doppelgänger** (different person) → **negative-signal it**

A different individual with the same name, confirmed by audit to be someone
else (different employer/city/title). Add their distinctive employer string to
`negative_signals.employers` so their rows auto-demote to UNCERTAIN.

Only after you've *confirmed* they're a different person. This is a §1.7
deliberate edit with a change_log entry.

### (c) A same-named **relative** the classifier can't separate by name → **`exclude`**

When the records are a relative's (son, spouse) and the only thing
distinguishing them is a middle initial — which `normalize_name` strips — a
signal edit can't safely separate them. Drop the relative's specific
transactions:

```
python -m scripts.cli exclude <txn_id> <slug> --reason "…" --source "…"
```

`exclude` removes that txn from the owner's classification entirely (not even
queued), survives reclassify, and is reversible with `unexclude`. It's the
negative counterpart of `attribute`.

> Prefer a `related_entities` entry (which routes the relative's rows to *their*
> slug) when the relative is separable. Today they often aren't, because the
> classifier collapses middle initials — see the [known limitation](#known-limitation-middle-initials)
> below. Until that's fixed, `exclude` is the honest interim tool.

### (d) A single owner row the rules can't reach → **`attribute`**

One transaction that is provably the owner's by human inspection but can't
satisfy the two-signal rule (e.g. a relative-misfiled suffix, sparse employer
field). Force-CONFIRM exactly that txn:

```
python -m scripts.cli attribute <txn_id> <slug> --reason "…" --source "…"
```

Txn-keyed, documented, survives reclassify, reversible with `unattribute`. Use
this instead of a name_variant edit when a same-named relative exists — a
variant would sweep the relative in; an attribution touches one row.

### (e) None of the above → **leave it UNCERTAIN**, or `resolve` it DISCARDED

If you can't tell who it is, it stays UNCERTAIN — that's the tier working as
designed. If it's a *stranger* you've positively identified (wrong city/state,
contradicting employer), record a sticky DISCARDED verdict so it stops
re-queuing:

```
python -m scripts.cli resolve <txn_id> <slug> --verdict DISCARDED --reason "…"
python -m scripts.cli bulk-discard <slug>   # discard every OPEN item for a slug
```

`resolve`/`bulk-discard` are queue-only — they never block a future attribution
— and reverse with `unresolve`.

**Never** do the two forbidden things (VERIFICATION.md §Anti-patterns):
auto-promote UNCERTAIN "because it looks like him," or lower a threshold
globally because you're "missing donations you know happened." Confirm the
donation exists in FEC, identify *which* signal would have caught it, and add
that signal deliberately.

---

## Step 4 — edit the YAML

Every signal change is a deliberate edit to `owners/<slug>.yaml` with a
`change_log` entry stating the justification and source (GOVERNANCE.md §1.7).
Validate before moving on:

```
python -m scripts.cli validate
```

---

## Step 5 — `reclassify <slug>`

```
python -m scripts.cli reclassify <slug> --reason "calibration: <what changed>"
# add --include-related if the YAML declares related_entities to route
```

`reclassify` wipes the slug's rows and re-scores them against the **stored raw
payloads** (no FEC re-fetch). It snapshots `master.db` and logs to
PROVENANCE_LOG first.

### The safety guard — read this before reclassifying a Phase-2 / incomplete-raw owner

`reclassify` (and `attribute`/`exclude`, which reclassify internally) will
**abort** if the rebuild would drop any currently-attributed row — whether
because raw is missing *or* because an earlier classifier scored a row the
current one no longer would (classifier divergence). manual_attributions and
EXCLUDED drops are recognized as intentional and don't trip it.

If the guard fires, **stop and understand the cause** — do not reach for
`--force`. A loud abort here is the guard doing its job; forcing past it is how
you silently lose verified donations. The one owner currently in this state is
**malone-john** (54 raw rows FEC can no longer return); its master.db is the
source of truth and must not be reclassified without first re-fetching raw.

Before any `reclassify`/`attribute` on such an owner, run the read-only probe:
load the owner YAML, `load_raw_payloads(slug)`, classify each stored
CONFIRMED/PROBABLE row, and count how many come back None/UNCERTAIN. If >0, the
guard *will* fire — diagnose first. Check file coverage too, though it is not
predictive of loss:

```
python -m scripts.cli raw-coverage <slug>
```

---

## Step 6 — verify

- Counts moved **as predicted**. If a variant add moved more than you expected,
  find out why before committing (it may have un-dropped name-no-match rows —
  good — or swept in a relative — bad).
- No relative or doppelgänger was pulled in. Spot-check the newly-CONFIRMED
  rows' cities/employers/titles.
- Re-run `audit <slug>` and `validate`.
- The op is reversible (snapshot + PROVENANCE entry exist). Reverse with the
  matching `un*` command, or `git checkout HEAD -- data/master.db
  catalog/PROVENANCE_LOG.md` if nothing's committed yet.

Then run the standard pre-commit gates:

```
python -m scripts.cli validate
python -m pytest -q
```

---

## Reading the suggestion checklist

`audit`'s **Suggestions** are heuristics, framed as questions, never directives
(GOVERNANCE.md §1.7 — the human decides). What each one means:

- *"promote employer X from verifying → strong"* — multiple PROBABLE rows carry
  an employer already in `verifying_signals`; promoting it to `strong_signals`
  would CONFIRM them on one signal. Do it only if the string is diagnostic of
  the owner alone.
- *"add ZIP X to strong_signals.zip_codes"* — that ZIP appears on ≥2 CONFIRMED
  rows; it's proven tied to the owner.
- *"review employer X — may be a doppelgänger"* — a PROBABLE employer that
  matches nothing in the block. Inspect the person; negative-signal only if
  confirmed to be someone else.
- *"PROBABLE records in city X"* — a city not in `verifying_signals.cities`.
  Add **only** if you can document it as a real secondary residence; otherwise
  leave UNCERTAIN.

"No automated suggestions surfaced" means the block looks tight or there's too
little data for the heuristics to bite — inspect manually if you still suspect
miscalibration.

---

## Calibrating a pilot to "done"

Per VERIFICATION.md, before declaring an owner's calibration complete:

1. **Spot-check 20 random CONFIRMED rows** against the FEC website. Acceptable
   misattribution rate: **zero**. One wrong row means the rule needs tightening.
2. **Spot-check 20 random UNCERTAIN rows.** For each, what single signal — if
   any — would have CONFIRMED it? If many are obviously-the-owner with a small
   add, add it deliberately.
3. **Audit all PROBABLE rows.** Mostly clearly-him-but-one-signal (fine) or
   mostly ambiguous (matching is too loose)?
4. **Sanity-check the aggregate** against OpenSecrets for order-of-magnitude.
   Totals needn't match; they should be in the same ballpark.

Document the calibration result in PROVENANCE_LOG.md.

---

## Known limitation: middle initials

`normalize_name` (`scripts/resolve_entities.py`) deliberately drops single-char
middle-initial tokens so "Steven A Cohen" and "Steven Cohen" share a canonical
form. The cost: a father/son pair distinguished *only* by middle initial (John
**S.** vs John **P.** Middleton) is indistinguishable to the matcher, so the
son can't be given his own `related_entities` slug — he has to be handled with
`exclude` (disposition (c)) or a negative employer signal.

Preserving middle initials in matching is the planned fix that would let such
relatives become properly tracked entities and retire those workarounds. Until
then, the dispositions above are the honest interim handling.

---

## Quick reference

| Situation | Tool |
|---|---|
| Real owner rows missing a signal | edit YAML → `reclassify` |
| Confirmed same-named different person | `negative_signals.employers` → `reclassify` |
| Relative separable only by middle initial | `exclude <txn> <slug>` |
| One owner row the rules can't reach | `attribute <txn> <slug>` |
| Positively-identified stranger in the queue | `resolve --verdict DISCARDED` / `bulk-discard` |
| Can't tell | leave UNCERTAIN |

All mutating commands snapshot `master.db` + log to PROVENANCE_LOG and are
reversible. `audit`, `sample`, `review`, `raw-coverage`, `validate` are
read-only.
