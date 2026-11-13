# A drug is matched by its US label spelling, not only its registry name

**Status:** Active
**Last reviewed:** 2026-11-14
**Applies to:** the SPL drug pass (`drugref.spl_entity_occurrence`, `drugref.spl_ddi_pair`) and its match
vocabulary (`drugref.ingest.spl_official_names`, `src/drugref/data/spl_official_name_exclusions.txt`); the
searchable names (`drugref.moiety_name`, `drugref.moiety_official_name`, `drugref.moiety_mesh_synonym`)
**Full derivation:** issues [#213](https://github.com/cairn-ehr/drugref/issues/213) and
[#231](https://github.com/cairn-ehr/drugref/issues/231); PROJECT-NOTES § "The US-label-spellings round", §
"The moiety-names round" and § "The MeSH-synonyms round"

## Context

drugref names each moiety once, with its registry display name, which is usually the INN: *paracetamol*,
*rifampicin*, *glibenclamide*, *salbutamol*. The SPL drug pass finds drugs in a label's interactions section by
matching those names. US labels use the USAN instead: *acetaminophen*, *rifampin*, *glyburide*, *albuterol*.

Measured over the 27,406 distinct interaction wordings of the 2026-08-22 openFDA corpus, the drug pass never
saw 13,693 *rifampin* spans, 6,588 *acetaminophen*, 2,440 *glyburide*, 1,135 *albuterol*, and so on. Official
names that are not display names account for 35,901 spans across 85 names. That total also counts names this
decision keeps out (*glucose*, *secretin*) and salt names the pass already saw in part (*esomeprazole
magnesium* as *esomeprazole*). Rifampin is one of the strongest CYP3A4 inducers there is, so missing it is serious.

## Decision

**Every UNII official name (UNII_Names `TYPE = of`) of a substance the registry holds is also a match key for
that substance's moiety**, with four rules:

- the registry's own display name always wins its key (*epinephrine* stays the L-isomer, although it is also an
  official name of the racemate);
- a name two moieties claim is dropped. drugref never settles that kind of ambiguity by "first wins". The same
  goes for a name of a UNII that two moieties claim;
- a short, reviewed exclusion list keeps out official names that are ordinary words in a label, each justified
  by a measured context distribution: *glucose* (blood glucose, glucose tests), *retinal* (the adjective), *pca*
  (patient-controlled administration), *glycol* (ethylene glycol's official name, and in a label the tail of *polyethylene glycol*);
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
- The same names are stored for searching (1,630 official names in `drugref.moiety_official_name` on the 26 Feb
  2026 release, searched through the `drugref.moiety_name` view), so a clinician who types *acetaminophen* finds
  paracetamol. Because the label matcher and the search share one rule, the exclusions apply to search as well:
  *glucose* does not find dextrose. The stored names are a snapshot of the registry, so they are rebuilt after
  every UNII ingest; `drugref status` warns when they are older than it.
- British and Australian names (*adrenaline*, *lignocaine*, *frusemide*) are not UNII official names: UNII lists
  them only as common names, alongside impurity and salt names. They come from MeSH instead, as the entry terms of
  each drug's preferred concept, linked by that concept's own UNII (`drugref.moiety_mesh_synonym`, name kind
  `mesh_synonym` in the view). Those names are for search only and never match a label, because they include
  misspellings and abbreviations (*Acetominophen*, *APAP*). A search name may match several moieties (MeSH's
  *Codeine* is the registry's *codeine anhydrous*, while another moiety is displayed *codeine*); the search shows
  them all and the clinician chooses. (The label matcher, which never sees these names, still drops an official name
  that two moieties share.)

## Related

- [A label naming two drugs is evidence, not an assertion](evidence-is-not-an-assertion.md): what the pass's
  pairs mean.
