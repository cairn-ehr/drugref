# drugref — drug effects on laboratory values: source evaluation

**Date:** 2026-11-27 · **Issue:** [#294](https://github.com/cairn-ehr/drugref/issues/294) (requested by
ReportTriage) · **Status:** source evaluation, step 1 of 4. The model, read view and gap view are the next
document; nothing is ingested yet. **Measured with:** `tools/lab_effect_survey.py` (openFDA export of
2026-08-22, 14 partitions, SHA-256s in the mining record §2) and the `drugref_allergy` database (MED-RT
2026.07.06, MeSH 2026).

## 1. The question, and the owner's rulings so far

ReportTriage ranks incoming laboratory reports, partly by the patient's medicines. It needs drugref to say, for
a substance or a class, which analyte is affected, in which direction and in which **role**. It also needs a
gap view, so that "drugref has no statement" can be told apart from "drugref says there is no effect".

**Owner rulings (2026-11-27):**

1. **Record and grade, the consumer filters ("A").** drugref records label statements with a strength grade
   taken from the section they appear in. Restricting it to effects a curator judged clinically salient ("B")
   is the later refinement, once there are more curators.
2. **A fourth role: the result itself is unreliable.** A substance can make a result untrustworthy, or not
   comparable with the usual reference range, without changing the patient's physiology. Clinicians find this
   hard to anticipate. The consumer side is tracked as
   [ReportTriage#36](https://github.com/cairn-ehr/ReportTriage/issues/36).

So the model needs four roles: **causes** the abnormality; makes it **more dangerous**; defines a
**monitoring protocol**; **interferes** with the result (false reading, or a reference range that no longer
applies).

## 2. Licences

Rule 6 (CLAUDE.md) is a blocker, not a cleanup. Sources already in drugref keep their clearance as recorded in
`NOTICE`. The new sources were checked on 2026-11-27 against the publisher's own page. **The LOINC and LiverTox
pages refused a second automated fetch (403, captcha), so their quotes below come from that one check. Re-read
both pages by hand before the ingest that uses them.**

| Source | Licence, as stated by the publisher | Verdict for drugref tables |
|---|---|---|
| openFDA `drug/label` | "public domain and made available with a Creative Commons CC0 1.0 Universal dedication", except content "clearly marked by a warning" (open.fda.gov/terms) | **Clear**, as already used (`NOTICE`). Label prose is written by the manufacturer, so drugref stores the facts it extracts and short quotes (the existing SPL quote budget), not bulk text |
| DailyMed SPL | NLM: government works "may be freely used"; content contributed by others "may" be copyrighted | **Clear on the same terms**, as already used (`NOTICE`) |
| MED-RT | public domain (VA), already ingested | **Clear** (`NOTICE`) |
| MeSH 2026 | NLM Terms and Conditions, already ingested | **Clear** (`NOTICE`) |
| LiverTox (NIDDK, NCBI Bookshelf NBK547852) | "This publication is in the public domain"; "copyright free" | **Clear with conditions**: exclude figures marked "Reprinted with permission"; bulk download from the LitArch FTP (`livertox_NBK547852.tar.gz`), never by crawling the Bookshelf; credit NIDDK |
| DILIrank 2.0 / DILIst (FDA NCTR) | no licence of its own; FDA site policy: "not copyrighted. They are in the public domain" | **Clear**, from fda.gov, not from the journal papers (copyrighted) |
| LOINC (Regenstrief) | free to "use, copy, or distribute" with a mandatory notice; extracted content must carry the code and its display name; field contents "shall not be changed"; parts used only "as they are presented"; not for "developing or promulgating a different standard"; no derivative works | **Not AGPL-compatible as bundled data**, because of the no-change, no-derivative and field-of-use terms. Usable only under its own licence and notice, outside the AGPL tables. **Owner decision, §6 Q1** |
| MedDRA (MSSO) | "may not grant any sublicense nor publish nor otherwise distribute MedDRA to a third party"; names "open-source platforms, publicly accessible datasets" as violations (MSSO statement, 2026-05-20) | **Blocked** |
| OnSIDES v3.1.1 | data CC BY 4.0, but every effect is a MedDRA code | **Blocked as data** (MedDRA). The method stays usable (MIT), as PROJECT-NOTES already records |
| SIDER 4.1 | CC BY-SA 4.0, but its effect terms are MedDRA; frozen since 2015 | **Blocked as data** (MedDRA) |
| CTCAE v6.0 (NCI) | NCI text "free of copyright", but "Each CTCAE v6.0 term is a MedDRA v28.0 LLT" | **Uncertain**: the grading text is clear, the term strings and LLT codes are not. Not needed for this work (thresholds stay in ReportTriage) |
| Young's *Effects of Drugs on Clinical Laboratory Tests*, Lexicomp, Micromedex, CLSI EP07 tables | proprietary | **Barred** |
| AMT, SNOMED CT, ATC | barred from drugref tables already | **Barred** |

**No openly licensed dataset dedicated to drug effects on laboratory values, or to assay interference, was
found.** The content has to be extracted (labels) or curated.

## 3. What each candidate holds, measured

### 3.1 US labels (openFDA): the main source for all four roles

`tools/lab_effect_survey.py` reads every record (262,032) and keeps human prescription labels with exactly one
substance: **32,998 labels, 1,931 substances**. A combination label is skipped, because its warning cannot be
attributed to one ingredient. For each substance and effect it keeps the **strongest** section that mentions
the effect, in the order boxed warning > warnings and cautions > warnings > precautions > laboratory tests >
drug interactions > adverse reactions.

**These are mention counts, an upper bound.** "Do not use in patients with hyperkalemia" mentions the effect
without saying the drug causes it. A statement-level reader will find fewer.

**Test set (the 10 rows in #294, expanded to 68 drug × effect checks with common class members):** 66 are
found in the label, 63 of them in a boxed warning or a warnings or precautions section. The two misses are
structural, not signal failures:

- **aspirin**: no single-substance prescription label (US aspirin is OTC);
- **trimethoprim and the INR**: the warfarin warning sits on the sulfamethoxazole/trimethoprim combination
  label, which the survey skips. Combination labels need an attribution rule (§5).

Weak spots: trimethoprim and creatinine is found only in adverse reactions. naproxen and creatinine, and
furosemide and creatinine, reach only precautions. The azoles and metronidazole state the INR effect in
precautions or drug interactions, which is the right place for an interaction seen through a laboratory value.
Carbimazole is not US-marketed, so methimazole stood in.

**Over-inclusion, and what the grade does about it** (substances, of 1,931, with any mention, and with a
mention in the boxed warning or a warnings section):

| Effect | Any mention | Boxed or warnings | Boxed only |
|---|---:|---:|---:|
| potassium high | 288 | 76 | 5 |
| potassium low | 460 | 183 | 9 |
| sodium low | 283 | 82 | 2 |
| creatinine high | 584 | 222 | 22 |
| bleeding | 852 | 411 | 68 |
| INR high | 297 | 80 | 3 |
| neutrophils low | 618 | 241 | 16 |
| platelets low | 731 | 244 | 14 |

The grade cuts each list to between a quarter and a half. **It does not make a salient list on its own**: 244
substances name thrombocytopenia in a warnings section. This is the expected cost of ruling A. Ruling B (curated
salience) is what closes it, and the read view must carry the grade so the consumer can filter meanwhile.

**The interference section** (`drug_and_or_laboratory_test_interactions`) is present for only **161
substances**. In an evenly spaced sample of 41:

| Reading | Count | Examples |
|---|---:|---|
| a false reading or method interference | 20 (2 of them imaging, not laboratory) | cephalosporins and urine glucose (copper reduction); fosinopril and one digoxin RIA kit; oxaprozin and benzodiazepine immunoassays; maltose and GDH-PQQ glucose strips; flucytosine and creatinine methods |
| a real change that shifts the reference range | 10 | oestrogens, progestins and androgens on binding globulins and total T4; IM lidocaine and CK; positive Coombs with cephalosporins |
| empty or "none known" | 5 | |
| another section misfiled by openFDA | 6 | atropine, tucatinib, zolmitriptan |

So the section is the right place for the fourth role, and often names the **method** ("tests that use
Benedict's and Fehling's solutions"), which matters because interference is usually method-specific. But it is
thin. Interference statements also appear in other sections. Biotin, the most common real-world case, has no
prescription label at all. The fourth role will need curated additions sooner than the other three.

### 3.2 MED-RT: a partial seed, with the role always missing

Against 26 test-set pairs checked in `drugref_allergy`:

- **`CI_with`** (already ingested as `moiety_condition_contraindication`) matches **11 of 26**: spironolactone
  and eplerenone with hyperkalaemia; desmopressin with hyponatraemia; warfarin, apixaban and clopidogrel with
  haemorrhage; methotrexate and clozapine with neutropenia; heparin, enoxaparin and methotrexate with
  thrombocytopenia. It has none of the ACE inhibitors, ARBs, NSAIDs or trimethoprim for potassium, none of the
  diuretics for potassium or sodium, no SSRI, no carbamazepine, no digoxin, and no kidney injury.
- **`CI_with` does not say the role.** Under Hyperkalemia it lists drugs that cause it (potassium salts,
  potassium-sparing diuretics). Under Hypokalemia it lists QT-prolonging drugs (sotalol, dofetilide, pimozide),
  which low potassium makes more dangerous. Under Neutropenia it lists drugs that cause it (cytotoxics) next to
  clozapine (a monitoring protocol). A reviewer must assign the role every time.
- **`induces`** (`moiety_induced_condition`, 154 rows) matches **none** of the test set.
- **The physiologic-effect axis gives mechanism-level seeds.** `Decreased Renal K+ Excretion [PE]` has 48
  members: the ACE inhibitors, the ARBs, amiloride and spironolactone. That is most of the potassium-high row.
  But `Increased Renal K+ Excretion [PE]` has 5 members and no loop or thiazide diuretic. The axis is uneven, so
  it can only be a seed, never a basis for "no statement means no effect".

### 3.3 LiverTox and DILIrank: the liver tests, with a time to onset

Not downloaded. Neither is in the test set. LiverTox is the one source found that states a usual **time from
starting the drug to the effect** (its latency field), which #294 asks for "where known". Its A–E likelihood
score is a grade the read view could carry. DILIrank gives a per-drug DILI concern class. Both are clear (§2).
Worth a later ingest for ALT, AST, ALP and bilirubin. Not first.

### 3.4 What no clear source gives

- **Time to effect** for anything except liver injury. Labels state it only occasionally ("within the first 3
  months" for agranulocytosis). It stays nullable and is curated.
- **Australian-only medicines and wording** (carbimazole). TGA product information is not checked or cleared
  here. That is the local tier's question (Slice 8).

## 4. The grain the sources speak at

- **Subject:** labels speak for one product, which resolves to a moiety through the existing SPL subject
  resolution. MED-RT speaks for moieties and classes. The test set speaks mostly in classes ("thiazide
  diuretics"), so class statements and `class_subtree` (which already answers "is this moiety in this class,
  including subclasses") are needed from the start.
- **Object:** an analyte plus a direction. Labels speak in clinical terms ("hyperkalemia", "agranulocytosis",
  "bleeding"), not analytes. drugref's MeSH `condition` registry already holds the clinical terms (Hyperkalemia
  D006947, Hypokalemia D007008, Hyponatremia D007010, Neutropenia D009503, Agranulocytosis D000380,
  Thrombocytopenia D013921, Hemorrhage D006470, Acute Kidney Injury D058186, Chemical and Drug Induced Liver
  Injury D056486). The analyte mapping still has to be authored: "hyperkalaemia" is potassium high, but
  "bleeding" reaches haemoglobin low only through a pathway, and "INR high" only with warfarin present.
- **Conditional statements:** the INR row is an interaction with a co-medication (warfarin), seen through a
  laboratory value. The model needs an optional co-medication condition. That should reuse the
  interaction layer's subjects, not a new vocabulary.
- **Interference:** analyte plus, often, the **method** ("copper reduction", "GDH-PQQ strip", "immunoassay").
  There is no open method vocabulary. The method stays free text with a quote, unless one is found.

## 5. How a first ingest would be validated

1. **Reproduce the test set.** The survey's 66 of 68 is the mention-level ceiling. The statement-level ingest
   reports which rows it reproduces, which it knowingly contradicts, and why. Done-when in #294 asks for this
   list of disagreements.
2. **A precision sample by hand,** as the class-mentions round did: n = 100 statements, stratified by section
   grade, read by a clinician, with Wilson lower bounds published.
3. **Combination labels** need a rule before they count. The trimethoprim INR miss shows the cost of skipping
   them. A measured option: accept a combination-label statement for an ingredient only when the single-
   ingredient label also exists and does not contradict it.
4. **Review and signing** go through the existing reviewer, as for every curated statement. Label-derived rows
   are recorded as "not confirmed" until a curator rules.

## 6. Questions for the owner (before the model is specified)

1. **How should analytes be identified, given LOINC's licence?** LOINC's no-change, no-derivative and
   field-of-use terms make it unfit for bundled data under rule 6. Options:
   - **(a) drugref-owned analyte concepts with no LOINC codes.** The consumer maps them to LOINC on its side.
     ReportTriage already holds LOINC codes for its results, and it is a small, stable list. My pick: it keeps
     drugref's tables clean, and the mapping lives where the LOINC licence already applies.
   - **(b) the same concepts, plus a LOINC mapping file shipped separately** under LOINC's own notice, as a
     node-local plug-in (the rule-6 pattern for encumbered sources).
   - **(c) treat LOINC as compatible and bundle the codes with its notice.** Not recommended without a ruling,
     because of the no-derivative clause.
2. **Is the analyte object a new registry, or the existing MeSH `condition` registry plus a mapping?** Labels
   name conditions (hyperkalaemia), and ReportTriage needs analytes (potassium, high). Keying statements to MeSH
   conditions reuses the existing machinery (`condition_subtree`, curated condition rulings) and the drug–
   disease layer's provenance. It needs a small authored condition → (analyte, direction) map. Bleeding to
   haemoglobin needs a pathway marker there.
3. **The additive-effect registry** (`db/020`) is keyed to MED-RT physiologic-effect classes and is empty. It
   answers "do these effects add up", which is a different question. This evaluation finds no reason to build
   on it, beyond reusing MED-RT `Decreased Renal K+ Excretion [PE]`-style classes as seeds.
