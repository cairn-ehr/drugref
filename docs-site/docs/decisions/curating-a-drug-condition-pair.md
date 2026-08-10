# Curating a drug–condition pair: the asymmetric key, a natural-key reference, and shipping empty

**Status:** Active
**Last reviewed:** 2026-08-10
**Applies to:** `drugref.curated_interaction`, `drugref.curated_condition` (`db/029`); their read views
`curated_ddi_pair`, `curated_condition_ruling`; their worklists `gap_uncurated_interaction_rule`,
`gap_uncurated_condition_contradiction`; and the operator check `curated_target_unresolved`
**Full derivation:** the [slice-5c.1 curated overlay design
spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-08-06-drugref-slice-5c1-curated-overlay-design.md)
([#51](https://github.com/cairn-ehr/drugref/issues/51)) and `db/029`

## Context

Slices 5a/5b/5b.2 project two families of **candidate** rows from upstream releases, and neither carries
drugref's own judgement: MED-RT's `CI_MoA`/`CI_PE` rules say a drug is contraindicated with a *class*, and
MeSH-keyed `CI_with`/`may_treat`/`may_prevent`/`may_diagnose` say a drug bears some relationship to a
*condition*. Neither says how severe, by what mechanism, what to do about it, or how well attested it is —
and neither can resolve the case where the release itself asserts two things that can't both be advice at
once. Plan C already built the append-only overlay MECHANISM (surrogate key, deferred single-live check,
one-way supersession) for five other tables; this slice adds the sixth and seventh, with no new PL/pgSQL,
and the two tables are not mirror images of each other.

**The drug–drug side has a class tier to curate at; the drug–condition side does not.**
`class_contraindication` holds 635 `CI_MoA`/`CI_PE` rules after the moiety gate (739 is the raw MED-RT
terminology-level count before gating — a documentation approximation, never the table's measured row
count); `ddi_candidate_pair` expands them at read time to 21,664 concrete pairs, and a view has no row
identity to reference. Curating at the rule grain is the only grain a curator can finish, and one graded
rule inherits to every pair it expands to. `moiety_condition_contraindication` (9,471 rows) and
`moiety_condition_indication` (14,674 rows) are already per-moiety with no class tier above them — the
grain here is the pair itself.

**The release asserts both an indication and a contraindication for the same pair, and MeSH cannot tell
them apart.** 168 (drug, condition) pairs in MED-RT 2026.07.06 carry both a `may_treat`/`may_prevent` row
and a `CI_with` row against the same condition — the flagship being nine beta-blockers asserted both
indicated and contraindicated for MeSH `D006333` *Heart Failure*, and both are true: first-line in stable
chronic HFrEF, contraindicated in acute decompensation, one descriptor for both states. This is issue #51
in one line.

## Decision

**1. `curated_interaction` keys on `(subject_moiety_uuid, object_class_uuid, relationship)` —
`curated_condition` keys on `(subject_moiety_uuid, object_condition_uuid)`, deliberately without
`relationship`.**

On the interaction side, including `relationship` costs nothing: the object class fixes the axis (an MoA
class only ever takes `CI_MoA`), so it cannot split one judgement into two. On the condition side the
relationship is *not* determined by the object — the same pair genuinely carries both an indication and a
contraindication. Key on `relationship` there and the beta-blocker/heart-failure judgement has to be
written **twice**, once against `may_treat` and once against `CI_with`, with nothing in the schema
preventing the two copies from disagreeing — reproducing, one layer up, the exact defect this slice exists
to fix. Key on the pair and there is one row, one ruling, one thing to correct.

`curated_condition.ruling` is CHECKed over four values, all of which retire the pair from its worklist
because all four mean a curator looked: `contraindicated`, `indicated`, `context_dependent` (the honest
answer for the beta-blocker case — not a hedge, the only true statement at MeSH's grain), and `spurious`
(the upstream assertion is wrong). `curated_interaction.applies` is the two-value equivalent on the other
table. Neither has a DEFAULT: absence of a row is a third state — nobody has looked — that no value can
express, and a ruling must be stated, never guessed.

**The cost, stated plainly:** a curator cannot grade the indication and the contraindication of one pair
separately under this key. `severity` grades the pair's contraindication aspect; `mechanism`/`management`
carry the two clinical states in prose. If a real case ever needs per-relationship grades, it is an
additive migration on a table that ships empty — not a reason to pre-split the key today.

**2. A curated row names its candidate by natural key, and carries no foreign key into it.**

Both `class_contraindication` and the `moiety_condition_*` tables are **rebuildable projections**, dropped
and rebuilt per `ingest_run.source`. A foreign key from a curated row into one of them would either block
that rebuild outright or cascade a curator's judgement away with the release that happened to raise it —
neither is acceptable for a fact drugref itself now asserts. So the curated row references its candidate
by natural key: stable, because `moiety_uuid` is immortal and `class_uuid` is minted deterministically from
`(source, source_code)`. The two foreign keys the tables *do* carry point at **identity**
(`substance_moiety`, `substance_class`, `condition`), which a projection rebuild never touches.

The cost of skipping the FK is that a rebuild *can* leave a judgement pointing at a candidate that no
longer exists, and nothing would say so by default. `curated_target_unresolved` says so: an operator check
view, modelled on `expansion_policy_unresolved`, deliberately **not** a gap kind — a vanished candidate is
an upstream-change signal for whoever ran the ingest, not a clinical question for a curator. Measured empty
on the real releases (§ below), as it must be with zero curated rows.

**3. The tier was signable, not signed — and that is why this slice ships with zero curated rows.**

When this record was written there was no signing infrastructure anywhere in the repo: no key
management, no signing identity, no verification path. `ROADMAP.md` and `PROJECT-NOTES.md` said "signed
overlay" outright, asserting a security property the schema did not have; both were corrected. What
cannot be done later is signing a row that already exists: the append-only floor refuses `UPDATE`, so a
row committed before signing exists would be permanently unsigned.

That asymmetry is the whole argument for shipping this slice's tables empty. With no curated rows, there
was nothing to strand, and the sequencing constraint fell out for free: **signing must land before the
first curated row** — which arrives with 5c.2's ONC-floor content, not with this slice.

**Delivered, 2026-08-10, and the ordering held.** Slice 5c.4 (`db/030`) built the registry, both
signature layers and `drugref verify`; see
[signing the curated overlay](signing-the-curated-overlay.md). It also **relaxed the constraint stated
above rather than merely satisfying it**: signatures are *detached rows*, not a column, so a curated row
can be signed at any later time and the irreversibility this section warned about is gone. Shipping
empty was good ordering, not a trap — and this tier is now signed, not merely signable.

## Consequences

- **The two worklists are shaped by the same asymmetry as the tables.**
  `gap_uncurated_condition_contradiction`'s key is `MOIETY:{uuid}/CONDITION:{uuid}` — matching
  `curated_condition`'s own natural key exactly, so one gap maps to one curatable row.
  `gap_uncurated_interaction_rule`'s key adds `/CI_AXIS:{relationship}`, matching `curated_interaction`'s.
  Folding either compound key onto one half would hand two independent clinical questions one immortal
  `question_uuid` — Plan C's `CLASS:a/CLASS:b` precedent, one tier up.
- **The read views and the gap views test for different things on purpose, and the two predicates must not
  be unified.** The read views (`curated_ddi_pair`, `curated_condition_ruling`) INNER JOIN for a *live,
  asserting* row (`applies` / `ruling <> 'spurious'`) — a consumer must ask for graded advice and receive
  only graded advice, never a NULL severity beside a real candidate that reads as "reviewed and harmless."
  The gap views test only for the *absence of any live row*, because a `spurious` ruling or a
  non-applying judgement is still a curator having looked, and must leave the worklist without ever
  reaching a consumer as advice. Collapsing the two predicates toward either end breaks the other end —
  `db/027`'s `_current`-vs-`_live` split on `class_expansion_policy` is the same lesson on a different
  table.
- **A `spurious` ruling records a disagreement without acting on it.** The row stands in
  `curated_condition`; the candidate it disagrees with stays exactly where it was in its projection. Making
  either fact — "drugref believes this upstream row is wrong" — visible to a consumer is left to 5c.2, when
  there is graded content to say it about.
- **Measured against the real releases** (UNII 26Feb2026 / MED-RT 2026.07.06 / MeSH 2026 /
  MeSH-relations 2026.07.06 / GSRS 2026-02-26, 2026-08-06): `gap_uncurated_condition_contradiction` is
  **168**, an exact match to issue #51's own figure — the pairs asserted as both an indication and a
  contraindication have not moved since 5b.2 measured them, because this slice adds no ingest logic.
  `gap_uncurated_interaction_rule` is **595**, not the ~739 the design spec's own prose estimates: 739 is
  the raw MED-RT terminology-level rule count, never `class_contraindication`'s actual (gated) row count,
  which is 635; of those, 40 rules pair with nobody in `ddi_candidate_pair` and are excluded by the view's
  own `JOIN` (documented in its `COMMENT ON` as deliberate — grading a rule that reaches no pair is a
  provable no-op), and all 40 are already accounted for by the two pre-existing "this class has no
  reachable members" gap views. `curated_target_unresolved`, `curated_ddi_pair` and `curated_condition_ruling`
  are all **0**, correct on an overlay with nothing curated yet. Every count this slice must not move
  (`ddi_candidate_pair` 21,664, `substance_moiety` 19,438, `condition_contraindication_expanded` 192,161)
  reproduced exactly, and `open_question` grew by precisely 168 + 595 = 763 rows, nothing else.
- **`gap_uncurated_interaction_rule` costs roughly 2.7 s on the full release, not milliseconds like every
  other view this slice adds** — but the cost is inherited whole from `ddi_candidate_pair`'s own
  unfiltered-scan cost (an `EXPLAIN ANALYZE SELECT count(*) FROM ddi_candidate_pair` with none of this
  slice's SQL involved costs the same), not from a duplicated recursive walk in the new view's own join —
  db/024's specific failure shape does not reproduce here. Filed as
  [#75](https://github.com/cairn-ehr/drugref/issues/75) rather than fixed in this slice: the fix, if one is
  wanted, belongs inside `ddi_candidate_pair`'s own definition (a prior slice's hot path, whose row count
  must not move), and PROJECT-NOTES already rejects building a second, differently-scoped implementation of
  the class DAG walk to work around a first one's cost.

## Related

- [The hybrid store](hybrid-store.md) — the same tier at the architecture level, now signed.
- [Signing the curated overlay](signing-the-curated-overlay.md) — what §3 above was waiting for.
- [A curated correction needs a deferred check, not a unique index](correcting-a-curated-assertion.md) —
  the single-live mechanism both new tables reuse unchanged.
- [The expansion policy is append-only, and `withdrawn` is a decision](expansion-policy-is-append-only.md)
  — the `_current`-vs-`_live` distinction this record's read-view/gap-view split repeats on new tables.
- [Append-only claims](append-only-claims.md) — the correction discipline in its original form.
