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

## Next

- **Composition tree** — specific substances (salts / esters / hydrates), then clinical
  drugs (moiety + strength + form).
- **The curated overlay (the moat)** — an append-only, signed layer adding severity,
  mechanism, management, and evidence grading on top of the candidate interaction rows.
- **Public HTTP API** — the co-equal-consumer interface; any EHR / pharmacy / app on the
  same footing.
- **Local tier** — country-specific packaging and pricing (Australia first: PBS + TGA
  ARTG), with nationally-licensed terminologies attached per node.
