# VERIFICATION — Three-Tier Classification

Every FEC record we ingest is classified into exactly one of four states. Three of them — CONFIRMED, PROBABLE, UNCERTAIN — describe attribution confidence. The fourth — SUPERSEDED — describes records replaced by a later restatement.

This file is the spec for that classification. Code in `scripts/resolve_entities.py` implements it; this is the rule it implements.

## The four states

### CONFIRMED
- Name match (after normalization) to one of the owner's `name_variants`.
- **PLUS** two or more confirming signals (`verifying_signals` matched).
- **OR** one strong, unique signal match (`strong_signals` matched).
- Exported to canonical CSVs without qualification.

### PROBABLE
- Name match.
- **PLUS** exactly one confirming signal.
- Exported with explicit status flag. Reports that cite PROBABLE records must say "probable donation by …" never "donated."

### UNCERTAIN
- Name match.
- **AND** zero confirming signals; or contradictory signals (e.g., employer matches but city is wrong by 2000 miles and there's no documented secondary residence).
- Routed to the `review_queue` table in `master.db`. **Not** in the canonical export.

### SUPERSEDED
- A previously-ingested record that FEC has restated or retracted.
- Old row marked SUPERSEDED with reason; new row ingested fresh and classified per rules above.

## Signal types

### Name normalization (required for any classification)
Compare against `name_variants` after the following normalization:
- Lowercase.
- Strip punctuation (`.`, `,`).
- Collapse whitespace.
- **Middle initials are optional but discriminating.** A single-char middle initial is stripped from the canonical first+last form (so "Steven A Cohen" and "Steven Cohen" share a form), but it is also retained separately and used as a discriminator: a record and a variant match only if their middle initials are *compatible* — compatible when either side has no initial (so a bare "John Middleton" matches "John S. Middleton"), but **incompatible when both carry an initial and they differ** ("John P. Middleton" does not match "John S. Middleton"). This is what lets a same-named relative who differs only by middle initial (a father/son like John S. vs John P.) be routed to their own `related_entity` instead of collapsing onto the principal. A spelled-out middle *name* ("John Powers Middleton") stays in the form and discriminates by not sharing a bare form; it is not treated as an initial. Implemented by `normalize_name` (returns the initials) + `names_match` (`_middle_initials_compatible`) in `scripts/resolve_entities.py`.
- Suffixes (Jr., Sr., II, III) are part of the name and matter — "John Smith Jr." does not match "John Smith".
- Hyphenated last names are matched both ways ("Smith-Jones" matches "Smith Jones" and "Jones-Smith").

Use `rapidfuzz` for fuzzy comparison only as a discovery aid for new name variants the registry might be missing — not as a substitute for an exact normalized match in classification.

### Confirming signals (each worth one point toward CONFIRMED)

**Employer match.** Donor's reported employer matches a string in `verifying_signals.employers`. Match is case-insensitive, whitespace-collapsed, but otherwise exact substring or full match. Do **not** stem or remove words ("Cohen Private Ventures LLC" matches "Cohen Private Ventures" via substring; but "Private Ventures" does not match "Cohen Private Ventures" — too lossy).

**Occupation match.** Donor's reported occupation matches a string in `verifying_signals.occupations`. Same matching rules as employer.

**City + State match.** Donor's city matches a string in `verifying_signals.cities` AND state matches a string in `verifying_signals.states`. Either alone is not a confirming signal — many cities have common names. City + state together is the unit.

### Strong signals (each one promotes to CONFIRMED on its own)

**Strong employer match.** Donor's employer matches a string in `strong_signals.employers`. These are unique-enough employer strings that a match is by itself diagnostic (e.g., "Cohen Private Ventures" is owned and named by Steve Cohen; nobody else is reporting that as their employer).

**Strong ZIP + name match.** Donor's ZIP appears in `strong_signals.zip_codes` and name matches. Used for rare cases (e.g., a private compound's ZIP).

### Negative signals (disqualify the record from a tier)

**Suffix mismatch.** "John W. Henry" vs "John W. Henry Jr." are different people. Suffix mismatch is automatic UNCERTAIN regardless of other signals — log to review and inspect.

