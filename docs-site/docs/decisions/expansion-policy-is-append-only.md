# The expansion policy is append-only, and `withdrawn` is a decision

**Status:** Active
**Last reviewed:** 2026-08-03
**Applies to:** `drugref.class_expansion_policy` (`db/010`, `db/027`) and its four readers —
`ddi_candidate_pair`, `gap_unreviewed_expansion_root`, `gap_dead_by_expansion_policy`,
`expansion_policy_unresolved`
**Full derivation:** the [expansion-policy history design spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-08-03-drugref-expansion-policy-history-design.md)
([#35](https://github.com/cairn-ehr/drugref/issues/35)) and `db/010`, `db/020`, `db/023`, `db/027`

**This record carries a standing correction to `db/010`.** That migration is applied and therefore
immutable, so its prose cannot be edited in place; what follows is what stands.

## Context

`class_expansion_policy` decides whether a class-level contraindication expands over the class DAG or
pairs only with the class's direct members. It is the one table in drugref that **gates recall**: its
fourteen seeded decisions — eleven `deny`, three `allow` — hold back thousands of candidate pairs, and
for a contraindication *fewer rows is the harm direction*.

`db/010` reasoned its storage tier out explicitly, and was right **for its time**:

> * NOT a rebuildable projection. […] An ingest that wiped curator judgement would re-open every
>   bucket silently on the next release.
> * NOT the append-only signed overlay either (**that tier arrives with Plan C**). This is small,
>   low-cardinality policy data in the same class as `ci_axis` and `source_tier`: edited in place,
>   reviewed by diff.

**Plan C has since landed.** The overlay floor it built is generic over the natural key and already
shared by four curated tables, so the parenthesis became the argument *for* moving this table rather
than against it. `db/027` moves it. **The second bullet no longer describes drugref**: the table is
append-only, and neither `DELETE` nor an in-place `UPDATE` is possible any more.

Two asymmetries made that worth doing. A curator flipping `deny` → `allow` overwrote `rationale`,
`reviewed_by` and `reviewed_against`, so *what did we last say about this class, against which release,
and why did we change our mind* was unanswerable from the database — git holds only the seed, and a
node operator's post-install edits were nowhere. And this was the last clinically-consequential curated
table with no floor: a single `UPDATE … SET decision = 'deny'` removed thousands of pairs with no audit
row and nothing reporting it.

## Decision

**1. The table takes Plan C's overlay floor, and its natural key deliberately stops being unique.**
A surrogate `policy_id` is the primary key; `(source, source_code)` is no longer unique, because a
correction *preserves* the natural key and history rows therefore share it. A `UNIQUE` constraint there
would reject the only sequence that can express a correction — insert the new judgement, then point the
old row at it — and leave in-place mutation as the only implementable revision, which is precisely what
the floor exists to prevent. `superseded_by` is one-way, set once, and must point at a **later** row on
the **same** class. At most one *live* row per class is still enforced, by `db/020`'s deferred
constraint trigger over the partial `class_expansion_policy_live_key` index (see
[a curated correction needs a deferred check](correcting-a-curated-assertion.md)); no new PL/pgSQL was
written for this table.

**2. `withdrawn` is a third `decision` value, because supersession alone can retire nothing.**
Before `db/027` the policy had three states and the third was *absence*:

| state | pair set | review worklist |
|---|---|---|
| no row | expands (`COALESCE(decision,'allow')`) | **asked** by `gap_unreviewed_expansion_root` |
| `allow` | expands | silent — a curator looked and said yes |
| `deny` | direct members only | silent — a curator looked and said no |
| `withdrawn` (`db/027`) | expands | **asked again** — the judgement no longer stands |

An append-only table can never return a class to *no row*, and a correction must point at a later row
carrying the same key, so every correction leaves another live row standing. Without a fourth state a
class that had ever been ruled on could never go back on the worklist — and `medrt_run` already tells
an operator to "re-key or **withdraw**" a decision whose class a release has stopped defining. That
used to mean `DELETE`; under the floor `DELETE` raises, so the schema would otherwise ship a warning
advising an impossible action.

A value rather than a boolean beside `decision`: one column is the ruling vocabulary and all four
readers branch on it, the withdrawn row keeps its `rationale` (the audit value #35 exists to buy), and
a reader that has never heard of `withdrawn` reads it as not-`deny` and expands — for a
contraindication, the safe direction. A boolean would admit two encodings of one state, and let a
consumer reading `decision` alone be confidently wrong.

**3. Every reader goes through `drugref.class_expansion_policy_current`.**

```sql
CREATE VIEW drugref.class_expansion_policy_current AS
SELECT … FROM drugref.class_expansion_policy
WHERE superseded_by IS NULL AND decision <> 'withdrawn';
```

All four readers keep their existing predicates and change only what they read from. The filter is
stated once rather than four times: this project has spent four rounds fixing one rule kept in two
places, and one view also means a **fifth** reader cannot forget it. It keeps `ddi_candidate_pair`'s
`LEFT JOIN` one-to-one, so a history row can never multiply a pair.

The view is named `_current`, not `_live`, because **live and binding are different questions**. A
withdrawn row is live — nothing superseded it — and does not bind.

## Consequences

- **Read `class_expansion_policy_current`, never the base table.** Since `db/027` the base table holds
  history, and history read as policy is a `deny` that stopped being true.
- **`withdrawn` is not `allow`.** It means *no current judgement*, so the class returns to the review
  worklist and its rules expand meanwhile. A consumer folding the two together silently retires a
  question nobody answered.
- **Revise through `interactions.record_expansion_decision` / `withdraw_expansion_decision`.** They own
  the insert-then-supersede ordering; `superseded_by` must reference a row that already exists, and the
  single-live check is deferred, so getting the order wrong fails at `COMMIT` rather than at the call
  that caused it. Withdrawing a class with no live decision raises `NoLiveDecisionError` — a caller
  error, not a no-op — and a withdrawal carries `class_name` forward from the row it retires, so it
  cannot introduce a name nobody reviewed.
- **Do not "restore" `UNIQUE (source, source_code)` for safety.** It would forbid every correction. The
  live-row invariant is the deferred trigger's, and the partial index is what keeps it linear; nothing
  else reads that index, so a test asserts it by name.
- **The deny-list still filters the rule's *object class*, never the walk.** `ci_class_subtree` does
  not read this table and must not start: *Decreased Coagulation Activity* is a descendant of a denied
  root and must still expand, which is how a rule reaches warfarin, apixaban and aspirin.
- **Provenance stays `reviewed_by` / `reviewed_against` / `reviewed_at`** rather than adopting Plan C's
  `source` / `ingest_run` / `asserted_at` triple. A policy decision is a human reading a release at a
  time unrelated to any ingest, the seed rows are written by a migration and have no run to point at,
  and `source` here already means *who defines the class* — it is half the natural key. Recorded so a
  later round reads this as a decision rather than an oversight.
- **Nothing moved.** Rebuilt from the real releases, the fourteen seeded decisions are all live and
  binding, `ddi_candidate_pair` is unchanged at **21,664** and `gap_dead_by_expansion_policy` at **1**.
  The new behaviour cannot be exercised by any release — no superseded or withdrawn row exists in a
  release-derived database — so it is pinned on controlled input and verified by mutation.

## Related

- [A curated correction needs a deferred check, not a unique index](correcting-a-curated-assertion.md)
  — the single-live mechanism this table reuses, and why retirement needs an explicit value.
- [Append-only claims](append-only-claims.md) — the correction discipline in its original form.
- [The hybrid store](hybrid-store.md) — why curated knowledge is append-only and projections are not.
