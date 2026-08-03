# drugref — expansion-policy history (#35)

**Date:** 2026-08-03 · **Status:** design, approved · **Issue:**
[#35](https://github.com/cairn-ehr/drugref/issues/35)

A debt round, not a slice. No new source, no new clinical claim, no new kind of knowledge. It moves the one
curator-policy table that **gates recall** — `class_expansion_policy` — onto the append-only overlay floor
Plan C built, so a revised deny/allow decision stops overwriting its own rationale and a `DELETE` stops being
possible at all.

## 1. Why now, and what #35 actually asks

`db/010` created the table with its storage tier reasoned out explicitly, and the reasoning was right **for
its time**:

> * NOT a rebuildable projection. […] An ingest that wiped curator judgement would re-open every bucket
>   silently on the next release.
> * NOT the append-only signed overlay either (**that tier arrives with Plan C**). This is small,
>   low-cardinality policy data in the same class as `ci_axis` and `source_tier`: edited in place, reviewed by
>   diff.

Plan C has since landed. The overlay floor exists, is **generic over the natural key**, and is already shared
by four tables — so the sentence in parentheses is now the argument for the change rather than against it, and
the issue's two cheaper options (an `updated_at` column; a history side-table written by trigger) buy strictly
less than adopting a shape the schema already has and already tests.

The two asymmetries #35 names:

1. **No history.** A curator flipping `deny` → `allow` overwrites `rationale`, `reviewed_by` and
   `reviewed_against` in place. "What did we last say about this class, against which release, and why did we
   change our mind" is unanswerable from the database. Git holds only the *seed*; a node operator's
   post-install edits are nowhere.
2. **No write protection.** Every other clinically-consequential curated table carries a floor refusing
   `DELETE`/`UPDATE`. This one has none, and it gates recall: a single
   `UPDATE drugref.class_expansion_policy SET decision = 'deny'` removes thousands of candidate pairs with no
   audit row and nothing reporting it.

**Not in this round:** [#36](https://github.com/cairn-ehr/drugref/issues/36) (the discovery heuristic counts
descendant classes rather than reachable members) — same table, but it changes which roots get asked about and
so needs a curator ruling plus its own re-measure; [#37](https://github.com/cairn-ehr/drugref/issues/37), not
urgent at a 3.1 ms filtered lookup; [#48](https://github.com/cairn-ehr/drugref/issues/48), still structurally
unreachable. No curation content: this round ships the mechanism, and the fourteen seeded decisions carry over
unchanged.

## 2. Withdrawal — the thing the floor would otherwise make impossible

**Plan C's hardest-won finding applies here directly.** Supersession must point at a later row carrying the
**same natural key**, so every correction leaves another live row standing: nothing can be *retired* by
superseding it. That is why `additive_effect` has `accumulates`, `interaction_group_member` has
`satisfies_role`, and `db/023` had to add `applies` to the table `db/020` stopped short of. The standing
instruction that came out of it is to ask, **before** deciding a table needs no ruling column, what
withdrawing one of its statements looks like.

On this table the question has a sharp answer, because **absent is not `allow`**:

| state | pair set | worklist |
|---|---|---|
| no row | expands (`COALESCE(decision,'allow')`) | **asked** by `gap_unreviewed_expansion_root` |
| `allow` | expands | silent — a curator looked and said yes |
| `deny` | direct members only | silent — a curator looked and said no |

Once a row exists, an append-only table can never return the class to the first row of that table. And the
codebase **already asks an operator to do exactly that**: when a release stops defining a class somebody ruled
on, `medrt_run` logs

> `… so they no longer bind: … Re-key or **withdraw** them in drugref.class_expansion_policy.`

Today "withdraw" means `DELETE`. Under the floor, `DELETE` raises. So withdrawal must become representable, or
this round would ship a schema whose own warning message advises an impossible action.

**Decision: a third `decision` value, `'withdrawn'` — not a boolean ruling column.** Four reasons.

- **One column, one truth.** `decision` is already the ruling vocabulary and every reader branches on it.
- **The withdrawn row keeps its `rationale`**, which is precisely the audit value #35 exists to buy: *why* we
  stopped believing a judgement, recorded against the release we last reviewed it under.
- **A reader that has never heard of the value is safe.** `COALESCE(decision,'allow') <> 'deny'` reads
  `'withdrawn'` as not-deny and expands — and for a contraindication, **more rows is the safe direction**.
- **A boolean beside a text ruling admits two encodings of one state.** `(deny, applies=false)` and
  `(allow, applies=false)` would mean the same thing, and a consumer reading `decision` without also reading
  `applies` would be confidently wrong — the footgun slice 5b.2 split a table to avoid.

`'withdrawn'` is **not** `'allow'`. It means *no current judgement*, so the class goes back on the worklist.

## 3. Schema — `db/027`

### 3.1 The natural key stops being unique

`policy_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY` replaces the `(source, source_code)` primary key,
which must be **dropped**: history rows carry the same natural key by definition, so uniqueness on it rejects
the only sequence that can express a correction. `db/020` records the same reasoning for `additive_effect`,
and `db/001` shipped the defect on `identity_claim` that `db/005` then had to repair. `superseded_by bigint
REFERENCES drugref.class_expansion_policy(policy_id)` carries the chain.

The `decision` CHECK is dropped and re-added as `CHECK (decision IN ('deny', 'allow', 'withdrawn'))`. It stays
a CHECK rather than becoming a lookup table: the vocabulary is closed, three values long, and read by four
views that branch on the literals.

### 3.2 The floor is reused, not copied

Both of Plan C's generic trigger functions take the natural key as arguments, so this table attaches to them
with no new PL/pgSQL:

- `forbid_overlay_rewrite('policy_id', 'source', 'source_code')` — BEFORE UPDATE OR DELETE. Refuses `DELETE`;
  refuses any change except `superseded_by`; makes that one-way, set once, always pointing at a **later** row
  carrying the same `(source, source_code)`.
- `forbid_multiple_live_assertions('source', 'source_code')` — an `AFTER INSERT OR UPDATE … DEFERRABLE
  INITIALLY DEFERRED` constraint trigger. At most one live row per key, checked **at COMMIT**, because a
  correction is momentarily two live rows and an immediate check would reject it (see
  `decisions/correcting-a-curated-assertion.md`).
- `CREATE INDEX class_expansion_policy_live_key ON drugref.class_expansion_policy (source, source_code) WHERE
  superseded_by IS NULL` — partial, **not unique**, matching the trigger's predicate exactly. `db/023` showed
  this index is what keeps that trigger linear rather than quadratic; nothing else reads it, so a test names
  it.

### 3.3 Provenance stays `reviewed_by` / `reviewed_against` / `reviewed_at`

This table takes Plan C's **floor** but not its `source` / `ingest_run` / `asserted_at` provenance triple.
Four reasons, and the loss is uniformity across the tier:

1. **The seed rows have no run to point at.** `ingest_run` is `NOT NULL` on all four Plan C tables, and
   `db/010`'s fourteen rows are written **by a migration**. Adopting the triple means minting a synthetic run
   row from SQL — breaking the invariant the ingest-operability round has just established, that
   `provenance.py` is the only writer of a run record — or making the column nullable here alone, which is not
   the triple but a fifth shape.
2. **`source` already means something else here, and it is load-bearing.** On Plan C's tables `source` is
   *who asserts* (`CHECK … IN ('DRUGREF')`). Here it is *who defines the class*
   (`CHECK … IN ('MED-RT','MeSH')`), it is half the natural key, and all four readers join it to
   `substance_class (source, source_code)`.
3. **A run id answers the wrong question.** Plan C's curation genuinely happens inside a `DRUGREF` run; a
   policy decision is a human reading a release at a time unrelated to any ingest, so a run id would record a
   coincidence as provenance. `reviewed_against = '2026.07.06'` answers what matters — *which release was this
   judged against, and is it stale* — and a run id cannot: run ids are per-database and do not survive the
   rebuild this table is designed to outlive.
4. **It would add rather than replace.** `reviewed_by`/`reviewed_against` are `NOT NULL` with fourteen rows of
   real content and would be kept regardless, so the triple is three extra columns, two of which restate
   `reviewed_at` and the existing `source`.

**Recorded here so a later round reads this as a decision rather than an oversight to "fix".**

### 3.4 `ALTER` in place, not a new table

The fourteen seed rows exist in every applied database and a node operator may have revised one; recreating
the table loses those edits and needs the seed re-expressed for no gain. On a replay from scratch `db/010`
runs first, so its `ON CONFLICT DO NOTHING` seed still has the old primary key when it needs it. Existing rows
acquire `superseded_by IS NULL` and are therefore live and binding, which is correct: nobody has withdrawn
anything.

## 4. The read path — one view, four readers

**There are four readers, and every one of them asks the same question: what binds *now*.**

```sql
CREATE VIEW drugref.class_expansion_policy_current AS
SELECT … FROM drugref.class_expansion_policy
WHERE superseded_by IS NULL AND decision <> 'withdrawn';
```

Each reader keeps its existing predicate and changes only what it reads from:

| reader | predicate (unchanged) | effect of a withdrawal |
|---|---|---|
| `ddi_candidate_pair` (db/012) | `LEFT JOIN` + `COALESCE(decision,'allow') <> 'deny'` | the class expands again |
| `gap_unreviewed_expansion_root` (db/012) | `NOT EXISTS` | **the question re-raises** — the point of the value |
| `gap_dead_by_expansion_policy` (db/018) | `JOIN … decision = 'deny'` | the rule stops being dead; the question retires |
| `expansion_policy_unresolved` (db/010) | `FROM` | drops out — a withdrawn decision binds nothing, so there is nothing left to re-key |

**Why one view and not the predicate written four times.** This project has spent four rounds fixing one rule
kept in two places — #31 (a reach measure stated twice, where only one copy learned a correction), db/018's
two near-identical CTEs, #40's two MeSH readers, #43's two checksums. Four copies of
`superseded_by IS NULL AND decision <> 'withdrawn'` is the same bet. One view also means a **fifth** reader
cannot forget the filter, and a superseded row can never multiply `ddi_candidate_pair`'s `LEFT JOIN`.

The view is named `_current` rather than `_live` deliberately: **live and binding are different questions.**
A withdrawn row is live (not superseded) and does not bind. The writer in §5 needs the *live* row including a
withdrawn one — that is a different question asked in exactly one place, not a fifth copy of this one.

**`ci_class_subtree` does not read this table and must not start.** The deny-list filters the rule's **object
class**, never the walk: `Decreased Coagulation Activity` is a descendant of a denied root and must still
expand, which is how a rule reaches warfarin, apixaban and aspirin. Pinned by
`test_a_descendant_of_a_denied_root_still_expands`, which stays green and untouched.

## 5. The writer — `interactions.py`

`interactions.py` already owns the policy **read** (`unresolved_expansion_policy`, "kept in this module
because it reads `class_expansion_policy`, which is contraindication-expansion policy and so this module's
business") and is 191 lines, so the writer joins it there rather than starting a module.

- `record_expansion_decision(conn, source, source_code, decision, class_name, rationale, reviewed_by,
  reviewed_against) -> int` — INSERT, then point whatever was live at the new row. Returns `policy_id`.
- `withdraw_expansion_decision(conn, source, source_code, rationale, reviewed_by, reviewed_against) -> int` —
  the same move with `decision = 'withdrawn'`, carrying `class_name` forward from the live row so a withdrawal
  cannot introduce a name that was never reviewed. **Raises if there is no live row:** withdrawing a decision
  nobody made is a caller error, not a no-op.

**The supersede UPDATE is written locally rather than shared with `accumulation._supersede`.** Sharing was
considered and rejected for this round: promoting a private helper to public API and refactoring four merged
Plan C call sites widens the blast radius of a round about a different table. The duplication is **filed as a
GitHub issue** rather than left implicit (rule 5), to be promoted to a shared primitive if a third owner
appears.

**The decision vocabulary is not restated in Python.** The CHECK is its one home; a typo should fail there,
not in a second list that can disagree with the first — `db/006`'s lesson, and the reason that migration
replaced a comment-enforced coupling with a foreign key.

## 6. Verification

Two halves, because they prove different things.

**Non-regression, against the real releases.** Rebuild a measurement database from the real releases through
`drugref ingest chain` (~110 s; every release file is on disk), leaving `drugref_ops` as the pre-round
baseline the way `drugref_planc` was for Plan C. `ddi_candidate_pair` must be **21,664**,
`gap_dead_by_expansion_policy` **1**, `gap_unreviewed_expansion_root` **0**, `open_question` **18,834** over
eleven kinds, and the fourteen seed rows must all be live and binding. Reasoning that a schema change cannot
move rows is exactly what this project's rules forbid.

**The new behaviour cannot be exercised by a release, so it is pinned on controlled input and verified by
mutation.** No superseded or withdrawn row exists in any release-derived database — the release makes every
new branch dead code from its point of view. That is the same shape as #42's descriptor-wins tie-break, #53's
`is_cap_exempt` and #47's named-row tie-break, and it gets the same treatment. Tests, written failing first:

- `DELETE` refused; an `UPDATE` of any column other than `superseded_by` refused.
- `superseded_by` one-way, set once, strictly forward, same `(source, source_code)`.
- Two live rows for one key rejected **at COMMIT** — forced with `SET CONSTRAINTS ALL IMMEDIATE`, because *a
  test that never commits proves nothing* (Plan C's trap; note that statement switches the mode for the rest
  of the transaction).
- `class_expansion_policy_live_key` asserted by name — nothing but the trigger reads it.
- A correction supersedes: the old row survives with its original `rationale`, and exactly one row binds.
- **A withdrawn class returns to `gap_unreviewed_expansion_root`** and disappears from
  `expansion_policy_unresolved`.
- A withdrawn `deny` drops out of `gap_dead_by_expansion_policy` and its held-back pairs return to
  `ddi_candidate_pair`.
- The seed's fourteen decisions still bind, and `test_a_descendant_of_a_denied_root_still_expands` is green.

## 7. Traps this round leaves for the next change

- **`'withdrawn'` is not `'allow'`.** It means *no current judgement*; the class returns to the worklist. A
  future reader that folds the two together silently retires a question nobody answered.
- **The view is `_current` (binding), not `_live` (unsuperseded).** The writer deliberately asks the other
  question. Merging them breaks withdrawal.
- **Four readers, one view.** A fifth reader must go through `class_expansion_policy_current`, or it will read
  history as policy.
- **The deny-list still filters the rule's object class, never the walk.** Nothing here changes that, and
  `ci_class_subtree` must stay ignorant of this table.
- **`db/010` §1's prose is now false** ("NOT the append-only signed overlay either … edited in place, reviewed
  by diff"). That migration is applied and immutable, so the standing correction lives in
  `docs-site/docs/decisions/` — not in the migration, and not only in this spec.
- **The natural key is no longer unique, and nothing in the database says so except a partial index and a
  deferred trigger.** A future migration adding `UNIQUE (source, source_code)` back "for safety" would forbid
  every correction.
