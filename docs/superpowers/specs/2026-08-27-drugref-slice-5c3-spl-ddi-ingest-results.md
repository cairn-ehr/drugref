# drugref — slice 5c.3: what `db/051` and the SPL ingest actually produced

**Measurement record, 2026-08-27.** Every figure here was re-derived from a fresh
database built from the real releases. Where this record and the
[design spec](2026-08-24-drugref-slice-5c3-spl-ddi-ingest-design.md) disagree,
**this record wins** — it is the measurement and the design was the prediction.

The design's own instruction is honoured literally: *the pair figures are a
FLOOR, not a target, and an ingest reproducing more is not failing its check.*
It reproduced more, and §3 says exactly where the extra came from.

---

## 1. How to reproduce it

```bash
# ~4 min: a database holding every vocabulary at once. drugref_spl is the
# measurement database both 5c.3 design rounds used; see PROJECT-NOTES
# § "Current DSN" for how it was built.
createdb -T drugref_spl drugref_spl051
DSN='host=localhost port=5532 dbname=drugref_spl051 user=postgres'
uv run drugref --dsn "$DSN" migrate            # applies db/051

# ~12.5 min wall clock on an M-series laptop, reading 19.3 GB.
uv run drugref --dsn "$DSN" ingest spl \
    --openfda downloads/OPENFDA \
    --dailymed downloads/DAILYMED/dm_spl_release_human_rx_part*.zip \
    --release 'openfda-2026-08-22+dailymed-2026-08-21'
```

The corpora are the ones the two measurement rounds read: openFDA `drug/label`,
14 partitions, `export_date` 2026-08-22, 1.73 GB; DailyMed Human Rx, 6 parts,
`last-modified` 2026-08-21, 17.6 GB. **`downloads/` is gitignored**, so the
per-file SHA-256s are in the mining measurement record §2 — verify against that
table, not against a manifest that disappears with the bytes it describes.

The combined `ingest_run.source_checksum` over all twenty files is
`5d6a894b30ce…`.

---

## 2. What it produced

| | measured |
|---|---|
| openFDA records read | 262,032 |
| section-carrying labels | **68,550** |
| distinct wordings | **27,406** (2.50 labels to one) |
| registry it resolved against | 19,438 moiety names, 19,438 live UNII claims |
| DailyMed labels looked for | 41,056 |
| DailyMed documents read | 54,813 |
| DailyMed labels found | **10,670** (26.0% of those looked for) |
| entity occurrences | **1,297,944** over 26,760 wordings |
| distinct moieties named | 2,151 |
| ambiguous occurrences | 5,176 (0.40%) |
| quoted windows | **138,187** |
| **distinct candidate pairs** | **29,952** |
| **novel against everything held** | **26,598 (88.8%)** |
| evidence rows (pair × citing label) | 1,470,708 |
| self-pairs excluded | 404,764 |
| recovery-register rows | 30,478 |

**Subjects, per label** — and they sum to 68,550 exactly:

| route | labels | rows |
|---|---|---|
| `openfda_unii` | 27,494 | 30,863 |
| `dailymed_active_moiety` | 10,555 | 12,502 |
| `dailymed_active_substance` | 23 | 24 |
| `absent_from_dailymed` | 30,386 | 30,386 |
| `unresolved` | 92 | 92 |

*Rows exceed labels because a combination product carries several subjects on one
route. The label count is the resolution rate; the row count is not, and
reporting the second would publish the combination rate as if it were the first.*

**The three counts the design said had licence to move: none of them moved.**
`substance_moiety` 19,438, `ddi_candidate_pair` 21,877, `exact_ddi_pair` 8,943 —
identical before and after, which is what "additive, no existing query changes"
has to mean in practice.

---

## 3. Against the design's expectations — and the one it got badly wrong

| | design | measured | |
|---|---|---|---|
| section-carrying labels | 68,550 | **68,550** | ✅ exact |
| distinct wordings | 27,406 | **27,406** | ✅ exact |
| labels with a resolved subject | 34,008 | **38,072** | +4,064 |
| unresolved, recorded | 34,542 | **30,478** | −4,064 |
| distinct candidate pairs | ≥ 29,258 | **29,952** | ✅ floor cleared by 694 |
| novel | ≥ 25,960 (88.7%) | **26,598 (88.8%)** | ✅ cleared by 638 |

The design predicted the direction of every one of these and said why: the probe
scanned only the 26,401 **orphan-wording** labels, while the ingest scans all
**41,056** labels with no resolved subject — the extra 14,655 being the 14,455
that share a keyed label's wording plus the 200 carrying a UNII drugref does not
hold. So the floor was a floor, and it was cleared.

