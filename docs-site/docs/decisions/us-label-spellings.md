# A drug is matched by its US label spelling, not only its registry name

**Status:** Active
**Last reviewed:** 2026-11-10
**Applies to:** the SPL drug pass (`drugref.spl_entity_occurrence`, `drugref.spl_ddi_pair`) and its match
vocabulary (`drugref.ingest.spl_official_names`, `src/drugref/data/spl_official_name_exclusions.txt`)
**Full derivation:** issue [#213](https://github.com/cairn-ehr/drugref/issues/213) and PROJECT-NOTES § "The
US-label-spellings round"

## Context

drugref names each moiety once, with its registry display name, which is usually the INN: *paracetamol*,
*rifampicin*, *glibenclamide*, *salbutamol*. The SPL drug pass finds drugs in a label's interactions section by
matching those names. US labels use the USAN instead: *acetaminophen*, *rifampin*, *glyburide*, *albuterol*.

Measured over the 27,406 distinct interaction wordings of the 2026-08-22 openFDA corpus, the drug pass never
saw 13,693 *rifampin* spans, 6,588 *acetaminophen*, 2,440 *glyburide*, 1,135 *albuterol*, and so on: 35,901
spans across 85 names. Rifampin is one of the strongest CYP3A4 inducers there is, so missing it is serious.

## Decision

**Every UNII official name (UNII_Names `TYPE = of`) of a substance the registry holds is also a match key for
that substance's moiety**, with four rules:

- the registry's own display name always wins its key (*epinephrine* stays the L-isomer, although it is also an
  official name of the racemate);
- a name two moieties claim is dropped. drugref never settles that kind of ambiguity by "first wins";
- a short, reviewed exclusion list keeps out official names that are ordinary words in a label, each justified
  by a measured context distribution: *glucose* (blood glucose, glucose tests), *retinal* (the adjective), *pca*
  (patient-controlled administration), *glycol* (the tail of *propylene glycol*);
- *secretin stimulation* is a suppression term: 497 of 503 *secretin* spans name the lab test.

UNII_Names is FDA public-domain data, so this adds no licensing question. The ingest already reads the file for
the class pass.

## Consequences

- On the 2026-08-22 corpus, drug × drug pairs rise from 29,952 to 30,624 and novel pairs from 26,598 to 27,194.
  Rifampicin's partners go from 143 to 500, paracetamol's from 97 to 203. Twenty-six false `sodium`/`magnesium`
  pairs go away, because *sodium aurothiomalate* and *esomeprazole magnesium* now take the whole span. The
  ingest's pair and novel floors were raised to those figures.
- A new official name can arrive with any UNII release, so the ingest prints how many names it adopted,
  shadowed, dropped as ambiguous and excluded on every run.
- An entry on the exclusion list is a precision judgement and needs its measurement next to it, the same rule
  as the suppression list.

## Related

- [A label naming two drugs is evidence, not an assertion](evidence-is-not-an-assertion.md): what the pass's
  pairs mean.
