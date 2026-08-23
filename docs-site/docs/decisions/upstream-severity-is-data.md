# The candidate tier carries an upstream severity, and the mapping is data

**Status:** Active
**Last reviewed:** 2026-08-23
**Applies to:** `drugref.ddi_source_severity`, `drugref.drugcentral_ddi_assertion`,
`drugref.drugcentral_ddi_pair` and `drugref.exact_ddi_pair` (`db/049`); every future candidate source that
publishes a severity of its own
**Full derivation:** the [DrugCentral `ddi` ingest design
spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-08-23-drugref-drugcentral-ddi-ingest-design.md)
§5.2 (the mapping) and §3.1 (the measurement that made the band the whole content of the source)

## Context

drugref stores drug information in two tiers. The **candidate tier** holds what upstream authorities say,
rebuilt from scratch on every ingest; the **curated overlay** holds drugref's own judgements, append-only and
signed. Until `db/049` the candidate tier carried no severity at all, and that was not an oversight — the two
authorities in it, MED-RT and the ONC high-priority list, state *that* two drugs interact and, in MED-RT's
case, *which drug the statement is about*, but neither publishes a grade drugref could store. Severity was
therefore the overlay's word: `severity_kind` (four grades, with `severity_rank` where **rank 1 is the most
severe**, so that the safe read is the one a caller writes by default) exists to rank curated rulings.

DrugCentral's drug–drug interaction table changes that, and it changes it in an unusually pure way. drugref
ingests only the half of that table sourced from the U.S. Veterans Health Administration's NDF-RT; measured
across all 7,571 of those rows, **every single `description` matches the template
`NAME1/NAME2 [VA Drug Interaction]`** — 35 characters at the shortest, 75 at the longest. There is no
mechanism, no management advice, no prose of any kind. What this source adds over a bare list of drug pairs is
**one severity band, and nothing else**. If drugref discards the band it has ingested a pair list; if it keeps
the band it has, for the first time, an upstream clinical grade in the candidate tier.

Keeping it is not free, and the cost is not storage. VA publishes two labels in this subset — `Critical`
(2,307 rows) and `Significant` (5,264) — and drugref's own vocabulary has four grades. Translating one into
the other is **a clinical judgement drugref makes on a consumer's behalf**, on 7,501 drug pairs, none of which
any curator will look at individually in any foreseeable round. A judgement at that scale, made once and then
invisible, is exactly the kind of thing that should not live in a parser.

There is a second-order problem too. DrugCentral's severity lookup is scoped *per reference*: the four other
labels in it (`Avoid combination`, `Contraindicated`, `Potentially significant` and a further `Critical`
usage) belong to the two references drugref does not ingest. So the mapping cannot be inferred from the
table's overall vocabulary, and a future release that added a third band to the ingested half would be a
silent change to the meaning of every row drugref stores.

## Decision

**The candidate tier may carry a severity — but only where the source states one, and the source's own word is
stored verbatim beside drugref's reading of it.** `drugcentral_ddi_assertion.severity_label` holds `Critical`
or `Significant` exactly as published, case included; `drugcentral_ddi_pair.severity` derives drugref's grade;
both appear in the same row, so the authority's word and drugref's interpretation of it are separately
visible and separately checkable. Where a source states no grade — MED-RT — `severity` is **NULL**, and that
NULL states a fact about the source rather than hiding a missing value.

**The mapping from an upstream band to a drugref grade is a table, not code.** `ddi_source_severity` holds one
row per `(source, source_label)`:

| source | source_label | severity |
|---|---|---|
| `DRUGCENTRAL` | `Critical` | `contraindicated` |
| `DRUGCENTRAL` | `Significant` | `moderate` |

Two reasons, and the second is the one that matters. The first is drugref's standing rule about vocabularies:
a list written once in Python and again in a database constraint is two lists to widen and one way to
disagree, and this project has lost several rounds to exactly that. The second is that **a clinical judgement
drugref makes on a consumer's behalf must be queryable.** A node operator who thinks `Significant` should map
to `major` rather than `moderate` can `SELECT` the mapping, see precisely what drugref did, and disagree with
it — and revising it is then a migration over two rows rather than a re-ingest of 7,571. A mapping compiled
into a parser can be read only by reading the parser.

**The mapping is keyed per source**, because two authorities may both use the word "Significant" and mean
different things by it. **It is a foreign key into `severity_kind`**, not a CHECK, because `severity_rank` is
what decides which of two disagreeing grades a consumer sees, and a grade with no agreed rank would make that
non-deterministic. **And the assertion table's severity column is a foreign key into the mapping**, so a
future release that invents a third band is refused at INSERT, loudly, rather than stored and silently mapped
to nothing by a view's join.

**The mapping itself follows the authority's own semantics.** VA/NDF-RT defines *Critical* as *avoid the
combination* and *Significant* as *may have clinical consequences; monitor or adjust*, so each band maps to
the drugref grade that says the same thing. **`major` deliberately carries no DrugCentral row at all**, and
that is a signal rather than an omission: a two-band authority has two bands, and spreading them across three
grades would invent a distinction VA does not draw.

## Consequences

- **Some pairs are graded a notch low, and that is stated rather than hidden.** A few `Significant` pairs —
  `fluvoxamine + tapentadol`, `apixaban + heparin` — are arguably `major`. Correcting them one pair at a time
  is what the curated overlay is for, and it is exactly why the mapping's revisability is load-bearing rather
  than decorative.
- **A candidate severity is not a drugref ruling.** It is what an upstream authority said, carried through a
  mapping drugref publishes. A curated ruling overrides it, and because the DrugCentral release is pinned to
  November 2023 with no successor published, nothing in this tier may drive an automatic alert: it feeds
  review.
- **The precedence rule between two conflicting rows is stated once, in SQL.** DrugCentral publishes 33 pairs
  in both orders and 4 of those disagree with themselves on severity; the pair view resolves them
  most-severe-wins, needing no `DESC` because rank 1 is the most severe. A consumer querying from any language
  gets that rule for free rather than reimplementing it.
- **Adding a fourth authority costs two rows and a test, not a parser change** — provided that authority
  publishes a band the mapping can express. One that publishes free-text severity, or a numeric scale, would
  need a fresh decision rather than a fresh row, and this record should be revised if that happens.
- **A consumer must read the upstream label to audit the grade, and it is there to read.** The alternative
  considered and rejected — mapping in the parser and storing only drugref's grade — would have made the
  upstream label unrecoverable without re-parsing a 1.4 GB database dump.

## Related

- [The hybrid store](hybrid-store.md) — the two tiers this record sits across: rebuildable projections beside
  an append-only, signed overlay.
- [The ONC high-priority floor is facts, not text](the-onc-high-priority-floor.md) — the other candidate
  source in the interaction layer, and the rule-6 argument that admitted it.
- [Licensing is a blocker](licensing-is-a-blocker.md) — why only one of DrugCentral's three interaction
  references is ingested at all.
- [Curating a drug–condition pair](curating-a-drug-condition-pair.md) — how a curated ruling overrides what
  the candidate tier says.