### ⇒ BUT THE `unresolved` BUCKET WAS NOT 14,680. IT IS 92.

This is the one number the design got wrong by two orders of magnitude, and the
error is instructive rather than arithmetic.

The design's route table filed **14,680** labels as `unresolved` — *"present,
read, and still unkeyable"* — and arrived at it as `14,455 + 25 + 200`. But the
14,455 had **never been read**: they were the labels the probe skipped as an
optimisation, and the design assigned them to a bucket whose own definition says
they were read. Scanned for real, **30,386 of the 41,056 targets are simply not
in the current DailyMed release**, and only **92 labels in the entire corpus are
present in it and still unkeyable**.

**That reframes what the recovery register is for.** It is overwhelmingly a
RELEASE gap (30,386 rows, 99.7%) and barely a REGISTRY gap (92 rows, 0.3%) —
which is the opposite of what a reader of the design's table would plan for. A
future recovery route should target the release (a fuller DailyMed corpus, or
another publisher of the same SPL) and not registry coverage.

⇒ **The lesson is one this slice already had a name for and applied anyway:
*absence is a population, not a bug* — and a population you did not read is not
evidence about the population you did.** The design put unscanned labels into a
bucket defined by having been scanned, and its own prose said so two paragraphs
below. Nothing checked the two against each other because they were prose.

### The other two the ingest re-derived rather than inherited

- **DailyMed resolves 99.1% of what it holds** — 10,578 of 10,670. The design
  measured 99.6% over its smaller sample (6,514 of 6,539). *A perfect resolution
  rate and a poor coverage rate remain separate facts*: it holds **26.0%** of
  what was asked for.
