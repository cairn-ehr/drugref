# drugref — FDA interaction and toxicity source spike

**Date:** 2026-08-16 · **Status:** source decision and implementation boundary ·
**Scope:** FDA CYP/transporter roles, DICTrank, DIRIL, DILIrank 2.0, and the
previously-unassessed DrugCentral `omop_relationship` table.

This spike answers two questions left open by the 5c.3 evaluation:

1. Is there a licence-clean source for the **potency bands** SPL section 7 uses but
   MED-RT cannot express?
2. Are there licence-clean drug→harm sources that can fill the QT, renal and hepatic
   evidence gaps without pretending that a research classification is a clinical
   contraindication?

The answer to both is **yes, from FDA**, with an important modelling boundary:

- FDA's CYP/transporter table is classification data and belongs in the class tier.
- FDA's toxicity ranks are evidence assessments and belong in a new **non-firing
  source projection**. They do not become contraindications, alerts, or curated
  additive-effect classes on ingest.
- DrugCentral's contraindication table is **not bundleable in its current published
  shape**. It contains no row-level provenance that can separate old OMOP content from
  later label curation, and its condition identifiers are SNOMED CT/UMLS.

No clinical data or schema is added by this spike. It fixes the source decision and the
shape an implementation round must preserve.

## 1. Rule 6: what is clean, and at what scope

FDA states that, unless otherwise noted, text and graphics on `fda.gov` are public
domain and may be reused freely. It also asks downstream users to record the copy date
and link to the live source because pages are updated:

<https://www.fda.gov/about-fda/about-website/website-policies>

That clears the FDA-authored classifications below. It does **not** turn copied
third-party material into federal work. DIRIL makes this distinction load-bearing: its
workbook includes DrugBank identifiers and descriptions, ATC/DDD fields, and the two
literature datasets FDA reconciled. Drugref may bundle only the narrow FDA-authored
projection named in §4.3, never the whole workbook or those columns.

| source key | authority and source | rule-6 decision |
| --- | --- | --- |
| `FDA-CYP` | FDA CYP and transporter examples | **Bundle**, public domain; pin fetch time and checksum because the HTML has no release identifier |
| `FDA-DICT` | FDA DICTrank workbook | **Bundle**, public domain FDA classification |
| `FDA-DIRIL` | FDA DIRIL workbook | **Bundle only the clean FDA projection** in §4.3; exclude DrugBank, ATC/DDD, product and literature-source columns |
| `FDA-DILI` | FDA DILIrank 2.0 workbook | **Bundle**, public domain FDA classification |
| `DRUGCENTRAL-OMOP` | DrugCentral `omop_relationship` | **Do not bundle**; compilation licence does not cure upstream OMOP/SNOMED/UMLS provenance, and the published row has no source discriminator |

This is separate from DrugCentral's `ddi_ref_id = 2` decision. That VHA NDF-RT subset
remains clean and remains the next cheap pairwise-DDI candidate.

## 2. Reproduction manifest

Files were retrieved from the live official sources on 2026-08-16. These checksums are
the release identity until FDA publishes an explicit version field:

| source | live source | SHA-256 |
| --- | --- | --- |
| FDA-CYP HTML | <https://www.fda.gov/drugs/drug-interactions-labeling/healthcare-professionals-fdas-examples-drugs-interact-cyp-enzymes-and-transporter-systems> | `7400dc898509e83d888ecd713897e59f3dc9d1c5f6cbd2f62a5d6ff8377ffa73` |
| DICTrank XLSX | <https://www.fda.gov/media/178811/download?attachment=> | `c50e55f6de01233dca24df749a1bff4fe0745d791b4d97ba05ebd7d9d193eb92` |
| DIRIL XLSX | <https://www.fda.gov/media/178824/download?attachment=> | `602edfda3da6b62bb52f8a88cd8fff5783f0635608dd0f2b0eb44299195b50dc` |
| DILIrank 2.0 XLSX | <https://www.fda.gov/media/113052/download?attachment=> | `1ca1352ff727af68e68e250eae2ed775bca8492335140ac0afd2233248694993` |

Registry coverage was measured against the current verification database,
`drugref_db038`, using case normalization, surrounding-whitespace removal
and internal-whitespace collapse. It is an **exact-name lower bound**, not a proposed
identity algorithm. DIRIL's separate result uses its published UNIIs.

