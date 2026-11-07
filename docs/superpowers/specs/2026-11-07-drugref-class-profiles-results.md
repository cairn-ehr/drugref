# Class profiles — measured results

**Run:** 2026-11-07 · **Database:** `drugref_profiles` (built from nothing at this branch, schema to `db/061`,
then migrated to `db/062` by the final review's fix wave, later to `db/064` and `db/065`) ·
**Design:** [`2026-11-07-drugref-class-profiles-design.md`](2026-11-07-drugref-class-profiles-design.md) ·
**Status:** measured; three findings filed (#202, #203, #204); `db/062` re-measured §2, §4 and §6 (the
read-cost figures; every row count is unchanged); `db/065` re-measured them again (§6 addendum); clinician read of §5 pending. **Re-run the cost figures with
[`tools/class_profile_census.sql`](../../../tools/class_profile_census.sql)** (its header carries the two
non-psql timings).

A class property absent from a profile, and every **not confirmed** below, means *no record for this drug* —
never *safe*, never *does not apply*.

## 1. The build

DSN `host=localhost port=5532 dbname=drugref_profiles user=postgres`. Wall times as run.

| step | command | wall |
|---|---|---|
| create | `createdb drugref_profiles` | 0 s |
| migrate | `drugref migrate` | 0 s |
| chain | `drugref ingest chain --downloads downloads --unii-release 26Feb2026 --medrt-release 2026.07.06 --mesh-release 2026 --mesh-relations-release 2026.07.06 --gsrs-release 2026-02-26` | 143 s (documented baseline ~137 s) |
| ONC | `drugref ingest onchigh --release ONCHigh-2015` | 8 s |
| FDA-CYP | `drugref ingest fda-cyp --page downloads/FDA/fda_cyp_2026-05-29.html` | 8 s |
| DrugCentral | `drugref ingest drugcentral --dump downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz --release 11012023` | 24 s |

Every ingest ran `register_from_gaps`, including the new kind. SPL was not ingested: it is not a property
source (spec §3, db/059 reads no `spl_ddi_pair`). `ingest_run` durations: UNII 9.7 s, MED-RT 10.1 s, MeSH
46.2 s, MED-RT (relations) 59.9 s, GSRS 16.3 s, ONCHIGH 7.8 s, FDA-CYP 7.9 s, DRUGCENTRAL 23.4 s.
`curated_property_ruling` is empty (0 rows), so `member_ruling_oriented` and `member_property_negation` are 0
and no member is a confirmed exception on this database.

## 2. View sizes and full-scan cost

`SELECT count(*)` per view, `psql \timing`, warm cache. The four views `db/062` changed (or that sit on
`class_property`) were re-measured after it; the db/061 time is kept beside them.

| view | rows | `count(*)` (db/062; db/061) |
|---|---:|---:|
| `class_profile_member` | 41,099 | 20 ms |
| `member_property_record` | 42,828 | 19 ms |
| `member_property` | 42,072 | 24 ms |
| `class_asserted_property` | 641 | 2 ms |
| `class_property` | 9,664 | 328 ms (3,804 ms) |
| `class_property_member` | 68,666 | 338 ms (3,931 ms) |
| `class_member_peculiarity` | 424,055 | 905 ms (4,268 ms) |
| `class_subclass_only_member` | 79,379 | 96 ms |
| `gap_unconfirmed_class_property` | 2,394 | 452 ms (3,882 ms) |

`class_asserted_property` by source: MED-RT 633, ONCHIGH 6, both 2 — all `interacts_with moiety`.
`class_pair_contraindication` is empty on this build, so no class × class assertion exists.

## 3. Classes and properties, per axis and per kind

Classes per axis (`substance_class`); *populated* = ≥ 1 direct `class_profile_member`; the observed route needs
≥ 2 members.

| source | concept_type | classes | populated | ≥ 2 members | ≥ 1 observed | ≥ 1 asserted | any property |
|---|---|---:|---:|---:|---:|---:|---:|
| FDA-CYP | PK | 65 | 60 | 47 | 27 | 0 | 27 |
| MED-RT | APC | 44 | 0 | 0 | 0 | 0 | 0 |
| MED-RT | EPC | 811 | 539 | 260 | 209 | 2 | 210 |
| MED-RT | MoA | 781 | 539 | 407 | 254 | 101 | 306 |
| MED-RT | PE | 1,873 | 582 | 493 | 378 | 774 | 948 |
| MED-RT | PK | 59 | (excluded) | — | 0 | 0 | 0 |
| MED-RT | TC | 66 | 63 | 63 | 38 | 0 | 38 |
| MeSH | PA | 568 | 501 | 454 | 69 | 0 | 69 |
| **total** | | 4,267 | 2,284 | 1,724 | 975 | 877 | 1,598 |

`class_property` rows (9,664): observed only 4,307, asserted only 5,347, both 10. By axis and kind:

| source | concept_type | relationship | object_kind | rows | observed only | asserted only | both |
|---|---|---|---|---:|---:|---:|---:|
| FDA-CYP | PK | CI_with | condition | 23 | 23 | 0 | 0 |
| FDA-CYP | PK | interacts_with | moiety | 69 | 69 | 0 | 0 |
| MED-RT | EPC | CI_with | condition | 266 | 266 | 0 | 0 |
| MED-RT | EPC | induces | condition | 1 | 1 | 0 | 0 |
| MED-RT | EPC | interacts_with | class | 9 | 9 | 0 | 0 |
| MED-RT | EPC | interacts_with | moiety | 758 | 754 | 3 | 1 |
| MED-RT | EPC | may_diagnose | condition | 1 | 1 | 0 | 0 |
| MED-RT | EPC | may_prevent | condition | 71 | 71 | 0 | 0 |
| MED-RT | EPC | may_treat | condition | 272 | 272 | 0 | 0 |
| MED-RT | MoA | CI_with | condition | 391 | 391 | 0 | 0 |
| MED-RT | MoA | induces | condition | 3 | 3 | 0 | 0 |
| MED-RT | MoA | interacts_with | class | 16 | 16 | 0 | 0 |
| MED-RT | MoA | interacts_with | moiety | 1,177 | 126 | 1,042 | 9 |
| MED-RT | MoA | may_prevent | condition | 56 | 56 | 0 | 0 |
| MED-RT | MoA | may_treat | condition | 257 | 257 | 0 | 0 |
| MED-RT | PE | CI_with | condition | 786 | 786 | 0 | 0 |
| MED-RT | PE | induces | condition | 6 | 6 | 0 | 0 |
| MED-RT | PE | interacts_with | class | 18 | 18 | 0 | 0 |
| MED-RT | PE | interacts_with | moiety | 4,464 | 162 | 4,302 | 0 |
| MED-RT | PE | may_diagnose | condition | 4 | 4 | 0 | 0 |
| MED-RT | PE | may_prevent | condition | 99 | 99 | 0 | 0 |
| MED-RT | PE | may_treat | condition | 571 | 571 | 0 | 0 |
| MED-RT | TC | CI_with | condition | 44 | 44 | 0 | 0 |
| MED-RT | TC | interacts_with | class | 1 | 1 | 0 | 0 |
| MED-RT | TC | may_prevent | condition | 3 | 3 | 0 | 0 |
| MED-RT | TC | may_treat | condition | 27 | 27 | 0 | 0 |
| MeSH | PA | CI_with | condition | 95 | 95 | 0 | 0 |
| MeSH | PA | induces | condition | 1 | 1 | 0 | 0 |
| MeSH | PA | interacts_with | class | 5 | 5 | 0 | 0 |
| MeSH | PA | interacts_with | moiety | 88 | 88 | 0 | 0 |
| MeSH | PA | may_prevent | condition | 12 | 12 | 0 | 0 |
| MeSH | PA | may_treat | condition | 70 | 70 | 0 | 0 |

Distinct classes per property kind (observed / asserted): CI_with condition 796 / 0; induces condition 10 / 0;
interacts_with class 47 / 0; interacts_with moiety 159 / 877; may_diagnose condition 5 / 0; may_prevent
condition 174 / 0; may_treat condition 559 / 0. Asserted properties are interactions only: no source states a
condition about a class, and the curated route (`curated_property_ruling`) is empty.

**`class_property_member` standings** (68,666): carries 29,172 · inherits 36,087 · not_confirmed 3,407 ·
confirmed_exception 0. Per axis: FDA-CYP PK 374 / 0 / 79; MED-RT EPC 5,758 / 6 / 547; MoA 7,651 / 15,207 / 797;
PE 11,954 / 20,874 / 1,336; TC 1,766 / 0 / 376; MeSH PA 1,669 / 0 / 272 (carries / inherits / not_confirmed).

**Structural checks**, all 0 on this database: MED-RT PK classes with a non-PK descendant in `class_subtree`;
MED-RT PK classes in `class_asserted_property`, in `class_property`, or in any `asserted_via`; observed
properties on a class with < 2 members. So `db/060`'s `asserted_by`, which does not apply the PK filter to
ancestors, is latent only — it cannot produce a PK source here.

## 4. The worklist — `unconfirmed_class_property`

**2,394** `gap_unconfirmed_class_property` rows = **2,394** current `open_question` rows of the kind, over
**1,247** drugs (median 1 per drug, max 37 — tacrolimus anhydrous). 1,837 are raised by one class, 315 by two,
242 by three to nine.

| relationship | object_kind | questions |
|---|---|---:|
| CI_with | condition | 1,103 |
| induces | condition | 5 |
| interacts_with | class | 36 |
| interacts_with | moiety | 507 |
| may_diagnose | condition | 1 |
| may_prevent | condition | 97 |
| may_treat | condition | 645 |

By raising class axis (a question raised on two axes counts on each): FDA-CYP PK 73 (19 classes); MED-RT EPC
547 (85); MoA 665 (120); PE 1,049 (204); TC 367 (33); MeSH PA 256 (45).

Largest single objects: `CI_with Drug Hypersensitivity` **711**, `may_treat Hypertension` 83, `CI_with
Pregnancy` 57, `may_treat Inflammation` 35, `interacts_with Monoamine Oxidase Inhibitors [MoA]` 34. Largest
raising classes: Decreased Blood Pressure [PE] 55, Unknown Cellular or Molecular Interaction [MoA] 54, Serotonin
Reuptake Inhibitor [EPC] 46, Protease Inhibitor [EPC] 42.

**`register_from_gaps` with the new kind**, timed in a rolled-back transaction on this database: at `db/062`
**5.37 s** whole (4.52 s on a second pass; 7.43 / 7.75 s at db/061); the new kind's SELECT alone (key and text,
as registered) is **0.43–0.49 s** (3.76 s at db/061, then the largest of the nineteen; the largest is now
`uncurated_interaction_rule`, 2.86 s, issue 75). Still 2,394 current questions. Since `db/062` every question's
text ends *"Answering does not change prescribing alerts."* (#204). For scale, the same run's other large kinds:
`unclassified_moiety` 16,089, `unruled_composition_activity` 2,245, `unmatched_ingredient` 2,150 rows.

## 5. Spot checks — for the owner's clinical read

MED-RT EPC classes chosen (`class_name ILIKE`): **beta-Adrenergic Blocker [EPC]** `b0723faa-540c-5d7a-870f-4466c1b351e5`
(15 members), **Angiotensin Converting Enzyme Inhibitor [EPC]** `854f9f76-6ac4-5903-bf5a-2197c35286be` (17),
**Serotonin Reuptake Inhibitor [EPC]** `ecdd6ecf-fb97-5475-81b6-01eb99ad267c` (8). Command:
`uv run drugref --dsn "<dsn>" profile class <uuid>`. Output pasted verbatim **except** that each `peculiar to`
line longer than six items is cut after six with `... (+N more)`; every property, standing and member is in full.

### beta-Adrenergic Blocker [EPC]

```text
beta-Adrenergic Blocker [EPC] [MED-RT EPC] -- 15 members: acebutolol, atenolol, betaxolol, bisoprolol, carteolol, carvedilol, esmolol, labetalol, levobunolol, metoprolol, nadolol, nebivolol, pindolol, propranolol, timolol anhydrous
  CI_with condition Drug Hypersensitivity
    observed in 13 of 15 members
    not confirmed for: levobunolol, metoprolol
  CI_with condition Heart Failure
    observed in 14 of 15 members
    not confirmed for: levobunolol
  may_treat condition Hypertension
    observed in 12 of 15 members
    not confirmed for: labetalol, levobunolol, nebivolol
  CI_with condition Hypotension
    observed in 13 of 15 members
    not confirmed for: levobunolol, nebivolol
  CI_with condition Pulmonary Edema
    observed in 13 of 15 members
    not confirmed for: levobunolol, nebivolol
  CI_with condition Shock, Cardiogenic
    observed in 14 of 15 members
    not confirmed for: levobunolol
  CI_with condition Sick Sinus Syndrome
    observed in 14 of 15 members
    not confirmed for: levobunolol
  peculiar to acebutolol: may_treat condition Angina Pectoris; CI_with condition Bradycardia; may_treat condition Myocardial Infarction; CI_with condition Pregnancy; may_treat condition Tachycardia, Ventricular; may_treat condition Ventricular Fibrillation; ... (+14 more)
  peculiar to atenolol: may_treat condition Angina Pectoris; may_treat condition Atrial Fibrillation; may_treat condition Atrial Flutter; CI_with condition Bradycardia; CI_with condition Heart Block; may_treat condition Heart Failure; ... (+21 more)
  peculiar to betaxolol: CI_with condition Atrioventricular Block; CI_with condition Bradycardia; may_treat condition Glaucoma, Open-Angle; CI_with condition Pregnancy Trimester, Second; CI_with condition Pregnancy Trimester, Third; interacts_with moiety acetohexamide; ... (+14 more)
  peculiar to bisoprolol: may_treat condition Angina Pectoris; CI_with condition Anuria; CI_with condition Atrioventricular Block; CI_with condition Bradycardia; may_treat condition Heart Failure; may_treat condition Myocardial Infarction; ... (+17 more)
  peculiar to carteolol: CI_with condition Bradycardia; CI_with condition Bronchial Hyperreactivity; may_treat condition Glaucoma, Open-Angle; CI_with condition Pregnancy; interacts_with moiety acetohexamide; interacts_with moiety chlorpropamide; ... (+11 more)
  peculiar to carvedilol: may_treat condition Angina Pectoris; CI_with condition Bradycardia; CI_with condition Bronchial Hyperreactivity; may_treat condition Heart Failure; CI_with condition Liver Failure; may_treat condition Myocardial Infarction; ... (+5 more)
  peculiar to esmolol: may_treat condition Angina Pectoris; interacts_with class Calcium Channel Antagonists [MoA]; may_treat condition Heart Failure; CI_with condition Hypertension, Pulmonary; CI_with condition Liver Failure; may_treat condition Myocardial Infarction; ... (+7 more)
  peculiar to labetalol: may_treat condition Angina Pectoris; CI_with condition Asthma; CI_with condition Bradycardia; CI_with condition Bronchial Hyperreactivity; CI_with condition Heart Block; may_treat condition Heart Failure; ... (+24 more)
  peculiar to levobunolol: CI_with condition Asthma; may_treat condition Glaucoma, Open-Angle
  peculiar to metoprolol: may_treat condition Angina Pectoris; may_treat condition Atrial Fibrillation; may_treat condition Atrial Flutter; interacts_with class Cardiac Rate Alteration [PE]; interacts_with class Cardiac Rhythm Alteration [PE]; may_treat condition Heart Failure; ... (+25 more)
  peculiar to nadolol: may_treat condition Angina Pectoris; CI_with condition Asthma; CI_with condition Bronchial Hyperreactivity; may_treat condition Heart Failure; may_treat condition Myocardial Infarction; interacts_with moiety acetohexamide; ... (+14 more)
  peculiar to nebivolol: CI_with condition Atrioventricular Block; CI_with condition Bradycardia; may_treat condition Hypertension, Malignant; CI_with condition Liver Diseases; interacts_with moiety fingolimod
  peculiar to pindolol: may_treat condition Angina Pectoris; CI_with condition Asthma; CI_with condition Atrioventricular Block; CI_with condition Bradycardia; CI_with condition Bronchial Hyperreactivity; may_treat condition Myocardial Infarction; ... (+18 more)
  peculiar to propranolol: may_treat condition Angina Pectoris; CI_with condition Asthma; CI_with condition Atrioventricular Block; CI_with condition Bradycardia; CI_with condition Bronchial Hyperreactivity; may_treat condition Esophageal and Gastric Varices; ... (+38 more)
  peculiar to timolol anhydrous: may_treat condition Angina Pectoris; CI_with condition Asthma; CI_with condition Atrioventricular Block; CI_with condition Bradycardia; CI_with condition Bronchial Hyperreactivity; CI_with condition Bronchial Spasm; ... (+7 more)
```

### Angiotensin Converting Enzyme Inhibitor [EPC]

```text
Angiotensin Converting Enzyme Inhibitor [EPC] [MED-RT EPC] -- 17 members: benazepril, benazeprilat, captopril, enalapril, enalaprilat anhydrous, fosinopril, fosinoprilat, lisinopril anhydrous, moexiprilat, perindopril, perindoprilat, quinapril, quinaprilat, ramipril, ramiprilat, trandolapril, trandolaprilat
  CI_with condition Drug Hypersensitivity
    observed in 17 of 17 members
  may_treat condition Hypertension
    observed in 14 of 17 members
    not confirmed for: captopril, enalapril, enalaprilat anhydrous
  peculiar to benazepril: CI_with condition Angioedema; may_prevent condition Diabetic Nephropathies; may_treat condition Diabetic Nephropathies; may_treat condition Heart Failure; CI_with condition Hyperaldosteronism; may_prevent condition Hypertrophy, Left Ventricular; ... (+22 more)
  peculiar to benazeprilat: CI_with condition Angioedema; may_prevent condition Diabetic Nephropathies; may_treat condition Diabetic Nephropathies; may_treat condition Heart Failure; CI_with condition Hyperaldosteronism; may_prevent condition Hypertrophy, Left Ventricular; ... (+8 more)
  peculiar to captopril: CI_with condition Angioedema; may_treat condition Arthritis, Rheumatoid; may_treat condition Bartter Syndrome; may_prevent condition Diabetic Nephropathies; may_treat condition Diabetic Nephropathies; may_treat condition Edema; ... (+17 more)
  peculiar to enalapril: CI_with condition Angioedema; may_treat condition Arthritis, Rheumatoid; may_treat condition Bartter Syndrome; may_prevent condition Diabetic Nephropathies; may_treat condition Diabetic Nephropathies; may_treat condition Edema; ... (+19 more)
  peculiar to enalaprilat anhydrous: CI_with condition Angioedema; may_treat condition Arthritis, Rheumatoid; may_treat condition Bartter Syndrome; may_prevent condition Diabetic Nephropathies; may_treat condition Diabetic Nephropathies; may_treat condition Edema; ... (+8 more)
  peculiar to fosinopril: CI_with condition Angioedema; interacts_with class Angiotensin-converting Enzyme Inhibitors [MoA]; may_prevent condition Diabetic Nephropathies; may_treat condition Diabetic Nephropathies; may_treat condition Heart Failure; CI_with condition Hyperaldosteronism; ... (+21 more)
  peculiar to lisinopril anhydrous: CI_with condition Angioedema; may_prevent condition Diabetic Nephropathies; may_treat condition Diabetic Nephropathies; may_treat condition Heart Failure; CI_with condition Hyperaldosteronism; may_prevent condition Hypertrophy, Left Ventricular; ... (+5 more)
  peculiar to moexiprilat: CI_with condition Pregnancy
  peculiar to perindopril: CI_with condition Angioedema; may_prevent condition Diabetic Nephropathies; may_treat condition Diabetic Nephropathies; may_treat condition Heart Failure; CI_with condition Hyperaldosteronism; may_prevent condition Hypertrophy, Left Ventricular; ... (+12 more)
  peculiar to perindoprilat: may_treat condition Coronary Artery Disease; may_treat condition Heart Failure
  peculiar to quinapril: CI_with condition Angioedema; may_prevent condition Diabetic Nephropathies; may_treat condition Diabetic Nephropathies; may_treat condition Heart Failure; CI_with condition Hyperaldosteronism; may_prevent condition Hypertrophy, Left Ventricular; ... (+20 more)
  peculiar to ramipril: CI_with condition Angioedema; may_prevent condition Diabetic Nephropathies; may_treat condition Diabetic Nephropathies; may_treat condition Heart Failure; CI_with condition Hyperaldosteronism; may_prevent condition Hypertrophy, Left Ventricular; ... (+19 more)
  peculiar to trandolapril: CI_with condition Angioedema; may_prevent condition Diabetic Nephropathies; may_treat condition Diabetic Nephropathies; may_treat condition Heart Failure; CI_with condition Hyperaldosteronism; may_prevent condition Hypertrophy, Left Ventricular; ... (+12 more)
```

### Serotonin Reuptake Inhibitor [EPC]

```text
Serotonin Reuptake Inhibitor [EPC] [MED-RT EPC] -- 8 members: citalopram, escitalopram, fluoxetine, fluvoxamine, nefazodone, paroxetine, sertraline, trazodone
  may_treat condition Depressive Disorder
    observed in 6 of 8 members
    not confirmed for: escitalopram, fluoxetine
  CI_with condition Drug Hypersensitivity
    observed in 8 of 8 members
  interacts_with class Monoamine Oxidase Inhibitors [MoA]
    observed in 8 of 8 members
  interacts_with moiety almotriptan
    observed in 6 of 8 members
    not confirmed for: nefazodone, trazodone
  interacts_with moiety amitriptyline
    observed in 6 of 8 members
    not confirmed for: nefazodone, trazodone
  interacts_with moiety amoxapine
    observed in 6 of 8 members
    not confirmed for: nefazodone, trazodone
  interacts_with moiety clomipramine
    observed in 6 of 8 members
    not confirmed for: nefazodone, trazodone
  interacts_with moiety desipramine
    observed in 6 of 8 members
    not confirmed for: nefazodone, trazodone
  interacts_with moiety doxepin
    observed in 6 of 8 members
    not confirmed for: nefazodone, trazodone
  interacts_with moiety eletriptan
    observed in 7 of 8 members
    not confirmed for: trazodone
  interacts_with moiety frovatriptan
    observed in 6 of 8 members
    not confirmed for: nefazodone, trazodone
  interacts_with moiety imipramine
    observed in 6 of 8 members
    not confirmed for: nefazodone, trazodone
  interacts_with moiety ioflupane i-123
    observed in 6 of 8 members
    not confirmed for: nefazodone, trazodone
  interacts_with moiety isocarboxazid
    observed in 6 of 8 members
    not confirmed for: escitalopram, nefazodone
  interacts_with moiety linezolid
    observed in 8 of 8 members
  interacts_with moiety metoclopramide
    observed in 6 of 8 members
    not confirmed for: nefazodone, trazodone
  interacts_with moiety naratriptan
    observed in 7 of 8 members
    not confirmed for: trazodone
  interacts_with moiety nortriptyline
    observed in 6 of 8 members
    not confirmed for: nefazodone, trazodone
  interacts_with moiety pethidine
    observed in 6 of 8 members
    not confirmed for: nefazodone, trazodone
  interacts_with moiety phenelzine
    observed in 6 of 8 members
    not confirmed for: escitalopram, nefazodone
  interacts_with moiety pimozide
    observed in 7 of 8 members
    not confirmed for: trazodone
  interacts_with moiety protriptyline
    observed in 6 of 8 members
    not confirmed for: nefazodone, trazodone
  interacts_with moiety rasagiline
    observed in 8 of 8 members
  interacts_with moiety rizatriptan
    observed in 7 of 8 members
    not confirmed for: trazodone
  interacts_with moiety selegiline
    observed in 7 of 8 members
    not confirmed for: escitalopram
  interacts_with moiety sibutramine
    observed in 7 of 8 members
    not confirmed for: trazodone
  interacts_with moiety sumatriptan
    observed in 7 of 8 members
    not confirmed for: trazodone
  interacts_with moiety tapentadol
    observed in 7 of 8 members
    not confirmed for: trazodone
  interacts_with moiety tramadol
    observed in 7 of 8 members
    not confirmed for: trazodone
  interacts_with moiety tranylcypromine
    observed in 6 of 8 members
    not confirmed for: escitalopram, nefazodone
  interacts_with moiety trimipramine
    observed in 6 of 8 members
    not confirmed for: nefazodone, trazodone
  interacts_with moiety zolmitriptan
    observed in 7 of 8 members
    not confirmed for: trazodone
  peculiar to citalopram: may_treat condition Alcoholism; may_treat condition Dementia; may_treat condition Diabetic Neuropathies; may_treat condition Obsessive-Compulsive Disorder; may_treat condition Tobacco Use Disorder; interacts_with moiety amiodarone; ... (+46 more)
  peculiar to escitalopram: may_treat condition Major Depressive Disorder; interacts_with moiety boceprevir; interacts_with moiety duloxetine; interacts_with moiety omeprazole; interacts_with moiety telaprevir; interacts_with moiety venlafaxine
  peculiar to fluoxetine: may_treat condition Bulimia; may_treat condition Major Depressive Disorder; may_treat condition Obsessive-Compulsive Disorder; may_treat condition Panic Disorder; interacts_with moiety alprazolam; interacts_with moiety atomoxetine; ... (+43 more)
  peculiar to fluvoxamine: may_treat condition Obsessive-Compulsive Disorder; may_treat condition Panic Disorder; interacts_with moiety alosetron; interacts_with moiety alprazolam; interacts_with moiety astemizole; interacts_with moiety celecoxib; ... (+36 more)
  peculiar to nefazodone: CI_with condition Liver Diseases; may_treat condition Stress Disorders, Post-Traumatic; interacts_with moiety alfentanil; interacts_with moiety alfuzosin; interacts_with moiety alprazolam; interacts_with moiety aripiprazole; ... (+83 more)
  peculiar to paroxetine: may_treat condition Feeding and Eating Disorders; may_treat condition Obsessive-Compulsive Disorder; may_treat condition Panic Disorder; may_treat condition Phobic Disorders; may_treat condition Stress Disorders, Post-Traumatic; interacts_with moiety anisindione; ... (+23 more)
  peculiar to sertraline: may_treat condition Feeding and Eating Disorders; may_treat condition Obsessive-Compulsive Disorder; may_treat condition Panic Disorder; may_treat condition Stress Disorders, Post-Traumatic; interacts_with moiety clozapine; interacts_with moiety cyproheptadine; ... (+6 more)
  peculiar to trazodone: may_treat condition Sleep Initiation and Maintenance Disorders; interacts_with moiety amprenavir; interacts_with moiety atazanavir; interacts_with moiety boceprevir; interacts_with moiety clarithromycin; interacts_with moiety cobicistat; ... (+19 more)
```

**Structural notes (not clinical conclusions):**

- No PK term, no empty class, and no observed property on fewer than two members appears in any of the three.
- None of the three has an asserted property or a subclass-only drug, so "asserted" and "in subclasses only"
  rendering is not exercised by these three (it is by the 877 classes with asserted properties, §3).
- Exact object match, no condition roll-up (spec §8): escitalopram and fluoxetine are *not confirmed* for
  `may_treat Depressive Disorder` while each lists `may_treat Major Depressive Disorder` as peculiar.
- `fosinopril: CI_with condition aliskiren` — MED-RT points a contraindication at a MeSH chemical
  supplementary record (`condition.scr_class = '1'`). Upstream data faithfully projected: 2 contraindication and
  2 indication records on this database, no class property; not filed.
- Salt/hydrate names (`timolol anhydrous`, `propafenone hydrochloride`) and prodrug/active-metabolite pairs
  (benazepril/benazeprilat) appear as separate members, as everywhere else (#67).

## 6. Read cost on a single class — the consumer view

`EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM <view> WHERE class_uuid = '<beta blocker>'`
([`tools/class_profile_census.sql`](../../../tools/class_profile_census.sql), three warm runs):

| view | rows | execution, db/062 | db/061 |
|---|---:|---:|---:|
| `class_property` | 7 | 387–613 ms | 3,669 ms |
| `class_property_member` | 105 | 486–716 ms | 3,710 ms |
| `class_member_peculiarity` | 300 | 413–575 ms | 3,689 ms |
| `class_subclass_only_member` | 0 | 36–40 ms | 34 ms |

At db/061 the correlated `asserted_by` subquery in `class_property` ran 5,357 loops × 0.629 ms = 3.37 s of the
3.67 s. `db/062` aggregates `asserted_by` in the grouped `asserted` CTE instead (output byte-identical over all
9,664 rows and all 2,394 gap rows, compared by `\copy` before and after the migration). A filtered read still
materialises every class before the filter (#202's structural point stands), but the whole materialisation now
costs about 0.4 s.

CLI wall time (`/usr/bin/time -p`, db/062): `profile class` **1.4–1.6 s** for each of the three classes (11.2–11.3
s at db/061); `profile drug` propranolol (20 class tags, no `--lens`) **1.71 s** (189 s), with one `--lens`
**1.12 s** (11.2 s). The drug profile now reads each view once for all its lenses (`class_uuid = ANY`), rather
than once per lens, and prints the full four-way split (shared, not confirmed for this drug, confirmed exception,
peculiar).

**Addendum, `db/065` (2026-11-08, #202).** `class_property` rewritten so a filter reaches every scan of
`class_membership` (no subquery referenced twice, so none is materialised; the self-partner test joins
`class_profile_member` directly rather than a shared CTE). Applied to
`drugref_profiles`; output identical to `db/064` (EXCEPT both ways: `class_property` 9,668 rows,
`class_property_member`, `class_member_peculiarity`, `gap_unconfirmed_class_property`). Same query, warm:

| view | rows | execution, db/065 | db/064 |
|---|---:|---:|---:|
| `class_property` | 7 | 51 ms | 748 ms |
| `class_property_member` | 105 | 118 ms | 840 ms |
| `class_member_peculiarity` | 300 | 86 ms | 767 ms |
| `class_subclass_only_property` (new, #209) | 0 | 34 ms | — |

Full `count(*)`: `class_property` 375 ms (473), `class_property_member` 400 ms (610), `class_member_peculiarity`
887 ms (1,447), `gap_unconfirmed_class_property` 449 ms (941). `class_subclass_only_property` holds 53,041
rows, all `inherits` (no rulings on this database). CLI: `profile class` **0.42 s** for each of the three
classes (1.4–1.6 s at db/062); `profile drug` propranolol, no `--lens`, **1.53 s** (1.71 s);
`register_from_gaps` 4.66 s. A filtered read still computes `member_property` (~30 ms) and the recursive
`class_subtree` (~15 ms) in full.

## 7. Decisions

- **The worklist is usable as built in size** (§4: the same order as `unmatched_ingredient` and
  `unruled_composition_activity`), **but its composition needs a design decision**:
  one near-universal object dominates it, and MED-RT's two catch-all "Unknown ..." classes act as drug classes —
  close to the spec §9 anti-goal. **Filed: #203.**
- **The read path was too slow for an interactive consumer** (§6). Filed as **#202**; `db/062` took the
  dominant cost out (single class ~0.4–0.6 s, a 20-lens drug profile 1.7 s). What `db/062` left open — a
  filtered read still computed every class — `db/065` closed (§6 addendum: a filter reaches every
  `class_membership` scan; `profile class` 0.42 s).
- **Interaction rulings do not reach the prescribing-alert path. Filed: #204** (owner design call). A
  `curated_property_ruling` *interacts_with* answer changes the class-profile views only, never
  `exact_ddi_pair` / `ddi_candidate_pair` / `curated_ddi_pair`: a *holds* never alerts, a *does not hold* hides
  the interaction in the help section while the alert still fires — against spec §4's agreement rule. 543 of the
  2,394 questions are interaction questions (507 moiety-object, 36 class-object). **Curators should not work them
  until #204 is decided.**
- The measuring round tuned nothing, by the spec's rule (§7); the only SQL change since is the final review's
  `db/062` (a correctness fix that also removed the cost).
- **Addendum, `db/063` (the pre-merge review).** A member had a standing against "interacts_with
  <itself>" whenever the drug belongs to a class said to interact with it: 129 such `class_property_member`
  rows on `drugref_profiles`, 4 of them self-questions on the worklist. `db/063` removes them (0 rows; the
  worklist is now **2,390**, 539 interaction questions: 503 moiety-object, 36 class-object). Every figure above
  is the `db/062` measurement and is left as measured. The denominator side of the same case is **#206**.
