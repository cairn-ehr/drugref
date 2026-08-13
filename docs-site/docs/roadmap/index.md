# Roadmap

drugref's **global tier** is built bottom-up — substance identity → chemistry → classes
→ interactions — followed by the public API and the local (country-specific) tier. It is
an advisory reference-data service and never sits on Cairn's signed inter-node wire core.

This is a reader-friendly summary; the working roadmap lives in the
[repository](https://github.com/cairn-ehr/drugref/blob/main/docs/ROADMAP.md).

## Built so far

- **Identity spine** — active-moiety registry with immortal UUIDs and append-only
  cross-reference claims, seeded from UNII / ChEBI / INN / RxNorm.
- **Classification** — a source-neutral drug-class registry with MED-RT (mechanism of
  action, physiologic effect, therapeutic class, and more) and MeSH pharmacologic
  actions, plus moiety↔class membership.
- **First interaction data** — MED-RT mechanism/effect contraindications as a rebuildable
  projection, expanded to candidate drug–drug pairs at read time. Candidate tier only —
  nothing here auto-fires a prescriber alert.
- **Open-question registry** — coverage gaps published as a queryable register that
  shrinks as coverage improves.
- **MeSH-keyed contraindications** — drug–disease contraindications over a MeSH condition
  registry, expanded **down** the disease DAG so a rule written against *Epilepsy* reaches
  a patient coded *Temporal Lobe Epilepsy*.
- **MeSH-keyed indications** — what a drug is *for* (`may_treat` / `may_prevent` /
  `may_diagnose`) and what it *causes* (`induces`), over that same registry. Generalised
  **up** the DAG at read time and never down, and never stored derived — see
  [An indication does not expand down the disease tree](../decisions/indications-do-not-expand.md).
- **Composition tree** — which registered moieties a specific substance (a salt, a
  hydrate) is composed of, and which of them the release marks pharmacologically
  active, from the FDA/NCATS GSRS public data dump — see
  [GSRS relationship direction](../decisions/gsrs-relationship-direction.md).
- **The curated overlay's floor (the moat's foundation)** — an append-only correction
  mechanism (supersession, never overwrite) shared by every curated table, and the
  overlay's first assertion shape: `curated_interaction`, keyed on the class-level
  rule, and `curated_condition`, keyed on the (drug, condition) pair — see
  [Curating a drug–condition pair](../decisions/curating-a-drug-condition-pair.md).
  It also holds the accumulation model (many drugs, one effect that adds up) and
  role-based interaction groups. All of it ships **empty by design**, with its worklists
  published as queryable gap views: against the current releases, 168 drug–condition
  pairs asserted as both an indication and a contraindication, and 595 interaction rules
  awaiting severity grading.
- **Signing the curated overlay** — curator-held Ed25519 keys signing individual
  judgements, an institutional key signing a per-release content manifest that catches
  omission as well as alteration, a key registry with time-scoped and blanket
  revocation, and `drugref keys | sign | verify | publish` over both. A signature is
  published as metadata and **never gates a read** — see
  [signing the curated overlay](../decisions/signing-the-curated-overlay.md).
- **Local-tier proof (Australia)** — a minimal PBS product layer bridged to the
  global moiety spine by name, the only licence-clean join, proving the local-tier
  pattern of jurisdiction scoping and structural encumbrance quarantine.
- **Operator tooling** — a `drugref` command-line interface (`migrate`, `status`,
  `ingest chain`, `policy record|withdraw|show`), crash-visible ingest provenance,
  and a CI gate running the full test suite and lint on every change.

- **First curated content** — the ONC high-priority drug–drug interaction list
  (Phansalkar 2012) as drugref's first curated rows, carrying severity, mechanism,
  management and evidence grading, and signed as they are written. The list enters as
  a **second candidate source** beside MED-RT rather than as bare assertion, so the
  provenance of every graded pair stays answerable.

    Four of the list's fifteen entries shipped, and the reason the other eleven did not
    is the more useful part. A class-level rule inherits its population from the
    source's class boundary, and that is only trustworthy when the class was defined by
    the same mechanism the interaction runs on — "CYP3A4 inhibitors" genuinely *is* the
    population an irinotecan exposure interaction runs over. A *therapeutic* class is
    not: "opioid agonists" conflates two different interaction mechanisms and includes
    loperamide, whose action is largely confined to the gut. Rules built on those
    classes were withheld pending literature review rather than published on the
    strength of a borrowed taxonomy. **Curated content is only worth its provenance if
    the population is right, not merely the pair.**

## Next
- **Clinical drugs** — moiety + strength + form, built on the composition tree.
- **Public HTTP API** — the co-equal-consumer interface; any EHR / pharmacy / app on the
  same footing.
- **Local tier, continued** — pricing, restriction texts, TGA ARTG, and the same
  pattern in other jurisdictions. drugref ships ingest code and schema only — each
  node supplies its own nationally-licensed data.
