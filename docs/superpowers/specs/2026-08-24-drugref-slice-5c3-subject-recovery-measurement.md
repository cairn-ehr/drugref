# Slice 5c.3 — subject recovery: can the unkeyed 60% be rescued?

**Measurement round, 2026-08-24.** No migration, no ingest, no schema.
Companion to
[the SPL mining measurement](2026-08-24-drugref-slice-5c3-spl-mining-measurement.md),
which left this as its one open design question and is not edited by this
document — a merged spec is immutable, and a correction to one lands as a new
record that says so.

The design round opened by asking the owner what to do about the
**41,056 section-carrying labels (60%) discarded before a pair can form**,
because openFDA's normalising `openfda` block is absent from them. The answer
was **measure the recovery route first, then decide**. This is that measurement.

**⇒ It changed the design twice.** The counterweight was *understated*, not
overstated — measured in the right unit it is 56% of the corpus, not 60% of the
labels — and the recovery route the previous round named turned out to be one of
**three**, only two of which are safe.

---

## 1. Reproduction

Same corpora, same checksums, same database as the parent measurement — see its
§2. Nothing was re-fetched.

```sh
CACHE=/tmp/spl        # the parent round's cache, rebuilt: its census reproduced
                      # EXACTLY (262,032 / 68,550 / 40,413 / 27,694 / 27,406)
export DSN="host=localhost port=5532 dbname=drugref_spl user=postgres"

uv run python -m tools.spl_ddi_spike extract --downloads downloads/OPENFDA --out $CACHE
uv run python -m tools.spl_recovery_probe reach    --cache $CACHE
uv run python -m tools.spl_recovery_probe scan     --cache $CACHE \
    --parts downloads/DAILYMED/dm_spl_release_human_rx_part*.zip \
    --out $CACHE/recovered.jsonl
uv run python -m tools.spl_recovery_probe resolve  --cache $CACHE \
    --recovered $CACHE/recovered.jsonl --dsn "$DSN"
uv run python -m tools.spl_recovery_probe elements --downloads downloads/OPENFDA \
    --cache $CACHE
uv run python -m tools.spl_recovery_probe yield    --cache $CACHE \
    --recovered $CACHE/recovered.jsonl --dsn "$DSN" \
    --suppress-terms tools/spl_suppress_terms.txt
```

Probe code is **throwaway** and says so in its docstrings:
`tools/spl_subject_recovery.py` (the pure functions) and
`tools/spl_recovery_probe.py` (the runner). Nothing under `src/drugref/` imports
either. 19 tests, on the parent round's terms: *the figures are worth exactly as
much as the parser that produced them.*

**The parent round's headline pair figure reproduced exactly** — 20,554 distinct
candidate pairs, 18,107 novel (88.1%) — through a *reimplementation* of its pair
rule rather than a call into it. That agreement is what makes every delta below
a delta rather than a second number computed a slightly different way.

---

## 2. ⇒ THE COUNTERWEIGHT WAS MEASURED IN THE WRONG UNIT, AND IT IS WORSE THAN REPORTED

The parent round reported the loss as **41,056 labels, 60% of the
section-carrying corpus**. Labels are the wrong unit and that round said so
itself in a different context — *"the de-duplication factor must be divided out
before any rate is quoted"*. Applied here it cuts both ways.

| | labels | |
|---|---|---|
| carry section 34073-7 | 68,550 | |
| keyed by `openfda.unii` | 27,694 | |
| unkeyed, but wording ALSO on a keyed label | **14,455** | recovery adds nothing |
| unkeyed, wording reachable no other way | **26,401** | the real target |

| | wordings | share |
|---|---|---|
| distinct wordings | 27,406 | |
| reachable through a keyed label | 12,061 | 44.0% |
| **ORPHAN — unkeyed labels only** | **15,345** | **56.0%** |

**14,455 of the 41,056 are redundant**: another manufacturer reprinting a
wording drugref can already reach, and recovering them would rediscover
statements it already has. That is the half that makes 60% an overstatement.

**But the surviving half is measured in wordings, and there it is 56.0%** — and
the published 20,554 pairs came from **only 12,061 wordings**. More than half
the corpus's distinct statements contributed nothing at all.

