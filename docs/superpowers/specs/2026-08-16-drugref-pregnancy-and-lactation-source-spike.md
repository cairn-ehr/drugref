# drugref — pregnancy and lactation source import and utility spike

**Date:** 2026-08-16 · **Status:** computational spike executed; clinician review
pending · **Scope:** MED-RT as the existing floor; LactMed, AEMPS CIMA and the French
public medicines database (BDPM) as candidate bundled sources; source discovery and
measurement only. See the
[measured results](2026-08-16-drugref-pregnancy-and-lactation-source-spike-results.md).

Pregnancy and breastfeeding are not drug–drug interactions. They are population states
against which a medicine can carry a contraindication, a product-specific warning, a
measured exposure, an observed infant effect, a recommendation to monitor, or simply an
absence of evidence. Drugref already represents the categorical end of that spectrum:
MED-RT projects pregnancy and lactation as MeSH conditions. It does not yet have a place
for the source-native evidence that makes those statements clinically intelligible.

This spike asks whether three licence-compatible sources can add that evidence without
turning regulatory prose or research observations into an alert:

1. Can Drugref retrieve, reproduce and update the source deterministically?
2. Can the source subjects be resolved to the global moiety spine without silent
   name-only matches?
3. What clinically useful information is genuinely structured, and what remains prose?
4. How much does the source add beyond MED-RT's existing pregnancy/lactation floor?
5. What is the smallest non-firing projection that preserves the source's meaning for a
   later clinician-reviewed design round?

No schema, ingest command, clinical assertion or bundled source payload is added by this
spike. It produces measurements and a source decision. Any source that survives still
opens with its own design and implementation round.

## 1. The boundary: discover evidence, do not manufacture advice

MED-RT's `CI_with` relationship is already a candidate-tier contraindication. LactMed
and product information documents have different grains and different semantics:

| source | natural record | useful meaning |
| --- | --- | --- |
| MED-RT | moiety × MeSH condition × relationship | source asserts `CI_with` |
| LactMed | active drug or substance record × revision | reviewed lactation summary, levels, effects and alternatives |
| CIMA | authorised product × SmPC version × section | regulator-published product information in Spanish |
| BDPM | authorised product × RCP version × section | regulator-published product information in French |

The spike must preserve those grains. In particular:

- a product warning is not automatically a property of every formulation, route or dose
  of every ingredient it contains;
- a combination-product statement is not independently assigned to each component;
- `not recommended`, `use only if benefit outweighs risk`, `monitor`, `no data` and
  `contraindicated` are not synonyms;
- absence of a LactMed adverse-effect report is not evidence that a medicine is safe;
- a milk concentration, relative infant dose or animal reproductive-toxicity finding is
  evidence, not a clinical ruling;
- breastfeeding exposure, effects in the infant and effects on milk production are
  separate questions, not one `lactation` boolean; and
- pregnancy stage, postpartum age, infant maturity, route, dose and formulation remain
  attached when the source supplies them.

Nothing discovered here writes `curated_condition`, changes a MED-RT candidate, fires an
alert or emits a normalized recommendation. A later clinician-reviewed promotion may do
so with its own evidence, reviewer, signature and supersession chain.

## 2. Rule 6: provisional source decisions and the deeds still required

These are source-scope decisions, not claims that every item hosted on the same website
has the same rights.

| source key | source scope | provisional rule-6 decision |
| --- | --- | --- |
| `MED-RT` | existing `CI_with` pregnancy and lactation conditions | **Already bundleable**; no new source or licence surface |
| `NLM-LACTMED` | LactMed records authored and published by NICHD/NLM, excluding linked papers | **Candidate bundle**; US-government/public-domain basis, but capture the record-level rights evidence before download |
| `AEMPS-CIMA` | CIMA nomenclator, REST results and segmented SmPC content | **Candidate bundle**; AEMPS expressly places CIMA in its reusable open-data service |
| `ANSM-BDPM` | BDPM open data and official RCP information | **Candidate bundle**; Licence Ouverte permits redistribution, extraction, transformation and commercial use with attribution |

### 2.1 LactMed

The official collection identifies the publisher as the National Institute of Child
Health and Human Development and shows only a US Department of Health and Human Services
trademark attribution in its copyright panel:

<https://www.ncbi.nlm.nih.gov/books/NBK501922/>

NLM states that information created by or for the US government is public domain, while
warning that Bookshelf can also host contributed material with separate rights:

<https://www.ncbi.nlm.nih.gov/home/about/policies/>

That distinction is load-bearing. The deed must retain the copyright panel and rights
metadata from the actual LactMed bulk archive, identify NICHD/NLM authorship, and exclude
linked journal abstracts, figures and full text. A generic statement that "NCBI is public
domain" does not clear a payload.

The source also requires acknowledgement and preservation of the fact that LactMed is a
registered HHS trademark. Drugref must reproduce the source's medical disclaimer beside
any rendered LactMed-derived content.

### 2.2 CIMA

AEMPS defines its open data as information anyone may use, reuse and redistribute, with
attribution where required, and explicitly lists CIMA's nomenclator and REST service:

<https://sede.aemps.gob.es/datos-abiertos/>

The deed must archive that page, the CIMA REST documentation and any more specific legal
notice returned by the service. It must state that Drugref is not endorsed by AEMPS and
record the required attribution in `NOTICE` before any payload is bundled.

### 2.3 BDPM

The BDPM Licence Ouverte grants reproduction, publication, redistribution, adaptation,
extraction, transformation, derived works and commercial exploitation. It requires the
producer, source and last-update date, and forbids implying official endorsement:

<https://base-donnees-publique.medicaments.gouv.fr/docs/telechargement/licence_bdpm.pdf>

The public download page adds an accuracy obligation: do not alter or misrepresent the
meaning, and keep the data current:

<https://base-donnees-publique.medicaments.gouv.fr/telechargement>

The open licence is clear. The bulk files cover products, presentations, composition
and related metadata, but do not expose the full RCP narrative. The live spike found
server-rendered RCP pages on the official mobile site, keyed by CIS, with stable section
4.3 and 4.6 anchors. Production use still needs a documented retrieval rate, update
tests and a decision on whether this official route is sufficiently supported. A
third-party scrape or republished corpus does not substitute for an official source.

### 2.4 Sources explicitly outside this spike

| source | decision |
| --- | --- |
| openFDA / DailyMed submitted label prose | **Withhold**: FDA's default public-domain/CC0 terms exclude copyrightable private-party submissions; do not infer that hosting cures applicant rights |
| EMA product information | **Withhold** pending a source-specific derivative-use and third-party-rights deed |
| Australian TGA pregnancy database | **Do not bundle**: the website terms prohibit relevant commercial redistribution without permission |
| MotherToBaby fact sheets | **Do not bundle**: CC BY-NC-ND is incompatible with rule 6 |
| Wikidata pregnancy categories | **Do not import clinically**: CC0 but measured references are too weak for an answer-path source; discovery/QA only |
| DrugCentral `omop_relationship` | **Do not bundle**: no row provenance separates OMOP-derived from later label-derived assertions |
| commercial monographs (TERIS, Reprotox, Briggs, Hale, DrugBank) | **Node-local only** under their own licences |

Evidence for those boundaries:

- openFDA's terms preserve a private-party copyright exception:
  <https://open.fda.gov/terms/>;
- EMA's notice preserves third-party rights:
  <https://www.ema.europa.eu/en/about-us/about-website/legal-notice>;
- TGA's website copyright terms restrict commercial redistribution:
  <https://www.tga.gov.au/about-us/using-our-website/copyright>;
- a representative MotherToBaby record states CC BY-NC-ND 3.0:
  <https://www.ncbi.nlm.nih.gov/books/NBK582980/>;
- Wikidata property P3489 is the pregnancy-category property examined for discovery:
  <https://www.wikidata.org/wiki/Property:P3489>; and
