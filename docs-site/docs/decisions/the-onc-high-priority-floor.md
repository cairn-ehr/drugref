# The ONC high-priority floor is facts, not text

**Status:** Active
**Last reviewed:** 2026-08-11
**Applies to:** the `ONCHIGH` candidate source (`source = 'ONCHIGH'` in `class_contraindication` /
`ddi_candidate_pair`, `db/031`); `src/drugref/data/onc_high_priority.toml`; every `curated_interaction` row
graded against it; `NOTICE`
**Full derivation:** the [slice-5c.2 design
spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-08-11-drugref-slice-5c2-onc-ddi-floor-design.md)
§10 ("Rule 6, discharged in writing") and §2 (why the ONC list enters as a second candidate source)

## Context

5c.2 is drugref's first slice to write clinical content — `mechanism`, `management`, `severity` — into the
curated overlay. Its content is the ONC high-priority drug–drug interaction list: a small, consensus-derived
set of interactions a prescribing system should never miss, identified by Phansalkar et al. (2012) under a
U.S. Office of the National Coordinator for Health IT (ONC) contract and updated by the Ayvaz/Boyce (2015)
follow-up. Rule 6 ([Licensing is a blocker](licensing-is-a-blocker.md)) makes the licence question a blocker
rather than a cleanup item, and every other source in `NOTICE` clears it against an explicit grant — CC0, CC
BY, or a stated public-domain/government-work status. The ONC list has none of that on its face: it is two
peer-reviewed journal papers, and drugref has found no licence file, no dedication, nothing to point at the
way UNII's CC0 or MeSH's terms-of-use are pointed at elsewhere in `NOTICE`.

That absence does not by itself block the slice, because the thing drugref needs a licence for and the thing
the papers' copyright covers are not the same thing — the argument below is why.

This repository already carries two open rule-6 deeds of a different shape: [issue
6](https://github.com/cairn-ehr/drugref/issues/6) (MED-RT) and [issue
25](https://github.com/cairn-ehr/drugref/issues/25) (PBS), each a placeholder for a written confirmation from
the source's own custodian, filed because the existing analysis rested on indirect signals — statute,
distribution channel, restriction tier — rather than a primary licence document. This record is not that
shape of gap. There is no custodian to write to and no licence document that would settle the question,
because the argument here does not turn on how RAND or ONC licensed their output. It turns on what a drug
interaction fact is, which is a determination this project can make itself, in writing, now — while staying
as honest about its limits as issues 6 and 25 are about theirs.

## Decision

**The pairs are facts, and facts are not copyrightable — that argument carries the whole weight, on its
own.** *Feist Publications, Inc. v. Rural Telephone Service Co.*, 499 U.S. 340 (1991), held that copyright
protects an author's original expression, never the facts an author reports, however much labour went into
discovering them. "Warfarin and an NSAID interact, by this mechanism, at this severity" is a fact about
pharmacology, not Phansalkar's or Ayvaz's prose describing it, and drugref never takes their prose. Under
Feist, that fact is drugref's to re-state — on the same footing UNII's identifiers or MeSH's descriptors
already stand on elsewhere in `NOTICE` — and the argument would hold even if the papers carried the
strictest all-rights-reserved notice, because copyright was never the thing standing between drugref and the
fact.

**The public-funding history is real, and it is not what this determination rests on.** The ONC list was
produced under a federal contract performed by RAND, and RAND's government-contract terms grant the U.S.
government an irrevocable licence to reuse the work — worth recording, and why ROADMAP already describes the
floor as "re-encoded from the papers; RAND/government-licensed." But a government-use licence is a grant *to
the government*, not a public licence to drugref, and leaning on it alone would leave the determination one
contract clause away from being wrong. The facts argument has no such dependency, which is why it is stated
first here and carries the weight; the funding history corroborates it but does not substitute for it.

**The re-encoding discipline is what keeps the facts argument true in practice, not just in principle**, and
it binds every entry the next task writes:

- **No verbatim text from either paper enters any field, ever.** Reproducing the papers' own sentences would
  copy their expression, not their facts, and Feist stops protecting the moment prose is quoted rather than a
  fact re-stated.
- **drugref authors every `mechanism` and `management` string itself.** That is what makes the string
  drugref's own judgement rather than a quotation — the same act that keeps it a fact-derived re-statement
  also makes it drugref's original expression, on the identical Feist logic that denies the papers a monopoly
  over the underlying fact.
- **Each entry cites its source paper** (the candidate's `citation` field), so a claim's provenance is
  inspectable by anyone auditing whether a given `mechanism` string strayed from re-statement into quotation.

**What would change the answer.** Two things, neither of which this slice does: bundling the papers' own
prose in any field would trade a facts argument for an ordinary infringement exposure, with no Feist defence
available; and treating the papers' *selection* of these particular pairs as the thing drugref reproduces
would raise a compilation-copyright question Feist leaves open — a compilation can be copyrightable in its
selection and arrangement even when every fact inside it is free. drugref does not redistribute the papers'
list as a compilation: each pair is graded independently through the curated overlay, on drugref's own
severity/evidence scale, against drugref's own authored prose, so what ships is drugref's judgement about
facts the papers helped surface — not the papers' compilation, republished.

## Consequences

- **Task 2 onward inherits this as a hard constraint, not a preference.** A step — automated or manual — that
  pastes paper text into `mechanism` or `management` breaks the determination this record makes, not merely a
  style rule; the check belongs in review, not just in this prose.
- **This determination does not join issue 6 or issue 25's queue.** It is a doctrine argument, not a pending
  licence confirmation, so there is no custodian reply that would resolve it further. It should be revisited
  — this record is living, not immutable — if a licence document for the ONC list surfaces, or if a specific
  challenge is raised against these pairs.
- **The determination covers the interaction pairs and drugref's own authored `mechanism`/`management`
  text.** It says nothing about, and licenses nothing in, any other content the two papers contain — their
  evidence-grading rationale narrative, for instance, if a later slice ever wanted to mine it beyond a
  per-entry citation.
- **`NOTICE` gets an `ONCHIGH` entry alongside UNII, ChEBI, MED-RT and MeSH**, even though — unlike them — its
  clearance is an argument rather than a grant. The entry says so.

## Related

- [Licensing is a blocker](licensing-is-a-blocker.md) — the general rule this record discharges for one
  source.
- [Curating a drug–condition pair](curating-a-drug-condition-pair.md) — the overlay ONC judgements are graded
  through, as a second candidate source beside MED-RT.
- [issue 6](https://github.com/cairn-ehr/drugref/issues/6) and [issue
  25](https://github.com/cairn-ehr/drugref/issues/25) — the repository's other open rule-6 deeds; both wait
  on a primary licence document from a source's custodian, which is the shape of gap this record does not
  have.
