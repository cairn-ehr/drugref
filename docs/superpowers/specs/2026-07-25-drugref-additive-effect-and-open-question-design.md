# Design — drugref global tier: additive-effect interactions, and the open-question registry

**Date:** 2026-07-25 · **Repo:** `github.com/cairn-ehr/drugref` · **Status:** design, pending
implementation plan. **Builds on:** the
[slice-5a contraindication design](2026-07-25-drugref-slice-5a-medrt-contraindication-design.md) (the
`class_contraindication` projection and `ddi_candidate_pair` this design sits beside), the
[slice-2a MED-RT design](2026-07-23-drugref-slice-2a-medrt-classification-design.md) (the `has_PE`
membership this design's whole leverage rests on) and the
[slice-1 moiety-spine design](2026-07-23-drugref-global-moiety-spine-design.md) (§5 own-immortal-UUID, and
the `superseded_by` correction overlay reused in §7).
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

**No new external source is introduced.** The contributor data is MED-RT `has_PE`/`has_MoA` membership,
licence-verified public-domain in the slice-2a gate. Everything added here is either derived from that or is
**drugref's own curated content**, authored in-project and therefore AGPL-3.0 by construction.

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
```

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
step 3a includes measuring it properly.

## 4. The model — accumulation primary, groups for exceptions

**Accumulation** covers the additive cases and gets its leverage from data already present: `has_PE`
membership answers "which drugs produce this effect" for 27,540 rows. The only thing missing is judgement,
and judgement is small enough to hand-curate.

**Groups** cover role-based combinations (the triple whammy) where the members play *different* parts and a
count is meaningless. Deliberately the minority mechanism.

Both are **curated**, so both live in the append-only signed overlay (5c's tier), not in a rebuildable
projection. Neither duplicates ingested data.

## 5. Schema

### 5.1 `additive_effect` — which effects accumulate, and when it matters

Expected cardinality: tens of rows, ever.

| column | notes |
|---|---|
| `effect_class_uuid` | PK → `substance_class(class_uuid)` |
| `threshold_major` `smallint` | minimum `major` contributors |
| `threshold_total` `smallint` | minimum contributors of any grade |
| `severity` `text` | `CHECK (severity IN ('contraindicated','major','moderate','minor'))` — deliberately the same four-level vocabulary a prescriber-facing consumer expects, and CHECK-constrained rather than free text so it cannot drift per curator |
| `clinical_note` `text` | what a prescriber needs told |
| `source`, `ingest_run`, `asserted_at`, `superseded_by` | overlay provenance (§5.4) |

Fires when `majors >= threshold_major AND contributors >= threshold_total`. Two smallints express the
realistic rules: "any two contributors" = `(0,2)`; "a major plus anything else" = `(1,2)`; "a major alone is
worth saying" = `(1,1)`. `CHECK (threshold_total >= threshold_major AND threshold_total >= 1)`.

### 5.2 `effect_contribution` — grade, not enumeration

**This table does not list contributors.** Membership already does. It only *promotes*:

> contributor set = members of `effect_class_uuid` (including DAG descendants, per §11 step 2);
> grade defaults to `minor`; a row here promotes a contributor class to `major`.

| column | notes |
|---|---|
| `effect_class_uuid` | → `substance_class` |
| `contributor_class_uuid` | → `substance_class` — a **class**, never a moiety |
| `magnitude` | `CHECK (magnitude IN ('major','minor'))` |
| PK | `(effect_class_uuid, contributor_class_uuid)` |

Keyed on **class** so a grade inherits to every member — the ROADMAP's "curate once, apply widely" lever
doing real work. Curating bleeding means marking the DOACs, VKAs and heparins major and leaving ~100 other
members at the default. A handful of rows, not a hundred.

*Why default-minor rather than default-excluded:* excluding uncurated members would discard the 27,540-row
leverage that makes this design worth building. Defaulting them to `minor` keeps the coverage while
`threshold_major >= 1` filters the noise.

### 5.3 `interaction_group` — the role-based exceptions

```
interaction_group(group_uuid PK, name, severity, clinical_note, source, ingest_run,
                  asserted_at, superseded_by)
interaction_group_member(group_uuid, role text, class_uuid, PK (group_uuid, role, class_uuid))
```

A group fires when the regimen covers **every distinct `role`** in its member set. The triple whammy is one
group with three roles (`NSAID`, `RAAS blocker`, `diuretic`), each role listing the classes that satisfy it.
No separate roles table: required roles are `SELECT DISTINCT role`, so a role cannot exist without a member
that satisfies it.

### 5.4 Correction semantics

All three tables are curated clinical assertions, so corrections **overlay** rather than mutate — the
`superseded_by` mechanism `db/005` hardened for `identity_claim` (set once, same subject, strictly forward),
reused unchanged. A superseded row is history, never deleted: what was believed, and when, stays answerable.

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

- extend the `db/003` `substance_class.source` CHECK with `'DRUGREF'`, and `ids._SOURCE_CANONICAL`
  correspondingly (the pair the migration comment already says to extend together);
- mint with the existing `ids.mint_class_uuid('DRUGREF', code)` — no new machinery;
- `source_code` is a drugref-assigned stable code (e.g. `NEPHROTOX`), so the UUID is deterministic and
  reproducible across instances exactly as MED-RT's are.

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
| `gap_unmatched_ingredient` | RxCUIs MED-RT classifies that no moiety carries (already counted as `unmatched_rxcuis`; made queryable) | ingested only |
| `gap_uncurated_additive_effect` | a PE class that **carries ≥1 CI rule or has ≥10 members in its subtree**, and has no `additive_effect` row — a pending *decision* | §5.1 table (may be empty) |
| `gap_ungraded_contribution` | members of a curated additive effect sitting at default `minor` — the promote-to-major review queue | §5.1 + §5.2 populated |

The first three depend on nothing but ingested data, which is what lets §11 step 1 ship before any curation
exists. `gap_uncurated_additive_effect` needs `additive_effect` to exist but not to be populated (it returns
*everything* when the table is empty, which is the correct initial answer). `gap_ungraded_contribution` is
only meaningful once curation has begun, so it lands with §11 step 4.

The `≥1 CI rule or ≥10 subtree members` criterion is a deliberately crude first filter, chosen to make the
initial worklist finite and reviewable rather than to be clinically precise; it is a view definition and
therefore cheap to retune once a curator has seen its output.

### 7.2 `open_question` — durable, addressable, and never closed by absence

Each gap row derives a question with **immortal deterministic identity**:

```
QUESTION_NAMESPACE = uuid5(_DRUGREF_ROOT, "question")     # beside MOIETY_/CLASS_NAMESPACE
question_uuid      = uuid5(QUESTION_NAMESPACE, f"{gap_kind}:{gap_key}")
```

The same trick as `class_uuid`: re-derivation on every ingest yields the same UUID, so the *derived* half is
a rebuildable projection while the *evidence* half is append-only. No new architecture — drugref's existing
hybrid store applied to a third kind of thing.

**`gap_key` must be pinned per `gap_kind`, because the UUID derives from it** and an external notifier will
hold references to it. It is the natural key of the thing the question is *about*, stringified — never a row
id, never anything ordering-dependent:

| `gap_kind` | `gap_key` |
|---|---|
| `unpopulated_contraindication` | the effect class's `class_uuid` |
| `uncurated_additive_effect` | the effect class's `class_uuid` |
| `ungraded_contribution` | `{effect_class_uuid}/{contributor_class_uuid}` |
| `unclassified_moiety` | the `moiety_uuid` |
| `unmatched_ingredient` | `RXNORM_IN:{rxcui}` |

Class and moiety UUIDs are themselves immortal, so a question's identity is as stable as its subject. A
pinned-literal test guards the derivation (§10).

| column | notes |
|---|---|
| `question_uuid` | PK, deterministic (above) |
| `gap_kind`, `gap_key` | what derived it |
| `question_text` | the literature-searchable statement |
| `search_expression` | what was asked, so re-asking is reproducible |
| `state` | `open` \| `evidence_under_review` \| `answered` \| `withdrawn` |

### 7.2.1 `question_source_check` — the watermark is per SOURCE TIER, not just literature

A single `evaluated_through` date was the first design here, and it was wrong: it assumes literature is the
only place an answer can come from. §3.5 disproves that — the answer to six of the dead rules was sitting in
an openFDA label the whole time. A question therefore needs to record **which tier has been consulted, at
what version, with what outcome**:

```
question_source_check(question_uuid, source, checked_at, source_version, outcome, note)
  source  : 'MED-RT' | 'openFDA-SPL' | 'MeDIC' | 'Wikidata' | 'FAERS' | 'literature'
  outcome : 'covered' | 'not_covered' | 'partial' | 'error'
  PK (question_uuid, source, source_version)
```

`source_version` is the release/label version checked, so a re-check against a *newer* version is a new row
rather than an overwrite — the same append-only discipline as the evidence table, and what makes "has this
been looked at since the January labels?" answerable.

**This is what makes the cost ladder enforceable rather than aspirational.** A question with no
`openFDA-SPL` row has not earned literature-mining effort yet, and the worklist views should order by
cheapest-unchecked-tier so the free sources are always exhausted first.

| tier | cost | licence | why this order |
|---|---|---|---|
| MED-RT (all files, all predicates) | free, on disk | public domain | §12-H — already paid for |
| openFDA SPL | free, public API | public domain / CC0 | **the source MED-RT is derived from** (§3.5) |
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
question_evidence(question_uuid, reference, verdict, confidence,
                  asserted_at, superseded_by, ingest_run)
```

A later finding may supersede an earlier one; nothing is deleted. Medicine revises, and the schema must let
it revise without destroying the record of what was believed before. Same mechanism as §5.4.

**Why deterministic UUIDs matter beyond tidiness:** an external tool cannot notify drugref about "that
renal vasoconstriction thing" — it needs a stable key. Building the identity now, before anything notifies
it, is far cheaper than retrofitting it onto questions already cited elsewhere. The transport itself
(messaging, polling, a human pasting a DOI) is deliberately out of scope (§13).

## 8. Output contract — facts and thresholds, not verdicts

drugref publishes what it knows and the thresholds it judges significant; the **consumer** intersects that
with a patient's regimen. Two views are the contract of record:

- `additive_effect_contributor(effect_class_uuid, moiety_uuid, magnitude)` — the flattened fact table,
  effect → members (with descendants) → grade.
- `interaction_group_member_moiety(group_uuid, role, moiety_uuid)`.

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

## 10. Testing (TDD, failing-test-first)

- **Pure functions first**: threshold evaluation (`majors`, `total` → fires?) is pure and gets a table-driven
  unit test with no database.
- **Accumulation acceptance matrix** (DB-gated): a curated effect with three members at mixed grades;
  assert firing at each `(threshold_major, threshold_total)`; assert default-minor for uncurated members;
  assert descendant contributors are included; assert a superseded `additive_effect` row stops firing.
- **Group semantics**: fires only when all distinct roles are covered; two drugs satisfying the *same* role
  do **not** fire it.
- **Question determinism**: a pinned `question_uuid` literal, guarding the derivation the way
  `test_class_registry_source_neutral.py` pins class UUIDs — an external notifier depends on it.
- **Watermark semantics**: a question with `state='open'` and a stale `evaluated_through` still appears in
  the worklist; a `withdrawn` one does not.
- **`DRUGREF`-minted classes** coexist with MED-RT and MeSH, and a per-source rebuild of either leaves
  drugref-authored classes untouched.

## 11. Sequencing and dependencies

1. **The question registry and gap views** (§7). No curation required, ships immediately, converts the
   foundation review's findings into standing infrastructure, and is the thing that produces value first.
   Depends on nothing beyond current `main` + PR #18.
2. **#15 descendant expansion, with a named deny-list** of the ~14 abstract PE organ-system roots (§3.2) —
   *not* a subtree-size threshold. Contributor sets in §5.2 are wrong without this, and it changes what
   several gap views return, so it precedes the curated tables.
3. **Slice 5b (MeSH disease descriptors), where it overlaps a gap** — moved ahead of DRUGREF minting by the
   §3.4 audit. `induces` / `may_treat` / `CI_with` all resolve once MeSH diseases are ingested, and `induces`
   already covers part of the nephrotoxicity gap this design would otherwise hand-curate. Curating before 5b
   risks paying for what the release supplies. The accessory crosswalk resolves 50.8% of the M-codes, which
   shrinks 5b's unknown but does not remove it (no tree numbers, 49% unresolved).
3a. **Extract from openFDA SPL, before any curation** (§3.5). MED-RT is derived from these labels, so a
   MED-RT gap should be checked against the label first. Two things belong here: **measure** the yield
   properly (does openFDA resolve the 41 dead rules and the 13 empty classes? — §3.5 is a 3-drug probe, not
   a measurement), and if it does, ingest the extraction as a **projection** with `source = 'openFDA-SPL'`,
   attributed in `NOTICE`. Public domain, so the licence gate is clear; extraction quality is the real risk
   and is why this lands as a candidate-tier projection reviewed via §7, not as fact.
3b. **MeDIC** — CC0 drug–disease indications/contraindications. Overlaps `may_treat`/`CI_with`, so import
   after 5b to make the overlap measurable rather than duplicated.
4. **`source = 'DRUGREF'` minting** (§6) — one migration, small, and scoped to what 5b, 3a and 3b did *not*
   supply. **This is now expected to be a much smaller set than first designed.**
5. **The curated tables** (§5) with an empty curation set, plus the read views (§8).
6. **Literature-backed curation**, driven by the §7 worklist, landing as `question_evidence` plus curated
   grades.

**Recommended decomposition — this spec is too large for one implementation plan.** Three plans:

- **Plan A — the open-question registry** (step 1): the three ingested-only gap views, `open_question`,
  `question_evidence`, deterministic UUID minting. Self-contained, ships value immediately, and needs none
  of the model below. *Start here.*
- **Plan B — descendant expansion** (step 2): closes #15 with the named deny-list. Independently useful —
  it improves `ddi_candidate_pair` whether or not the accumulation model is ever built.
- **Plan C — the accumulation model** (steps 4–5): `DRUGREF` minting, the three curated tables, the read
  views, and the two remaining gap views. **Gated on slice 5b** (step 3) for any effect 5b might supply —
  see §12-H.

Step 6 is continuous curation work, not a plan. Slice 5b keeps its own separate spec. Each of A/B/C gets its
own spec-to-plan cycle if it grows beyond what this document already settles.

**A precondition on Plan C, learned the hard way (§12-H): before curating any gap, audit every file and
every predicate in the relevant release for content that already covers it.** Plan A's worklist is the
mechanism — it carries the `skipped_predicates` inventory so the question "did we already have this?" is
asked automatically rather than remembered.

## 12. Design tensions recorded

**A. Default-minor vs default-excluded contributors** (§5.2). Resolved to default-minor: excluding
uncurated members throws away the ingested-membership leverage that motivates the whole design. Accepted
cost: an uncurated effect with `threshold_major = 0` would fire on weak contributors, so `threshold_major
>= 1` is the recommended default when curating a new effect.

**B. Subtree-size threshold vs named deny-list** for descendant expansion. Resolved to the deny-list. Size
worked for coagulation (6 descendants) and CNS depression (4) by topological luck; it encodes no clinical
distinction and would silently change meaning when MED-RT reshapes its hierarchy. A named list of abstract
roots states what is actually meant and fails visibly.

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
(210 `RxNorm→MED-RT`, 38 `MED-RT→MED-RT`) ingestible today, contradicting the HANDOVER note that `has_SC`
"points into MeSH"; and the accessory `NDFRT-NUI_MeSH-CUI` crosswalk resolves **5,030 of 9,908 (50.8%)** of
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
