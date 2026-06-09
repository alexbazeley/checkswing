# reports/

This directory holds **interpretation** — editorial analysis built on top of the
archive's neutral data. It is the one place in this repository where framing,
narrative, and a point of view are allowed (CHARTER.md §Editorial framing,
GOVERNANCE.md §1.8 and §6).

The boundary, restated:

- **The data layer never editorializes.** `data/master.db`, `data/legislation.db`,
  and the dashboard store neutral, sourced facts in a law-librarian's tone.
- **`reports/` is where the show's voice lives.** Briefs here interpret the joined
  data, make arguments, and connect dots — clearly labeled as interpretation, and
  always traceable back to the neutral evidence.

## Brief format (the standing convention)

Briefs here are **straight reports on the documented links** — what the joined
data shows about owner money and the legislation it touches. They are **not**
show-prep sheets:

- **No "Talking points" section** and **no "Discussion questions" section.** Those
  belong to episode prep in the editorial workspace, not to an archive report.
- A brief leads with the finding, lays out what the data does and does not support,
  and ends with methodology/provenance so every figure is traceable.
- The honest-finding discipline still applies: where the data undercuts a tempting
  narrative, the brief says so plainly.

## Contents

- **`2026-05-31_save-americas-pastime-act.md`** — the first donations × legislation
  brief (Phase 3 exit criterion): MLB owner donations joined to the 2018 vote that
  carried the Save America's Pastime Act.
- **`2026-06-08_no-tax-subsidies-for-stadiums.md`** — the committee-of-referral
  join: MLB owner donations to current members of the Senate Finance / House Ways
  and Means committees that hold the recurring (and recurringly dead) bills to end
  the federal tax subsidy for stadium bonds.
- **`data/`** — the *neutral* machine-generated join outputs the briefs are built
  on (CSV + JSON), produced by `python -m scripts.cli policy-join`. These carry no
  interpretation; they are the reproducible evidence layer. Regenerate them to
  refresh a brief's numbers.

## Reproducing a brief's numbers

Every figure in a brief traces to a `reports/data/` file. To regenerate the
Save America's Pastime Act evidence:

```
python -m scripts.cli policy-join \
  --bill 115-hr-1625 \
  --sponsors-of 114-hr-5580 --sponsors-of 115-hr-1625 \
  --out save-americas-pastime-act
```