## 3. FDA-CYP: the missing potency vocabulary

Source:
<https://www.fda.gov/drugs/drug-interactions-labeling/healthcare-professionals-fdas-examples-drugs-interact-cyp-enzymes-and-transporter-systems>

The live page contains **245 rendered table rows** (some substances occur on more than
one row). FDA says five entries are not drugs: St John's wort, curcumin, diosmin,
tobacco smoking and grapefruit juice. The table also carries combination strings, such
as `atazanavir and ritonavir`, whose reported role must not be assigned independently
to either component.

The useful grain is:

```text
substance or regimen × system/pathway × role × potency × qualifier
```

- system: `CYP` or `transporter`
- pathway: CYP1A2, CYP2C8, CYP2C9, CYP2C19, CYP2D6, CYP3A, P-gp, BCRP,
  OATP1B1/OATP1B3, OAT1/OAT3, OCT2/MATE
- role: inhibitor, inducer, or substrate
- potency: strong, moderate, weak, sensitive or moderately sensitive where FDA
  defines one; null for transporter roles without that vocabulary
- qualifier: the numbered footnote, because several memberships are dose-, route-,
  preparation-, metabolite- or genotype-dependent

FDA gives quantitative definitions. For CYP inhibitors, strong means a sensitive
substrate AUC increase of at least 5-fold, moderate 2 to less than 5-fold, and weak
1.25 to less than 2-fold. For inducers, the corresponding AUC decreases are at least
80%, 50 to less than 80%, and 20 to less than 50%.

### 3.1 Decision: project roles as source-defined PK classes

The table defines membership, not pairwise clinical advice. The least-new-mechanism
shape is therefore the source-neutral class registry:

- `substance_class.source = 'FDA-CYP'`
- `concept_type = 'PK'`
- deterministic `source_code`, for example `cyp:1a2:inhibitor:strong` and
  `transporter:pgp:substrate`
- `class_membership.relationship = 'has_PK'`
- **no inferred parent edges in the first release**

This reuses the class identity and membership mechanisms without pretending that FDA
published identifiers it did not. `source_code` is explicitly a drugref normalization
key; the live URL, fetch time, checksum and raw table heading remain the provenance.

`class_membership` cannot preserve row qualifiers today. The implementation must add a
source-evidence projection keyed to the membership (raw subject, source row key,
footnote/qualifier, ingest run), or withhold every qualified row. Silently dropping a
qualifier is not permitted.

### 3.2 What this does and does not unlock

It gives SPL mining the exact class grain needed for a statement such as “strong
CYP1A2 inhibitors are contraindicated; moderate or weak inhibitors should be avoided.”
An SPL rule may point at the exact FDA potency class rather than MED-RT's undifferentiated
inhibitor class.

It does **not** create DDI pairs by joining inhibitors to substrates. FDA describes the
table as an optional, non-exhaustive interpretive guide and excludes other mechanisms.
A pair still requires an SPL assertion or a curated clinical source before it can enter
`class_contraindication` or the curated overlay.

Combination rows become an explicit unresolved-regimen work item. Non-drug rows become
an explicit unsupported-entity count. Neither is exploded into moiety memberships.

## 4. FDA toxicity sources: evidence projections, not clinical rules

The three sources answer “what evidence does FDA's classification assign to this
substance?” They do not all identify one MeSH condition, and their rank vocabularies are
not clinical severity vocabularies. Forcing them directly into
`moiety_induced_condition`, `moiety_condition_contraindication` or an additive-effect
self-pair would discard that distinction.

The implementation shape is a rebuildable `source_toxicity_assessment` projection with,
at minimum:

```text
source · source_record_key · source_checksum · raw_subject
resolved_moiety (nullable) · toxicity_kind · concern
finding_type · label_section · source_severity · keywords
evidence_link · ingest_run
```

`source_record_key` is LTKBID for DILIrank and a deterministic row-content hash for the
two workbooks that publish no stable row ID. Unresolved or ambiguous subjects are data,
not log lines, and must reach the open-question register. Nothing in this table fires a
clinical alert. A later clinician-reviewed promotion can create a curated condition
relation or class membership with its own evidence and signature.

### 4.1 DICTrank

