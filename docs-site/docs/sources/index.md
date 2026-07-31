# Data sources & licensing

Every fact in drugref traces to a public-domain or openly licensed upstream release, and
the entire database can be rebuilt from those sources by anyone. Only
**AGPL-compatible** sources are ever bundled — see the decision record on [licensing as a
blocker](../decisions/licensing-is-a-blocker.md).

## Bundled sources

| Source | What it provides | Licence |
| --- | --- | --- |
| **FDA GSRS / UNII** | The global substance-registration backbone; the identity anchor | Public domain |
| **ChEBI** | Chemical entities of biological interest; chemistry + cross-references | CC BY 4.0 |
| **MED-RT** | Mechanisms of action, physiologic effects, therapeutic classes, contraindications, and MeSH-keyed indications (`may_treat` / `may_prevent` / `may_diagnose`) and drug-induced states (`induces`) | Public domain (US NLM / VA) |
| **MeSH** | Pharmacologic actions and descriptors | Public domain / NLM terms |
| **RxNorm** | Normalised drug names and codes | Openly redistributable subset |

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

## Documentation licence

The prose on this documentation site is licensed **CC BY-SA 4.0**, distinct from the
project's code, which is **AGPL-3.0**.