### The orphan half is not inferior material

A 56% orphan share would still not justify the work if those wordings were
thin — a boilerplate stub repeated by labels that never got an identity block.
Measured, the opposite:

| population | wordings | name ≥ 1 known moiety | moiety occurrences / wording | distinct moieties |
|---|---|---|---|---|
| reachable (keyed) | 12,061 | 97.8% | 44.0 | 1,846 |
| **ORPHAN** | 15,345 | **97.2%** | **49.3** | 1,862 |

The orphan half is **denser**, and names slightly more distinct drugs. Whatever
makes a label lack an `openfda` block, it is not the quality of its section 7.

---

## 3. Route 2 — DailyMed's XML, and what it actually reaches

The parent round named this route: the `set_id` joins to DailyMed's own SPL,
which carries the ingredient list. Measured over all six Human Rx parts,
targeting only the 26,401 labels §2 identified as worth looking for:

| | labels | |
|---|---|---|
| targeted | 26,401 | |
| **found in DailyMed** | **6,539** | **24.8%** |
| ABSENT from DailyMed | 19,862 | 75.2% |
| found but carrying no UNII | **0** | |
| resolved against drugref | **6,514** | **99.6% of those found** |
| — on the active MOIETY | 6,498 | |
| — on the SALT only | 16 | [#67](https://github.com/cairn-ehr/drugref/issues/67) |

**⇒ ORPHAN WORDINGS RESCUED: 4,671 — 30.4% of the 15,345 targeted.**

**⇒ AND THE FIRST READING OF THAT TABLE WAS WRONG, BY 44 LABELS.** It reported
6,583 found and 6,558 resolved, because the summariser counted the scan's ROWS.
DailyMed ships successive **versions** of one label as separate documents
sharing a `set_id`, so 44 labels were counted twice. Recorded rather than
quietly corrected, on this project's standing terms — and note what caught it:
not the probe's own tests, but **cross-checking the total against an
independent pass** that computed resolution directly from the cache. A tally
that only ever agrees with itself is not checked. The row count is now
de-duplicated by `set_id` and pinned by
`test_one_set_id_read_TWICE_is_one_label`. The rescued-wording figure was
unaffected: it was already a set.

**The route is excellent and its coverage is poor, and those are separate
facts.** Where DailyMed holds the label the subject resolves **99.6%** of the
time, and *zero* labels were found carrying no UNII at all. The limit is not the
reading, it is the release: DailyMed publishes **current in-use** Human Rx
labels, while openFDA carries archived ones too — the same asymmetry the parent
round recorded from the other direction (openFDA 68,550 section-bearing labels
against DailyMed's 39,743).

**Why the XML route is structurally safe**, and this is the property route 3
lacks: SPL marks an active ingredient as an element
(`<activeIngredientSubstance>`, or `<ingredient classCode="ACTIB">`) distinct
from `<inactiveIngredientSubstance>` / `classCode="IACT"`. The excipient is not
merely ranked lower — it is **a different element**, so excluding it is a
parsing rule, not a judgement. Both spellings are read, because reading only the
modern one would *under*-count, and under-counting is the direction that quietly
kills a design option by making it look not worth building.

---

## 4. ⇒ THE ROUTE THE PARENT ROUND DID NOT KNOW ABOUT, AND WHY IT IS NOT SAFE

The 19,862 labels DailyMed lacks are not beyond reach. `openfda` is present on
**100%** of unkeyed records — it is simply **empty** — but
**`spl_product_data_elements` is populated on 40,633 of 40,856 (99.5%)**:

> `METOPROLOL TARTRATE METOPROLOL TARTRATE METOPROLOL TARTRATE METOPROLOL
> LACTOSE MONOHYDRATE MICROCRYSTALLINE CELLULOSE SODIUM STARCH GLYCOLATE TYPE A
> POTATO SILICON DIOXIDE MAGNESIUM STEARATE …`

One flattened uppercase string: product name, active ingredients, active
moieties, then excipients, **with no delimiter between them**. It needs no
second corpus and it reaches nearly everything — so it had to be measured rather
than dismissed.

**And it could be measured properly, because route 2 produced a ground-truth
set**: 6,317 labels whose true active moiety DailyMed's XML already gave.

| | share of 6,317 |
|---|---|
| true moiety is AMONG the names in the field | **98.9%** |
| true moiety is the FIRST name | 52.2% |
| true moiety is rank 0 or 1 | 88.1% |
| **mean registry moieties matched per label** | **7.69** |

**Recall is superb and precision is the problem.** The field names excipients,
and excipients are real registry moieties. Taking every name would key a real
interaction statement to lactose.

### The positional rules, and the error that matters

| rule | picks exactly the truth | extra is only a SALT of the truth | **genuinely wrong subject** |
|---|---|---|---|
| **rank 0 only** | 52.2% | 41.6% | **6.2%** |
| ranks 0–1 | 8.5% | 40.8% | **50.7%** |

**Splitting salt spellings out of the error is what makes this honest.** A rule
picking `metoprolol tartrate` where the truth is `metoprolol` has the right drug
and the wrong grain — [#67](https://github.com/cairn-ehr/drugref/issues/67)'s
problem, already known and already unsolved. A rule picking `silicon dioxide`
has the wrong drug. Counted together, rank 0 looks 47.8% wrong; counted apart it
is **6.2%** wrong, and the residual is itself dominated by hydrate and ester
spellings (`divalproex sodium`, `azithromycin monohydrate`, `doxycycline
hyclate`) rather than excipients.

**The excipients enter at rank 1**, which is exactly what SPL's generation order
predicts — the product name comes first. Under `ranks 0–1` the wrong picks are
`silicon dioxide` (421), `lactose monohydrate` (412), `magnesium stearate`
(271), `croscarmellose` (231), `anhydrous lactose` (179).

**The salt-likeness test OVER-counts on purpose** — token containment of display
names, which cannot see that `divalproex sodium` is valproate — so 6.2% is an
*upper* bound on the genuine error. That is the conservative direction for a
claim that a route fails.

---

## 5. What each route buys, in pairs

The round's own pair rule, its own suppression list, its own held-pair
baseline — the only variant whose exclusions were each measured.

| | wordings with a subject | distinct pairs | novel | novel % |
|---|---|---|---|---|
| 1. `openfda.unii` (published baseline) | 12,061 (44.0%) | 20,554 | 18,107 | 88.1% |
| **2. + DailyMed XML (structural)** | **16,754 (61.1%)** | **31,618** | **28,269** | **89.4%** |
| 3. + rank-0 name (heuristic) | 27,376 (99.9%) | 36,580 | 32,828 | 89.7% |

**Route 2 adds 11,064 pairs — +53.8% — of which 10,162 (91.8%) are novel**, a
*higher* novelty rate than the baseline it extends. For scale, DrugCentral's
entire slice was justified on 7,501 pairs at 91% new: **the recovery half alone
is bigger than that slice.**

**⇒ EVERY PAIR FIGURE HERE IS A FLOOR, and the reason is a probe optimisation
that the ingest must not inherit.** The scan targeted only the 26,401
orphan-wording labels; the **14,455 unkeyed labels whose wording a keyed label
also carries were never read**. They were skipped because they cannot rescue a
*wording* — but a label's SUBJECT is its own, and an unkeyed label sharing
another's wording may be a different drug, which would form pairs nobody has
counted. The saving was real (it removed 35% of the scan) and the design must
still scan every unkeyed label, because the unit that governs a pair is the
subject, not the wording.

**Route 3's coverage is spectacular and its yield is not.** It takes wordings
from 61.1% to 99.9% and adds only **4,962** further pairs — under half what
route 2 added from a third as many wordings. The remaining labels talk about
drugs already paired through other labels, so buying the last 39% of wordings
with a 6.2% wrong-subject rate purchases progressively less.

---

## 6. ⇒ THE STORED-PROSE BUDGET, AND WHY IT CANNOT BE PER-OCCURRENCE

[#154](https://github.com/cairn-ehr/drugref/issues/154) was answered by the
owner on 2026-08-24: **bundle a quoted window only** — the matched span plus a
bounded context, with the rest referenced by citation. That answer needs a
window rule, and the obvious per-occurrence rules do not survive measurement.

The corpus averages **~48 moiety occurrences per wording** and a mean section
length of **3,663 characters**. Measured over 5,868 wordings:

| per-occurrence rule | mean % of section stored | median | ≥ 90% of section |
|---|---|---|---|
| the containing sentence | **80.4%** | 84.6% | 32.8% |
| ±120 characters | 89.6% | 94.2% | 65.9% |
| ±60 characters | 74.9% | 77.9% | 15.6% |

**A per-occurrence window is not a quotation. It is the section, reassembled.**
Any of these would make "we store a quoted window" and "we store the prose"
the same act, and the second is the one the owner declined.

**The bound must be per WORDING.** Measured:

| rule | mean % stored | median | windows / wording | distinct moieties keeping a window |
|---|---|---|---|---|
| first occurrence per moiety, ±60 | 34.3% | 31.5% | 15.0 | all |
| **+ cap at 25% of section chars** | **14.7%** | **15.5%** | **6.6** | 47.3% |
| + hard cap at 600 chars | 18.2% | 12.6% | 3.7 | 47.3% |

**⇒ The proportional cap is the rule** (owner's call, 2026-08-24): ±60
characters around the **first** occurrence of each distinct moiety, kept in
pair-priority order until **25% of the section's characters** are spent. It
stores **14.7% of a section on average**, it is proportional so a short section
is not over-quoted nor a long one under-quoted, and it must be a **constraint
rather than a convention** — the failure mode is silent, additive, and only
visible in aggregate.

The 52.7% of distinct moieties that lose a window lose **only the window**.
Their occurrence, offsets and citation are stored regardless, because those are
clear under either reading of rule 6.

---

## 7. What the design round carries forward

1. **Recovery ships, and it is structural.** Routes 1 and 2 only:
   `openfda.unii` and DailyMed's `activeIngredient` block. 31,618 pairs, 28,269
   novel. Both read a field that distinguishes an active ingredient from an
   excipient *structurally*, so neither can key a statement to lactose.
2. **Route 3 does not ship** (owner's call, 2026-08-24), and it is recorded
   rather than forgotten: 6.2% genuinely wrong at rank 0, for +4,962 pairs.
3. **The 6,317-label overlap is a permanent calibration set.** Any future
   heuristic route has ground truth to be measured against before it ships, and
   this round is the precedent for using it.
4. **Unresolved labels are recorded, not discarded.** 19,862 orphan-wording
   labels are absent from DailyMed today and may not be tomorrow; a recovery
   route should run against a stored list, not a re-read of 1.73 GB.
5. **The quote budget is a constraint**: ±60 chars, first occurrence per
   moiety, 25% of section characters, enforced in the schema.
6. **Salt-grain resolution is [#67](https://github.com/cairn-ehr/drugref/issues/67)
   reached from a third side.** 17 labels resolve on the salt alone, and 41.6%
   of the name route's "errors" are salt spellings. Three sources now want the
   same missing relation.

---

## 8. Traps and standing notes

- **Count wordings, not labels, and re-derive the unit every time.** The parent
  round wrote the rule down and this round still found a figure quoted the wrong
  way — in the parent round's own summary. 60% of labels is 56% of wordings, and
  the two mean opposite things about whether the work is worth doing.
- **A perfect resolution rate and a poor coverage rate are separate facts.**
  DailyMed resolves 99.6% of what it holds and holds 24.9% of what was asked
  for. Reporting either alone describes a different source.
- **Split salt-grain errors out of precision figures.** The same measurement
  reads 47.8% wrong or 6.2% wrong depending on whether "right drug, wrong salt"
  is counted as a miss, and only one of those numbers supports a decision.
- **Ground truth from one route is how another route gets measured.** Route 2's
  output was route 3's validation set. A heuristic with no ground truth
  available should be treated as unmeasured, not as unmeasurable.
- **`openfda` present ≠ `openfda` populated.** The block exists on 100% of
  unkeyed records and is empty. A presence check would report full coverage.
- **A recovery probe that reads the wrong substance produces a confident number
  pointing the wrong way**, so both parsing traps are pinned as tests: an
  inactive ingredient is never the subject, and the salt is not the moiety.