Source:
<https://www.fda.gov/science-research/bioinformatics-tools/drug-induced-cardiotoxicity-rank-dictrank-dataset>

Workbook sheet `Table S1`, `A1:H1319`: **1,318 rows** with trade name,
generic/proper name, active ingredient, cardiotoxicity type, label section, concern,
keywords and severity.

| measurement | result |
| --- | ---: |
| distinct active-ingredient strings | 1,291 |
| exact matches to Drugref display names | **1,245 (96.44%)** |
| distinct generic/proper strings | 1,264 |
| exact matches to Drugref display names | **1,168 (92.41%)** |
| cardiotoxicity `NA / Mixed / Heart Damage / Arrhythmia` | 450 / 345 / 293 / 230 |
| label section `AR / WP / no / SP / BW / Clinical Pharmacology / withdrawn / overdosage` | 383 / 365 / 262 / 102 / 91 / 86 / 27 / 2 |

The workbook, case-normalized, contains **341 most, 527 less, 107 ambiguous and 343
no-concern** rows. FDA's landing page says 341 / 528 / 106 / 343. The one-row
less-versus-ambiguous disagreement is an upstream inconsistency: preserve the workbook
value, report the page reconciliation, and fail if either distribution changes under
the same checksum.

A deliberately broad `qt|torsad` keyword scan finds **228 rows**. Restricting that set
to concern other than `no` leaves **149 rows over 133 non-empty active-ingredient
strings**. This is a **review population only**. Some no-concern rows contain negative
phrasing such as “QT interval is not prolonged” or say a study was not conducted, and
the non-no rows still need a human to distinguish QT evidence from surrounding prose.

Therefore DICTrank qualifies the standing claim: FDA does publish an open cardiotoxicity
dataset containing QT evidence, but it still does not publish a dedicated,
CredibleMeds-equivalent torsades-risk list. Issue 93's eventual wording must make that
distinction.

### 4.2 DILIrank 2.0

Source:
<https://www.fda.gov/science-research/liver-toxicity-knowledge-base-ltkb/drug-induced-liver-injury-rank-dilirank-20-dataset>

Workbook sheet `version 2`, `A1:F1338`: title + header + **1,336 rows**. Fields are
LTKBID, compound name, severity class, label section, concern and change comment.

- 217 most concern
- 351 less concern
- 354 ambiguous
- 414 no concern
- 987 unchanged, 300 new, 49 revised from version 1
- **1,265/1,336 compound names (94.69%)** exactly match Drugref display names

LTKBID is stable source provenance, not a substance identity. Name resolution must use
the same form/composition-aware worklist discipline as other sources; a salt-named
compound is not silently collapsed to its base.

### 4.3 DIRIL — high value, narrow clean projection

Source:
<https://www.fda.gov/science-research/bioinformatics-tools/drug-induced-renal-injury-list-diril-dataset>

The official workbook has one sheet, `A. DIRIL (317)`, with **317 data rows and 25
columns**. FDA's reconciled result is complete: **171 nephrotoxic and 146
non-nephrotoxic** in `My Findings (Toxicity)`.

The file's declared used range is accidentally `A1:Y1048381` even though data ends at
row 318. Its worksheet XML is 209 MB uncompressed, which explains why a generic
workbook importer tried to materialize approximately one million styled rows. The
production parser must stream rows 1–318, require the exact header, and reject any
non-empty cell after row 318. It must not infer the data range from the workbook's
declared dimension.

Identity coverage is unusually good:

- 316 distinct primary UNIIs and 187 secondary UNIIs;
- **315/317 rows have at least one UNII in Drugref**;
- 166 rows have **two** UNIIs that both resolve, so “all matching UNIIs” is not a safe
  automatic expansion rule;
- the unmatched rows are strontium ranelate (`04NQ160FRU`) and pentosan polysulfate
  (`F59P8B75R4`).

The clean bundled projection is limited to:

```text
raw name · UNII 1 · UNII 2
My Findings (Toxicity) · FDA label/document link
```

Treat the raw name and UNIIs only as mapping evidence: revalidate them against
Drugref/GSRS and persist the resolved Drugref moiety UUID. Do not turn workbook
identity fields into new Drugref identity claims.

Even within that projection, the FDA link is absent on 78 rows. Preserve that absence;
do not manufacture a label citation from the product-name field.

Explicitly excluded from the bundled projection:

