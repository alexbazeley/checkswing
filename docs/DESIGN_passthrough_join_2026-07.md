# Design — the legislation pass-through (committee → candidate) join

**Status:** ✅ **IMPLEMENTED** in PR #106 (was: design-first proposal, IMPROVEMENT_PLAN_2026-07 §5.6, the (L) item). `policy_join` carries the `include_indirect` flag and a `join_tier` label on every row; the indirect-authorized tier resolves ~1,298 donations / $2.76M. This document is retained as the design record — read it for the tier taxonomy and neutrality reasoning, not as a statement of pending work.
**Author:** 2026-07-11 session. Grounded in live `master.db` (schema v10) + `legislation.db` counts, not estimates.
**Companion rules:** [GOVERNANCE.md §6](../GOVERNANCE.md) (Phase-3 neutrality), [VERIFICATION.md](../VERIFICATION.md), §1.1 (earmark/conduit dedup), §3.11 (the join denominator caveat).

---

## 1. The problem

The owner→donation→legislator join (`scripts/policy_join.py`) — the engine behind the two published briefs and the new `#/legislation` dashboard — matches a donation to a legislator **only through `donations.recipient_candidate_id`**:

```sql
JOIN legislator_fec_ids x ON x.fec_candidate_id = d.recipient_candidate_id
```

But `recipient_candidate_id` is populated on almost nothing, because MLB owners give to **committees**, not to a bare candidate id. Measured on the live counted set (CONFIRMED+PROBABLE, `counted=1`):

| | rows | dollars | share of $ |
|---|--:|--:|--:|
| **Total counted giving** | 4,176 | $34,497,695 | 100% |
| carries `recipient_candidate_id` (**joinable today**) | 43 | $170,955 | **0.5%** |
| committee recipient, no candidate id (**dark**) | 4,133 | $34,326,740 | 99.5% |

So the join sees **half a percent** of the money. Every "owner X gave to legislators who voted on bill Y" figure is computed against that 0.5% slice — honest (the §3.11 caveat says so) but tiny.

---

## 2. The key finding — the crosswalk is already in `master.db`

`committees.candidate_ids` (populated by the existing FEC committee-enrichment step, `ingest_committee`) already carries the FEC candidate id(s) a committee is tied to. **756 of 1,063** committees have it. So the pass-through join is a **JOIN, not a fetch** — no new FEC round-trips, no new secret, no schema-v11 crosswalk table required for the core win. This de-risks the item from "L, needs a fetch campaign" to "M, needs a carefully-gated query."

`committees.candidate_ids` is a JSON array string, e.g. `["H6OH16029"]`. `legislation_db.legislator_fec_ids` (1,713 rows) already maps `fec_candidate_id → bioguide_id`, which is what the existing joins consume. So the resolution chain is:

```
donation.recipient_committee_id
   → committees.candidate_ids  (master.db, already populated)
   → legislator_fec_ids.fec_candidate_id → bioguide_id  (legislation.db)
   → the existing vote / sponsor / committee-membership joins
```

---

## 3. The honesty crux — `candidate_ids` present ≠ "gave to the candidate"

**This is the whole design.** A committee having a `candidate_ids` link does **not** mean money to it reached that candidate's campaign. A leadership PAC links to its sponsoring member; a joint fundraising committee lists every participant; an "unauthorized" PAC may name a candidate it was formed around. Folding those into "owner gave $X to candidate Y" would be a fabricated attribution — exactly what GOVERNANCE §1.1/§1.9 forbid.

The FEC **`designation`** field is the gate that separates a candidate's *own campaign* from everything else. Measured on the dark (no-direct-candidate) money, by `designation_label`:

| designation_label | rows | dollars | of which committee has candidate_ids | verdict |
|---|--:|--:|--:|---|
| **Principal campaign committee** | 1,942 | $4,042,369 | $4,041,869 | ✅ **candidate's own campaign** |
| **Authorized by a candidate** | 104 | $191,855 | $191,855 | ✅ **candidate's own campaign** |
| Joint fundraising committee | 309 | $6,172,316 | $2,531,940 | ❌ split/conduit — see §1.1 |
| Leadership PAC | 128 | $484,300 | $48,400 | ❌ access money, not campaign |
| Unauthorized (Super/industry/party PAC) | 1,217 | $21,118,435 | $2,877,523 | ❌ not to a candidate |
| Lobbyist/Registrant PAC | 422 | $2,296,464 | $24,900 | ❌ not to a candidate |