- DrugCentral's row-provenance failure is recorded in the FDA interaction and toxicity
  [source spike](2026-08-16-drugref-fda-interaction-and-toxicity-source-spike.md#5-drugcentral-omop_relationship-the-provenance-audit-fails).

## 3. The existing floor: measure the delta, not an imaginary blank slate

The current MED-RT/MeSH projection already proves the patient-state model. On
`drugref_db038`, against MED-RT 2026.07.06, direct
`moiety_condition_contraindication` rows are:

| MeSH condition | direct rules | distinct moieties |
| --- | ---: | ---: |
| Pregnancy (`D011247`) | **549** | **549** |
| Lactation (`D007774`) | **66** | **66** |
| **combined** | **615** | — |

MED-RT also names narrower states, including first, second and third pregnancy trimesters
and ectopic or abdominal pregnancy. The spike must re-run these queries against the named
verification database and pin the MED-RT release before comparing another source.

For every candidate source, report at least:

- resolved moieties already carrying a direct MED-RT pregnancy rule;
- resolved moieties already carrying a direct MED-RT lactation rule;
- resolved moieties absent from each MED-RT set;
- records that cannot be reduced to a moiety, such as foods, botanicals, procedures,
  radiopharmaceutical regimens or multi-ingredient products; and
- source records whose subject resolves but whose useful statement remains product-,
  route-, dose- or stage-specific.

"New to MED-RT" is a discovery population, not proof of a missing contraindication.

## 4. Reproduction manifest

Every retrieval writes a manifest before parsing:

```text
source_key · source_url · retrieved_at · response_media_type
source_release_or_revision · payload_sha256 · payload_bytes
licence_url · licence_sha256 · parser_version
```

The spike must retain only the small, reviewable fixtures allowed by the source terms;
full source downloads stay in an ignored temporary directory. A fetch with no release ID
uses retrieval time plus checksum as its immutable identity.

### 4.1 LactMed retrieval experiment

Use the collection's official bulk link, not HTML crawling. The collection currently
links to this NLM Literature Archive tarball from its record-format page:

<https://www.ncbi.nlm.nih.gov/books/NBK547442/>

<https://ftp.ncbi.nlm.nih.gov/pub/litarch/90/6c/lactmed_NBK501922.tar.gz>

The live table of contents exposed **1,929 items** on 2026-08-16. The archive was
**208,950,187 bytes**, with HTTP `Last-Modified: 2026-07-21 06:45:55 GMT`, and contained
1,950 XML members: 1,940 evidence records with a source-native lactation summary and 10
collection/support documents. The 11-record archive/HTML discrepancy is retained as an
upstream observation. These counts are change detectors, not a release identity; the
retrieved payload is identified by its own SHA-256 in the measured report.

The parser experiment must enumerate every XML record without loading the archive into
memory and report:

- record count, duplicate record numbers and missing revision dates;
- title, scientific name, synonyms, CAS Registry Number, drug class, record number and
  last revision date availability;
- presence of Summary of Use During Lactation, maternal levels, infant levels, effects
  in breastfed infants, effects on lactation/breastmilk and alternative drugs;
- section markup stability across a stratified fixture; and
- references as citations only, without retrieving or redistributing the cited works.

The official format says the title is normally the USAN active portion, usually without
the salt; CAS is the parent compound; and records can combine racemates/isomers or cover
botanicals. Those are identity warnings, not conveniences.

### 4.2 CIMA retrieval experiment

The documented base is `https://cima.aemps.es/cima/rest/`. The REST specification exposes
JSON in UTF-8, product lookup, active ingredients, a change register, and segmented
document sections:

<https://www.aemps.gob.es/apps/cima/docs/CIMA_REST_API.pdf>

For each sampled authorised medicine:

1. retrieve the product record and retain `nregistro`, authorization/commercial state,
   active-ingredient rows, strength, dosage form, route and document revision;
2. retrieve the section list for document type 1 (Ficha Técnica / SmPC);
3. retrieve sections 4.3 and 4.6 as JSON or plain text, preserving the section number,
   title and upstream HTML separately;
4. hash the normalized section and the raw response independently; and
5. exercise `registroCambios` to prove incremental refresh and withdrawal handling.

CIMA exposes local active-ingredient IDs and codes. They may be retained as
`AEMPS-CIMA` source identifiers. Do not ingest the service's ATC or SNOMED identifiers:
an open wrapper does not erase the upstream vocabulary's separate rights.

### 4.3 BDPM retrieval experiment

Use the official monthly bulk files to establish products, presentations and composition.
The experiment established an official CIS-keyed, server-rendered RCP route on
`m.base-donnees-publique.medicaments.gouv.fr`. The target sections are 4.3 and 4.6, not
an unbounded scrape of every document.

The live probe established:

- an official record key linking composition to the RCP;
- a stable official URL or endpoint;
- section boundaries or a reproducible parser for section 4.3/4.6;
- a source revision where the RCP page supplies one.

It did not establish production rate limits or a change feed for RCP narrative. Those
remain prerequisites for a production importer, even though the computational utility
experiment could proceed from a bounded deterministic sample.

No community-enriched BDPM export enters the experiment unless its own row-level
provenance proves each field came from the licensed official source.

## 5. Identity experiment: exact claims first, names only as a measured lower bound

The spike does not add identity claims. It produces a resolution worklist against the
existing registry.

Resolution order:

1. **Exact existing claim:** a source-supplied UNII or CAS that matches one live Drugref
   identity claim.
2. **Exact source crosswalk already owned by Drugref:** only where its provenance and
   licence are independently cleared.
3. **Normalized exact name candidate:** case-fold, trim and collapse whitespace for a
   coverage measurement, but mark the result `candidate_name`; never auto-admit it.
4. **Form/composition review:** salts, solvates, esters, racemates, metabolites,
   biologics, vaccines, botanicals, radiopharmaceuticals and combination products.

Every source emits disjoint counts:

```text
resolved_exact_claim
candidate_unique_name
ambiguous_name
combination_or_regimen
unsupported_entity_kind
unresolved
```

The report must show examples from every non-empty bucket. A percentage that silently
drops combinations, botanicals or ambiguous names fails the experiment.

For CIMA and BDPM, resolve each ingredient but preserve the product as the assertion
scope. A two-ingredient product can have two resolved ingredients and still have **zero**
moiety-level statements safe to project.

## 6. Utility experiment: what can a clinician actually discover?

The spike records source-native evidence locations, not automatically assigned clinical
categories. A deterministic keyword or heading search may build the review sample, but
its hits and misses must be reported and it cannot become the import contract.

### 6.1 LactMed utility

Measure:

- how many resolved records contain maternal milk levels, relative infant dose, infant
  serum/urine levels, reported infant effects, lactation/milk-supply effects and
  alternative medicines;
- how often dose, route, postpartum timing, stage of lactation, infant age or prematurity
  is present in the useful passage;
- how often the summary contains conflicting evidence or conditional wording;
- how many records describe no direct human measurement; and
- how many record titles cover combinations, non-drug exposures or multiple molecular
  forms that cannot map one-to-one to a moiety.

The result should answer whether LactMed is useful only as linked narrative, or whether
some quantitative observations can be projected without losing their denominators,
units, timing and citation.

### 6.2 CIMA and BDPM utility

For each regulator, measure:

- products with section 4.3, section 4.6, or both;
- distinct ingredient sets and Drugref-resolved ingredient sets;
- pregnancy, trimester, fertility, lactation, breastfeeding and infant concepts found
  by a documented discovery query;
- product statements that are formulation/route/dose-specific;
- combinations whose statement cannot be allocated to one component;
- duplicate or near-duplicate sections across products with the same ingredient set;
- conflicting or materially different passages across products sharing an ingredient;
  and
- change/withdrawal frequency observable through the official update mechanism.

Cross-source comparison is performed only after preserving product scope. A difference
between CIMA and BDPM is a review question, not evidence that one source is wrong.

### 6.3 Stratified clinical review

The spike must include a clinician-readable sample chosen before reviewing the answers:

- direct MED-RT pregnancy overlap;
- direct MED-RT lactation overlap;
- resolved but absent from the relevant MED-RT set;
- first/second/third-trimester language;
- route- or dose-qualified language;
- combination products;
- LactMed quantitative milk or infant levels;
- reported infant effects and effects on milk production; and
- explicit uncertainty, conflict or absence-of-data language.

For each sampled record, the reviewer records whether the extracted boundary is correct,
whether identity and product scope are preserved, what new question it permits Drugref
to ask, and whether any normalization would be clinically unsafe. The spike does not ask
the reviewer to author final Drugref advice.

## 7. Provisional non-firing shape to test, not yet a schema

The experiments write a source-neutral line-delimited JSON file so the grains can be
compared without opening a migration:

```text
source · source_record_key · source_revision · source_url
record_kind · raw_subject · resolved_moieties[] · resolution_status
product_key · ingredient_scope[] · formulation · route · dose
population_context · context_qualifier
section_code · section_title · evidence_kind
raw_text · raw_text_sha256 · references[]
retrieved_at · ingest_manifest_key
```

`population_context` is only `pregnancy`, `lactation` or `mixed` in the spike.
`context_qualifier` preserves source text such as trimester, peripartum, postpartum or
infant maturity; it is not normalized to a clinical ontology yet.

`evidence_kind` is source-structural, not interpretive:

- `source_contraindications_section`
- `source_pregnancy_lactation_section`
- `lactmed_summary`
- `maternal_level`
- `infant_level`
- `infant_effect`
- `lactation_effect`
- `alternative_medicine`

There is deliberately no `safe`, `avoid`, `severity`, `recommendation` or `fires` field.
If the trial file cannot represent a source without adding one, the spike reports the
missing source distinction instead of inventing a universal vocabulary.

Raw narrative is retained only in the temporary spike output and small fixtures. Whether
Drugref should later bundle full source text, bounded source sections, or only derived
facts plus links is a separate product and licence decision.

## 8. Required report and acceptance criteria

The spike is complete when one checked-in report gives, for each source:

1. **Licence deed:** exact payload scope, upstream terms, attribution, disclaimer,
   excluded fields/content and reviewer decision.
2. **Reproduction:** official URLs, retrieval time, revision, byte size and SHA-256.
3. **Parse account:** input records, emitted records, malformed/skipped records and a
   reconciliation proving nothing disappeared silently.
4. **Identity account:** every bucket in §5, with denominators and examples.
5. **Clinical-grain account:** single moiety, multi-moiety product, non-drug exposure,
   formulation/route/dose qualifiers and pregnancy/lactation subcontexts.
6. **MED-RT delta:** overlap, additions-to-review and contradictions-to-review, never
   labelled automatic additions or corrections.
7. **Utility account:** the source-native evidence fields and section coverage in §6.
8. **Update account:** full rebuild, incremental refresh, withdrawal and changed-text
   behaviour under an unchanged record key.
9. **Failure fixture:** at least one ambiguous identity, combination product, qualified
   statement and uncertainty statement that a naive importer would flatten wrongly.
10. **Decision:** `design next`, `defer`, `node-local` or `reject`, with the measured
    reason and estimated implementation surface.

No minimum match percentage is set before measurement. A narrow source can be valuable
if it adds otherwise unavailable evidence, and a high-coverage source can still fail if
its rights, identity, update path or clinical grain are not auditable.

## 9. Recommended execution order

1. **Freeze the MED-RT floor.** Reproduce the 615 direct pregnancy/lactation rows and
   enumerate narrower pregnancy states against the current release.
2. **Run LactMed first.** It has an official bulk archive, a documented record format and
   the clearest breastfeeding-specific utility question.
3. **Run CIMA second.** Its REST API, segmented SmPCs and change register make it the best
   candidate for measuring product-level pregnancy and lactation evidence.
4. **Run the BDPM access probe third.** Its licence is clean; the official RCP route was
   sufficient for a bounded utility sample, while production sustainability remains a
   design prerequisite.
5. **Compare, then design.** Choose projection grains from the measured records. Do not
   make three unlike sources fit a schema chosen from the first one.

The likely implementation order after a successful spike is therefore LactMed as a
breastfeeding evidence projection, CIMA as product-scoped regulatory evidence, then BDPM
as a corroborating second jurisdiction. That is a hypothesis for the spike to test, not
an implementation commitment.

## 10. Open questions the spike must leave answered or explicitly blocked

1. Does the current LactMed bulk payload contain an explicit record-level public-domain
   marker, or must the deed rest on demonstrated NICHD/NLM authorship plus the collection
   copyright panel?
2. Does AEMPS's open-data declaration cover the complete segmented SmPC content as well
   as REST metadata, and what exact attribution text should `NOTICE` carry?
3. Is the official BDPM mobile RCP route supported for production retrieval, and what
   rate and change-detection contract should Drugref use?
4. Which source identifiers may be redistributed without carrying ATC, SNOMED CT or
   another separately encumbered vocabulary through an open wrapper?
5. Can LactMed's quantitative observations be represented with their units,
   denominators, timing, population and citation, or should the first release keep them
   as reviewed narrative sections only?
6. Is product identity needed in the global tier, or can a source-product key remain
   scoped entirely inside a rebuildable evidence projection?
7. What exact evidence grain should open a Drugref question: source record, section,
   ingredient set, moiety plus population context, or a clinician-selected passage?
8. How will public consumers distinguish upstream regulatory language, upstream expert
   synthesis and Drugref's own signed clinical judgement without conflating them in one
   recommendation field?

Until those questions are answered, MED-RT remains the only pregnancy/lactation source
on Drugref's candidate answer path. The new sources are evidence-discovery candidates,
not permission to broaden that path.