- `drugbank_id`, DrugBank-derived description, InChIKey and related chemistry fields;
- `Label_Gong` and `Label_Shi` source classifications;
- product-name/date/country aggregations;
- every 2023 ATC/DDD field;
- `Origional Name/Notes`.

The row's FDA adjudication can be bundled; the workbook as a whole cannot be treated as
one undifferentiated public-domain payload.

## 5. DrugCentral `omop_relationship`: the provenance audit fails

DrugCentral's 2023 paper states that indication/contraindication content through 2012
came from OMOP 4.4 and later content was manually curated from approved labels:

<https://pmc.ncbi.nlm.nih.gov/articles/PMC10692006/>

The live DrugCentral DRS API was inspected on 2026-08-16. Its
`relationship_name=contraindication` response now contains **27,731 rows** (60 more
than the paper's 27,671), over **1,457 structures and 1,492 concepts**. Each row exposes
only:

```text
id · struct_id · relationship_name · concept_id · concept_name
UMLS CUI · UMLS semantic type · SNOMED concept ID · SNOMED full name
```

There is no source kind, source date, label identifier, citation, curator, or field that
separates the pre-2012 OMOP rows from post-2012 label curation. **The clean subset is
therefore not selectable.** The presence of a high row ID is not evidence of creation
date and must not be used as a proxy.

The table is also not purely drug→disease. Its contraindication objects include
pregnancy, breastfeeding, procedures, risk states, co-administered drugs and drug
classes. Examples in the live data include fluvoxamine, rosuvastatin, monoamine oxidase
inhibitors and strong CYP-inducer descriptions. That mixed object domain is clinically
reasonable, but it needs typed resolution rather than a disease-only import.

Measured identifier gaps reinforce rather than cause the rejection: 853 rows have no
SNOMED ID and 852 no UMLS CUI. Drugref could ignore both restricted identifier columns
and independently map `concept_name` to MeSH, but that would not recover assertion
provenance.

**Decision:** no bundled or node-global projection from `omop_relationship`. Reconsider
only if DrugCentral publishes row-level `source_kind` plus a label identifier/date or
citation sufficient to select and audit label-derived rows. Node-local consumers with
their own OMOP/SNOMED/UMLS rights may still attach it as a separately licensed plug-in.

## 6. Implementation order

1. **Keep the planned DrugCentral NDF-RT DDI slice first.** It is pairwise, measured as
   6,337 new pairs, and its row-level reference discriminator makes the clean subset
   selectable.
2. **Land `FDA-CYP` before SPL mining.** SPL already proved it needs potency-specific
   classes; mining first would either drop the band or build a temporary vocabulary.
3. **Land the generic non-firing toxicity projection with DIRIL first.** It addresses
   the thinnest existing coverage (four MED-RT renal `induces` rows) and has 315/317
   identifier reach, while forcing the parser and third-party-column exclusion rules.
4. Add DICTrank and DILIrank as second and third writers to the same projection.
5. Only after clinician review should any toxicity record be promoted into a curated
   class or condition assertion. QT review is part of that promotion, not ingest.

## 7. Verification gates for the implementation rounds

Every source implementation must prove all of these before it can be bundled:

- exact live URL, retrieval timestamp, SHA-256 and expected headers are recorded in
  `ingest_run` or source metadata;
- the parser fails on unknown enum values, missing required cells, duplicate source
  keys, or an unchanged checksum with changed counts;
- FDA-CYP reports combination rows, non-drug rows, unresolved substances and qualified
  memberships separately;
- no qualified FDA-CYP membership lands without its qualifier evidence;
- DIRIL persists none of the excluded third-party columns in §4.3;
- ambiguous multi-UNII DIRIL rows are recorded rather than exploded automatically;
- unresolved toxicity subjects reach the open-question register;
- `ddi_candidate_pair`, condition contraindication views, curated views and every
  clinician-facing alert count are byte-identical before and after a toxicity ingest;
- source clearing is scoped to one source and cannot delete another writer's rows;
- every new source spelling lands in the database CHECKs and
  `ids._SOURCE_CANONICAL` in the same migration;
- a second run at the same checksum is idempotent.

The core safety invariant is short: **ingest preserves evidence; curation creates
clinical judgement.** These sources are valuable precisely because Drugref can keep
those two operations separate.
