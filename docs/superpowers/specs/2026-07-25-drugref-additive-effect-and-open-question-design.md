# Design — drugref global tier: additive-effect interactions, and the open-question registry

**Date:** 2026-07-25 · **Repo:** `github.com/cairn-ehr/drugref` · **Status:** design, pending
implementation plan. **Builds on:** the
[slice-5a contraindication design](2026-07-25-drugref-slice-5a-medrt-contraindication-design.md) (the
`class_contraindication` projection and `ddi_candidate_pair` this design sits beside), the
[slice-2a MED-RT design](2026-07-23-drugref-slice-2a-medrt-classification-design.md) (the `has_PE`
membership this design's whole leverage rests on) and the
[slice-1 moiety-spine design](2026-07-23-drugref-global-moiety-spine-design.md) (§5 own-immortal-UUID, and
the `superseded_by` correction overlay reused in §5 and §7 — see §5.0 for the row shape it requires, and
§12-J for what went wrong when this document cited it without adopting it).
**Depends on PR #18** (the foundation-review hardening: `db/005` supersession invariants, `db/006` `ci_axis`)
and on **[#15](https://github.com/cairn-ehr/drugref/issues/15)** (DAG-descendant expansion — §11).

**Scope of change:** two related additions to the interaction layer.

1. **An additive-effect model.** Most clinically important combination hazards are not pairwise
   contraindications between two drugs; they are *several drugs each contributing to one physiologic
   effect* until the aggregate matters. This design models that by accumulation over the **`has_PE`
   membership drugref already ingests**, with a small curated judgement layer on top — plus a narrow
   role-based group mechanism for the combinations accumulation genuinely cannot express.
2. **An open-question registry.** The gaps this exposes (effects with no members, contributions with no
   grade, moieties with no classification) are not defects to be hidden but a **precisely-stated,
   externally-addressable worklist**. Publishing it is itself a contribution, and it is the integration
   seam for literature-mining tooling.

**The reframing this design rests on:** *additive effects* and *n-ary interactions* are one problem, not
two. Warfarin + aspirin + apixaban is not three pairwise interactions; it is three drugs converging on
decreased coagulation. Opioid + benzodiazepine + gabapentinoid is the same shape on CNS depression.
**Physiologic effect is the convergence axis** — mechanisms diverge, effects add up — which is why the PE
hierarchy carries more prescribing weight than MoA despite MoA being the more familiar axis.

**Out of scope (each stated explicitly in §13):** dose- or exposure-weighted contribution (strength lives
in slice 4+, so it is structurally unavailable); notification/messaging transport into the question
registry (an API-slice concern — this design fixes the *addressable identity* that makes it possible
later); the HTTP API; any auto-firing prescriber alert; and drug–disease contraindications (slice 5b).

---

## 1. Licence gate (rule 7 — cleared before any bundling)

**§§4–8 introduce no new external source.** The contributor data is MED-RT `has_PE`/`has_MoA` membership,
licence-verified public-domain in the slice-2a gate. Everything in the model and the registry is either
derived from that or is **drugref's own curated content**, authored in-project and therefore AGPL-3.0 by
construction. That is the gate this document clears, and it covers everything it specifies a schema for.

**§11 steps 4 and 5 do introduce new sources, and this gate does not clear them.** openFDA SPL, MeDIC,
Wikidata and FAERS appear in the sequencing (§11) and in the cost ladder (§7.2.1) as *candidates whose
consultation order this design fixes* — not as sources it authorises ingesting. Rule 7 is a per-source gate
run before bundling, and asserting "public domain, so it's clear" inline is exactly the shortcut §12-I
records this design making twice already. Each of those sources needs its own gate, in the spec that
actually ingests it:

| source | claimed licence | what the gate must still establish |
|---|---|---|
| openFDA SPL | public domain (US federal) | redistribution terms of the *bulk* downloads, and whether openFDA's not-for-clinical-use disclaimer must ride along in `NOTICE` |
| MeDIC | CC0 | version pinned, attribution form, and that the CC0 grant covers the whole distribution rather than the schema alone |
| Wikidata | CC0 | per-statement provenance is CC0 but *referenced* content may not be — supplement only |
| FAERS | public domain | prioritisation only; it must never reach the answer path (§12-I) |

The `question_source_check.source` vocabulary (§7.2.1) naming a tier is a record of *what was consulted*,
which is not itself a bundling act. Ingesting any of it is.

One consequence worth stating up front: §6 makes drugref **mint its own class concepts**. That is not a
licence question (we own what we author) but it is a *provenance* question, and §6 addresses it — a
drugref-authored effect class must be as clearly attributed as an ingested one, or a consumer cannot tell
which authority stands behind a given statement.

## 2. Why the current model cannot express the hazard

`class_contraindication` is strictly binary: one `subject_moiety_uuid`, one `object_class_uuid`.
`ddi_candidate_pair` expands it to drug *pairs*. Three real hazards do not fit:

- **The "triple whammy"** — NSAID + ACE-inhibitor/ARB + diuretic → acute kidney injury. Any *two* is
  routine practice; all three is the hazard. Not expressible as a pair, at any severity.
- **Additive physiologic effect** — several anticoagulants, or several CNS depressants, where risk emerges
  from the count and grade of contributors rather than from any single pair.
- **Effects the public terminologies never modelled at all** — see §3.4.

Note that a curated overlay layered on the existing model does not fix this: the ROADMAP specifies 5c as
*referencing* 5a/5b rows, so it inherits their arity. This is why the decision belongs **before** 5c, while
the interaction layer is one table old.

## 3. Ground truth — measured against the real 2026.07.06 release

All figures from `medrt.parse` over `Core_MEDRT_2026.07.06_XML.xml` (3,634 classes, 3,961 DAG edges, 27,540
memberships, 739 `CI_MoA`/`CI_PE` rules). Measured at the terminology level over RxCUIs, so the moiety gate
does not confound them.

### 3.1 The direct-membership read path has a 65% recall gap

| | direct only | + DAG descendants | invisible today |
|---|---|---|---|
| `CI_MoA` (462 rules) | 14,350 pairs | 18,363 | **4,013 (21.9%)** |
| `CI_PE` (277 rules) | 5,829 pairs | 39,354 | **33,525 (85.2%)** |
| both (739 rules) | **20,179** | **57,717** | **37,538 (65.0%)** |

### 3.2 …but the gap is bimodal, and size alone is the wrong discriminator

One class — `Hematologic Activity Alteration [PE]`, 114 descendants — accounts for **48.7%** of the gap
alone; the top five contributors, all abstract PE organ-system buckets, account for 68%. **Every one of the
14 CI object classes with a subtree larger than 20 is a PE "Activity Alteration" bucket; not one is a MoA
class.** Unbounded expansion would therefore be dominated by exactly the low-specificity fan-out that
justified the original direct-only default.

The inverse also holds, and it is the finding that matters:

```
Decreased Coagulation Activity [PE]      8 CI rules,  4 direct members, 109 in subtree
  └ Decreased Coagulation Factor Activity [53]  warfarin, apixaban, rivaroxaban, edoxaban,
                                                argatroban, dicumarol, tinzaparin, danaparoid …
  └ Decreased Platelet Aggregation      [45]    aspirin, ticagrelor, ticlopidine, vorapaxar …
  └ Increased Fibrinolysis/Thrombolysis [10]    alteplase, tenecteplase, streptokinase …

Central Nervous System Depression [PE]   4 CI rules, 11 direct members, 174 in subtree
Serotonin Uptake Inhibitors [MoA]        3 CI rules, 77 direct members,   0 hidden
```

A contraindication saying *"not with anything that decreases coagulation activity"* currently reaches
dabigatran and misses warfarin, apixaban, aspirin, every heparin and every thrombolytic.

The last row is the control: the **MoA** serotonin class needs no expansion whatsoever. MoA and PE behave
differently, which is why §11 prescribes a **named deny-list of abstract roots** rather than a subtree-size
threshold — size captured the coagulation and CNS cases only by luck of topology, and encodes no clinical
reasoning that would survive MED-RT reshaping its tree.

**The inclusion criterion for that list is qualitative, not the size measurement that found it.** Size is
how these 14 classes were *discovered*; freezing a size-derived enumeration would inherit the arbitrariness
of the threshold and add staleness on top. What actually distinguishes them is legible in their names and
their position: they are **abstract organ-system "Activity Alteration" buckets that assert no specific
physiologic effect** — `Hematologic Activity Alteration`, `Cardiovascular Activity Alteration` and their
siblings name a *system that is affected*, not an *effect that accumulates*. A contraindication against
"anything that alters hematologic activity" is not a clinical statement a prescriber can act on; one
against "anything that decreases coagulation activity" is. The test to apply to a candidate is therefore:

> Would a contraindication naming this class alone tell a prescriber what to avoid? If it names only the
> organ system, deny expansion. If it names the direction and the function, expand.

`Decreased Coagulation Activity [PE]` (109 in subtree) passes and is expanded despite being large; the size
of a subtree is evidence about fan-out, never about specificity.

**The deny-list is a filter on the CI rule's object class, not a barrier during traversal.** A rule whose
object class is on the list expands to its *direct members only*; a rule whose object class is anywhere
else expands over the full descendant closure, and the deny-list does not truncate that walk. The
distinction is load-bearing and the wrong reading is implementable: `Decreased Coagulation Activity` is a
**descendant** of the denied `Hematologic Activity Alteration`, so a traversal barrier would leave the
coagulation rules unexpanded — deleting the single most important case this design exists to fix.

**The list needs a per-release review gate**, or it silently rots the first time MED-RT adds an abstract
root. §7's own rule — a gap is a query, never a report — supplies the mechanism: a
`gap_unreviewed_expansion_root` view listing CI object classes whose subtree exceeds the discovery
threshold and which appear on neither the deny-list nor a reviewed-and-allowed list. A new abstract root in
the next release then surfaces as an open question rather than as a silent fan-out. The threshold survives
in that view as a *discovery heuristic for the worklist*, which is the only job it was ever fit for.

### 3.3 MED-RT does not assert the n-ary cases even pairwise

`Cyclooxygenase Inhibitors [MoA]` (61 members) and `Angiotensin-converting Enzyme Inhibitors [MoA]` (37
members) both exist and are well populated. **Zero** CI rules connect them. The triple whammy is absent
from the source, not merely unreachable by the view.

### 3.4 Some effects are absent from the *class* vocabulary — but not always from the release

**MED-RT defines no nephrotoxicity CLASS.** Zero classes match "nephrotox"; the only "toxic" hit in the
whole terminology is a therapeutic category for antidotes. Its 70-class renal hierarchy is pure physiology
(ion excretion, arterial vasoconstriction, filtration pressure) with no toxicity vocabulary. Confirmed
against the complete concept inventory — the release defines exactly eight concept types, all pharmacologic:

```
1873 PE   811 EPC   781 MoA   66 TC   59 PK   44 APC   31 HC   30 EXT     (3,695 concepts)
└──────────────── 3,634 ingested as classes ─────────────┘  └─ 61 not ─┘
```

The two totals in this document are both correct and count different things: **3,695** is every concept the
release defines, **3,634** (the figure in §3's header) is what drugref ingests as `substance_class` rows —
the six types in `medrt.INGESTED_CONCEPT_TYPES`, which sum to exactly that. The 61-concept difference is
`HC` + `EXT`, MED-RT's own housekeeping categories: no `has_*` association targets them, so they classify
no drugs and can carry no membership.

**But MED-RT is not silent on nephrotoxicity.** The `induces` predicate (170 assertions, `RxNorm → MeSH`)
carries drug-induced adverse states, including four renal ones:

```
phenacetin            -> Kidney Failure, Acute        magnesium trisilicate -> Kidney Failure, Chronic
sevoflurane           -> Kidney Failure, Acute        methoxyflurane        -> Nephrocalcinosis
```

So nephrotoxicity is expressed as a **drug→disease** relation, not as a physiologic-effect class. Coverage is
thin (4 assertions) but non-zero, and it arrives **free with slice 5b's MeSH disease ingest** rather than
needing curation. §6 and §11 are amended accordingly: 5b should precede DRUGREF-minted effect classes,
because it may supply some of what would otherwise be hand-curated.

This matters as a method point too. The `induces` predicate was visible in `medrt.py`'s
`skipped_predicates` output the whole time; it was framed as "indication/adverse-effect content for slice
5b" and never inspected for overlap with the gaps this design set out to fill. **Before curating a gap,
check the predicates already on disk.** §7's worklist should therefore carry the release's own
`skipped_predicates` inventory as a standing prompt, not just derived gaps.

Relatedly, **41 of 739 CI rules (5.5%) name a class with no members anywhere in its subtree** — across 13
distinct empty classes, of which `Genitourinary Arterial Vasoconstriction [PE]` (7 rules) and `Renal
Arterial Vasoconstriction [PE]` (6 rules) are the largest. MED-RT asserts the concern and never files a drug
under it. These rules can never produce a pair under any expansion policy.

**This is what forces §6 and motivates §7.** Nephrotoxicity cannot be modelled by pointing at an ingested
class, because no such class exists; and the 41 dead rules are not breakage but the highest-value curation
worklist available — upstream authority already vouching that the answer matters.

### 3.5 The "dead" rules are largely an indexing loss, not a knowledge gap — openFDA has the statements

**MED-RT is derived from FDA structured product labels.** So a MED-RT gap is prima facie evidence of a
derivation loss, and the label is the first place to look — not the literature. Probed against the openFDA
label API (public domain, reachable, `drug_interactions` / `contraindications` /
`warnings_and_cautions` / `use_in_specific_populations` all present as structured fields):

`Renal Arterial Vasoconstriction [PE]` — 6 CI rules, **zero members in MED-RT** (§3.4). Its six subjects are
all ARBs. Every label checked names the interacting class explicitly:

| label | names NSAID / COX-2 class in `drug_interactions` | renal-harm wording |
|---|---|---|
| losartan | yes — NSAID, non-steroidal anti-inflammatory, COX-2, cyclo-oxygenase | acute renal failure, deterioration of renal function |
| valsartan | yes | acute renal failure |
| telmisartan | yes | acute renal failure, deterioration |

**The knowledge is not missing. MED-RT simply failed to file NSAIDs under the effect class its own
contraindication points at.** The same probe found nephrotoxicity stated in labels too — gentamicin in the
boxed warning, tacrolimus in warnings — though unevenly (vancomycin and ibuprofen did not hit the two
sections probed), which matches the "requires extraction and review" triage.

**Caveat on strength of evidence:** this is a 3-drug and 4-drug probe, not a coverage measurement. It is
enough to establish that openFDA *should be consulted before curating*, not enough to quantify yield. §11
step 4 includes measuring it properly.

## 4. The model — accumulation primary, groups for exceptions

**Accumulation** covers the additive cases and gets its leverage from data already present: `has_PE`
membership answers "which drugs produce this effect" for 27,540 rows. The only thing missing is judgement,
and judgement is small enough to hand-curate.

**Groups** cover role-based combinations (the triple whammy) where the members play *different* parts and a
count is meaningless. Deliberately the minority mechanism.

Both are **curated**, so both live in the append-only signed overlay (5c's tier), not in a rebuildable
projection. Neither duplicates ingested data.

## 5. Schema

### 5.0 The shared overlay row shape — stated once, because getting it wrong is subtle

Every table in this section is an **append-only curated assertion corrected by overlay**, so every one of
them has the same skeleton. It is written out here rather than four times below, and it is **not** the
obvious natural-key schema:

| column | notes |
|---|---|
| `<table>_id` | `bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY` — a **surrogate** key |
| …the table's own columns… | |
| `source`, `ingest_run`, `asserted_at` | write-once provenance |
| `superseded_by` | `bigint REFERENCES <table>(<table>_id)`, one-way, NULL while live |

plus, for each table's natural key *K*:

```sql
CREATE UNIQUE INDEX <table>_live_unique ON drugref.<table> (K) WHERE superseded_by IS NULL;
```

**Why the natural key cannot be the primary key.** Correction-by-overlay means *inserting the new row and
then pointing the old one at it*. Both rows carry the same natural key, so a primary key on *K* rejects the
correction outright and the table can only ever be mutated in place — which is precisely what the overlay
exists to prevent. This is not hypothetical: `db/001` shipped `identity_claim` with a unique index covering
superseded rows as well as live ones, and `db/005` had to fix it after a superseded `(moiety, scheme, value)`
became permanently un-re-assertable. `identity_claim` is therefore keyed on a surrogate
`identity_claim_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY` with uniqueness enforced by a
**partial** index over live rows only (`db/005` step 1). These tables copy that, exactly.

**The surrogate is also what makes "strictly forward" representable.** `db/005`'s `forbid_claim_rewrite`
enforces one-way supersession by requiring `superseded_by > identity_claim_id` — a monotonically increasing
integer is what makes a cycle unrepresentable. With a natural-key PK there is no ordering column at all, so
the forward-only invariant §5.4 inherits could not be checked even in principle. Each table below gets a
rewrite trigger modelled on `forbid_claim_rewrite`: DELETE forbidden, only `superseded_by` mutable, set once,
never unset, and always pointing at a later row **with the same natural key** (the analogue of db/005's
same-moiety rule — a correction replaces a statement about *this* effect, not a different one).

### 5.1 `additive_effect` — which effects accumulate, and when it matters

Expected cardinality: tens of rows, ever.

| column | notes |
|---|---|
| `additive_effect_id` | surrogate PK (§5.0) |
| `effect_class_uuid` | → `substance_class(class_uuid)`; **natural key** — `UNIQUE (effect_class_uuid) WHERE superseded_by IS NULL` |
| `threshold_major` `smallint` | minimum `major` contributors |
| `threshold_total` `smallint` | minimum contributors of any grade |
| `severity` `text` | `CHECK (severity IN ('contraindicated','major','moderate','minor'))` — deliberately the same four-level vocabulary a prescriber-facing consumer expects, and CHECK-constrained rather than free text so it cannot drift per curator |
| `clinical_note` `text` | what a prescriber needs told |
| `source`, `ingest_run`, `asserted_at`, `superseded_by` | overlay provenance (§5.0, §5.4) |

Fires when `majors >= threshold_major AND contributors >= threshold_total`. Two smallints express the
realistic rules: "any two contributors" = `(0,2)`; "a major plus anything else" = `(1,2)`; "a major alone is
worth saying" = `(1,1)`. `CHECK (threshold_total >= threshold_major AND threshold_total >= 1)`.

`threshold_major = 0` is **legal but load-bearing**, and tension A is what makes it dangerous: with every
uncurated member defaulting to `minor`, an effect curated at `(0, 2)` fires on any two members of a
109-member subtree, most of which no curator has looked at. The schema cannot forbid it — `(0, 2)` is the
correct encoding for a genuinely curated effect where every member really does count — so it is surfaced
instead of prohibited: `gap_uncurated_threshold` (§7.1) lists effects with `threshold_major = 0` and fewer
graded contributors than `threshold_total`, i.e. the ones firing purely on defaults.

### 5.2 `effect_contribution` — grade, not enumeration

**This table does not list contributors.** Membership already does. It only *promotes*:

> contributor set = members of `effect_class_uuid` (including DAG descendants, per §11 step 2);
> grade defaults to `minor`; a row here promotes a contributor class to `major`.

| column | notes |
|---|---|
| `effect_contribution_id` | surrogate PK (§5.0) |
| `effect_class_uuid` | → `substance_class` |
| `contributor_class_uuid` | → `substance_class` — a **class**, never a moiety |
| `magnitude` | `CHECK (magnitude IN ('major','minor'))` |
| `source`, `ingest_run`, `asserted_at`, `superseded_by` | overlay provenance (§5.0, §5.4) |
| natural key | `UNIQUE (effect_class_uuid, contributor_class_uuid) WHERE superseded_by IS NULL` |

**Promotion regrades; it never recruits.** A row here changes the grade of moieties that are *already*
contributors — formally, the **intersection** of the contributor class's membership with the effect class's
membership-plus-descendants. It cannot add a moiety to the contributor set. This is the direct consequence
of the headline above ("this table does not list contributors"), and the alternative reading is the one an
implementer will otherwise reach for, so it is stated as a rule:

```
grade(effect E, moiety m) = 'major'  if m ∈ members(E) ∧ ∃ live row (E, C, 'major') with m ∈ members(C)
                          = 'minor'  if m ∈ members(E)
                          = undefined otherwise — m is not a contributor, and no row here makes it one
```

**An explicit `magnitude = 'minor'` row is not redundant**, which is why the CHECK admits it: it records
*"a curator looked at this class and it really is minor"*, and that is a different fact from *"nobody has
looked"* even though both grade to `minor`. The distinction is what keeps the review queue finite —
`gap_ungraded_contribution` (§7.1) lists members with **no `effect_contribution` row at all**, not members
whose grade is `minor`. Reading it the other way would leave every reviewed-and-confirmed-minor member in
the queue permanently, re-earning the same curator attention forever: the nagging failure mode §7.2.1
diagnoses for questions, in the curation layer.

Keyed on **class** so a grade inherits to every member — the ROADMAP's "curate once, apply widely" lever
doing real work. Curating bleeding means promoting the classes whose members are the serious bleeders
(`Decreased Coagulation Factor Activity [PE]` and its 53 members; the direct-Xa and direct-thrombin EPC
classes, which intersect the effect's membership through apixaban, rivaroxaban and dabigatran) and leaving
~100 other members at the default. A handful of rows, not a hundred.

**A row whose intersection is empty is a silent no-op** — a curator promotes a class that shares no member
with the effect and nothing happens, with no error anywhere. That is a curation mistake the schema cannot
catch (both UUIDs are valid `substance_class` references), so §7.1's `gap_ineffective_contribution` surfaces
it: rows whose promoted class intersects the effect's contributor set in zero moieties. It is also the gap
view most likely to fire immediately after a MED-RT reshuffle moves a class out from under an effect.

*Why default-minor rather than default-excluded:* excluding uncurated members would discard the 27,540-row
leverage that makes this design worth building. Defaulting them to `minor` keeps the coverage while
`threshold_major >= 1` filters the noise.

### 5.3 `interaction_group` — the role-based exceptions

Three tables, not two, and for the same reason the moiety spine has `substance_moiety` beside
`identity_claim`: the group's **identity** must outlive any particular assertion about it, because
`interaction_group_member` and any external citation point at it.

```
interaction_group(group_uuid PK, source_code, first_seen_ingest)
    -- immortal identity only. group_uuid = ids.mint_group_uuid('DRUGREF', source_code),
    -- deterministic exactly as class_uuid is (§6), so it is reproducible across instances.
    -- Append-only, never superseded: there is nothing here to correct.

interaction_group_assertion(interaction_group_assertion_id PK, group_uuid → interaction_group,
                            name, severity, clinical_note,
                            source, ingest_run, asserted_at, superseded_by)
    -- what is CLAIMED about the group, and the part that gets corrected.
    UNIQUE (group_uuid) WHERE superseded_by IS NULL

interaction_group_member(interaction_group_member_id PK, group_uuid → interaction_group,
                         role text, class_uuid → substance_class,
                         source, ingest_run, asserted_at, superseded_by)
    UNIQUE (group_uuid, role, class_uuid) WHERE superseded_by IS NULL
```

**Membership is versioned too, which the first draft of this design got wrong.** It gave the group header
`superseded_by` and left `interaction_group_member` a bare natural-key table — so the header was append-only
while the part that actually determines whether the group *fires* was mutable in place. Correcting which
classes satisfy the `diuretic` role would have silently rewritten history, and the record of what drugref
believed when it fired an alert would have been destroyed by the correction. Members carry the full overlay
skeleton (§5.0).

A group fires when the regimen covers **every distinct `role`** among its **live** members. The triple
whammy is one group with three roles (`NSAID`, `RAAS blocker`, `diuretic`), each role listing the classes
that satisfy it. No separate roles table: required roles are `SELECT DISTINCT role WHERE superseded_by IS
NULL`, so a role cannot exist without a live member that satisfies it — and superseding the last member of a
role *removes the role* rather than leaving a group that can never fire.

### 5.4 Correction semantics

All four tables carrying assertions — `additive_effect`, `effect_contribution`,
`interaction_group_assertion`, `interaction_group_member` — are curated clinical statements, so corrections
**overlay** rather than mutate. The mechanism is the one `db/005` hardened for `identity_claim` (set once,
same subject, strictly forward), reused **with the surrogate-key row shape it actually requires** (§5.0),
not bolted onto a natural-key table where it cannot work. A superseded row is history, never deleted: what
was believed, and when, stays answerable.

`interaction_group` itself is the exception and carries no `superseded_by`: it holds nothing but a
deterministic UUID and its provenance, so there is nothing about it that can be wrong. Retiring a group is
superseding its assertion, not deleting its identity — the same discipline that keeps `moiety_uuid`
immortal while its claims come and go.

## 6. drugref as its own authority (`source = 'DRUGREF'`)

Nephrotoxicity (§3.4) has no *class* in MED-RT for `additive_effect` to point at — and MED-RT defines no
disease concepts at all, so there is nothing else in the release to point at either (the accessory
`NDFRT-NUI_MeSH-CUI` crosswalk carries 15,311 legacy NDF-RT disease NUIs, but **zero** of them are defined as
concepts in the current release, so they cannot serve as a condition vocabulary).

**Sequencing consequence:** do slice 5b *first* where the two overlap. `induces` already asserts four renal
toxicities against MeSH disease descriptors (§3.4), so minting a `DRUGREF` nephrotoxicity class before 5b
risks hand-curating what 5b supplies. Mint a `DRUGREF` class only where the release genuinely says nothing —
which §7's worklist is what tells you.

Where minting *is* warranted, drugref becomes **one more authority in its own registry**:

- extend **all three** places an authority's spelling is pinned — they are a trio, not a pair, and db/005's
  own comment says so ("Extend this together with `ids._SOURCE_CANONICAL` and `substance_class`'s own
  CHECK"):

  | # | what | today | why it bites |
  |---|---|---|---|
  | 1 | `db/003` `substance_class.source` CHECK | `('MED-RT', 'MeSH')` | rejects the class row |
  | 2 | `db/005` `ingest_run.source` CHECK | `('UNII', 'CHEBI', 'MED-RT', 'MeSH')` | **rejects the ingest run** — and every curated row in §5 carries `ingest_run`, so nothing can be written at all |
  | 3 | `ids._SOURCE_CANONICAL` | `MED-RT`, `MEDRT`, `MESH` | keeps the stored spelling and the UUID key in lockstep |

  Missing #2 is the one that actually stops the migration: a `'DRUGREF'` class row is useless if no
  `ingest_run` may exist to attribute it to. The same applies to `'openFDA-SPL'` in §11 step 4.

- **add an explicit `_SOURCE_CANONICAL` entry per source — do not rely on the fall-through.**
  `canonical_source` returns `_SOURCE_CANONICAL.get(s.upper(), s.upper())`, so an authority not listed is
  **upper-cased**. `'DRUGREF'` survives that by luck; the sources §11 introduces do not —
  `'openFDA-SPL'` → `OPENFDA-SPL`, `'MeDIC'` → `MEDIC`, `'Wikidata'` → `WIKIDATA`. A CHECK written against
  the mixed-case literal then never matches what is stored, and a per-source rebuild silently deletes
  nothing. `MeSH` already needs its entry for exactly this reason; each new source needs one too, and the
  same spelling must be used in `question_source_check.source` (§7.2.1) so the two vocabularies cannot
  drift apart.
- mint classes with the existing `ids.mint_class_uuid('DRUGREF', code)` — no new machinery for the part
  this section is about;
- `source_code` is a drugref-assigned stable code (e.g. `NEPHROTOX`), so the UUID is deterministic and
  reproducible across instances exactly as MED-RT's are. `interaction_group` (§5.3) needs one genuinely new
  function, `ids.mint_group_uuid`, because a group is not a `substance_class` and must not share its
  namespace — it is `uuid5(GROUP_NAMESPACE, …)` beside the existing `MOIETY_`/`CLASS_`/`QUESTION_NAMESPACE`,
  and it inherits their collision test (§10).

Nephrotoxicity then becomes: mint `Nephrotoxicity [PE, DRUGREF]`, curate NSAIDs / aminoglycosides /
calcineurin-inhibitors / contrast media as contributors, and let the triple whammy group reference it.

This is what the 2a.1 source-neutral refactor was *for*, and it costs almost nothing. It does change what
drugref is — an authority, not only an aggregator — which is why the provenance must stay legible: a
consumer must always be able to ask *which authority asserted this*, and `substance_class.source` already
answers it.

**Constraint:** a `'DRUGREF'` class must never be minted where an ingested class already covers the concept.
That is a curation discipline, not something the schema can enforce; §7's `gap_uncurated_additive_effect`
surfaces the candidates so the ingested option is seen first.

## 7. The open-question registry

**Design rule: a gap is a query, never a report.** Generated documents are stale on write and nobody trusts
them. As views over ingested + curated data, gaps are always current, shrink visibly as curation lands, and
make "how much do we not know" a number that can be watched per release.

### 7.1 The derived gap views

| view | states | needs |
|---|---|---|
| `gap_unpopulated_contraindication` | a CI rule names effect *E*; **no drug is filed under *E*** (41 rules / 13 classes) | ingested only |
| `gap_unclassified_moiety` | registry moieties with no `has_PE` membership — structurally unable to participate | ingested only |
| `gap_unreviewed_expansion_root` | a CI object class whose subtree exceeds the discovery threshold and which is on neither the deny-list nor the reviewed-and-allowed list (§3.2) | ingested + the deny-list |
| `gap_unmatched_ingredient` | RxCUIs MED-RT classifies that no moiety carries | ingested **+ a new persisted table** — see below |
| `gap_uncurated_additive_effect` | a PE class that **carries ≥1 CI rule or has ≥10 members in its subtree**, and has no `additive_effect` row — a pending *decision* | §5.1 table (may be empty) |
| `gap_uncurated_threshold` | an `additive_effect` with `threshold_major = 0` and fewer graded contributors than `threshold_total` — fires on defaults alone (§5.1) | §5.1 + §5.2 populated |
| `gap_ineffective_contribution` | an `effect_contribution` row whose promoted class intersects the effect's contributor set in **zero** moieties — a silent no-op (§5.2) | §5.1 + §5.2 populated |
| `gap_ungraded_contribution` | members of a curated additive effect with **no `effect_contribution` row at all** — the review queue. Not "members at `minor`": an explicit `minor` row means *reviewed* and leaves the queue (§5.2) | §5.1 + §5.2 populated |

Only the **first two** depend on nothing but ingested data. `gap_unreviewed_expansion_root` additionally
needs the deny-list, so it lands with the expansion work (§11 step 2) that introduces it.

**`gap_unmatched_ingredient` is not free, and the first draft of this design said it was.** The claim that
it is "already counted as `unmatched_rxcuis`; made queryable" is true about the *count* and misleading about
the *data*: `medrt_run` builds the unmatched set locally and reports `unmatched_rxcuis=len(unmatched)` — the
integer survives, the RxCUIs are discarded when the function returns. There is nothing in the database to
build a view over. Making it queryable therefore needs a small persisted table
(`ingest_unmatched_ingredient(ingest_run, rxcui, name)`, rebuilt per run like any other projection) **and a
change to the ingest path**, not a view definition. It is still cheap, but it is a code change with its own
test, and §11 step 1's scope is corrected accordingly.

`gap_uncurated_additive_effect` needs `additive_effect` to exist but not to be populated (it returns
*everything* when the table is empty, which is the correct initial answer). The remaining three are only
meaningful once curation has begun, so they land with §11 step 7, alongside the curated tables they read.

The `≥1 CI rule or ≥10 subtree members` criterion is a deliberately crude first filter, chosen to make the
initial worklist finite and reviewable rather than to be clinically precise; it is a view definition and
therefore cheap to retune once a curator has seen its output.

### 7.2 `open_question` — durable, addressable, and never closed by absence

Each gap row derives a question with **immortal deterministic identity**:

```
QUESTION_NAMESPACE = uuid5(_DRUGREF_ROOT, "question")   # beside MOIETY_/CLASS_/GROUP_NAMESPACE
question_uuid      = uuid5(QUESTION_NAMESPACE, f"{gap_kind}:{gap_key}")
```

The same trick as `class_uuid`: re-derivation on every ingest yields the same UUID, so the *derived* half
(`open_question` itself) is a rebuildable projection, while everything a curator or a notifier contributes —
`question_state`, `question_source_check`, `question_evidence` — is append-only and keyed by that UUID. No
new architecture; drugref's existing hybrid store applied to a third kind of thing, with the split drawn
where §12-K says it belongs.

**`gap_key` must be pinned per `gap_kind`, because the UUID derives from it** and an external notifier will
hold references to it. It is the natural key of the thing the question is *about*, stringified — never a row
id, never anything ordering-dependent. **One format throughout:** `SCHEME:value`, with `/` joining the parts
of a compound key.
The scheme prefix is redundant for a UUID and mandatory for anything else, so requiring it everywhere costs
one token per key and removes the question of which kinds have one. These strings are frozen forever by the
UUID derivation, so the convention is settled here rather than after the first external citation:

| `gap_kind` | `gap_key` |
|---|---|
| `unpopulated_contraindication` | `CLASS:{class_uuid}` |
| `uncurated_additive_effect` | `CLASS:{class_uuid}` |
| `unreviewed_expansion_root` | `CLASS:{class_uuid}` |
| `ungraded_contribution` | `CLASS:{effect_class_uuid}/CLASS:{contributor_class_uuid}` |
| `ineffective_contribution` | `CLASS:{effect_class_uuid}/CLASS:{contributor_class_uuid}` |
| `uncurated_threshold` | `CLASS:{effect_class_uuid}` |
| `unclassified_moiety` | `MOIETY:{moiety_uuid}` |
| `unmatched_ingredient` | `RXNORM_IN:{rxcui}` |

**`gap_kind` may not contain `':'`, and this must be enforced at mint time.** Found while implementing Plan
A, by the test written before the function: the joiner alone does *not* separate the two fields. Kind `a:b`
with key `c` and kind `a` with key `b:c` both build `"a:b:c"` and mint **one question for two unrelated
gaps** — a silent merge, invisible downstream and permanent once cited. `gap_key` must keep its colons (they
are the scheme prefixes), so the constraint belongs on `gap_kind`, which is drugref's own closed vocabulary
and has no use for one. With that, the first `':'` splits kind from key unambiguously.

Class and moiety UUIDs are themselves immortal — `substance_class` UUIDs are derived from `(source, code)`
and `db/005` makes `moiety_uuid` immortal outright — so a question's identity is as stable as its subject. A
pinned-literal test guards the derivation (§10).

| column | notes |
|---|---|
| `question_uuid` | PK, deterministic (above) |
| `gap_kind`, `gap_key` | what derived it |
| `question_text` | the literature-searchable statement |
| `search_expression` | what was asked, so re-asking is reproducible — **deferred, see below** |
| `first_derived_ingest`, `last_derived_ingest` | write-once / refreshed provenance: when this question first appeared, and whether the gap that derives it is still open |
| `is_current` | **added while implementing Plan A**: is the gap still derived? See below |

**`search_expression` is NOT in `db/007`, deliberately.** Plan A derives questions; it does not run
searches, so nothing populates this column and no plan has yet decided what a search expression looks like.
Migrations are immutable once applied, so shipping the column would freeze a guess permanently. The plan
that actually mines literature adds it, in its own migration, with the shape its searches need.

**`is_current` was forced by the cascade, and it is not cosmetic.** §7.2 says a closed gap must be able to
leave the projection, and every curated table is `ON DELETE CASCADE` from `open_question` — but those
tables are also append-only, with a trigger that refuses `DELETE`. Those two facts do not merely trade off
against each other: deleting a closed question that carries any curator row trips the trigger and **aborts
the entire ingest transaction**. The registry therefore deletes only untouched questions and retains the
rest with `is_current` false — excluded from `question_worklist`, still citable, and restored to current
under the same UUID if the gap reopens.

**`state` does not live here** — and the first draft of this design put it here, which would have broken
tension F. The reasoning: §7.2 calls the derived half "a rebuildable projection", re-derived from the gap
views on every ingest. A `withdrawn` flag on a rebuildable table is erased by the next rebuild and the
suppressed question comes straight back; `answered` likewise. Curator intent is not derivable from the gap
views, so it cannot live on the table those views rebuild.

It moves to its own append-only table, keyed by the deterministic `question_uuid` — which is exactly what
the immortal identity is *for*:

```
question_state(question_state_id PK, question_uuid → open_question,
               state, rationale, source, ingest_run, asserted_at, superseded_by)
  state : 'open' | 'evidence_under_review' | 'answered' | 'withdrawn'
  UNIQUE (question_uuid) WHERE superseded_by IS NULL
```

Same overlay skeleton as §5.0, for the same reason: a question moving from `evidence_under_review` back to
`open` is a correction with a history worth keeping. A question with no `question_state` row is `open` by
default, so the derived half can register thousands of questions without writing a state row for any of
them. The rebuild of `open_question` is then an upsert on `question_uuid` that refreshes `question_text`,
`last_derived_ingest` and `is_current`, and touches nothing a curator owns.

### 7.2.1 `question_source_check` — the watermark is per SOURCE TIER, not just literature

A single `evaluated_through` date was the first design here, and it was wrong: it assumes literature is the
only place an answer can come from. §3.5 disproves that — the answer to six of the dead rules was sitting in
an openFDA label the whole time. A question therefore needs to record **which tier has been consulted, at
what version, with what outcome**:

```
question_source_check(question_source_check_id PK,          -- surrogate (§5.0)
                      question_uuid → open_question,
                      source, source_version, checked_at, outcome, note)
  source  : CHECK IN ('MED-RT','openFDA-SPL','MeDIC','Wikidata','FAERS','literature')
  outcome : CHECK IN ('covered','not_covered','partial','error')
  source_version : text NOT NULL          -- see below
  UNIQUE (question_uuid, source, source_version)
```

`source_version` is the release/label version checked, so a re-check against a *newer* version is a new row
rather than an overwrite — the same append-only discipline as the evidence table, and what makes "has this
been looked at since the January labels?" answerable.

**Two corrections to the first draft of this table.**

*The key could not hold a literature row.* `PK (question_uuid, source, source_version)` makes
`source_version` NOT NULL by definition, and literature has no release version — so the one tier the whole
watermark idea started from was the one tier the key could not record. Rather than allow NULL (which would
also silently permit unlimited duplicate checks, since NULLs do not conflict), `source_version` stays NOT
NULL and **every tier defines what it means**:

| source | `source_version` |
|---|---|
| `MED-RT` | the release string, e.g. `2026.07.06` |
| `openFDA-SPL` | the openFDA export date the query ran against |
| `MeDIC` | the distribution version |
| `Wikidata` | the ISO date of the query |
| `FAERS` | the quarterly extract, e.g. `2026Q2` |
| `literature` | the ISO date the search ran (`2026-07-25`) — which is the right answer anyway: re-asking the literature is a *new search on a later corpus*, and the date is exactly what "has this been looked at recently?" means |

The uniqueness constraint moves off the primary key for the reason §5.0 gives — this table records
observations rather than assertions and is never superseded, but the surrogate key keeps it consistent with
its neighbours and gives an `ORDER BY` that does not depend on `checked_at` ties.

*`source` and `outcome` were left as free text* while §5.1 argued at length that `severity` must be
CHECK-constrained "so it cannot drift per curator". The same reasoning applies with more force here, since
the cheapest-unchecked-tier ordering is a **join against these literals**: a row written as `'openfda-spl'`
does not merely look untidy, it makes the question appear never to have been checked and re-earns expensive
literature effort forever. Both get CHECKs, and `source` uses the same spellings as `_SOURCE_CANONICAL`
(§6) so the two vocabularies cannot diverge.

**This is what makes the cost ladder enforceable rather than aspirational.** A question with no
`openFDA-SPL` row has not earned literature-mining effort yet, and the worklist views should order by
cheapest-unchecked-tier so the free sources are always exhausted first.

| tier | cost | licence | why this order |
|---|---|---|---|
| MED-RT (all files, all predicates) | free, on disk | public domain | §12-H — already paid for |
| openFDA SPL | free; **bulk download**, not the API, for anything corpus-wide | public domain, gate pending (§1) | **the source MED-RT is derived from** (§3.5) |
| MeDIC | free bulk | CC0 | drug–disease indications/contraindications seed |
| Wikidata | free | CC0 | supplement only — cross-identifiers, candidate leads |
| FAERS | free | public domain | signal *prioritisation*, not decision support |
| literature mining | costly, high value | varies | for what none of the above answers |
| hand curation | most costly | drugref's own | last resort, and the durable value-add |

**Watermark, not closure.** "No evidence found" is `open` with recent `question_source_check` rows, **not** a
terminal state. Medicine is young and fast-moving; a question unanswerable this month may be answerable
next, and re-evaluation is incremental — re-check only sources whose version has moved since the last row.
The *only* terminal state is `withdrawn` (malformed or duplicated question). This is the property that makes
leaving thousands of questions open sustainable rather than nagging.

An `answered` question also stays in the registry and keeps accepting evidence.

### 7.3 `question_evidence` — append-only, supersedable

```
question_evidence(question_evidence_id PK,                  -- surrogate (§5.0)
                  question_uuid → open_question,
                  reference_scheme, reference_value, verdict, confidence,
                  source, ingest_run, asserted_at, superseded_by)
  reference_scheme : CHECK IN ('DOI','PMID','PMCID','NCT','SPL','URL')
  UNIQUE (question_uuid, reference_scheme, reference_value) WHERE superseded_by IS NULL
```

A later finding may supersede an earlier one; nothing is deleted. Medicine revises, and the schema must let
it revise without destroying the record of what was believed before. Same mechanism and same row shape as
§5.4 and §5.0.

**`reference` is split into a scheme and a value rather than left free text**, which is what the first draft
had. Three reasons, in increasing order of importance. It makes citations *dedupable* — the same paper
arriving as a bare DOI, a DOI URL and a PubMed link is otherwise three rows saying one thing, and the
`UNIQUE` above cannot help. It makes them *resolvable* without guessing. And it is the field most likely to
be rendered by a downstream consumer, so `URL` being one scheme among several — rather than the implicit
default — keeps unvalidated links a deliberate, visible choice rather than the path of least resistance for
whatever pastes into it. `URL` stays in the vocabulary because some evidence genuinely has no better
identifier; consumers rendering it should treat it as untrusted, and the `COMMENT ON` (§9) says so.

**Why deterministic UUIDs matter beyond tidiness:** an external tool cannot notify drugref about "that
renal vasoconstriction thing" — it needs a stable key. Building the identity now, before anything notifies
it, is far cheaper than retrofitting it onto questions already cited elsewhere. The transport itself
(messaging, polling, a human pasting a DOI) is deliberately out of scope (§13).

## 8. Output contract — facts and thresholds, not verdicts

drugref publishes what it knows and the thresholds it judges significant; the **consumer** intersects that
with a patient's regimen. Two views are the contract of record:

- `additive_effect_contributor(effect_class_uuid, moiety_uuid, magnitude)` — the flattened fact table,
  effect → members (with descendants) → grade. **Unique on `(effect_class_uuid, moiety_uuid)`**; see the
  conflict rule below.
- `interaction_group_member_moiety(group_uuid, role, moiety_uuid)` — live members only.

**The conflict rule is part of the contract, not an implementation detail.** One moiety can reach one effect
through several promoted classes: aspirin is a member of `Decreased Platelet Aggregation`, and a curator may
also have promoted an EPC class it belongs to. Without a stated rule the view emits that moiety twice, and
since §8's whole evaluation is *count the contributors*, a duplicated row is the difference between firing
and not firing at `threshold_total = 2` — one drug counted as two. So:

> `magnitude = max(magnitude)` over all live promotions, with `major > minor`, grouped by
> `(effect_class_uuid, moiety_uuid)`. The view is unique on that pair, and a consumer may rely on it.

`major` winning is the safety-preserving direction, and it also matches what a curator means: promoting a
class is an assertion that *these members matter more*, never a demotion of anything already promoted.

This is the one place the "genuinely small" claim below needs qualifying: the intersection and count really
are small, but only because the view guarantees one row per moiety. A consumer computing the count from a
non-deduplicated join would get it wrong, which is why the guarantee is stated here rather than left to be
inferred.

Consumers read `additive_effect` for the thresholds and clinical notes, and apply them to the intersection.
This keeps the global tier **stateless and free of patient data**, matches the existing read-time-expansion
pattern (`ddi_candidate_pair`), and lets a consumer cache the facts. The evaluation itself is a set
intersection and a count.

Every view carries `upstream_release` / `asserted_at` so staleness is answerable from the read path — the
lesson `db/006` applied to `ddi_candidate_pair`.

## 9. Clinical-safety posture

Unchanged from 5a and restated because this design widens what drugref says:

- **Candidate tier. Nothing here auto-alerts.** A threshold being met is an input to review, not a rendered
  warning.
- **Absence carries no information.** §3.4 is the proof: 41 rules point at empty classes and MED-RT has no
  nephrotoxicity concept at all, so "no finding" may mean "not modelled anywhere". Every read path must be
  documented to that effect via `COMMENT ON`, per `db/006`'s precedent — `--` comments do not survive to
  the catalog.
- **Grades and thresholds are drugref's own clinical judgements**, not upstream facts. They must be
  attributed as such (`source = 'DRUGREF'`) and be traceable to evidence via §7.3 wherever they rest on it.
- **Curated ≠ verified.** A `major` grade with no `question_evidence` behind it is an opinion; the registry
  makes that visible rather than letting it pass as sourced.
- **Extracted text is untrusted input.** §11 step 4 parses free-text label sections written by third
  parties into a clinical database. Extraction lands as a candidate-tier projection reviewed through §7 and
  is never promoted to fact by the extractor itself; `question_evidence.reference_scheme = 'URL'` and
  `question_text` are likewise author-supplied strings that a consumer may render. The `COMMENT ON` for each
  must say so, for the same reason `db/006` moved the directionality contract into the catalog: the
  constraint that lives only in a design document is the one that gets lost.

## 10. Testing (TDD, failing-test-first)

- **Pure functions first**: threshold evaluation (`majors`, `total` → fires?) is pure and gets a table-driven
  unit test with no database.
- **Accumulation acceptance matrix** (DB-gated): a curated effect with three members at mixed grades;
  assert firing at each `(threshold_major, threshold_total)`; assert default-minor for uncurated members;
  assert descendant contributors are included; assert a superseded `additive_effect` row stops firing.
- **The overlay row shape (§5.0), on every one of the four assertion tables.** These get the same three
  assertions each, and they are the tests that would have caught the natural-key-PK defect: a correction
  **inserts** rather than failing on a uniqueness violation; the superseded row survives and stays readable;
  the read views see only the live row. `additive_effect` alone is not enough coverage —
  `effect_contribution`, `interaction_group_assertion` and `interaction_group_member` are the ones most
  likely to be implemented with the natural key, because that is the shape they read as.
- **Supersession is one-way**, per table: `superseded_by` cannot be unset, cannot be re-pointed, and cannot
  reference an earlier row — mirroring `db/005`'s `forbid_claim_rewrite` tests.
- **Contributor promotion regrades, never recruits** (§5.2): a moiety in the promoted class but *not* in the
  effect's membership does not appear in `additive_effect_contributor` at all; and a promotion whose
  intersection is empty shows up in `gap_ineffective_contribution`.
- **Reviewed-minor leaves the queue** (§5.2): a member with an explicit `magnitude = 'minor'` row grades
  identically to an uncurated one but is **absent** from `gap_ungraded_contribution`, while the uncurated
  one remains. The two are indistinguishable by grade alone, so only this assertion pins the difference.
- **The `additive_effect_contributor` conflict rule** (§8): a moiety reachable through two promoted classes
  appears **once**, at `major`. Asserted directly, because a consumer's count depends on it.
- **Group semantics**: fires only when all distinct roles are covered; two drugs satisfying the *same* role
  do **not** fire it; superseding the last live member of a role removes the role rather than leaving a
  group that can never fire.
- **Descendant expansion and the deny-list** (Plan B — currently untested by this list, and the whole
  content of a shippable slice): a denied root expands to direct members only; an allowed class expands over
  its full closure; `Decreased Coagulation Activity [PE]` reaches warfarin, apixaban and aspirin after
  expansion and only dabigatran before it; and — the regression test the §3.2 control case exists to
  provide — `Serotonin Uptake Inhibitors [MoA]` returns an identical pair set with expansion on and off.
- **Question determinism**: a pinned `question_uuid` literal, guarding the derivation the way
  `test_class_registry_source_neutral.py` pins class UUIDs — an external notifier depends on it. Pin one
  literal per `gap_kind`, since the `gap_key` format (§7.2) is frozen per kind, not globally.
- **Curator state survives rebuild** (§7.2): register a question, mark it `withdrawn`, re-run the derivation,
  assert it is still `withdrawn` and still absent from the worklist. This is the test that distinguishes the
  corrected design from the one that put `state` on the rebuildable table, where it would silently pass on a
  fresh database and fail only on the second ingest.
- **Watermark semantics**: a question with no `question_state` row is treated as `open`; an `open` question
  whose newest `question_source_check` is old still appears in the worklist (absence of evidence is not
  closure); a `withdrawn` one does not appear.
- **Source-check append-only-ness**: re-checking the same source at a *newer* `source_version` inserts a
  second row rather than overwriting; re-checking at the *same* version conflicts. A `literature` check
  records the search date as its version and is admissible — the case the first draft's primary key could
  not represent at all.
- **Cheapest-unchecked-tier ordering**: a question with no `openFDA-SPL` check sorts ahead of one that has
  been checked there, so the ladder that governs where effort goes is asserted rather than assumed.
- **`DRUGREF`-minted classes** coexist with MED-RT and MeSH, and a per-source rebuild of either leaves
  drugref-authored classes untouched.
- **The four namespaces do not collide**: a moiety, a class, a group and a question minted from the *same*
  input string yield four different UUIDs — extending
  `test_class_uuids_still_cannot_collide_with_moiety_uuids` to the two new namespaces rather than assuming
  uuid5 makes it impossible.
- **The source trio stays in lockstep** (§6): a source admitted to `substance_class.source` is admitted to
  `ingest_run.source` and canonicalises to the same spelling through `ids.canonical_source` — the assertion
  that fails loudly if a future source extends one CHECK and forgets the other two.

## 11. Sequencing and dependencies

1. **The question registry and gap views** (§7). No curation required, ships early, converts the foundation
   review's findings into standing infrastructure, and is the thing that produces value first. Depends on
   nothing beyond current `main` + PR #18 — with **one correction to the original claim**: of its gap views
   only `gap_unpopulated_contraindication` and `gap_unclassified_moiety` are pure views over existing
   tables. `gap_unmatched_ingredient` additionally needs the unmatched RxCUIs *persisted*, which
   `medrt_run` does not do today (§7.1), so this step carries a small ingest-path change and its own test.
2. **#15 descendant expansion, with a named deny-list** of the ~14 abstract PE organ-system roots (§3.2) —
   *not* a subtree-size threshold, and applied as a filter on the CI rule's object class rather than as a
   traversal barrier. Contributor sets in §5.2 are wrong without this, and it changes what several gap views
   return, so it precedes the curated tables. Ships `gap_unreviewed_expansion_root` with it, so the list
   cannot rot silently across releases.
3. **Slice 5b (MeSH disease descriptors), where it overlaps a gap** — moved ahead of DRUGREF minting by the
   §3.4 audit. `induces` / `may_treat` / `CI_with` all resolve once MeSH diseases are ingested, and `induces`
   already covers part of the nephrotoxicity gap this design would otherwise hand-curate. Curating before 5b
   risks paying for what the release supplies. The accessory crosswalk resolves 50.8% of the M-codes, which
   shrinks 5b's unknown but does not remove it (no tree numbers, 49% unresolved).
4. **Extract from openFDA SPL, before any curation** (§3.5). MED-RT is derived from these labels, so a
   MED-RT gap should be checked against the label first. Two things belong here: **measure** the yield
   properly (does openFDA resolve the 41 dead rules and the 13 empty classes? — §3.5 is a 3-drug probe, not
   a measurement), and if it does, ingest the extraction as a **projection** with `source = 'openFDA-SPL'`,
   attributed in `NOTICE` and admitted to all three source lists (§6). Corpus-wide work uses the bulk
   downloads, not the per-request API. **The rule-7 gate for openFDA runs in that spec, not this one**
   (§1) — extraction quality is the real risk, and is why this lands as a candidate-tier projection reviewed
   via §7 rather than as fact.
5. **MeDIC** — CC0 drug–disease indications/contraindications, gate pending (§1). Overlaps
   `may_treat`/`CI_with`, so import after 5b to make the overlap measurable rather than duplicated.
6. **`source = 'DRUGREF'` minting** (§6) — one migration, small, and scoped to what steps 3–5 did *not*
   supply. **This is now expected to be a much smaller set than first designed.**
7. **The curated tables** (§5) with an empty curation set, plus the read views (§8) and the four
   curation-dependent gap views (§7.1).
8. **Literature-backed curation**, driven by the §7 worklist, landing as `question_evidence` plus curated
   grades.

*(Steps 4 and 5 were `3a`/`3b` in the first draft. `3a.` is not a Markdown ordered-list marker, so those two
steps rendered as loose paragraphs and split the list in two — renumbered rather than re-broken.)*

**Recommended decomposition — this spec is too large for one implementation plan.** Three plans:

- **Plan A — the open-question registry** (step 1): the two pure gap views, the persisted-unmatched change
  behind the third, `open_question`, `question_state`, `question_source_check`, `question_evidence`, and
  deterministic UUID minting. Self-contained, ships value early, and needs none of the model below.
  *Start here.*
- **Plan B — descendant expansion** (step 2): closes #15 with the named deny-list and its review gate.
  Independently useful — it improves `ddi_candidate_pair` whether or not the accumulation model is ever
  built.
- **Plan C — the accumulation model** (steps 6–7): `DRUGREF` minting, the four assertion tables of §5, the
  read views, and the remaining gap views. **Gated on slice 5b** (step 3) for any effect 5b might supply —
  see §12-H.

Step 8 is continuous curation work, not a plan. Slice 5b keeps its own separate spec. Each of A/B/C gets its
own spec-to-plan cycle if it grows beyond what this document already settles.

**A precondition on Plan C, learned the hard way (§12-H): before curating any gap, audit every file and
every predicate in the relevant release for content that already covers it.** Plan A's worklist is the
mechanism — it carries the `skipped_predicates` inventory so the question "did we already have this?" is
asked automatically rather than remembered.

## 12. Design tensions recorded

**A. Default-minor vs default-excluded contributors** (§5.2). Resolved to default-minor: excluding
uncurated members throws away the ingested-membership leverage that motivates the whole design. Accepted
cost: an uncurated effect with `threshold_major = 0` would fire on weak contributors, so `threshold_major
>= 1` is the recommended default when curating a new effect. That recommendation is **advice the schema
cannot enforce** — `(0, 2)` is legitimate for a fully curated effect — so §7.1's `gap_uncurated_threshold`
makes the risky combination visible instead: `threshold_major = 0` with fewer graded contributors than
`threshold_total` means the effect is firing on defaults nobody reviewed.

**B. Subtree-size threshold vs named deny-list** for descendant expansion. Resolved to the deny-list. Size
worked for coagulation (6 descendants) and CNS depression (4) by topological luck; it encodes no clinical
distinction and would silently change meaning when MED-RT reshapes its hierarchy. A named list of abstract
roots states what is actually meant and fails visibly.

**Sharpened after review:** naming the list is not enough if the *membership criterion* is still size — a
frozen size-derived enumeration inherits the arbitrariness and adds staleness. §3.2 now states a qualitative
criterion (does the class name an effect a prescriber can act on, or only an organ system?), keeps size as a
*discovery heuristic for the worklist* rather than a rule, and adds `gap_unreviewed_expansion_root` so a new
abstract root in the next release surfaces as a question instead of silently fanning out.

**C. Class-level vs moiety-level grading** (§5.2). Class-level, for curation economy. Accepted cost: a
moiety that is an atypical member of its class cannot be graded individually. Revisit only if real cases
appear; a moiety-level override table is additive.

**D. Evaluate vs publish** (§8). Resolved to publish. Keeps patient data out of the global tier and lets
consumers cache. Accepted cost: N consumers each implement the intersection. Mitigated by the logic being a
set intersection and a count — genuinely small — and a convenience SQL function remains additive later.

**E. Grade vocabulary `major`/`minor`.** Two values only, deliberately. A finer scale invites precision the
evidence cannot support, and dose — the thing that would actually justify a scale — is unavailable until
slice 4. Widening the CHECK later is additive.

**F. Auto-registered vs curator-promoted questions** (§7.2). Auto-registered from the gap views, with
`withdrawn` to suppress noise. Rationale: the default should be that a known gap *is* a question; requiring
a manual promotion step means real gaps sit unregistered because nobody did the paperwork.

**G. drugref minting its own classes** (§6). Accepted, but **narrowed** after auditing the release: mint only
where the release genuinely says nothing, and run slice 5b first where the two overlap (§3.4 — `induces`
already covers four renal toxicities). Tension: drugref-authored classes have no external validation.
Mitigated by attribution (`source`) and by §7.3 evidence links.

**H. Existing sources were assumed exhausted before they were.** This design initially justified curation
and literature-mining for gaps without auditing everything already on disk. The audit found: `induces`
covers nephrotoxicity as a drug→disease relation (§3.4); `has_SC` has **248 MED-RT-targeted assertions**
(210 `RxNorm→MED-RT`, 38 `MED-RT→MED-RT`) ingestible today **without the MeSH bridge** — which the HANDOVER
and ROADMAP note that `has_SC` "points into MeSH" does not account for. Stated precisely, since the first
draft of this tension overstated it: `has_SC` is 3,632 assertions, and the MeSH characterisation is right
for 3,384 of them (2,916 `RxNorm→MeSH`, 468 `MED-RT→MeSH`). It is *incomplete*, not wrong — but the 248 it
omits need no bridge at all, so they were available the whole time the follow-up was filed as blocked on
one; and the accessory `NDFRT-NUI_MeSH-CUI` crosswalk resolves **5,030 of 9,908 (50.8%)** of
the MeSH M-codes the release references, reducing (though not removing — no tree numbers, 49% unresolved)
slice 5b's stated unknown.

Hypotheses checked and **closed negative**, recorded so they are not re-litigated: class-level
`has_PE`/`has_MoA`/`has_TC` (756 edges, [#8](https://github.com/cairn-ehr/drugref/issues/8)) propagate only
**299** extra contributions (+2.5%) across 13 effects and fill **zero** previously-empty effects; the 13
empty classes of [#19](https://github.com/cairn-ehr/drugref/issues/19) have **zero** class-level
contributors, so that finding stands; `Core_MEDRT_DTS.xml` is the same content as the core XML (96,516
associations in both); the `Core_MEDRT_SPL` archive holds class *listings* only (1,873 PE / 781 MoA / 1,127
EPC NUIs), no membership; the `NDFRT-NUI_RxNorm-RxCUI` crosswalk is concept identity (including dose forms),
not membership; and `CI_with` is 11,524 `RxNorm→MeSH` against 2 `RxNorm→MED-RT`, so treating it as
MeSH-keyed was correct.

**I. The cost ladder was inverted — twice.** §12-H caught it within one source; the same error held across
sources. This design initially proposed literature mining and hand curation as the mechanism for filling
gaps, without first consulting the CC0 sources already triaged as *core source* and *import now* in the
project's own interaction-source strategy: **openFDA SPL, MeDIC, Wikidata, FAERS**.

Probing openFDA settled it (§3.5): the ARB labels name the NSAID/COX-2 class explicitly, with acute-renal-
failure wording — so `Renal Arterial Vasoconstriction [PE]`'s six "dead" rules are a MED-RT **indexing loss**,
not missing knowledge. MED-RT is *derived* from these labels; a gap in the derivative is the wrong place to
conclude the knowledge does not exist.

Resolved: §7.2.1 replaces the single literature watermark with **per-source-tier check rows**, and the
worklist orders by cheapest-unchecked-tier, so free structured sources are exhausted before expensive ones.
A question with no `openFDA-SPL` check has not earned literature-mining effort. FAERS stays out of the answer
path entirely — it prioritises the worklist, it does not populate it.

**The generalisable rule, in two parts: audit every file and predicate of a source before curating (§12-H);
and audit every source in the tier list — especially the one your source is derived FROM — before mining or
curating (this tension).** §7's worklist mechanises both: it carries the `skipped_predicates` inventory and
the per-tier check rows, so "did we already have this?" is asked structurally rather than remembered.

**J. The append-only mechanism was cited but not actually adopted.** §5 originally gave every curated table
a *natural-key* primary key — `additive_effect(effect_class_uuid)`,
`effect_contribution(effect_class_uuid, contributor_class_uuid)`,
`interaction_group_member(group_uuid, role, class_uuid)` — while §5.4 claimed the `db/005` overlay
mechanism was "reused unchanged". The two are incompatible: an overlay correction inserts a second row with
the *same* natural key, which a primary key on that key rejects, leaving in-place mutation as the only
possible implementation — the exact thing the overlay exists to prevent.

The mechanism was cited by name without adopting its **shape**. `identity_claim` is keyed on a surrogate
`bigint GENERATED ALWAYS AS IDENTITY`, with uniqueness enforced by a *partial* index over live rows only,
precisely because `db/001` shipped a full-coverage unique index and `db/005` had to repair it after
superseded values became permanently un-re-assertable. The surrogate is also what makes db/005's
"strictly forward" rule (`superseded_by > identity_claim_id`) expressible at all; with a natural-key PK
there is no ordering column, so the invariant §5.4 claimed to inherit could not have been checked.

Resolved: §5.0 states the row shape once and every assertion table adopts it. Two consequences worth
naming — `interaction_group` splits into an immortal identity table plus a supersedable assertion table
(the `substance_moiety` / `identity_claim` split, for the same reason), and `interaction_group_member`
gains the overlay columns it lacked entirely, having been the one table where mutation-in-place would have
silently rewritten the part that decides whether a group fires.

**The generalisable rule: citing a prior mechanism obliges adopting its constraints, not just its name.**
The cheapest check is to open the migration and compare column lists — which is what §12-H and §12-I say
about *data*, applied here to *schema*.

**K. Curator intent was placed on a rebuildable projection.** §7.2 put `state` (including `withdrawn`) on
`open_question`, the table it simultaneously described as re-derived from the gap views on every ingest. A
`withdrawn` flag on a rebuilt table is erased by the next rebuild, so tension F's noise-suppression answer
would have quietly stopped working — and, worse, would have *passed* every test written against a fresh
database, failing only on the second ingest of a long-running instance.

Resolved: `state` moves to an append-only `question_state` keyed by the deterministic `question_uuid`,
with absence meaning `open`. The immortal identity §7.2 built for external notifiers turns out to be
exactly what lets curator state live beside a rebuildable projection without being owned by it.

**The generalisable rule: in a hybrid store, every column belongs to either the rebuildable half or the
append-only half, and the test that distinguishes them is "would a rebuild destroy this?"** Asked of each
column in §5 and §7, it is also what surfaced tension J.

## 13. Explicitly out of scope

- **Dose/exposure-weighted contribution** — strength is slice 4+; weighting without it would be false
  precision (tension E).
- **Notification/messaging transport** into the question registry — an API-slice concern. This design fixes
  only the addressable identity (§7.2) that makes it possible without a later migration.
- **The HTTP API** (slice 6) and any **auto-firing prescriber alert** (§9).
- **Drug–disease contraindications and indications** (`CI_with`, `may_treat` …) — slice 5b.
- **Retiring `class_contraindication` / `ddi_candidate_pair`.** The pairwise projection stays exactly as it
  is; this design sits beside it. Nothing built on the existing contract changes.
- **Automated evidence appraisal.** Whether a reference supports a grade is a judgement this schema
  *records* (§7.3) and does not make.