**Address contradiction without secondary-residence documentation.** City + state is the unit (mirroring the positive City+State signal). If a record carries both a city and a state and that pair is not a documented residence — city in `verifying_signals.cities` **and** state in `verifying_signals.states` — the record drops to UNCERTAIN even if employer also matches. A same-named city in the wrong state (e.g., Greenwich, KS for a Greenwich, CT owner) is therefore flagged rather than slipping through on a generic employer match. When the record has a city but no state, city-only membership is used. (Why: this catches family-name collisions where Owner X's signal employer is also held by a relative who lives elsewhere.)

**Spouse-name collision.** If a record matches a name in the owner's `related_entities` (e.g., the spouse), it is attributed to the spouse entity, not to the owner. Spouse records carry the spouse's slug, not the owner's. Never silently fold spouse donations into the owner's totals.

**Negative employer signal (anti-pattern).** If `owners/<slug>.yaml` has a `negative_signals.employers` block and a record's employer matches one of those strings (same case-insensitive substring rule as positive employer match), the record is automatically demoted to UNCERTAIN with reason "matches negative employer signal: <string>". This catches known same-name doppelgängers — e.g., a different Steven Cohen at Elliott Management who lives at overlapping Greenwich ZIPs but is a distinct individual from the owner. Adding a string to `negative_signals.employers` is a deliberate, change_log-traced decision (GOVERNANCE.md §1.7) made only after manual audit confirms the doppelgänger is a different person.

## Scoring algorithm (pseudocode)

```
def classify(record, owner):
    if not name_normalized_match(record.contributor_name, owner.name_variants):
        return None  # Not this owner's record; skip entirely
    
    # Route to a related entity only on EXACT suffix agreement. A canonical
    # match with a differing suffix (owner "Smith Sr" vs related "Smith Jr")
    # is NOT that entity — it falls through to the owner check below.
    if matches_related_entity(record, owner.related_entities) and suffix_agrees:
        return classify_for_related_entity(record, matched_entity)
    
    if suffix_mismatch(record, owner):
        return UNCERTAIN, "suffix mismatch"
    
    if matches_negative_employer_signal(record, owner.negative_signals):
        return UNCERTAIN, "matches negative employer signal: <details>"
    
    strong_hits = count_strong_signal_matches(record, owner.strong_signals)
    confirming_hits = count_confirming_signal_matches(record, owner.verifying_signals)
    
    if address_contradicts_without_documentation(record, owner):
        return UNCERTAIN, "city/state outside documented residences"
    
    if strong_hits >= 1:
        return CONFIRMED, "strong signal: <details>"
    if confirming_hits >= 2:
        return CONFIRMED, "two confirming signals: <details>"
    if confirming_hits == 1:
        return PROBABLE, "one confirming signal: <details>"
    return UNCERTAIN, "name match only"
```

The `<details>` string is persisted on the donation row so any record's tier can be audited from the row alone.

## Calibration: how to know you got the rules right

Before declaring the Cohen pilot complete:

1. **Spot-check a sample of CONFIRMED rows.** Pull 20 random CONFIRMED records. Manually verify each via the FEC website. Acceptable error rate: zero misattributions. If even one is wrong, the rule needs tightening.

2. **Spot-check a sample of UNCERTAIN rows.** Pull 20 random UNCERTAIN records. For each, identify what additional signal — if any — would have made it CONFIRMED. If many UNCERTAINs would be obviously-Cohen with a small signal addition, consider adding it (deliberately, with a `change_log` entry).

3. **PROBABLE audit.** Pull all PROBABLE records. Are they mostly clearly-him-but-only-one-signal? Or are they mostly ambiguous? If the latter, the matching is too loose.

4. **Compare aggregate to OpenSecrets.** Pull Cohen's OpenSecrets summary as a sanity check on order-of-magnitude. We don't need to match their total — they may include things we exclude, and vice versa — but the totals should be in the same ballpark.

This calibration pass is a **prerequisite** for moving past Phase 1. Document the results in `catalog/PROVENANCE_LOG.md`.

## Review queue workflow

The `review_queue` table in `master.db` holds UNCERTAIN records awaiting human adjudication (list with `python -m scripts.cli review-queue`; export a Markdown snapshot with `export-review-queue`). Each entry contains:
- Transaction ID
- Full contributor block (name, employer, occupation, city, state, ZIP)
- Recipient committee + candidate
- Amount, date
- Reason for UNCERTAIN classification
- Link to raw payload

A human review session does the following per item:
- (a) Adds a new signal to the owner YAML (with `change_log` entry) and re-runs classification, **or**
- (b) Marks the record DISCARDED with reason ("not this owner — different person with same name in Cleveland").

Every adjudication is logged in `catalog/PROVENANCE_LOG.md`. Never silent.

## Anti-patterns

- **Auto-promoting UNCERTAIN to PROBABLE because "it looks like him."** Forbidden. UNCERTAIN must be adjudicated with an explicit signal addition or a DISCARD decision.
- **Lowering the threshold because "we're missing donations we know happened."** First confirm the missing donation exists in FEC; if so, identify *which signal* would have caught it; add that signal deliberately. Do not loosen rules globally.
- **Stem-matching or word-removal on employer strings.** "Point72 Asset Management" should not match "Point Park University" because both contain "Point." The matching rules are deliberately strict on this.
- **Letting the spouse's donations be counted toward the owner's total.** They are separate entities. Reports that say "the Cohens gave $X" must explicitly join the two entities, not silently merge.
