# drugref — slice 5c.1: the curated overlay's assertion shape

**Date:** 2026-08-06 · **Status:** design, approved · **Issues:**
[#51](https://github.com/cairn-ehr/drugref/issues/51) (owned, and the case that drives the shape) ·
[#52](https://github.com/cairn-ehr/drugref/issues/52), [#55](https://github.com/cairn-ehr/drugref/issues/55),
[#67](https://github.com/cairn-ehr/drugref/issues/67) (routed to 5c, **not** answered here — §11)

The first cut of the curated overlay: the tables in which drugref may state **severity, mechanism, management
and evidence grade** over the candidate rows slices 5a/5b/5b.2 already project, plus their read path and their
worklist. **Ships with an empty curation set**, exactly as Plan C did — this slice builds the shape, and
curation itself is step 8.

## 1. Scope, and the four things this slice is not

ROADMAP's slice 5c bundles five separable subsystems. Building them together would produce one spec nobody can
review and one branch nobody can measure, so this slice takes the first and names the rest as successors:

| | | |
|---|---|---|
| **5c.1 — this slice** | the assertion tables, read path, worklist | ships empty |
| 5c.2 | the **ONC high-priority DDI floor** as first content (Phansalkar 2012 / Ayvaz 2015, re-encoded from the papers under RAND's irrevocable government licence) | needs 5c.1's shape |
| 5c.3 | **SPL/DailyMed mining** (ONSIDES-*method*, MIT precedent) | a full ingest slice of its own |
| 5c.4 | **signing** — §9 | must land before the first curated row |
| separately | issues #52, #55, #67 | §11 |

**DDInter is not in that ladder, and the omission is deliberate.** It is CC BY-NC-SA: non-commercial, therefore
not AGPL-3.0-compatible and not bundleable under rule 6. ROADMAP's "DDInter *if its licence confirms*" was
written before the check; the check has been done and the answer is no. It may only ever attach as a
node-local, separately-licensed plug-in, like every other encumbered source.

This slice adds **no new source and no new dependency**, so rule 6 raises nothing further: the only content
that could enter these tables is drugref's own judgement, `source = 'DRUGREF'`.

## 2. What a curated row attaches to

The two candidate families have different natural grains, and the difference is not cosmetic.

**Drug–drug.** `class_contraindication` holds ~739 class-level `CI_MoA`/`CI_PE` rules. `ddi_candidate_pair`
expands them at read time to **21,664** concrete pairs — it is a **view**, so a pair has no stable row identity
to reference at all. Curation therefore keys on the **rule**: 739 curatable statements, each inheriting to
every pair it expands to. This is Plan C's own lever, in its own words — *"keyed on class so a grade inherits
to every member … a few rows, not a hundred"* — and it is the only grain at which hand-curation of this
surface is finishable.

**Drug–condition.** `moiety_condition_contraindication` (13,463 assertions) and `moiety_condition_indication`
(18,314) are already per-moiety, and there is no class tier above them to curate. The grain is the moiety and
the condition.

**Neither curated table carries a foreign key into a candidate table.** Candidates are rebuildable projections,
deleted and rebuilt per `ingest_run.source`; an FK would either block the rebuild or cascade curator judgement
away with it. The curated row names the candidate by its **natural key** — stable, because `moiety_uuid` is
immortal and `class_uuid` is minted from `(source, source_code)` — and `curated_target_unresolved` (§8) reports
any curated row whose candidate is no longer projected. That is not a new pattern: `expansion_policy_unresolved`
already does exactly this, and reports 0.

### 2.1 `source` is dropped from both curated keys

Both `moiety_condition_*` tables put `source` **in** the primary key, for db/006 finding 2's reason: without it,
a second authority's independent assertion is swallowed by `ON CONFLICT DO NOTHING` and then deleted by the
next MED-RT rebuild. That argument is about **upstream assertions**, and it does not carry to this tier.

A curated row is **drugref's judgement about a clinical fact**, not a record of who said it. Keying it on the
upstream source would mean that two authorities asserting the same interaction produce two competing drugref
rulings that the single-live trigger cannot reconcile and that a consumer would have to choose between. One
fact, one live judgement. `source` remains as a **column** (`CHECK (source IN ('DRUGREF'))`, widened per
authority as 5c.2 and 5c.3 land), because it records who *authored the judgement* — which is the licence-led
layering ROADMAP describes, and a different question from which upstream release raised the candidate.

## 3. The asymmetric key, and why it is the design's load-bearing choice

`curated_interaction` keys `(subject_moiety_uuid, object_class_uuid, relationship)` — `class_contraindication`'s
key minus `source`. Including `relationship` costs nothing there: the object class fixes the axis (an MoA class
takes `CI_MoA`, a PE class `CI_PE`), so it cannot split one judgement in two, and mirroring the candidate key
keeps the join exact.

`curated_condition` keys **`(subject_moiety_uuid, object_condition_uuid)` — without `relationship`**, and that
asymmetry with its sibling is deliberate.

On the condition side the relationship is **not** determined by the object. The same `(drug, condition)` pair
genuinely carries both an indication and a contraindication — that is issue 51 in one line, and it is **168
distinct pairs**, over 154 moieties and 40 conditions, arising from 175 indication rows and 168 contraindication
rows. The flagship: nine beta-blockers are asserted both `may_treat` and `CI_with` against MeSH `D006333`
*Heart Failure*, and **both are true** — first-line in stable chronic HFrEF, contraindicated in acute
decompensation — because MeSH has one descriptor for both states.

Key on `relationship` and that single judgement must be written **twice**, once against `may_treat` and once
against `CI_with`, with nothing preventing the two copies from disagreeing. Key on the pair and there is one
row, one ruling, one thing to correct. The whole reason this slice exists is that the projection tier cannot
express this case; a key that re-splits it would reproduce the defect one layer up.

**The cost, stated plainly:** a curator cannot grade the indication and the contraindication of one pair
separately. That is the intended trade — the ruling (§4) is *about the pair*, and `severity` grades its
contraindication aspect. If a future round finds a real case needing per-relationship grades, it is an additive
migration on a table that ships empty.

## 4. The tables

Both attach to Plan C's floor with **no new PL/pgSQL** — `forbid_overlay_rewrite` as `db/020` wrote it,
`forbid_multiple_live_assertions` as `db/023` rewrote it (equality predicates, so an index can serve them),
each over a partial `<table>_live_key` index matching the trigger's predicate exactly. `db/023` measured what
happens without that index: a sequential scan per row, quadratic, **5,773 ms for 2,000 rows** against **42 ms**
with it. Nothing but the trigger reads those indexes, so a test names each one — `db/027`'s standing rule.

The surrogate primary key is likewise not a preference: a correction keeps the **same natural key**, so a
primary key on that key rejects the correction outright and in-place mutation becomes the only possible
implementation. `db/001` shipped that defect on `identity_claim` and `db/005` had to repair it.

### 4.1 `curated_interaction`

```
curated_interaction_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
subject_moiety_uuid     uuid   NOT NULL REFERENCES substance_moiety(moiety_uuid)
object_class_uuid       uuid   NOT NULL REFERENCES substance_class(class_uuid)
relationship            text   NOT NULL          -- CHECK ('CI_MoA','CI_PE')
applies                 boolean NOT NULL         -- the RULING; no DEFAULT
severity                text                     -- CHECK, NULL iff NOT applies
mechanism               text
management              text
evidence_grade          text                     -- CHECK, NULL iff NOT applies
question_uuid           uuid   REFERENCES open_question(question_uuid)
source                  text   NOT NULL          -- CHECK ('DRUGREF')
reviewed_by             text   NOT NULL
reviewed_against        text   NOT NULL
reviewed_at             timestamptz NOT NULL DEFAULT now()
superseded_by           bigint REFERENCES curated_interaction(curated_interaction_id)
```

The two foreign keys are into **immortal identity**, not into a projection: `substance_moiety` and
`substance_class` are the registry itself, which a rebuild does not touch. The candidate rule in
`class_contraindication` is referenced by key only, per §2.

### 4.2 `curated_condition`

Same floor, same provenance and evidence columns. Differences:

```
curated_condition_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
subject_moiety_uuid     uuid   NOT NULL REFERENCES substance_moiety(moiety_uuid)
object_condition_uuid   uuid   NOT NULL REFERENCES condition(condition_uuid)
ruling                  text   NOT NULL          -- the RULING; no DEFAULT
severity                text                     -- CHECK, NULL iff ruling = 'spurious'
```

Every other column — `mechanism`, `management`, `evidence_grade`, `question_uuid`, `source`, the provenance
triple, `superseded_by` — is identical to `curated_interaction`'s, with the same CHECKs and the same floor
wiring. `object_condition_uuid` references `condition(condition_uuid)`, the registry's own condition identity,
not a projection.

`ruling` is CHECKed over four values:

| value | meaning | worklist |
|---|---|---|
| `contraindicated` | the contraindication stands; any indication is outweighed | leaves |
| `indicated` | the indication stands; the contraindication is not clinically operative | leaves |
| `context_dependent` | **both are correct, in different clinical states** — the beta-blocker case | leaves |
| `spurious` | reviewed; the upstream assertion is wrong | leaves |

`context_dependent` is an honest answer, not a hedge: it is the *only* true statement about metoprolol and
heart failure at MeSH's grain, and `mechanism`/`management` carry the states in prose for a prescriber while
the enum is what a consumer branches on.

### 4.3 Rulings, and why both tables have one

`applies` and `ruling = 'spurious'` exist because of the standing rule in PROJECT-NOTES that **three separate
rounds** have had to add a ruling column after the fact — `additive_effect.accumulates`,
`interaction_group_member.satisfies_role`, `interaction_group_assertion.applies` (`db/023`), and
`class_expansion_policy.decision = 'withdrawn'` (`db/027`):

> Supersession alone can never withdraw anything. A correction must point at a later row carrying the SAME
> natural key, so every correction leaves another live row standing. **Ask what WITHDRAWING one of a table's
> statements looks like before deciding it needs no ruling column.**

Asked here, the answer is sharp. A curator who reviews a candidate and finds it wrong has, without a ruling
column, no way to say so: the gap view would re-ask about that rule every release forever, and the only
alternative — deleting the candidate — is a projection the next rebuild restores. Neither column has a
DEFAULT: a ruling must be stated, never guessed.

**Absence is not a ruling either.** No row means *nobody has looked*, and the read path (§7) returns nothing
for it. That is the same three-state distinction `class_expansion_policy` draws between no row, `allow` and
`deny`, and the same one slice 3 got right in `is_active_component` — where, note, **collapsing the unruled
state to `false` passed all 895 tests**. §10 says what a test must therefore prove.

### 4.4 One completeness CHECK per table

In `additive_effect_ruling_is_complete`'s shape, written as one CHECK rather than as several nullable columns
nobody cross-checks:

- an **asserting** row (`applies` / `ruling <> 'spurious'`) states `severity` **and** `evidence_grade`;
- a **non-asserting** row states neither.

So "graded but fires on nothing", and "asserts something with no severity and no evidence behind it", are both
unrepresentable rather than merely discouraged.

`severity` reuses Plan C's exact vocabulary — `contraindicated | major | moderate | minor` — rather than
minting a second ladder one table over. Two vocabularies for one concept is the "two lists in two places"
footgun `db/006` was written to remove, and it is what a consumer would have to reconcile at render time.

## 5. `evidence_grade`

CHECKed over four levels, strongest first: `established | probable | suspected | theoretical`.

This is the **documentation** ladder the interaction literature uses — *how well attested is this?* — and not
GRADE, which grades confidence in a recommendation derived from trial evidence and asks a question no DDI row
answers. `theoretical` is the honest label for a mechanism with no reports behind it, and having it in the
vocabulary is what stops a curator rounding such a row up to `suspected` for want of anywhere to put it.

There is **no `unknown` level.** A row that asserts a severity must state how well attested it is; a curator
who cannot is describing a question, not an assertion, and the question registry is where that belongs.

## 6. Evidence lives in `question_evidence`, not in a second store

`question_uuid` is a **nullable** foreign key into `open_question`. Where a curated row answers a gap question,
the citations behind it are already reachable: `question_evidence` holds them with `reference_scheme`,
`verdict`, its own supersession chain, and its existing warning that `reference_value` is untrusted input a
consumer must escape. Plan C's table comments already promise exactly this — grades *"traceable to evidence
through question_evidence wherever they rest on any"* — so this slice honours a promise rather than building a
second evidence store beside it.

Nullable, because a curator may assert something no gap view asked about. **`curated is not verified`**: a grade
with no evidence behind it is an opinion, and a NULL `question_uuid` is what makes that visible instead of
implied.

One interaction to respect: `questions.register_from_gaps` **deletes** a question whose gap has closed, but
only when nothing cites it — it retains, and marks `is_current = false`, any question carrying curated work.
That guard is keyed to three tables today (`question_state`, `question_source_check`, `question_evidence`).
The two new tables reference `open_question` directly, so the guard must learn about them, or a closing gap
will hit the append-only trigger and abort the whole ingest. This is a small change with an outsized failure
mode; §10 requires a test that closes a cited gap.

## 7. Read path — inner joins, and candidates left alone

Two views, both returning **only** live, asserting curated rows joined to their candidates:

- **`curated_ddi_pair`** — `ddi_candidate_pair` INNER JOIN the live applying `curated_interaction` row on
  `(subject_moiety, via_class, relationship)`. One graded rule reaches every pair it expands to; that
  inheritance is the entire argument for rule-level curation.
- **`curated_condition_ruling`** — live non-`spurious` `curated_condition` rows joined to whichever candidates
  exist for the pair, so a consumer sees drugref's ruling *and* the upstream assertions it rules on, including
  both sides of an issue-51 contradiction. **Its grain is one row per `(curated ruling, candidate assertion)`**,
  not one per ruling: the beta-blocker case returns two rows carrying the same `context_dependent` ruling, one
  naming `may_treat` and one naming `CI_with`. Aggregating the candidates into an array instead would hide
  which relationships the ruling actually reconciles, and #41's finding was precisely that folding a key
  component under an aggregate breaks a view's grain.

**Inner joins throughout, and that is the structural point.** `db/019` split `induces` into its own table
rather than adding a WHERE clause, arguing that a consumer who forgets a filter on a shared table reads a
therapeutic claim off the wrong row. The same forgetfulness here — a LEFT JOIN returning every candidate with a
NULL severity beside it — renders an unreviewed candidate as though a curator had passed it. A consumer must
**ask** for graded advice and receive only graded advice; the candidate views remain the place to ask what
upstream said.

**The candidate views do not change, and their row counts must not move.** `ddi_candidate_pair` stays at
**21,664**. A `spurious` ruling does **not** delete its candidate: `db/027`'s precedent of letting curation gate
a projection (`deny` withholds 233 pairs) governs drugref's own reading of the DAG, which is a different act
from contradicting an upstream assertion. Keeping them separate is what keeps *"what did the release say"*
answerable next to *"what does drugref say"*, and keeps the projection reproducible from its source alone.

So a `spurious` ruling **records a disagreement without acting on it**: the row stands in `curated_condition`,
the candidate stands in its projection, and no view renders either as advice. Nothing surfaces the pair to a
consumer as "drugref believes this upstream row is wrong" — deciding how, or whether, to say that is a
question for 5c.2, when there is content to say it about. Naming the deferral here so a later reader finds a
decision rather than a hole.

Following `db/027`'s naming trap — *the view is `_current` (binding), NOT `_live` (unsuperseded)* — each view is
named for what it means, and a `spurious` row is live without binding.

**Performance is measured, not reasoned.** The curated tables are tiny and `ddi_candidate_pair` is a hot path
already measured at 3.6 ms and shown to cost 18.8 ms under a plausible-looking simplification. `EXPLAIN
ANALYZE` on `curated_ddi_pair` is a deliverable, not a nicety — and `db/024`'s lesson applies to the gap views
below: a recursive walk named twice inside a correlated `NOT EXISTS` cost **59 s** where the hoisted form costs
**465 ms**, and a synthetic fixture with no edges looked fine.

## 8. Worklist — two gap kinds, and one check view

Two new gap kinds — the thirteenth and fourteenth; `gap_kind` is a name, never a number — registered in
`questions._GAP_SOURCES`. Neither name contains a colon, which `ids.mint_question_uuid` rejects outright: kind
`a:b` with key `c` and kind `a` with key `b:c` would mint **one** question for two unrelated gaps. **The
`gap_key` formats below are frozen on first mint** — `question_uuid` is `uuid5(gap_kind, gap_key)`, immortal, and externally
citable — so they are stated here rather than left to the implementation, and both are distinct from the
existing `MOIETY:` / `CLASS:` / `RXNORM_IN:` shapes:

- **`uncurated_condition_contradiction`**, `gap_key = 'MOIETY:{uuid}/CONDITION:{uuid}'` — the pairs asserted as
  both an indication and a contraindication with **no live `curated_condition` row of any ruling**. **168
  today**, and the highest-value curation queue drugref has: every row is a real clinical distinction the
  projection tier provably cannot carry. The key matches `curated_condition`'s own natural key exactly, so one
  gap maps to one curatable row. A compound key follows Plan C's `CLASS:a/CLASS:b` precedent — folding it onto
  either half would hand two independent questions one immortal UUID.
- **`uncurated_interaction_rule`**, `gap_key = 'MOIETY:{uuid}/CLASS:{uuid}/CI_AXIS:{relationship}'` —
  `class_contraindication` rules with no live `curated_interaction` row, **ranked by
  `count(*)` over the pairs the rule itself contributes to `ddi_candidate_pair`**, not by
  `descendant_class_count`. This applies issue #36's finding *before* repeating it: ranking
  `gap_unreviewed_expansion_root` by descendant class count spent a curator's explicit decision on a root whose
  expansion was a provable no-op. Order ~739, comparable to Plan C's `gap_uncurated_additive_effect` at 381.

**Both gap views test for the absence of a live row, while the read views (§7) test for a live *asserting*
row — and the two predicates are deliberately different.** A `spurious` or non-applying row is live, binds
nothing, and *has* been reviewed: it must leave the worklist and must not reach a consumer. Collapsing the two
predicates into one breaks whichever end it is collapsed toward — `db/027` met exactly this as its
`_current`-versus-`_live` distinction, and folding `withdrawn` into `allow` there silently retired a question
nobody had answered.

Deliberately **not** a gap kind: the 13,463 uncurated drug–condition contraindications at large. A queue nobody
can finish is a stale generated document, which is precisely what these views exist to replace.

**`curated_target_unresolved`** — an operational check view, not a question: curated rows whose candidate is no
longer projected after a rebuild. Modelled on `expansion_policy_unresolved`, which reports 0 and is not a gap
kind either, because a vanished candidate is an **operator** signal about an upstream change, not a clinical
question for a curator.

## 9. Provenance, and why "signed" is not yet true

Provenance is `db/027`'s triple — `reviewed_by` / `reviewed_against` / `reviewed_at` — **not** Plan C's
`ingest_run` foreign key. PROJECT-NOTES records that as a decision against, not an oversight, and the reason
applies here with more force: a human curator's assertion has no ingest run at all, and a NOT NULL FK into
`ingest_run` would force every curated row to invent one. `reviewed_against` names the release the judgement was
formed against, which is what makes *"is this ruling stale?"* answerable.

**Signing is deferred to 5c.4, with an argument rather than an omission.** No signing infrastructure exists
anywhere in the repo — no key management, no signing identity, no verification path. Adding a signature column
later is an additive migration and costs nothing. What *cannot* be done later is signing rows already
committed: the floor refuses UPDATE, so a row written before signing exists is permanently unsigned.

That is the strongest argument for **shipping this slice empty**. With no rows, there is nothing to strand —
so the sequencing constraint is simply that **signing lands before the first curated row**, i.e. before 5c.2.
Until then the tier is **signable, not signed**, and ROADMAP's and PROJECT-NOTES' unqualified "signed overlay"
overstates what exists. Both are corrected in this round's wrap-up, and a decision record states the position.

## 10. What ships, and what a test must prove

Ships: `db/029`, a `curation.py` writer module (pure argument handling plus `overlay.supersede`, under 500
lines, no transaction of its own — the caller owns it, as everywhere in these modules), the two read views, the
two gap views, the check view, and the `questions.py` registration for the two new kinds.

**Does not ship: a CLI.** Plan C shipped its five tables with no operator surface and curation as step 8; the
same applies. The writer plus tests is the deliverable, and `drugref curate` arrives with the curation round
that needs it. Stated here so it is a decision on the record rather than an omission someone later reads as
debt.

Ships **empty**: no curated rows, no seed. Every count this branch measures must therefore be **unchanged** —
`ddi_candidate_pair` 21,664, `substance_moiety` 19,438 — with `open_question` growing by exactly the two new
gap kinds' rows and by nothing else.

TDD throughout, and three assertions the slice-3 round proves are not optional:

1. **The ruling columns must be killed by a mutation test.** Deleting slice 3's `if record.active_moieties
   else None` guard — collapsing every unruled edge to `false` — **passed all 895 tests**, because the writer
   and the parser each tested NULL at their own end and nothing tested the decision between them. A test must
   fail when `applies` or `ruling` is defaulted, when the completeness CHECK is dropped, and when a `spurious`
   row starts appearing in a read view.
2. **A fixture comment stating a role is not evidence the role is exercised.** Slice 3's gap-view case reached
   the view *never*; the view had rows only incidentally, which is exactly what made it look covered. Each gap
   view's test must assert the specific row it exists to find, by key.
3. **A test that never commits proves nothing** about a deferred constraint. The single-live tests force it
   with `SET CONSTRAINTS ALL IMMEDIATE`, and a correction sequence (INSERT then supersede) must be shown to
   survive commit while a double-live one aborts.

Plus: the `register_from_gaps` retention test of §6 (close a cited gap, assert the ingest does not abort), a
test naming each partial live-key index, and the `EXPLAIN ANALYZE` measurement of §7 recorded in PROJECT-NOTES.

## 11. What this slice does not answer

- **[#52](https://github.com/cairn-ehr/drugref/issues/52)** — 422 indication assertions stored against a
  broader MeSH record than the release named, with no `concept_ui` on the row to detect it. A **projection**
  defect, fixable where the row is written; the overlay cannot repair a grain mismatch it inherits.
- **[#55](https://github.com/cairn-ehr/drugref/issues/55)** — `indications_for_condition` offering
  generalisations through a boolean rather than a structure. A read-path split on the projection tier. This
  slice sets the precedent it wants (§7's inner joins are the same structural argument) without pre-empting the
  parity decision that issue turns on.
- **[#67](https://github.com/cairn-ehr/drugref/issues/67)** — salt↔base strength equivalence. A different data
  shape entirely: a factor per `(salt, base)` pair, not a severity over a candidate. It needs either an
  authoritative source, licence-checked **before** download, or its own curation surface — and it blocks
  nothing today, since no consumer is offered a strength conversion.
- **The 168 pairs themselves.** This slice gives them a queue and a place for the answer. Answering them is
  curation, and curation is step 8.

## 12. Implementation order

1. `db/029` — `curated_interaction` + its floor wiring and live-key index; tests first.
2. `curated_condition` + floor wiring; the four-value `ruling`; both completeness CHECKs.
3. `curation.py` — the two writers over `overlay.supersede`.
4. `curated_ddi_pair`, `curated_condition_ruling`; the inner-join and `spurious`-exclusion tests.
5. `gap_uncurated_condition_contradiction`, `gap_uncurated_interaction_rule`, `curated_target_unresolved`; the
   `_GAP_SOURCES` entries; the `register_from_gaps` retention fix and its test.
6. Measure the assembled chain end to end on a real release: every prior count unchanged, the two gap kinds'
   real cardinalities recorded, `EXPLAIN ANALYZE` on both read views and both gap views.
7. Correct the "signed overlay" wording in ROADMAP and PROJECT-NOTES; publish the decision record for §3's
   asymmetric key and §9's signable-not-signed position.