The **only** honest candidate attribution is `designation ∈ {P (Principal), A (Authorized)}`:

> **indirect-authorized ≈ $4.23M** (2,046 donations) — money to a legislator's **own campaign committee**, resolvable to that legislator and thus to their votes/sponsorships.

That is a **~25× unlock** over the $171K direct tier, and every dollar of it can be described truthfully as "to {legislator}'s campaign committee."

Everything else stays **out of the candidate join by construction.** That is not a loss — it is the §3.11 caveat made precise. The "leadership-PAC / party money is the real access story" framing (plan §5.6) is true *editorially*, but the neutral data layer must not render it as a contribution *to a candidate*; it is a contribution *to a PAC/party*, which is a different, already-correct fact the archive already stores.

---

## 4. Proposed design

### 4.1 Tier taxonomy (a new `join_tier` label on every joined row)

| `join_tier` | rule | dollars | meaning surfaced to the reader |
|---|---|--:|---|
| `direct` | `recipient_candidate_id` present | $0.17M | "to the candidate (FEC-coded)" |
| `indirect-authorized` | committee `designation ∈ (P,A)`, single resolvable `candidate_ids` | ~$4.23M | "to {candidate}'s campaign committee" |
| *(unjoined)* | everything else (JFC, leadership PAC, party, super PAC, unauthorized) | ~$30M | never joined to a candidate; counted only in totals |

