# OWNER_SCHEMA — Owner Entity YAML Schema

Every tracked owner has a YAML file at `owners/<slug>.yaml`. This file is the **spec** for that owner — what we call them, what signals confirm them, what related entities we also track under them. The ingestion pipeline reads only the YAML; the YAML is the contract.

Owner YAMLs are version-controlled. Changes are logged in the file's own `change_log` block AND in `catalog/PROVENANCE_LOG.md` if they affect classification.

## Required fields

```yaml
slug: lastname-firstname               # filename-safe, lowercase, hyphenated. Must match filename.
name: Full Legal Name                  # as most commonly filed
team: Team Name                        # full team name, no abbreviation
role: Principal owner                  # one of: Principal owner | Co-owner | Limited partner (with caveat)
status: pilot                          # one of: queued | pilot | active | paused
tenure_start_date: YYYY-MM-DD          # when this person took MLB control-person role; partial OK
tenure_end_date: null                  # null while current; YYYY-MM-DD when they sell
```

### `family_tenure_start_date` (optional)

For family-inheritance cases where the team has been in this person's family
longer than they personally have held the formal MLB control-person role.
Set to the date the FAMILY acquired the team. Used by the timeline visual to
show family ownership context — a rectangle from `family_tenure_start_date`
to `tenure_end_date` (or now), with a vertical marker at `tenure_start_date`
indicating when this person personally became control person.

```yaml
family_tenure_start_date: 1984-09-07   # Carl Pohlad's 1984 Twins purchase
```

Leave unset for owners who founded their team's ownership group (e.g., Steve
Cohen 2020, John Henry 2002, Tom Ricketts 2009) — for them `tenure_start_date`
is already the family acquisition.

### `name_variants` (required, ≥2 entries)

Every observed or expected name variation that should be queried against FEC. Conservative additions only — based on (a) what we've actually seen filed, or (b) clearly trivial variations of the canonical name.

```yaml
name_variants:
  - "Steven A Cohen"
  - "Steven Cohen"
  - "Steve Cohen"
  - "Cohen, Steven"
  - "Cohen, Steven A"
```

Do NOT include misspellings unless they have been observed in actual FEC data. Adding "Stephen Cohen" as a precaution would invite false positives.

### `verifying_signals` (required)

The signal set used by `VERIFICATION.md` rules. Each subsection lists strings that, when matched against an FEC record, count as one confirming signal.

```yaml
verifying_signals:
  cities: ["greenwich", "stamford", "new york"]   # lowercase, no punctuation
  states: ["CT", "NY"]                            # USPS two-letter codes
  employers:                                       # case-insensitive substring matches
    - "Point72"
    - "Point72 Asset Management"
    - "S.A.C. Capital"
    - "SAC Capital"
    - "Cohen Private Ventures"
    - "New York Mets"
  occupations:
    - "investor"
    - "owner"
    - "principal"
    - "ceo"
    - "founder"
```

City/state and employer are the workhorses. Occupation is often filed inconsistently and is the weakest signal — useful as a confirmer but rarely on its own.

### `strong_signals` (required, may be empty arrays)

Unique-enough signals that one match is sufficient for CONFIRMED status. Be very conservative populating this — these escalate single-signal records to CONFIRMED.

```yaml
strong_signals:
  employers:
    - "Cohen Private Ventures"          # named after the owner; not used by anyone else
    - "Point72 Asset Management"        # ditto
  zip_codes: []                          # only for rare residential cases
```

A string belongs in `strong_signals.employers` only if it is **virtually never filed by anyone else** as their employer. "Hedge fund manager" is not a strong signal. "Point72 Asset Management" is. When in doubt, leave it in `verifying_signals.employers` and require a second signal.

### `negative_signals` (optional, may be omitted entirely)

Anti-patterns. Identifying strings for a **different person who shares a name** with the owner and is known to confuse the classifier. A record whose employer matches a negative-signal string is automatically demoted to UNCERTAIN with reason "negative employer signal", regardless of any other signals that matched. Use sparingly — this is for cases where audit has identified a same-name doppelgänger.

```yaml
negative_signals:
  employers:
    - "Elliott Management"   # different Steven Cohen at Paul Singer's hedge fund
    - "Elliott Mgmt"
```

Add a negative-signal entry only after you've manually traced the doppelgänger via FEC and confirmed they are a distinct person. Document the rationale in `change_log`. Negative signals can be removed later; they are not destructive (the records they catch go to UNCERTAIN, not deletion).

