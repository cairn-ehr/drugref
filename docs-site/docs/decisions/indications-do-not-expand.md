# An indication does not expand down the disease tree

**Status:** Active
**Last reviewed:** 2026-07-31
**Applies to:** Slice 5b.2 — MeSH-keyed indications (`may_treat`, `may_prevent`, `may_diagnose`) and `induces`
**Full derivation:** the [slice-5b.2 design spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-07-30-drugref-slice-5b2-mesh-indication-design.md) (§3.2, §3.3, §5.2–§5.4) and `db/019`

## Context

drugref already expands one kind of drug–condition rule **down** the MeSH disease DAG.
`db/014` gives the argument: a patient coded *Temporal Lobe Epilepsy* **is** a patient
with epilepsy, so a contraindication written against *Epilepsy* holds for them. The walk
is **subsumption on the patient's state**, and for a contraindication *fewer* rows is the
harm direction — a rule that fails to fire is the dangerous outcome.

Slice 5b.2 adds MED-RT's therapeutic assertions over that same registry. The obvious move
is to reuse the same walk. **It is unsound, and the release shows exactly how badly.**

Applied to an indication, walking down the DAG does not narrow a patient's state — it
**distributes a therapeutic claim over the object's subclasses**:

Measured on the rows drugref actually **stores**, over slice 5b.2's 5,963-condition
registry:

| predicate | rows drugref stores | if expanded down the DAG | multiplier |
|---|---:|---:|---:|
| `may_treat` | 12,662 | 177,953 | **14.1×** |
| `may_prevent` | 1,888 | 88,897 | **47.1×** |
| `may_diagnose` | 124 | 9,493 | **76.6×** |
| `induces` | 154 | 930 | 6.0× |

The design spec's version of this table gave 13.0× / 40.9× / 75.0× over 15,302 / 2,668 /
155 — a **different population** twice over, and both differences are worth naming because
they recur throughout this record. Those counts are distinct `(drug, MeSH record)` pairs
**before** the moiety gate; the release itself asserts **15,319** `may_treat`, **2,670**
`may_prevent`, **155** `may_diagnose` and **170** `induces`, and drugref stores only the
rules whose subject a moiety carries. The multipliers were also computed over slice 5b's
narrower 5,203-condition registry, which this slice widened (§3.6). The argument is
unaffected and slightly stronger.

The worked cases are the argument, not the multipliers. **One** `may_treat` rule on
*Neoplasms* (`D009369`, 708 descendants in this registry) would manufacture 708 therapeutic
claims — "treats Adenocarcinoma", "treats Astrocytoma", "treats Basal Cell Carcinoma".
*Infections* (`D007239`) would manufacture 812; *Cardiovascular Diseases* 478. **MED-RT
asserted none of them.**

So for an indication, **more rows is the harm direction** — the exact inverse of the
premise drugref's other expansion rests on. A deny-list of "roots too abstract to expand"
would not fix it, because the unsoundness is in the walk itself, not in which roots it
starts from.

## Decision

**Nothing derived is ever stored, and the DAG is walked in the other direction at read
time, labelled.**

1. **Store only what the release asserts.** `moiety_condition_indication` and
   `moiety_induced_condition` hold assertions and nothing else. There is deliberately
   **no `condition_indication_expanded` view** to mirror the contraindication side's:
   nothing is stored expanded, so the base table *is* whole-set access, and a second walk
   would only create a quantity that could disagree with the first.

2. **Generalise UP, never down.** `drugref.indications_for_condition(condition)` walks
   from the patient's condition to its **ancestors**. "Phenytoin is indicated for
   *Epilepsy*, a more general form of this diagnosis" is a **weaker** statement than the
   release makes, and it is true; the downward claim is stronger, and false.

3. **A derived row is labelled as one.** `is_direct = false` means a *weaker* claim, not
   a wider one — that is the whole safety contract of this slice. A consumer **must**
   render such a row as *"indicated for &lt;ancestor&gt;, a more general form of this
   diagnosis"* and never as an indication for the coded diagnosis. `object_condition` is
   returned as a column for exactly that purpose.

