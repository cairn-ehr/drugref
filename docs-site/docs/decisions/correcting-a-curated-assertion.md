# A curated correction needs a deferred check, not a unique index

**Status:** Active
**Last reviewed:** 2026-08-01
**Applies to:** every append-only curated assertion table — `question_state` (`db/007`) and Plan C's
`additive_effect`, `effect_contribution`, `interaction_group_assertion`, `interaction_group_member`
(`db/020`, `db/023`)
**Full derivation:** the [additive-effect & open-question design spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-07-25-drugref-additive-effect-and-open-question-design.md)
(§5.0, §5.4) and `db/005`, `db/007`, `db/020`, `db/023`

## Context

drugref never overwrites a clinical statement. A correction **inserts** the new row and then points the
old one at it, so what drugref believed — and when — stays answerable. That matters most for exactly the
rows that already fired an alert.

Two things have to be true at once: at most **one live row per subject**, and a correction has to be
*expressible*. The design spec (§5.0) says to get the first with a partial unique index over live rows:

```sql
CREATE UNIQUE INDEX <table>_live_unique ON drugref.<table> (K) WHERE superseded_by IS NULL;
```

That is what `db/005` does for `identity_claim`, and there it works. **On a curated assertion table it
cannot.**

The difference is what a correction *changes*. Correcting an identity claim replaces one identifier with
a **different** one, so the corrected row lands on a different natural key and the two never collide.
Correcting a curated assertion — a revised threshold, a regraded contributor, a renamed group — is still
a statement about **the same subject**, so both rows carry the same key. Between the `INSERT` and the
`UPDATE` that supersedes the old row, two live rows share one key, and an immediate unique index rejects
the insert.

There is no ordering that escapes it. Pointing first is impossible (`superseded_by` is a foreign key, so
the target must already exist). Doing both in one data-modifying CTE does not help — the index is checked
as the `INSERT` runs. And the constraint cannot simply be deferred: only unique **constraints** can be
`DEFERRABLE`, and those cannot be partial, while "unique among *live* rows" is inherently partial.

So the index leaves in-place mutation as the only implementable correction — **precisely what the overlay
exists to prevent**, reached by a different route than the natural-key primary key that §5.0 warns about.

## Decision

Enforce single-live with a **`DEFERRABLE INITIALLY DEFERRED` constraint trigger**, checked at `COMMIT`.

`db/007` reached this first for `question_state`. `db/020` adopts it for all four Plan C assertion tables
through one generic function, `forbid_multiple_live_assertions()`, parameterised by the natural-key
columns — so one rule lives in one place rather than in four near-copies that can drift apart.

**Generic must not mean unindexable.** `db/020` compared a `jsonb` projection of the natural key
(`to_jsonb(t) @> $1`), which is readable, generic, and which no index can serve. Because this is a
`FOR EACH ROW` trigger, a transaction inserting *n* rows then performed *n* sequential scans of a table
*n* rows longer by the end — measured at 236 ms for 400 rows and **5,773 ms for 2,000**, quadratic.
`db/023` rebuilds the same check as an **equality predicate per natural-key column**, composed from the
same trigger arguments, backed by a partial `<table>_live_key` index over live rows: 42 ms for the same
2,000 rows, and linear. The generality was never the problem — the comparison was.

The partial unique index remains correct, and remains in use, wherever a correction changes the natural
key: `identity_claim` (`db/005`) and `question_evidence` (`db/007`), whose key includes the reference.

**The test for this must force the check.** A test that never commits proves nothing about a deferred
constraint, so every correction test issues `SET CONSTRAINTS ALL IMMEDIATE` before asserting. Note that
doing so switches the mode for the rest of the transaction — a later insert in the same test then gets
checked eagerly and needs `SET CONSTRAINTS ALL DEFERRED` first.

## Consequences

- A correction is expressible: insert, then point, in that order, and both rows are legitimately live in
  between.
- The invariant is not weakened. Leaving both rows live is still a contradiction and still fails — just
  at `COMMIT` rather than at the statement.
- A violation surfaces later than it would with an index, at the end of the transaction that caused it.
  That is the price of expressing a correction at all, and the single-writer functions in
  `accumulation.py` and `questions.py` keep the two statements together so the window is one function
  call wide.
- **Retirement needed its own answer, and it is not supersession.** Because supersession must point at a
  later row carrying the same natural key, every correction leaves another live row standing — so
  nothing could ever be *withdrawn*. `question_state` already solved this with a `withdrawn` value;
  `db/020` gives `interaction_group_member` a `satisfies_role` boolean (retiring a member is an insert of
  `false`) and `additive_effect` an `accumulates` boolean (a curator can rule that an effect does **not**
  accumulate). Both were required by behaviour the spec asks for and could not otherwise have been built:
  §5.3's "superseding the last member of a role removes the role", and §5.2's principle that *reviewed*
  must be distinguishable from *nobody looked* so a worklist stops nagging.
- **Every table with this shape needs that column** — the rule generalises, and `db/020` stopped one
  table short of it. `interaction_group_assertion` has the same shape and so had the same hole: a group
  always keeps exactly one live assertion once it has any, so there was no way to say "drugref no longer
  asserts this group". `db/023` gives it an `applies` boolean. When adding a fifth assertion table, ask
  what *withdrawing* one of its statements looks like **before** deciding it needs no ruling column;
  supersession will not do it.

## Related

- [Append-only claims](append-only-claims.md) — the overlay mechanism this refines.
- [The hybrid store](hybrid-store.md) — why curated knowledge is append-only in the first place.