The tier is a **derived label computed at query time** — no stored status change, so it can't corrupt the three-tier CONFIRMED/PROBABLE/UNCERTAIN classification (which is about *whether the donation is the owner's*, an orthogonal axis). A donation is `CONFIRMED` (it's really the owner's) **and** `indirect-authorized` (it reached a candidate's campaign via their committee).

### 4.2 Query change (`policy_join.py`)

Each of the three join functions (`vote_donation_rows`, `sponsor_donation_rows`, `committee_donation_rows`) becomes a `UNION ALL` of two arms carrying a literal `join_tier`:

- **direct arm** — the current query, `join_tier='direct'`.
- **indirect arm** — resolve through the committee:

```sql
FROM master.donations d
JOIN master.committees cm       ON cm.committee_id = d.recipient_committee_id
                               AND cm.designation IN ('P','A')      -- authorized only
JOIN json_each(cm.candidate_ids) ce                                 -- explode the array
JOIN legislator_fec_ids x       ON x.fec_candidate_id = ce.value
JOIN legislators l              ON l.bioguide_id = x.bioguide_id
...                                                                  -- then votes/sponsors/memberships as today
WHERE (d.recipient_candidate_id IS NULL OR d.recipient_candidate_id = '')  -- don't double-count the direct arm
```

`json_each` is available in the SQLite the project already uses. `attach_for_join` already ATTACHes `master`, so `master.committees` is reachable.

### 4.3 Neutrality guardrails (non-negotiable, enforced in the query + surfaced in output)

1. **Authorized-only gate.** `designation IN ('P','A')`. Never resolve a candidate through a PAC/JFC/party committee.
2. **Single-candidate gate.** 41 principal committees carry *multiple* `candidate_ids` (a committee id reused across candidates/cycles). Resolve **only** when `json_array_length(candidate_ids) = 1`; a multi-candidate committee is ambiguous → leave unjoined (route to a review note, not to a guessed candidate). Sizing without this gate over-attributes; measure the residue and report it.
3. **Tier is always shown.** Every joined row/rollup carries `join_tier`; the dashboard and briefs must render `indirect-authorized` distinctly from `direct` (e.g. a "via campaign committee" sub-label), never merging them into an undifferentiated "gave to".
4. **JFC money stays with §1.1.** A joint-fundraising-committee gift's *real* ultimate recipients are already captured (or excluded as conduit legs) by the earmark/`counted` machinery. The pass-through join must not re-attribute JFC dollars to a candidate — that would double-count against §1.1.
5. **`days_before_vote` unchanged.** Still neutral arithmetic; the tier does not change its meaning.

### 4.4 Schema

**None required for the core win** — `committees.candidate_ids` + `legislator_fec_ids` already exist. Optional hardening, only if a persisted crosswalk is wanted for auditability:

- A view or a materialized `committee_candidate_resolution(committee_id, candidate_id, designation, resolved_via, is_ambiguous)` — a pure projection, rebuilt on demand, never a source of truth. Pairs with §1.3's `sub_id` work only in that both are "make the join keys explicit"; they are independent otherwise.

---

## 5. Output & presentation impact

- **`policy_join` outputs** (`reports/data/*.json`) gain a `join_tier` column; `summarize_by_owner` splits totals into `direct_amount` / `indirect_authorized_amount`.
- **`#/legislation` dashboard** (`build_legislation_data.py` `_brief_rollup`): the per-owner rollup shows both tiers; the denominator caveat is **rewritten** from "direct-to-candidate only" to "direct + to-authorized-campaign-committee; PAC/party/JFC money excluded from candidate attribution by construction." The number moves from ~$0.37M joinable to ~$4.4M.
- **The two briefs** (`reports/*.md`): re-verify numerically after the change (they are dollar-exact today); add one sentence on the indirect tier. This is a **published-total-adjacent change** → provenance-log it and restate the join denominator in the same PR (plan's standing rule).

---

## 6. Sizing summary

| tier | dollars | rows | join keys (all already present) |
|---|--:|--:|---|
| direct | $0.17M | 43 | `recipient_candidate_id` |
| indirect-authorized (P/A, single cand) | **~$4.2M** | ~2,000 | `committees.candidate_ids` + `legislator_fec_ids` |
| **joinable after this change** | **~$4.4M** | ~2,050 | (12× the money, ~48× the rows of today) |
| structurally unjoinable-at-candidate | ~$30M | ~2,100 | correct — PAC/party/JFC/super-PAC |

The ~$30M is not a backlog to chase; it is money that genuinely did not go to a candidate's campaign, and the archive already records *where* it went (the committee). A separate, clearly-labeled **"access tier"** (owner → leadership-PAC/party committee, no candidate claim) could be a *future* editorial surface, but it is out of scope here and must never borrow the word "candidate."

---

## 7. Implementation plan (when green-lit)

1. **Audit gate #2** — count multi-candidate authorized committees among the counted set; confirm the single-candidate residue is small and log it (no silent drop).
2. `policy_join.py` — add the `indirect-authorized` UNION arm + `join_tier` to all three join functions; unit tests with a synthetic principal-committee fixture (and a leadership-PAC fixture that must **not** join).
3. `summarize_by_owner` — split by tier; test.
4. `build_legislation_data.py` — carry `join_tier` into the rollup; rewrite the denominator caveat string; pytest (build-shape change).
5. `#/legislation` frontend — render the tier distinctly; browser-verify.
6. Re-verify + restate the two briefs' totals; PROVENANCE_LOG entry; methodology note.
7. Gates: `validate` + `pytest` green; provenance logged.

**Acceptance:** the join covers ~$4.4M with every indirect row labeled and traceable committee→candidate→legislator; zero PAC/JFC/party dollars attributed to a candidate; a synthetic leadership-PAC donation is provably excluded from the candidate tier; briefs re-verify to the dollar.

---

## 8. Open questions for the maintainer

- **Access tier (future):** do we ever want a neutral "owner → leadership-PAC/party" surface (money to a member's *PAC*, explicitly not their campaign)? It is defensible as neutral fact but invites the causal read the archive avoids. Recommend: not now.
- **The 307 committees without `candidate_ids`:** a low-priority FEC enrichment re-fetch would recover a small tail of authorized committees currently missing the id. Worth a `state-freshness`-style flag, not a blocking dependency.
