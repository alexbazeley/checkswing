# Project review — MLB Owner FEC Donations Archive (CheckSwing)

*Reviewed 2026-06-08. Scope: full repo — governance docs, classifier, ingestion,
state/legislation layers, CLI, dashboard, tests, CI. State at review: 38 owner
YAMLs, 4,139 federal donations (3,925 CONFIRMED / 212 PROBABLE, ~$33.8M),
10 states live in `state.db` (1,011 matched rows), 3 bills / 2 votes in
`legislation.db`, 507 tests.*

---

## Overall assessment

This is a genuinely strong project — the rare data project where the integrity
discipline is the product, and it shows. The three-tier classifier, the
provenance trail, the separation of `master.db` / `state.db` / `legislation.db`,
the snapshot-before-mutate rule, and the "official portal is the record,
aggregator is only a discovery pointer" stance are all coherent and consistently
enforced in code, not just asserted in docs. The test suite (507 tests across 35
files, network mocked, temp DBs) and the CI gate make the whole thing safe to
evolve. Most of what follows is refinement, not repair.

The headline issues are small and concrete: one real test-isolation bug, one
piece of stale documentation that now contradicts the code, and a set of
structural/scaling pressures that are worth getting ahead of before the archive
grows further.

---

## What's working well

- **The classifier matches its spec.** `resolve_entities.py` faithfully
  implements VERIFICATION.md — name-as-gate, two-signal CONFIRMED, single-signal
  PROBABLE, negative/address-contradiction demotions, related-entity routing on
  exact suffix agreement. It's readable and well-commented.
- **Provenance is real, not decorative.** Raw-before-parse, supersession instead
  of overwrite, the `reclassify` guard that aborts rather than silently drop a
  row whose raw is missing, `raw-coverage` to audit the gap, and durable
  verdict tables (`manual_attributions`, `review_resolutions`) that survive a
  reclassify. The `malone-john` "do not reclassify" caveat is exactly the kind
  of honesty this project promises.
- **Scope discipline.** Federal stays FEC-only; state and legislation are
  physically separate databases reusing the same classifier verbatim. The
  charter's "we'd rather miss a real donation than misattribute one" is encoded,
  not just stated.
- **Phase progress is well ahead of the charter.** Phase 2 (all owners) is
  effectively done, Phase 3 (legislation join) is wired and producing a brief,
  and Phase 4 has gone from a CA pilot to ten live states.

---

## Issues found (highest priority first)

### 1. Test-isolation bug in the refresh lock — `refresh_all` ignores the patched lock path

`scripts/refresh.py:291` calls `with _acquire_lock():` with no argument. The
function signature is `def _acquire_lock(path: Path = REFRESH_LOCK)`, so the
default binds the **module-level `REFRESH_LOCK` object at import time**. The
`refresh_world` test fixture does `monkeypatch.setattr(refresh, "REFRESH_LOCK",
lock_path)`, which rebinds the *name* but not the already-captured default — so
every `TestRefreshAll` test acquires the **real** `data/.refresh.lock`, not a
temp one.

Consequences:
- The tests aren't hermetic; they write to the repo's real lock path during a
  test run.
- If a stale lock exists (it does right now — a crashed run left
  `data/.refresh.lock` with `pid=5`), all 7 `TestRefreshAll` tests fail. That's
  exactly what happens on a clean checkout here: **500 pass, 7 fail**, purely on
  the leftover lock.

Fix is one line — resolve the global at call time:

```python
with _acquire_lock(REFRESH_LOCK):
```

and add a regression test that patches `REFRESH_LOCK` to a temp path and asserts
`refresh_all` uses it. Worth also having `_acquire_lock` default to `None` and
resolve `REFRESH_LOCK` inside the function body, so this class of bug can't recur.

### 2. Stale documentation — the "middle initials" known-limitation section is now wrong

`docs/CALIBRATION_PLAYBOOK.md` §"Known limitation: middle initials" still says
`normalize_name` "deliberately drops single-char middle-initial tokens" and that
a John **S.** vs John **P.** Middleton pair is "indistinguishable to the
matcher," with the discriminator described as a "planned fix."

That fix has shipped. `normalize_name` now returns the middle initials
separately, and `names_match` calls `_middle_initials_compatible` to treat
conflicting initials as a non-match — exactly as VERIFICATION.md §"Name
normalization" describes. The two docs now contradict each other.

The subtlety worth documenting precisely (rather than just deleting the section):
the discriminator only bites when **both** the record and the matched variant
carry conflicting initials. Because owners keep a bare variant for recall —
`middleton-john.yaml` lists both `"John Middleton"` and `"John S. Middleton"` —
a record for "John P. Middleton" still matches the *bare* variant (empty initial
is compatible with anything) and routes to the owner. So the operational
conclusion ("you still need `exclude` for the son when a bare variant exists") is
right, but for a different reason than the doc gives. Rewrite the section to
describe the actual behavior and the bare-variant interaction; it's currently
misleading to a future maintainer.

### 3. The charter's phase status lags reality

CHARTER.md still frames Phase 4 as "**ACTIVE (California pilot)**" and the
sourcing as a CA pilot, while ten states are live in `state.db` and SOURCES.md
already documents all ten. Phase 2 reads as in-progress but is essentially
complete (38 owners, all validating). A reader using the charter as the map gets
a stale picture. Reconcile CHARTER.md, README, SOURCES.md, and
STATE_DONATION_SCHEMA.md so the phase markers match the database. This matters
more than usual here because the charter is cited as the scope authority.

### 4. Monoliths are accumulating

