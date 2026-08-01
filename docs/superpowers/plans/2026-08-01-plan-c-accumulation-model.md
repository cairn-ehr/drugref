# Plan C — the accumulation model — IMPLEMENTATION PLAN

> **Status: forward plan.** The **canonical design is
> [§4–§8, §10 and §11 steps 6–7 of the additive-effect & open-question design
> spec](../specs/2026-07-25-drugref-additive-effect-and-open-question-design.md)**; this file only orders the
> build into TDD-sized tasks. If the two disagree, the spec wins. If a measurement here contradicts the spec,
> **stop and update the spec first**, then continue.

**Goal:** give drugref the model it currently cannot express — *many drugs, one accumulating effect* — and the
role-based exception (the triple whammy) that a count cannot represent. Ships the schema and the read contract
with an **empty curation set**, which is what §11 step 7 asks for: the tables, the two read views of §8, and
the four curation-dependent gap views of §7.1.

**Definition of done:** full suite green with `DRUGREF_TEST_DSN` set; the four assertion tables carry the §5.0
overlay shape (surrogate PK + partial-live-unique + one-way rewrite trigger) and a correction *inserts* rather
than colliding; `additive_effect_contributor` is unique on `(effect_class_uuid, moiety_uuid)` with `major`
winning; a group fires only when every distinct live role is covered; the four new gap views ship with pinned
`question_uuid` literals; `ddi_candidate_pair` is **byte-identical** on the real release (21,664 pairs) and no
slower; `NOTICE` unchanged (**no new source** — drugref is the authority here, and it asserts nothing yet).

## The three decisions this plan settles, and the evidence for them

### 1. One general walk (`class_subtree`), and `ci_class_subtree` stays exactly as it is — measured

§5.2's contributor set is "members of `effect_class_uuid` **including DAG descendants**", so Plan C needs a walk
down `class_parent`. `db/012`'s `ci_class_subtree` already walks it and its `COMMENT ON` calls itself "**THE ONE
PLACE drugref WALKS THE CLASS DAG**", so the obvious move is to generalise that view and filter it per caller —
one implementation, which is the rule the interaction debt round came out of (trap 3: *two implementations of
one expansion rule is the danger*).

**Measured on the real release, and it refutes the obvious move.** `ci_class_subtree` is scoped to the 104
classes a contraindication actually names. An unscoped closure — every class as its own root — is 18× wider, and
re-expressing `ci_class_subtree` as a filter over it costs the hot path 5×:

| | rows | filtered pair lookup (247 rows) |
|---|---:|---:|
| `ci_class_subtree` as it is (roots = the 104 classes a CI rule names) | **1,233** | **3.6 ms** |
| full closure (roots = all 4,202 classes), filtered back to CI roots | 22,754 | **18.8 ms** |