## Optional fields

### `city_state_alone_insufficient` (optional boolean, default false)

```yaml
city_state_alone_insufficient: true
```

For a **high-frequency name** (e.g. "John Fisher"), a lone city+state match
corroborates almost nothing — everyone in that metro shares it — so it sweeps
same-named strangers into PROBABLE on a single weak signal. Set this flag to make
`city_state` **insufficient on its own** for PROBABLE: a row whose only confirming
signal is `city_state` stays UNCERTAIN unless a discriminating signal
(employer / occupation / ZIP) also hits.

It changes nothing else: `city_state` still counts toward a two-signal CONFIRMED,
and the documented city still prevents the address-contradiction demotion — so
strong-signal rows (unique employer / documented ZIP) are unaffected. Use it
instead of deleting the residence city from `verifying_signals.cities`, which would
both fail validation rule 3 and make every in-city record *contradict* (demoting
even strong-signal rows to UNCERTAIN). Opt-in per owner; can also be set on a
`related_entities` block. Applies equally to federal (`master.db`) and state
(`state.db`) classification, since both share the classifier.

### `related_entities` (optional but encouraged)

Other people or organizations whose donations we also track, attributed to their own slug (never silently to the owner).

```yaml
related_entities:
  - kind: spouse
    slug: cohen-alexandra
    name: Alexandra Cohen
    name_variants: ["Cohen, Alexandra", "Alexandra Cohen"]
    relationship_source:
      description: "WSJ profile of the Cohens"
      url: ""
      accessed: ""
    verifying_signals:
      cities: ["greenwich"]
      states: ["CT"]
      employers: ["philanthropist", "homemaker"]
      occupations: ["philanthropist", "homemaker"]
    strong_signals: { employers: [], zip_codes: [] }
    
  - kind: pac
    slug: point72-pac
    name: Point72 Asset Management PAC
    committee_id: "C00XXXXXXX"   # FEC committee ID
    ownership_link_documented:
      description: "Point72 SEC ADV filings list Cohen as principal"
      url: ""
      accessed: ""
```

`kind` is one of: `spouse`, `child`, `parent`, `sibling`, `pac`, `business_entity`.

A related entity creates **its own classification path** — its donations are classified against its own signals and tagged with its own slug. Reports that aggregate (e.g., "the Cohen family") join slugs explicitly.

### `sources` (required, ≥1 entry)

Sources used to populate this file. Per SOURCES.md, these are Tier 2 (identity) sources, never used for donation facts.

```yaml
sources:
  - description: "MLB Mets ownership page"
    url: "https://www.mlb.com/mets"
    accessed: "2026-05-22"
    archive_url: ""
  - description: "Point72 firm leadership page"
    url: "https://www.point72.com/leadership/"
    accessed: "2026-05-22"
    archive_url: ""
```

### `notes`

Factual clarifications. Not interpretation.

```yaml
notes: |
  Cohen has primary residence in Greenwich, CT and secondary in NYC.
  Filings from 2014–2016 sometimes report employer as "S.A.C. Capital"
  (the predecessor firm that was wound down). Treat as expected, not
  as an anomaly.
```

### `change_log` (append-only)

Every change to this file that affects classification (signal additions, name variant additions, related-entity additions) records an entry here.

```yaml
change_log:
  - date: 2026-05-22
    change: "Created."
    by: "maintainer"
  - date: 2026-05-23
    change: "Added 'Cohen Private Ventures' to strong_signals.employers based on confirmed filings."
    by: "automated pipeline"
    references:
      - "data/raw/cohen-steven/2026-05-23T14-00-00Z__schedule_a.json"
```

### `audit`

```yaml
audit:
  created: 2026-05-22
  last_ingestion: null
  last_signal_review: 2026-05-22
```

## Validation rules

A new or modified owner YAML must pass these checks before being used in an ingestion run:

1. `slug` matches the filename.
2. `name_variants` has at least 2 entries.
3. `verifying_signals.cities` and `.states` are non-empty (every owner has at least one documented residence).
4. `verifying_signals.employers` has at least 1 entry.
5. `sources` has at least 1 entry.
6. `strong_signals.employers` strings, if present, do not also appear in `verifying_signals.employers` (no duplication across signal tiers).
7. Every `related_entity` has its own `verifying_signals` block.
8. `tenure_start_date` is present and is a valid date.
9. Any `change_log` entry references a date that is ≤ today.

A validator script in `scripts/validate_owners.py` enforces these.