- **The salt route stays tiny**: 23 labels, against the design's 16. It is still
  counted apart, because it still needs the salt-to-base step of
  [#67](https://github.com/cairn-ehr/drugref/issues/67) that drugref does not
  have.

---

## 4. The quoted window, re-derived from the shipped writer

The design's window rule was measured by `tools/spl_quote_budget.py` over the
probe's cache. These figures come from the SHIPPED writer's own output, read back
out of `spl_wording_quote`, over the 26,760 wordings that name a moiety:

| | design | measured |
|---|---|---|
| mean % of a section stored | 20.4% | **20.5%** |
| merged windows per wording | 5.1 | **5.2** |
| distinct moieties covered by a window | 71.6% | **74.5%** |
| share of the 25% budget actually used | — | **88.1%** |

The first two reproduce. The third is 2.9 points higher and the cause is §5's:
the shipped matcher holds no class vocabulary, so a few thousand spans a class
name used to consume are now moiety occurrences, and more of them fall inside a
stored window.

**In absolute characters, and stated per population because the two differ by
enough to matter** — the first draft of this section quoted the stored figure
against the whole corpus's budget and the percentages against the naming-a-moiety
subset, which is the mixed-denominator error this slice has recorded three times:

| population | section characters | 25% budget | stored |
|---|---|---|---|
| all 27,406 wordings | 104,384,065 | 26,106,268 | 22,954,172 (87.9% of budget) |
| the 26,760 naming a moiety | 104,163,385 | 26,050,856 | 22,954,172 (88.1% of budget) |

*Every stored character belongs to a wording that names a moiety, so the stored
column is identical; only the denominators move.* **22.0% of the corpus's prose
is stored, against the 25% the determination allows** — and it holds because a
trigger enforces it rather than because the writer intended to. `db/051`'s
`spl_wording_quote_within_budget` re-computes the sum per wording at commit,
refuses an overlap, and refuses a window naming a character the wording does not
have.

---

## 5. Dropping the class vocabulary CHANGED THE DRUG × DRUG YIELD

The openFDA-only arm — the arm the design's 20,554-pair baseline was computed
over — yields **20,747** here. That is a **+193** difference against a figure
this slice's own design cites, and it is not noise.

The cause is measured rather than asserted, by
`tools/spl_class_vocabulary_delta.py`, which runs the whole 27,406-wording corpus
through both vocabularies and prints the difference:

| | shipped (drug × drug) | + 8,534 class entries | delta |
|---|---|---|---|
| moiety occurrences | 1,297,944 | 1,286,775 | **+11,169** |
| wordings naming a moiety | 26,760 | **26,721** | +39 |
| openFDA-arm distinct pairs | 20,747 | **20,554** | **+193** |

**The right-hand column reproduces the design's two published figures exactly** —
20,554 pairs over 26,721 wordings — which is the strongest available evidence
that the shipped matcher and the measurement round's are the same rule with one
vocabulary removed, and that the +193 is that removal and nothing else.

The mechanism is longest-match-wins: the measurement round matched moieties **and
classes together**, because it was sizing both grains at once, and a class name
consumes a span a moiety name would otherwise have matched — `Serotonin Uptake
Inhibitors` swallowing `serotonin`. **11,169 occurrences** were being consumed
that way. The shipped ingest is drug × drug only, so they come back.

⇒ **A vocabulary is part of a measurement's definition, not its scenery.**
Deferring the class half did not merely postpone the class figures; it moved the
drug × drug ones. Any future round that re-adds classes must expect the drug ×
drug yield to fall, and must not read the fall as a regression.

---

## 6. Two performance findings, one of them repo-wide

### ⇒ `ingest_run.finished_at − started_at` IS NOT A DURATION, FOR ANY FEED

Measured on this database, across every run it holds:

| source | `finished_at − started_at` |
|---|---|
| UNII, MED-RT, MeSH, GSRS, DRUGCENTRAL, FDA-CYP, ONCHIGH | 1.3 – 24 ms |
| MED-RT (`mesh_rel_run`) | 48.3 s |
| SPL | 49.9 s |

Every one of those is wrong as a runtime, and the SPL ingest demonstrably takes
**about 12.5 minutes**. `provenance.open_run` stamps `started_at DEFAULT now()`,
and `finish_run` sets `finished_at = now()` inside the *work* transaction —
and `now()` is `transaction_timestamp()`, not the clock. So the subtraction
measures the gap between two transaction start times and never the work between
them. The two nonzero rows are the two orchestrators that do heavy reading
*between* opening the run and their first write.

An operator sizing a rebuild from this column would conclude every feed loads in
milliseconds. **Filed, not fixed** — the fix touches every orchestrator's
provenance and belongs in its own round.

### The write half is slow, and the `spl_label_subject` `COPY` is why

~12.5 min total, of which the read of 262,032 openFDA records, the scan of 6
DailyMed parts and the SHA-256 over 19.3 GB together take **under 50 seconds**.
The remaining ~11.5 minutes are the write, and within it the `COPY` of 73,867
subject rows alone runs **over four minutes at 100% server CPU** — against 0.6 s
for the same row count in a synthetic probe against the same schema. Not
diagnosed; filed.

### ⇒ AND THE FIRST DIAGNOSIS OF THE STALL WAS WRONG, WHICH IS WHY IT IS RECORDED

The ingest's first run did not finish: it sat **25 minutes at 100% CPU** in the
self-pair read-back and was cancelled. The obvious cause was foreign-key checks
against a freshly bulk-loaded parent with no statistics — and that was measured
and **refuted**: 20,000 child rows against an unanalyzed 68,550-row parent insert
in **175 ms**, because PostgreSQL's RI triggers use a plan pinned to the parent's
primary key rather than a re-planned query.

The real cause was the same missing statistics one table further on: the
orchestrator's own read-backs join tables the same transaction just `COPY`'d, so
the planner costs them as if empty and picks a nested loop over 1.3 million
occurrence rows. `spl_evidence.analyze_source_tables` now runs inside the
transaction before them, and `test_the_projection_is_ANALYZED_before_its_own_read_backs`
pins it by asserting `pg_class.reltuples >= 0` for all five tables — a mutation
that removes the `ANALYZE` fails it.

---

## 7. What the round changed about the design

Nothing in the shape: five tables, two views, one gap view, the parser/orchestrator
split and the source-admission trio all shipped as specified. Three additions the
design left open, each recorded where it was decided:

1. **The second view is named.** The design said "two views" and named one.
   `spl_ddi_pair` is PAIR grain — `count(*)` *is* the pair count, directly
   comparable with `drugcentral_ddi_pair` — and `spl_ddi_evidence` is EVIDENCE
   grain, one row per citation. This project has published a figure in the wrong
   unit in three consecutive rounds; a consumer who counts the wrong view now
   gets a name that contradicts them.
2. **The gap view is not a question kind**, and `db/051` §8 argues it: a curator
   cannot answer *"not in the current DailyMed release"*, which is db/012's test
   for whether the review gate may ask at all — and §3 above shows that is
   **99.7%** of the register, not a corner of it.
3. **The suppression derivation ships as a candidate generator, never a
   vocabulary.** `spl_match.next_word_profiles` / `suppression_candidates` and
   `tools/spl_suppress_derive.py`. `lead`/`to` and `warfarin`/`sodium` have the
   same distributional shape and opposite readings, so the tool measures, ranks
   and stops.

---

## 8. What is still owed

- **[#158](https://github.com/cairn-ehr/drugref/issues/158)** — route 3's
  calibration set, unchanged by this round.
- The two performance findings in §6, both filed rather than fixed.
- The class grain, the potency band, the word-order gap and salt-grain
  resolution: exactly as the design spec §8 left them.
