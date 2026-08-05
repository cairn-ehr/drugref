# drugref — slice 3: the composition tree (salts, solvates, and what GSRS actually says)

**Date:** 2026-08-05 · **Status:** design, approved · **Issues touched:**
[#33](https://github.com/cairn-ehr/drugref/issues/33), [#30](https://github.com/cairn-ehr/drugref/issues/30)
— **neither is closed by this slice; see §8.**

The first slice since 2b to add a **new external source**, and therefore the first in a long while where
CLAUDE.md rule 6 is a gate rather than a formality. It adds the composition level *beside* the moiety registry:
which specific substances (salts, solvates, hydrates) are composed of which registered moieties, and — where
the release says so — **which of those components is the pharmacologically active one**.

Everything below was measured against the real release before it was written. **Four things ROADMAP asserts
about this slice are refuted by that measurement**, and they are stated first because three of them change the
design and the fourth changes what the slice may claim.

## 1. The rule-6 gate, cleared first

Nothing was downloaded until the licence was established, because rule 6 makes an incompatible licence a
blocker rather than a cleanup item.

**GSRS (Global Substance Registration System)**, FDA + NCATS, is the system that mints UNIIs — so drugref is
already keyed on its output; this slice reads its relationships for the first time. From the licensing page:

> Unless otherwise noted, the data provided by GSRS is public domain and made available with a Creative
> Commons **CC0 1.0 Universal** dedication. Under CC0, NCATS has dedicated the work to the public domain by
> waiving all rights to the work worldwide under copyright law … You can copy, modify, distribute and perform
> the work, **even for commercial purposes**, all without asking permission.

> The content, documentation, code, and related materials provided by GSRS is licensed under the **Apache
> License, Version 2.0**.

**Verdict: compatible, and unusually cleanly so.** CC0 imposes no attribution, no share-alike and no
non-commercial term, so it composes with AGPL-3.0 without qualification — unlike MeSH (attribution +
version-currency) or ChEBI (CC BY 4.0), both of which drugref already carries. The Apache-2.0 term governs the
*software*, which drugref does not use or redistribute.

**Two caveats recorded rather than waved past:**

1. **"Unless otherwise noted"** is a per-record exception clause. No noted exception was found on any record
   read, but the clause means the dedication is not unconditional, and the honest posture is the one
   [#6](https://github.com/cairn-ehr/drugref/issues/6) takes for MED-RT: re-confirm against the live
   source-release document before the first production load. Added to PROJECT-NOTES § "Verify before the first
   production load".
2. The dump is **not** reachable from the documented API. The public API returns substance records with
   `relationships` **stripped entirely** — verified by control, not assumed: `1D06KZ672I`, whose `ACTIVE
   MOIETY` edge was read directly out of the dump bytes, comes back from
   `/api/v1/substances/{uuid}` with zero relationships. A first pass that queried the API concluded "GSRS holds
   no active-moiety data for magnesium sulfate", which was an artifact of the transport. **The dump is the only
   route**, and the API is not a fallback for it.

`NOTICE` gains a GSRS entry naming the dedication and the release. This is a **bundled-source** decision under
rule 6, not a node-local plug-in: CC0 permits redistribution outright.

## 2. What the release actually is

| | |
|---|---:|
| file | `dump-public-2026-02-26.gsrs` |
| bytes (gzip) | 321,487,817 |
| decompressed | ~2.05 GB |
| format | JSON-lines, **one substance record per line, each prefixed by two tab characters** |
| records | 173,080 |
| records carrying a UNII | 168,002 |
| drugref moieties present in it | **19,438 of 19,438 (100%)** |

The 100% is worth stating plainly: unlike every previous bridge in this project, **the join loses nothing**.
There is no coverage shortfall to explain, because GSRS is the registry drugref's own UNII keys come from.

## 3. Three corrections the release makes to ROADMAP

### 3.1 `ACTIVE MOIETY` is not the composition edge

ROADMAP §"Slice 3" says the tree is keyed "with `parent_moiety_uuid` from **GSRS active-moiety
relationships**". Measured, `ACTIVE MOIETY` is the **ion** level and is the wrong edge for a composition tree:

* 33,647 edges, of which **23,944 (71%) are self-references** — a substance asserting that it *is* its own
  active moiety.
* For the magnesium family, *every* form — anhydrous, heptahydrate, and drugref's own "unspecified form"
  record — points to the same target, `T6V3LHY838 MAGNESIUM CATION`.

Using it as a substance-equivalence join is not merely imprecise, it is the **discredited-inference shape this
project has already refused once**. 35 substances share `MAGNESIUM CATION` as their active moiety, **27 of them
drugref moieties**, including magnesium chloride, magnesium nitrate, magnesium carbonate and **levomefolate
magnesium**. Joining on it asserts that a folate salt is interchangeable with magnesium sulfate — the same
error as expanding a sulfonamide rule over MeSH's structural chemical tree, which
`decisions/withheld-chemical-class-contraindications.md` records as withheld.

The composition edge is instead **`SALT/SOLVATE ↔ PARENT`** (15,199 edges after normalisation) and
**`SOLVATE ↔ ANHYDROUS`** (1,635). `ACTIVE MOIETY` still earns a place in this design, but as a **discriminator
within** a composition (§5.2), never as an edge of it.

### 3.2 The direction is inverted from the naive reading

**For a relationship of type `A->B` stored on record X and pointing at Y: X plays role B, and Y plays role A.**
The stored relationship is the **inbound** edge.

This is the same class of upstream erratum as MED-RT's `Parent Of` running parent → child, already recorded in
PROJECT-NOTES — and like that one, it is invisible to a small fixture and produces plausible-looking garbage at
scale. Read naively, one "salt" had **124 parents**. Read correctly, the busiest *parents* are:

| parent | salts |
|---|---:|
| Maleic Acid | 124 |
| Tartaric Acid | 123 |
| Anhydrous citric acid | 117 |
| ZINC CATION | 95 |
| Fumaric acid | 79 |

— exactly the counterions a base should have many salts of. **Two independent checks confirm the convention**,
and the spec requires both to be kept as tests:

1. **The mirror check.** GSRS stores most edges twice, once from each end. Normalised under this convention the
   two encodings agree on **15,039** edges, out of **15,109** (`SALT/SOLVATE->PARENT`) and **15,150**
   (`PARENT->SALT/SOLVATE`) respectively. Under the inverted reading they would agree on essentially none.
2. **The functional check.** Every solvate has exactly **one** anhydrous parent (`{1: 1635}`, MAX = 1). Under
   the inverted reading the cardinality is many-to-many and meaningless.

The **70** edges present only as `SALT/SOLVATE->PARENT` and the **111** present only as `PARENT->SALT/SOLVATE`
are **upstream asymmetry: counted, not repaired.**

### 3.3 `parent_moiety_uuid` cannot hold the data

**1,089 salts (7.7%) have more than one parent base — 800 of them within drugref's registry.** These are not
data errors; they are correct compositions of multi-component substances:

```
Gadoterate meglumine   -> Gadolinium cation (3+), Gadoteric acid, Tetraxetan, Meglumine
ZINC GLYCINATE CITRATE -> ZINC CATION, Glycine, Anhydrous citric acid
FERROUS ASPARTO GLYCINATE -> FERROUS CATION, ASPARTIC ACID, Glycine
```

A single-valued foreign key would have to drop three of gadoterate meglumine's four components, silently. The
relation is a **composition**, inherently many-to-many, and the table is shaped accordingly.

### 3.4 The tree is nearly flat

Only **81 nodes** are both a salt and a parent. There is no deep hierarchy to walk, which is why this slice
introduces **no recursive view** and none of Plan C's closure machinery. (PROJECT-NOTES already carries the
standing warning that a recursive view must be measured against a real DAG; the cheapest way to honour it here
is not to add one.)

## 4. What is deliberately NOT in this slice

**Salt↔base strength equivalence, which ROADMAP scopes into slice 3, is not deliverable from this source.**
`BASIS OF STRENGTH` exists but is **409 edges over 400 subjects** — against 15,199 composition edges — and its
`amount` objects are *assay and potency specifications*, not conversion factors:

```
CARVEDILOL        -> CARVEDILOL        99–101 WEIGHT PERCENT
GENTAMICIN SULFATE -> gentamicin        590 MICROGRAM/MG (lowLimit only)
```

Many are self-referential. This answers "how pure is this material", not "how much salt equals how much base".
The only other route is molecular weight, present on **9,308 of 173,080 records (5.4%)**, and deriving a
dose-conversion factor from it would make drugref **compute a clinical quantity with no upstream authority
behind it** — precisely what the projection tier is forbidden to do, by the same rule that stops a projection
inventing a line of therapy. **Deferred to slice 5c**, where a human may assert a factor with provenance and a
signature, and filed as its own issue.

Also out of scope: wiring the read path into `ddi_candidate_pair`. That view is a measured hot path (3.6 ms,
and PROJECT-NOTES records that a plausible re-expression cost it **5×**); changing what it expands over is its
own round with its own measurement.

## 5. Schema — `db/028`

### 5.1 One rebuildable projection

`substance_composition` is a **projection keyed by `ingest_run.source = 'GSRS'`**, delete-and-rebuild like
`class_membership` and `class_contraindication` — outside slice 1's append-only floor, because a substance
whose composition the release corrects must be able to change.

```sql
CREATE TABLE drugref.substance_composition (
    substance_unii      text   NOT NULL,   -- the COMPOSITE; deliberately not an FK
    component_moiety    uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    relation            text   NOT NULL REFERENCES drugref.composition_relation(relation),
    is_active_component boolean,           -- NULL = UNRULED. No DEFAULT. See 5.2.
    ingest_run          bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (substance_unii, component_moiety, relation)
);
```

**`substance_unii` is text and not a foreign key, and that is the design.** 4,425 of the 7,377 composites are
not drugref moieties and this slice **mints no new identity for them** — no second registry, no second immortal
UUID, no dual residence to reconcile. The composite side is a *key from the source*; only the component side
is a drugref identity. This is what makes the awkward fact in §7.1 a non-event rather than a migration.

`composition_relation` is a two-row vocabulary table (`SALT_SOLVATE`, `SOLVATE_ANHYDROUS`) that `relation` is a
foreign key into — following `db/006`'s `ci_axis`, which replaced a comment-enforced CHECK↔CASE coupling, and
the standing rule that **a vocabulary written down twice is two things that can disagree**.

### 5.2 `is_active_component` — and why NULL is a third answer

GSRS's `ACTIVE MOIETY` axis, useless as an edge (§3.1), is exactly right as a **discriminator inside a
composition**. Measured: where a salt declares both a composition and an active moiety, **the active moiety is
one of its own components 95.1% of the time** (6,368 of 6,696), and in **589** multi-component cases it selects
a strict subset:

```
ZINC GLYCINATE CITRATE     ACTIVE: ZINC CATION      not active: Glycine, Anhydrous citric acid
Gadoterate meglumine       ACTIVE: Gadolinium (3+)  not active: Gadoteric acid, Meglumine, Tetraxetan
FERROUS ASPARTO GLYCINATE  ACTIVE: FERROUS CATION   not active: ASPARTIC ACID, Glycine
```

That is the drug/counterion separation the read path needs.

**But only 6,696 of 14,090 salts declare an active moiety at all**, so the column has **no DEFAULT and NULL
means *unruled*, not *inactive*.** This is the same distinction the project has now paid for twice: `allow` is
not the same as absent in `class_expansion_policy`, and `withdrawn` is not `allow`. Defaulting NULL to `false`
would silently retire 2,641 rows nobody has ruled on; defaulting to `true` would propagate through
counterions. The 328 cases where the declared active moiety is **not** among the components are counted as
upstream inconsistency, not repaired.

### 5.3 What gets written

| | rows |
|---|---:|
| `SALT_SOLVATE` | 7,962 |
| `SOLVATE_ANHYDROUS` | 709 |
| **total `substance_composition`** | **8,671** |
| distinct composites (`substance_unii`) | 7,377 |
| distinct component moieties | 4,433 |
| composites that are not themselves moieties | 4,425 |
| `is_active_component = true` | 5,029 |
| `is_active_component = false` | 1,001 |
| `is_active_component IS NULL` (unruled) | 2,641 |

**4,092 moieties — 21.1% of the registry — gain at least one salt child.**

## 6. The read path and the gap view

### 6.1 Only the active component propagates

One view — `moiety_active_in_composite` — exposing, for a moiety, the composites it is the **active** component
of: `is_active_component IS TRUE` and nothing else. `false` propagates nothing; **`NULL` propagates nothing**,
because an unruled row is not evidence of anything. The predicate is written `IS TRUE` rather than
`= true` deliberately, so a NULL can never be coerced into a match by a later rewrite.

The rule this encodes: *a contraindication or interaction asserted on moiety M reaches composite S only where
the release says M is what makes S pharmacologically active.* Maleic acid's 124 salts therefore stay unlinked,
which is the whole point — expanding them would be alert-fatigue by construction and would repeat the
withheld-chemical-class error at a new level.

The safety asymmetry is acknowledged rather than assumed away. PROJECT-NOTES records that **for a
contraindication, fewer rows is the harm direction**, and this rule deliberately chooses fewer rows for the
2,641 unruled edges. That is defensible *only because the shortfall is published rather than hidden* — which is
what §6.2 is for. It is the same trade the 103 unresolved CI objects already take: withheld, counted, and put
in front of a curator.

### 6.2 Gap kind 12

`gap_unruled_composition_activity` — one row per composite carrying components but **no** activity ruling,
feeding `open_question` like the other eleven. Population: **2,226 composites**.

Two standing rules bind it. **The view's grain must be the `gap_key`'s grain** (#41) — the key is the
composite, and the view groups by the composite, so no two gaps can fold onto one immortal `question_uuid`.
And the gap must be *reachable*: unlike [#48](https://github.com/cairn-ehr/drugref/issues/48), this one is
populated on the real release from day one.

## 7. Parser, orchestrator, fixture

### 7.1 `ingest/gsrs.py` — pure and streaming

Mirrors `mesh.py`: gzip stream, one `json.loads` per line, yields normalised edges, **touches no database**.
It never holds the 2.05 GB in memory. Measured, a full **parse** pass over the real dump is **~8 s** (the write
adds 8,671 rows on top) — cheaper than the MeSH leg, so this adds on the order of 7% to the ~114 s chain rather
than doubling it. The chain figure must be re-measured with GSRS in it, not extrapolated from this.

**The direction convention of §3.2 lives in exactly ONE function**, with the mirror check and the
functional check as its tests. This is the single most dangerous line in the slice: inverted, it produces a
fully-populated, entirely wrong table that no aggregate count would flag.

**One awkward fact, which the §5.1 shape makes harmless: 3,195 GSRS salts are already drugref moieties.** The
gate admitted them, `moiety_uuid` is immortal, and the gate is strictly monotone — so they cannot be demoted,
and this slice does not try. A row may be a moiety *and* have components; the two statements are about
different things and the schema lets both be true. (42 are both salt and parent.) Related and equally
unrepaired: 3,631 drugref moieties carry an `ACTIVE MOIETY` edge to something else, i.e. GSRS would not call
them active moieties. That is a **moiety-gate** question, not a composition one; it belongs with
[#26](https://github.com/cairn-ehr/drugref/issues/26)'s lineage and is filed, not fixed here.

### 7.2 `ingest/gsrs_run.py` — the only writer

Owns the transaction, per the architecture invariant. Specifically:

* `writer = 'gsrs_run'`, added to **`db/025`'s CHECK *and* `provenance.WRITERS`** — the pair that PROJECT-NOTES
  records must move together.
* Opens the run through `provenance.py`, the only file permitted to write `ingest_run` (two grep contracts).
* Its table tuple joins `db.clear_source_tables` and is **independently restated** in
  `tests/test_source_clear_contract.py`, so a dropped table fails rather than silently persisting.
* Checksums its input via `ingest/checksum.py`, the one implementation.
* Rebuilds `open_question` before commit, as the other orchestrators do.
* A CLI step and a `chain` step, whose input glob is **`dump-public-*.gsrs`** and whose release tag is
  **stated, never parsed from the filename** — the rule `#60` produced.

### 7.3 The fixture is extracted, never written

`make_gsrs_subset.py`, committed and re-runnable, cutting a subset from the real dump — because the last
hand-written fixture in this repo invented an `INN_ID`, a CAS and a UNII, and because slice 5b found five spec
errors that only a real-release fixture could surface. It must carry, at minimum:

1. a single-parent salt;
2. **`ZINC GLYCINATE CITRATE`** — multi-parent, and the case a single FK would truncate;
3. a solvate/anhydrous pair;
4. an active-vs-counterion discrimination (so a mutation defaulting NULL is caught);
5. **both mirror encodings of one edge**, so the direction test is exercised on real bytes;
6. a composite with components but no active moiety, so the gap view has a row;
7. **the magnesium family**, as the case this slice does *not* resolve (§8).

## 8. What this slice does not resolve, stated plainly

ROADMAP annotates [#33](https://github.com/cairn-ehr/drugref/issues/33) and
[#30](https://github.com/cairn-ehr/drugref/issues/30) as "**Closed by slice 3**". Measured, that is wrong, and
this spec does not inherit the claim.

**Issue 33's own proposed fix is refuted by the release.** It predicts that GSRS gives
`ML30MJ2U7I → DE08037SAB` and `SK47B8698T → DE08037SAB`. It does not. **Nothing anywhere in GSRS points at
`DE08037SAB`** — drugref's magnesium moiety has **0 inbound references** across all 173,080 records; it is a
`mixture`-class "unspecified form" record that is an orphan in the relationship graph. Every magnesium form
instead points at `MAGNESIUM CATION`, which the moiety gate does not admit.

Under the §6.1 rule, a composition hop recovers:

| population | recovered |
|---|---|
| MeSH descriptor UNII keys reaching no gated moiety (706) | **94** |
| MeSH descriptor CAS keys reaching no gated moiety (1,977; 825 name a GSRS substance) | **68** |
| the magnesium flagship | **not recovered** |

So issue 33 **narrows and stays open**, and its text needs correcting against this measurement — a number in an
issue is a claim about a release, not about the code, and this is the third round to find one stale.

**Issue 30 (the PBS salt-strip stand-in) is not measured here**, because the verification database carries no
PBS release — the chain loads UNII/MED-RT/MeSH only. Its yield is an implementation-step measurement, not a
design input, and the slice may claim nothing about it until then. What is already known stands: the heuristic
contributes **5 bridge rows, 0.03%**.

## 9. Verification

Ordinary TDD throughout (failing test first). Beyond that, the checks this design would be worthless without:

1. **The direction convention**, by both the mirror check and the solvate functional check (§3.2), on real
   fixture bytes.
2. **Multi-parent survival**: `ZINC GLYCINATE CITRATE` keeps all three components. A test that would pass
   against a single-FK schema is not testing this.
3. **NULL is not `false`**, verified by mutation: defaulting the column must make the gap view lose its 2,226
   rows and the read view gain rows, and a test must fail when it does.
4. **`ACTIVE MOIETY` is never used as an edge** — a grep contract, in the shape of
   `tests/test_overlay_contract.py`'s: it may only ever be read into `is_active_component`.
5. **Full re-measure against the real releases** with GSRS in the chain, re-confirming that every existing
   published figure is unmoved — this slice adds a table and changes no SQL any of them depend on, so
   `ddi_candidate_pair` **21,664**, `open_question` **18,834 + gap kind 12**, and the 5b/5b.2 figures must all
   reproduce. Any movement is a defect, not a discovery.
6. `ruff check src tests` and `mkdocs build --strict`.

## 10. Traps this slice leaves for the next change

* **The direction convention is invisible when wrong.** Inverting it yields a plausible, fully-populated table.
  Its two tests are the only thing standing between the schema and confident nonsense — do not delete them.
* **`ACTIVE MOIETY` is a discriminator, never an edge**, and never a substance-equivalence join. The
  temptation is real and specific: it would appear to close issue 33. It also merges levomefolate magnesium
  with magnesium sulfate.
* **NULL means unruled.** A future writer "tidying up" the nullable column re-runs a mistake this project has
  now made in three different tables.
* **`substance_unii` is deliberately not an FK.** Adding one deletes 4,425 composites, which is two-thirds of
  the table, and re-opens the second-registry question §5.1 exists to avoid.
* **`ACTIVE MOIETY` self-edges (23,944) are not compositions.** Filtering them is load-bearing; without it,
  every moiety becomes its own component.
* **The 3,195 salts that are already moieties are not a bug to fix.** They are immortal. Any future attempt to
  "clean the registry" by removing them violates slice 1's floor and the monotone-gate test.