4. **The vocabulary column is named for what it licenses.** `condition_indication_axis`
   carries `generalises_to_descendants`, deliberately **not** `expands_descendants`. The
   contraindication axis's flag says *the rule fires for the descendant*; this one says
   *a rule on an ancestor may be **offered** for this condition, labelled*. Same graph,
   different claim — and naming them alike would invite a future reader to unify two
   things this record says must not be unified. It has **no DEFAULT**: a predicate added
   later must state its own answer.

5. **`induces` licenses no walk at all.** It has no row in the axis table and its own
   relation. What a drug *causes* in a general state is not a claim about that state's
   subtypes, and 170 rows do not need a mechanism. It also gets its own table rather than
   a `relationship` filter on a shared one, because the unfiltered read of a table must
   be one true sentence: a consumer who forgot the filter would read *"carbamazepine
   treats agranulocytosis"* off an `induces` row.

## Consequences

**The value is in the DAG, read the other way.** Measured on the real releases (UNII
26Feb2026 → MED-RT 2026.07.06 → MeSH desc/supp/pa 2026), of the **5,963** conditions the
registry holds:

- **1,305** carry a direct therapeutic indication;
- **3,719** carry none, but have an **ancestor** that does — these are patients coded at
  a finer granularity than MED-RT works at, and they get **nothing** from the stored rows
  alone;
- **939** have no indication at or above them.

So the read path is not a nicety: it is what makes 3,719 of 5,963 conditions answerable
at all.

**What drugref ships:** **14,674** therapeutic indication rows (`may_treat` 12,662,
`may_prevent` 1,888, `may_diagnose` 124) over 3,632 moieties and 1,305 conditions, plus
**154** drug-induced-state rows over 108 moieties and 49 conditions. These are **candidate
tier** — MED-RT does not track label updates, so rows feed review and must never
auto-alert — and **an indication is not a recommendation**: MED-RT asserts that a drug
*may* treat a condition, never that it is appropriate for a given patient, first-line,
correctly dosed, or safe in combination, and it asserts no ordering among the drugs that
treat one condition.

**One quantity, stated once.** `condition_indication_reach` is the single statement of
"what reaches this condition", and `gap_condition_without_indication` is a **filter on one
of its columns** (`= 0`) rather than a second walk — so the partition is true by
construction. The function and the view are pinned against each other by test *and*
re-checked on the real release: **5,963 conditions checked, 276,343 rows, zero
disagreements in either direction.**

