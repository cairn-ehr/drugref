# Data sources & licensing

Every fact in drugref traces to a public-domain or openly licensed upstream release, and
the entire database can be rebuilt from those sources by anyone. Only
**AGPL-compatible** sources are ever bundled — see the decision record on [licensing as a
blocker](../decisions/licensing-is-a-blocker.md).

## Bundled sources

| Source | What it provides | Licence |
| --- | --- | --- |
| **UNII** (FDA/NCATS) | The global substance-registration backbone; the identity anchor | Public domain |
| **GSRS public data dump** (FDA/NCATS) | Salt/solvate composition and active-moiety relationships (the composition tree) | CC0 1.0 Universal, *unless otherwise noted* — no exception found on any ingested record |
| **ChEBI** | Chemical entities of biological interest; chemistry + cross-references | CC BY 4.0 |
| **MED-RT** | Mechanisms of action, physiologic effects, therapeutic classes, contraindications, and MeSH-keyed indications (`may_treat` / `may_prevent` / `may_diagnose`) and drug-induced states (`induces`) | Public domain (US NLM / VA) |
| **MeSH** | Pharmacologic actions and descriptors | Public domain / NLM terms |
| **RxNorm** | Normalised drug names and codes | Openly redistributable subset |
| **FDA CYP/transporter table** | FDA's own examples of CYP and transporter substrates, inhibitors and inducers, by potency band | Public domain (US FDA website policy) |
| **DrugCentral `ddi`** — the NDF-RT half only | Drug–drug interaction pairs at moiety grain, each carrying the VA's own severity band | CC BY-SA 4.0 over DrugCentral's compilation; the ingested rows are a US federal work (VHA NDF-RT) |
| **SPL section 34073-7** (openFDA `drug/label` + DailyMed) | Which known drugs each US prescription label's DRUG INTERACTIONS section *names*, with character offsets, a citation and a bounded quoted window | CC0 1.0 as published by openFDA; NLM disclaims copyright status over the same labeling, so clearance was decided per column |

The GSRS *software*, which drugref neither uses nor redistributes, is separately licensed
Apache-2.0; only the public data dump is bundled.

FDA asks downstream users of fda.gov content to record when they copied it and to link the
live page, because it is revised in place. drugref records the retrieval timestamp, the
page's own `dateModified` stamp and a SHA-256 of the fetched bytes on every ingest run —
which is also what lets a re-fetch of unchanged material be told apart from a genuine
revision. Its table is an *optional, non-exhaustive* interpretive guide, so drugref stores
the memberships FDA states and never joins its columns into interaction pairs FDA does not.

DrugCentral is the one bundled source whose licence question had to be answered **per
reference rather than per source**. Its `ddi` table cites three references, and its own
CC BY-SA licence over the compilation is not evidence of a right to relicense a
third-party compendium inside it — so drugref reads the dump's own `reference` table and
ingests only `ddi_ref_id = 2`, the VHA's NDF-RT. *Stockley's Drug Interactions* (a
copyrighted book) and Lexicomp Online (a commercial compendium) are excluded. Because that
`2` is a surrogate key in a database dump published once, the constant is not trusted on
its own: every ingest re-reads the reference row and **aborts** unless its recorded authors
and title still match, so a re-published dump that renumbered its references stops rather
than bundling an excluded one.

SPL is the source whose licence question had to be answered **per column rather than per source**, because its two publishers take opposite positions on the same bytes: openFDA dedicates the bulk `drug/label` export CC0 1.0, while NLM/DailyMed disclaims — *"cannot guarantee the copyright status for any item"* — over labeling submitted to the FDA by companies. Entity occurrences and character offsets are facts and are clear under either reading; a `set_id`/`version` citation is a citation, not a copy; **the section text in full is never stored**; and a **bounded quoted window** — ±60 characters around the first occurrence of each named drug, to a hard cap of 25% of the section — is bundled under drugref's own determination and enforced by a database trigger rather than by convention. Measured on the 2026-08-22 release, 20.5% of a section is stored on average. See [bundling a quoted window](../decisions/bundling-a-quoted-window.md).

Upstream attributions are recorded in the repository's
[`NOTICE`](https://github.com/cairn-ehr/drugref/blob/main/NOTICE) file.

## The licence rule

Licensing is a hard rule, not an afterthought. Every dependency and every bundled
reference-data source must be AGPL-3.0-compatible, **checked before it is added**.
Licence-encumbered national or commercial sources — ATC, SNOMED CT / AMT, ICD-10-AM,
eTG, AMH, commercial DrugBank — attach only as **node-local, separately-licensed
plug-ins**. They never contaminate the commons.

## Notable exclusion

**ATC** (the WHO Anatomical Therapeutic Chemical classification) is **not** bundled: its
licence is NonCommercial and NoDerivatives, incompatible with an openly redistributable
commons.

## Evaluated for the interaction tier, and what came of each

Candidate sources are checked against the licence rule *and* against what they actually
contain. Both checks matter, and only the first one is about licences:

| Candidate | Licence | Outcome |
| --- | --- | --- |
| **DDInter** | CC BY-NC-SA — NonCommercial | **Excluded.** Not AGPL-compatible; node-local plug-in only |
| **OnSIDES** (data) | CC BY 4.0 — clean | **Not a fit.** Its unit is one label × one MedDRA adverse-effect term; there is no drug–drug pair anywhere in its shipped data. Its MIT-licensed *method* remains the model for label mining |
| **DrugCentral** `ddi` | CC BY-SA 4.0 | **Bundled, partially — shipped 2026-08-23.** 7,571 of its 7,621 interaction rows (dump `11012023`, `dbversion` 54) come from the VHA's NDF-RT (US federal, clean) and are ingested, yielding 7,501 distinct drug pairs. The remaining 50 cite *Stockley's Drug Interactions* (a copyrighted book) and Lexicomp (commercial) and are **excluded** — a share-alike licence over a compilation is not evidence of the right to relicense a third-party compendium inside it. The release is pinned to November 2023, with no successor published, so it is a floor that does not refresh |
| **CredibleMeds** (QT risk) | Registration-gated, not redistributable | **Excluded.** No open, redistributable QT-prolongation list is published by FDA, EMA or BfArM either |

The pattern worth stating plainly: **a source can be perfectly licence-clean and still be
the wrong source.** OnSIDES cleared the licence gate and failed on content; DrugCentral
cleared it only for the part of its content whose provenance was actually checked, and only
that part is bundled.

## Documentation licence

The prose on this documentation site is licensed **CC BY-SA 4.0**, distinct from the
project's code, which is **AGPL-3.0**.
