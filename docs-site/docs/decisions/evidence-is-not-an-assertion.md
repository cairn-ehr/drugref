# A label naming two drugs is evidence, not an assertion that they interact

**Status:** Active
**Last reviewed:** 2026-08-27
**Applies to:** `drugref.spl_ddi_evidence`, `drugref.spl_ddi_pair`,
`drugref.spl_entity_occurrence` and `drugref.exact_ddi_pair` (`db/051`); every future source read out of
free prose rather than out of a structured field
**Full derivation:** the [SPL drug × drug ingest design
spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-08-24-drugref-slice-5c3-spl-ddi-ingest-design.md)
§1 and §5.2, and [what the ingest actually
produced](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-08-27-drugref-slice-5c3-spl-ddi-ingest-results.md)

## Context

drugref's largest interaction source is **SPL section 34073-7 — DRUG INTERACTIONS**, the section every US
prescription label carries. Read across the 2026-08-22 openFDA export, 68,550 labels carry it, and the
moieties they name yield **29,952 distinct drug–drug pairs, 26,598 (88.8%) of which no other source drugref
holds mentions at all** — four times what DrugCentral's entire interaction slice contributed.

That material is prose. A label writes sentences like *"concomitant use with strong CYP3A4 inhibitors is
contraindicated"*, or *"monitor prothrombin time"*, or *"no clinically significant interaction was
observed"* — and the last of those names two drugs in order to say they do **not** interact.

So there are two quite different things drugref could store, and they are easy to confuse because the same
scan produces both:

1. **that a label's interactions section names two drugs**, with the exact character offsets; and
2. **that the two drugs interact**, at some severity, in some direction.

The second requires reading what the sentence *means*. Every other source in drugref's candidate tier
supplies it directly: MED-RT publishes a typed predicate, the VA publishes a severity band, the ONC list
publishes a curated pair. SPL publishes neither — it publishes English.

## Decision

**drugref stores the first and refuses the second.** The standing rule is *ingest preserves evidence;
curation creates clinical judgement*, and this slice holds that line exactly:

- `spl_entity_occurrence` records **which known moiety is named where**, with offsets, and nothing about
  what is said. There is no relation column, no direction and **no severity column at all**.
- `spl_ddi_evidence` publishes one row per (pair, citing label) — the pair, the `set_id`/`version` citation,
  the offsets, and a bounded quoted window where one could be stored. Its meaning is *this label's
  interactions section names both of these drugs*.
- `spl_ddi_pair` aggregates that to one row per unordered pair, so `count(*)` **is** the candidate-pair count
  and is directly comparable with the other sources' figures.

**And SPL is deliberately NOT an arm of `exact_ddi_pair`.** That view means *an authority asserted these two
drugs interact*. SPL evidence, read without relation extraction, means *a label's interactions section names
both*. The second is a weaker claim, and **a read path that cannot tell them apart makes the stronger one
unfalsifiable**. A consumer wanting both takes the union explicitly, and can always see which source said
what.

The potency band is refused for the same reason and one more. FDA's own guidance bands ciprofloxacin as a
*moderate* CYP1A2 inhibitor while naming tizanidine as the substrate against which it behaves as a *strong*
one — so the band is a property of the **pair**, not of the drug or its class. Reading one off a sentence is
relation extraction wearing a different hat, and a band that is really pair-scoped cannot be stored on
anything else without being wrong somewhere.

## Consequences

**What it costs.** drugref cannot answer "how severe is this interaction?" from SPL, and a naive consumer who
treats `spl_ddi_pair` as a warning list will over-warn: the 29,952 pairs include every drug a label mentions
in order to say it is *safe* alongside. The view's name and its published comment both say so, and the
evidence view exists precisely so a reader can go and look at the words.

**What it buys.** Everything here is checkable. An occurrence is a character range in an identified wording;
a reader can cut the span back out and disagree. Nothing in the tier depends on a model's reading of a
sentence, so nothing has to be re-validated when a better model arrives — and the curated overlay, which is
where clinical judgement legitimately lives, gains 26,598 candidate pairs to rule on that it did not have.

**What it commits us to.** Relation extraction is not forbidden forever; it is forbidden *in ingest*. If
drugref ever grades these pairs, the grade will be a curated assertion with its own provenance, sitting above
this evidence and citing it — not a column quietly added to a projection that is rebuilt from scratch on
every release.

## Related

- [The candidate tier carries an upstream severity](upstream-severity-is-data.md) — the contrasting case,
  where the authority *does* publish a band and drugref stores it verbatim beside its own reading of it.
- [Bundling a quoted window, not the section](bundling-a-quoted-window.md) — how much of the prose may be
  stored beside this evidence, and why that is a schema constraint.
- [The ONC high-priority floor is facts, not text](the-onc-high-priority-floor.md) — the same
  facts-not-expression line drawn over a different source.
- [The hybrid store](hybrid-store.md) — why rebuildable projections and the curated overlay are separate
  tiers in the first place.