**Cost:** a consumer who wants the strong claim ("this drug treats exactly this
diagnosis") gets fewer rows than a naive expansion would give them, and must handle the
`is_direct` flag to use the rest. That is the intended trade — a manufactured therapeutic
claim is worse than an absent one.

## Two ways a claim still gets widened, and both are counted

Walking up rather than down removes the *derived* widening. It does not remove every
widening, and this record would be dishonest if it stopped at the walk. Two remain. Both
are properties of the **stored** rows, both are measured on every run, and neither is
resolvable from public-domain sources — they are slice 5c's curated work.

### 1. A drug can be both indicated **and** contraindicated for one condition

**168 `(drug, condition)` pairs** are in `moiety_condition_indication` **and**
`moiety_condition_contraindication` at once on the 2026.07.06 release — from 175
indication rows (7 pairs carry two therapeutic predicates), over 154 moieties and 40
conditions. Split by predicate: `may_treat`/`CI_with` 140, `may_prevent`/`CI_with` 32,
`may_diagnose`/`CI_with` 3.

**Worked case.** MED-RT asserts `may_treat` **and** `CI_with` for carvedilol against
`D006333` *Heart Failure*. So do atenolol, bisoprolol, metoprolol, propranolol, timolol,
nadolol, labetalol and esmolol. Both assertions are correct clinically and they are not in
conflict: beta-blockers are first-line disease-modifying therapy in **stable chronic
HFrEF** and are contraindicated in **acute decompensated** failure. MeSH has one descriptor
for both states, so the distinction has nowhere to live, and MED-RT states each flatly with
no severity, no qualifier and no line of therapy. The same shape covers alteplase for
*Stroke* (ischaemic vs haemorrhagic), budesonide and flunisolide for *Asthma*, activated
charcoal and pralidoxime for *Poisoning*, and carboplatin for *Bone Marrow Diseases*.

**These are the hardest rows in the release, not noise.** A consumer calling
`indications_for_condition()` and `contraindications_for_condition()` for one patient gets
"carvedilol is indicated" and "carvedilol is contraindicated" with equal confidence. The
pair **must not be read as a contradiction to be resolved automatically** — discarding
either side loses a true statement. And because the two read paths walk in **opposite**
directions, the collision multiplies below the object rather than staying at 168.

drugref's answer today is to **count it and say so**:
`MeshRelSummary.also_contraindicated_pairs` reports it every run, both tables'
`COMMENT ON` state it, and a test pins the counter against a direct query so it cannot
quietly stop counting. Whether a consumer should be told through an eighth gap kind or a
read-path flag is [#51](https://github.com/cairn-ehr/drugref/issues/51).

### 2. 422 assertions are stored against a **broader** condition than the release named

MED-RT names a MeSH **ConceptUI**; drugref keys a condition on the **record** that owns it,
because many concepts resolve to one record and keying on the concept would split one
clinical condition into rows no rebuild could merge. When the named concept is the record's
*preferred* one, nothing is lost. When it is **subordinate**, the concept can be *narrower*
than the record — and the assertion is stored against something broader than the release
said.

Measured: **422 of 18,314 assertions (2.30%)** — `may_treat` 340, `may_prevent` 80,
`induces` 2 — arrive through **90 non-preferred ConceptUIs** collapsing onto **85 broader
records**, over 102 distinct `(predicate, concept, record)` triples.

**This is the other way a claim gets widened, and it runs in the unsafe direction.** The
walk-direction decision above is about rows drugref *derives*; this is about rows drugref
*stores*, and the `is_direct = false` label cannot help because these rows are
`is_direct = true`. Note that the same collapse hits the contraindication half harder —
**550 of 13,463** assertions via 81 concepts — and there it is **safe**: broadening a
contraindication widens recall, which is the direction `db/014` wants. Slice 5b.2 is where
the harm direction flips.

**Worked case.** MED-RT asserts `may_treat` against `M0335931` **"Seizures, Focal"** — a
non-preferred concept — for eslicarbazepine acetate (RxCUI 1482501) and eslicarbazepine
(1482502). drugref stores `may_treat` on `D012640` **"Seizures"**. Eslicarbazepine is a
sodium-channel blocker licensed for focal-onset seizures which, like carbamazepine and
phenytoin, can **aggravate** generalised myoclonic and absence seizures. A patient coded at
`D012640`, or anywhere below it, now receives it as a therapeutic option. The release never
said that.

Most of the 102 triples are benign synonymy — `M0002909` "Breast Cancer" → *Breast
Neoplasms* is not a loss. A clear minority is genuine narrowing collapsed upward:

| MED-RT names | drugref stores |
|---|---|
| Cardiomyopathy, Hypertrophic Obstructive | Cardiomyopathy, Hypertrophic |
| Myasthenia Gravis, Generalized | Myasthenia Gravis |
| Multiple Sclerosis, Secondary Progressive | MS, Chronic Progressive |
| Lymphoma, Low-Grade | Lymphoma, Non-Hodgkin |
| Sarcoma, Epithelioid | Sarcoma |
| Cardiac Death (`may_prevent`) | Death |
| Vertigo, Peripheral | Vertigo |

drugref stores all 422 rather than withholding them — dropping every broadened assertion
would lose far more than it saves, and nothing on the row distinguishes the synonymy from
the narrowing. So the count is the remedy for now:
`MeshRelSummary.indications.broadened_object_assertions` reports it every run, and it is
the production reader `MeshRecord.is_preferred_concept`'s own docstring always promised.
Putting the named `concept_ui` on the row, so a consumer can detect **which** rows were
widened, is [#52](https://github.com/cairn-ehr/drugref/issues/52).

## Erratum — three spec figures were computed **before the moiety gate**

The design spec predicted several figures that the end-to-end run then contradicted. **In
every case the code is right and the spec's figure answers a different question**: the
spec measured over the objects the *release* names, while the database only stores a rule
whose **subject some moiety carries**. 1,426 indication subject RxCUIs match no moiety, so
the stored population is smaller than the release's.

Per-slice specs under `docs/superpowers/specs/` are immutable once merged, which is why
these corrections live here.

**1. `gap_condition_without_indication` returns 97 rows, not 66.** The worklist is **80**
conditions carrying a C (Diseases) or F (Psychiatry) tree number plus **17** tree-less
`SCRClass = 3` rare diseases. The spec's 66 (55 + 11) is what the view returns if every
therapeutic assertion in the release is treated as reaching its object, gate or no gate —
re-measured that way it gives 64, within two rows of the spec's figure. The gate is part
of the pipeline, so **97 is the published truth**, and the same correction applies to
§3.3's coverage split (spec 1,453 / 3,655 / 855; measured **1,305 / 3,719 / 939**).

**2. `condition_subtree` is 11,512 → 11,605, not 12,311 → 12,415.** Both pairs are right
about different populations, and the difference is again the gate: `condition_subtree` is
scoped to the conditions that **stored** contraindication rows name — **641** roots —
while the spec measured the walk over the **677** `CI_with` object records the release
*references*. Re-measured over 677 roots, the pre-slice and post-slice registries give
exactly **12,311** and **12,415**. Over the 641 roots the view actually walks, the same
widening gives **11,512 → 11,605 (+93)**.

**3. `condition_contraindication_expanded` grows 191,728 → 192,161 (+433, +0.226%)**, not
the ≈192,500 / +0.39% the spec predicted — the same 677-vs-641 discrepancy, and a
*smaller* move than predicted, which is the safe direction the spec's own criterion asks
for.

**What the erratum does not touch is the finding itself.** §3.6's claim — that a shared
registry **completes DAG edges** and so legitimately grows the contraindication
expansion — is confirmed exactly, including its per-root detail. The CI root set is
**byte-identical** across the two runs (641 roots), **no** root's subtree shrank, and
**10** grew: *Nervous System Diseases* **+59** and *Neuromuscular Diseases* **+10**, both
precisely as the spec named them, then *Infections* +8, *Congenital Abnormalities* +6,
*Wounds and Injuries* +3, and five more by one or two.

The mechanism is worth restating, because it recurs every time the registry widens: a
condition bears **several tree numbers**, and an edge is written only when **both**
endpoints are registered. A condition already in the contraindication closure via one tree
number can have a second tree parent that was never registered — until the indication half
registers it. The edge then appears, and the condition becomes reachable from a
contraindication root it could not be reached from before. *Acute Pain* really is filed
under nervous-system disease in MeSH, and a contraindication on that root really should
reach it. **This is a completion, not a regression, and it runs in the safe direction.**

Every figure the release *asserts* is unchanged, which is the regression signal that
matters: `moiety_condition_contraindication` **9,471**, `moiety_contraindication`
**1,442**, `gap_unresolved_ci_object` **103 rows / 405 rules**, `ddi_candidate_pair`
**21,664**.

**The database catalog lags this record in one place, and cannot be made to catch up.**
`db/015`'s `COMMENT ON VIEW drugref.condition_subtree` describes the registry as "1,690 of
the registry's 5,203 conditions (5,190 descriptors + 13 tree-less SCRs)". Slice 5b.2 widened
that registry to **5,963** (5,929 descriptors + 34 SCRs), of which **2,149** have several
parents. `db/015` is **merged, and a merged migration is immutable** — its checksum is in
every applied ledger — so the figures a DBA sees from `\d+ drugref.condition_subtree` are
slice 5b's and will stay slice 5b's until some later migration has an independent reason to
re-issue that comment. Re-issuing it *only* to correct a comment would be a migration whose
whole content is documentation, which is a worse trade than one stale catalog string with a
correction recorded here. `db/019`'s own comments **were** corrected in place, because that
migration is on this branch and unmerged — immutability starts at merge (`HANDOVER.md`).

## Related

- [A structural chemical tree is not a clinical class](withheld-chemical-class-contraindications.md)
  — the other half of MED-RT's MeSH-keyed content, and the other decision about a walk
  drugref refuses to take.
- [The hybrid store](hybrid-store.md) — why a rebuildable projection is not the place for
  an inference drugref cannot defend.
- [Roadmap](../roadmap/index.md)