Both produce a **byte-identical** `ddi_candidate_pair` (21,664 rows), so this is purely a cost difference — and
it is the cost [#37](https://github.com/cairn-ehr/drugref/issues/37) is already open about. **Root-scoping is
what makes that walk cheap**, so it keeps its roots and this plan does not touch it.

Plan C then gets **one** general walk, `class_subtree`, rooted on *every* class. Not two: a root-scoped curated
walk would be cheaper for the read views, but `gap_uncurated_additive_effect` has to measure subtree size for
the 1,873 PE classes **nobody has curated yet** — a discovery view's roots are by definition the classes absent
from the curated tables — so a curated-root walk cannot serve it and the alternative is a third recursion. The
full closure costs **18.5 ms**, which is affordable for a view read once per ingest and acceptable for a read
path that ships with an empty curation set. If that read path ever gets hot, root-scoping it is the known fix,
and the number above is the argument for it.

The consequence is a documentation debt this plan pays rather than leaves: `db/012`'s "THE ONE PLACE" claim
stops being true the moment `class_subtree` exists, so `db/021` **re-issues that `COMMENT ON`** to say there are
two walks, which is which, and why they are not merged. A comment that quietly goes false is the defect the
Plan B review round found five of.

### 2. Promotion and role membership BOTH inherit down the DAG — the spec is silent, so this states it

§5.2 is explicit that the *effect's* contributor set includes descendants. It is silent on two others, and
silence here is not neutral — an implementer picks one and nothing records that a choice was made:

- **the promoted class** (`effect_contribution.contributor_class_uuid`) — inherits. "Keyed on **class** so a
  grade inherits to every member" is the stated lever, and a promotion that stopped at direct members would
  make the lever depend on where MED-RT happens to file a drug.
- **the role class** (`interaction_group_member.class_uuid`) — inherits, for the same reason plus the safety
  direction `db/010` settled: a group that fires is an *advisory*, and missing a member is the harm direction.

Both are stated in `COMMENT ON` and pinned by test, so a future reader finds the decision rather than
re-deriving it.

### 3. `interaction_group` gets a new namespace, not a reused one

A group is not a `substance_class` and must not share `CLASS_NAMESPACE` — the same argument `mint_condition_uuid`
already carries (a MeSH descriptor that is both a PA class and a condition would otherwise mint **one** UUID for
two kinds of thing). §10 requires the collision test be *extended*, not assumed: five namespaces, one input
string, five distinct UUIDs.

## Tasks (TDD — failing test first, in this order)

**1. `ids`: the fifth namespace and the source trio.** `GROUP_NAMESPACE`, `mint_group_uuid(source, code)`, and
an **explicit** `_SOURCE_CANONICAL['DRUGREF']` entry — §6 is emphatic that relying on the upper-case
fall-through is what bites a mixed-case source later. Tests: determinism, the five-namespace collision matrix,
and the lockstep assertion (a source admitted to `substance_class.source` is admitted to `ingest_run.source`
and canonicalises to the same spelling).

**2. `db/020` — the source trio, the five tables, the overlay floor.** `ingest_run.source` and
`substance_class.source` CHECKs gain `'DRUGREF'` (drop-and-re-add, per `db/009`'s note that the constraint is
named `ingest_run_source`). Then `additive_effect`, `effect_contribution`, `interaction_group`,
`interaction_group_assertion`, `interaction_group_member`. Every assertion table gets the §5.0 skeleton and
**one** generic rewrite trigger rather than four near-copies of `forbid_claim_rewrite`. Tests: the overlay shape
on **each of the four** assertion tables (correction inserts; superseded row survives; read views see only the
live row), and supersession one-way per table.

**3. `db/021` — `class_subtree` and the two §8 read views.**
`additive_effect_contributor(effect_class_uuid, moiety_uuid, magnitude, …)`, unique on the pair with
`max(magnitude)` and `major > minor`; `interaction_group_member_moiety(group_uuid, role, moiety_uuid)`. Also
re-issues `db/012`'s `ci_class_subtree` `COMMENT ON` (decision 1). Tests: promotion **regrades, never recruits**;
the conflict rule (one moiety reachable through two promoted classes appears once, at `major`); descendants
included; a superseded `additive_effect` stops firing.

**4. Pure threshold evaluation.** `accumulation.fires(majors, total, threshold_major, threshold_total)` — a pure
function, table-driven test, no database. The three realistic encodings `(0,2)`, `(1,2)`, `(1,1)` are the table.

**5. `db/022` — the four gap views + the eleventh `gap_kind`.**
`gap_uncurated_additive_effect` (needs the table to exist, not to be populated — it returns *everything* when
empty, which is the correct initial answer), `gap_uncurated_threshold`, `gap_ineffective_contribution`,
`gap_ungraded_contribution`. Tests: **reviewed-minor leaves the queue** (an explicit `minor` row grades
identically to an uncurated member but is absent from `gap_ungraded_contribution` — only this assertion
distinguishes them), an empty-intersection promotion appears in `gap_ineffective_contribution`, and one pinned
`question_uuid` literal per new kind.

**6. `accumulation.py` writers + `questions.py` wiring.** Single-writer curation functions in the shape
`questions.set_state` already uses (insert-then-point, in that order). Four new `_GAP_SOURCES` entries.

**7. Verify against the real releases.** Re-run the whole chain; confirm `ddi_candidate_pair` is still 21,664
and the filtered lookup is still ~3.6 ms; confirm the four new gap views return the expected empty-curation
answers; record every number in HANDOVER.

## What this plan deliberately does NOT do

- **No curation.** The tables ship empty. §11 step 8 (literature-backed curation) is continuous work, not a
  plan, and §12-H's precondition — audit every file and predicate before curating a gap — binds it, not this.
- **No `DRUGREF`-minted classes.** §6 says mint only where the release genuinely says nothing, and 5b.2's
  `induces` has since landed the nephrotoxicity content §3.4 flagged. The *ability* to mint is what this plan
  adds; exercising it is a curation decision with a worklist behind it.
- **Nothing changes for `class_contraindication` / `ddi_candidate_pair`** (§13). This model sits beside the
  pairwise projection.