- `scripts/cli.py` — 1,584 lines, ~40 commands in one file.
- `scripts/ingest.py` — 928 lines.
- `mockup/index.html` — 8,635 lines / ~400 KB, hand-maintained, with inline CSS
  and JS (it is *not* generated by `build_data.py`).

None of these is broken, but each raises the cost of the next change and the
onboarding cost for a second contributor. Suggestions: split the CLI into Click
command groups by domain (federal / state / legislation / calibration /
maintenance) backed by thin modules; consider whether the dashboard's JS should
move to a small set of files (even without a build step) or be partially
generated. The dashboard is the single biggest "only one person can safely touch
this" risk in the repo.

### 5. State adapters are near-parallel and worth factoring

Ten `fetch_<state>.py` + `<state>_adapter.py` pairs share a lot of shape
(download/stream → map columns into the classifier's record dict → upsert). The
`StateSource` registry (`state_sources.py`) and `enrichment_base.py` show the
instinct is already there. A common `StateAdapter` base (or a declarative
column-map per portal) would shrink the per-state surface area and make state #11
a config entry rather than a new file. Worth a focused refactor pass now, while
ten examples make the right abstraction obvious, rather than at twenty.

### 6. `master.db` in Git LFS is the scaling constraint

The ~124 MB LFS object already forced the refresh cadence from weekly to monthly
to stay inside the free 1 GB/month LFS bandwidth (each matrix bucket + commit
jobs + Cloudflare deploy pull it). As coverage grows this gets worse, and the
dashboard's freshness story degrades with it. Options to weigh: stop committing
`master.db` and rebuild it in CI (tensioned against "raw is not a guaranteed
backup" — would need a durable raw store first); publish it as a release asset or
to object storage instead of LFS; or split the committed DB into a smaller
"canonical export" blob plus an out-of-band full archive. No action needed today,
but it's the constraint most likely to bite the project's "kept current" promise.

### 7. Review-queue backlog has no triage surface

9,419 federal UNCERTAIN rows plus 7,740 state. `audit.py` does heuristic
surfacing per owner, but there's no burndown view or batch-prioritization (by
amount, by recurring employer, by owner P/C ratio). As a maintenance reality this
is the work that actually keeps accuracy high, and it's currently CLI-only and
unmeasured.

### Minor

- Dashboard loads ~8 MB `data.json` client-side; heavy on mobile. Accessibility
  markup is present but light (~31 aria/role/alt across 8.6k lines). Worth an
  a11y/perf pass given it's the public face.
- `data/master.db` shows as modified (uncommitted) in the working tree — worth a
  conscious commit-or-revert so the source of truth isn't left dirty.
- Zero `TODO`/`FIXME` in `scripts/` — clean, noted as a positive.

---

## Expansion ideas

Roughly in order of leverage for the show's editorial mission.

- **Deepen Phase 3 — it's the differentiated payoff.** The join machinery exists
  but the index holds only 3 bills / 2 votes / 1 brief. Build out the
  MLB-relevant corpus (antitrust-exemption history incl. the Curt Flood Act,
  MiLB pay / Save America's Pastime Act lineage, stadium-financing federal
  action, RSN/broadcast and the Diamond Sports collapse, sports-betting
  post-PASPA) and generate more per-episode briefs. The "donation N days before
  vote Z" computation is already neutral arithmetic you can surface.
- **Money-flow / network views.** `committee_disbursements_by_recipient` already
  holds ~395k rows — owner → committee → downstream recipient is a graph you can
  visualize ("who this committee in turn funded") beyond the current per-committee
  beneficiary lists.
- **Explicit family-aggregate views.** The schema keeps spouses/family as
  separate entities (correctly). A first-class, clearly-labeled "the Cohens"
  rollup on the dashboard — joining slugs, never merging silently — is a natural
  editorial unit.
- **Finish the state map and publish coverage honestly.** Drive remaining teams'
  home states through the `StateSource` registry; the existing `#/state-coverage`
  ledger is a good base for a public "what we cover and why some states are out"
  page.
- **Phase 5 alerting.** Diff reports on new donations, plus 48-hour-notice
  tracking in election season — a scheduled job that surfaces deltas would make
  the archive feel live and is squarely in the charter.
- **A documented public dataset + data dictionary.** A versioned CSV/Parquet/JSON
  export (CONFIRMED, and a `_with_probable` variant, status preserved) with a
  published schema would let others cite the archive and would double as a
  reproducibility artifact. A tiny read-only query endpoint could follow.
- **Entity-resolution maintenance loop.** `rapidfuzz` is already a dependency for
  variant discovery — a periodic "candidate new name variants we're probably
  missing" report (and a "candidate doppelgängers" report) would turn calibration
  from reactive to proactive. Pair with finally resolving the bare-variant
  middle-initial case so separable relatives can become real `related_entities`.
- **A data-quality dashboard.** Review-queue burndown, raw-coverage gaps,
  per-owner PROBABLE ratio, last-ingestion age — the operational health of the
  archive, in one view.
- **Lobbying sibling project (LD-1/LD-2).** The charter already names it as a
  separate effort; it's the most natural adjacency to owner political influence.

---

## Suggested near-term sequence

1. Fix the `_acquire_lock` call (#1) + regression test — restores a green suite.
2. Rewrite the middle-initials playbook section (#2) and reconcile CHARTER /
   README / SOURCES phase status (#3) — cheap, removes the misleading docs.
3. Refactor the state adapters to a common base (#5) before adding state #11.
4. Decide the `master.db`/LFS strategy (#6) deliberately, before coverage grows.
5. Then pick an expansion track — Phase 3 depth is the highest editorial payoff.
