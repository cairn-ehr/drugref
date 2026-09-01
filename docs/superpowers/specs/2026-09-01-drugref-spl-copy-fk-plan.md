# drugref — issue 160: why the `spl_label_subject` `COPY` took ten minutes

**Measurement record, 2026-09-01.** Every figure here was measured on the real
2026-08-22 openFDA + 2026-08-21 DailyMed releases, on fresh databases built for
this round. Where this record and
[the 2026-08-27 ingest results](2026-08-27-drugref-slice-5c3-spl-ddi-ingest-results.md)
disagree about the CAUSE, **this record wins** — that one filed the symptom
undiagnosed and said so, and its one causal claim about foreign keys is
corrected in §5 below.

---

## 1. How to reproduce it

```bash
# ~0.5 s: drugref_spl is the pre-db/051 base both 5c.3 design rounds used.
createdb -T drugref_spl drugref_spl160fix
DSN='host=localhost port=5532 dbname=drugref_spl160fix user=postgres'
uv run drugref --dsn "$DSN" migrate

uv run drugref --dsn "$DSN" ingest spl \
    --openfda downloads/OPENFDA \
    --dailymed downloads/DAILYMED/dm_spl_release_human_rx_part*.zip \
    --release 'openfda-2026-08-22+dailymed-2026-08-21'
```

The one-variable ablation of §3 replays dumps of the real rows
(`\copy (SELECT * FROM drugref.<table>) TO …` from a completed run) into two
freshly cloned databases; the script is reproduced in §3.

---

## 2. What was measured, before anything was changed

The round opened by re-running the ingest end to end on `drugref_spl160`,
because the reader-skip census round's verification predated its own review's
fixes. **Every published figure reproduced exactly** — 68,550 labels of 262,032
records, 27,406 wordings, 29,952 pairs (26,598 novel), 73,867 subject rows,
1,297,944 occurrences, 138,187 quoted windows, `source_checksum`
`5d6a894b30ce…`, and all five route tallies — in **12 min 51 s** against the
census round's 10 min 43 s on a quieter machine.

`pg_stat_activity` was polled once a second for the whole run, which is how the
statement timings below were taken **without modifying the code under
measurement**:

| statement | rows | wall clock | backend state |
|---|---|---|---|
| `COPY spl_wording` | 27,406 | < 1 s | — |
| `COPY spl_label` | 68,550 | ~6 s | `idle in transaction` / `ClientRead` |
| **`COPY spl_label_subject`** | **73,867** | **630 s** | **`active`, no wait event** |
| `COPY spl_entity_occurrence` + `spl_wording_quote` | 1,436,131 | ~35 s | `active` |

⇒ **THE CONTROL WAS ALREADY INSIDE THE RUN.** 1,297,944 occurrence rows landed
in 35 s while 73,867 subject rows took 630 s — **17.6× more rows, 18× less
time**, in the same transaction, through the same writer, from the same client.
Two of the three causes issue 160 listed as untried die on that one line: it is
not the row volume, and it is not `COPY` (every one of these is a `COPY`).

A true interval CPU sample — cumulative CPU time diffed between two `ps`
readings, because macOS's `ps -o pcpu` reports a **lifetime average** and would
have shown a backend that burned a core an hour ago — put the backend at **96%
of a core**, and its entire lifetime CPU was this one statement. So the cost was
genuinely server-side and genuinely CPU.

---

## 3. The cause, taken from a stack sample rather than guessed

`sample <backend pid> 8` while the `COPY` was running. **6,748 of 6,748 samples**
were under one call path:

```
CopyFrom → AfterTriggerEndQuery → afterTriggerInvokeEvents → ExecCallTriggerFunc
  → RI_FKey_check_ins → RI_FKey_check → ri_PerformCheck → SPI_execute_snapshot
    → ExecLockRows → ExecScan → IndexNext → index_getnext_slot
      → index_fetch_heap → heap_hot_search_buffer → HeapTupleSatisfiesVisibility
```

100% in the **foreign-key check**, fired as an after-row trigger — and inside it,
in *heap fetches and visibility checks*, with `ReleaseAndReadBuffer` walking from
page to page. That is not the shape of a check that finds one row; it is the
shape of a scan that reads thousands and throws them away.

**A foreign-key check is a query, and the planner may satisfy it with any parent
index whose leading columns the check's equality quals cover.** The check reads

```sql
SELECT 1 FROM ONLY drugref.spl_label x
 WHERE ingest_run = $1 AND source = $2 AND set_id = $3 AND version = $4
 FOR KEY SHARE OF x
```

and `spl_label` carries **two** usable indexes: `spl_label_pkey` on all four
columns, and `spl_label_by_wording` on `(ingest_run, source, text_key)`.
`EXPLAIN`, with the parent in exactly the state the ingest leaves it in — freshly
`COPY`d inside the same transaction, never analysed:

```
LockRows  (cost=0.41..8.45 rows=1 width=10)
  ->  Index Scan using spl_label_by_wording on spl_label x  (cost=0.41..8.44 …)
        Index Cond: ((ingest_run = '9'::bigint) AND (source = 'SPL'::text))
        Filter:     ((set_id = '…'::text) AND (version = '5'::text))
```

`ingest_run` and `source` are **constant for the entire load**, so that index
condition matches **all 68,550 rows** and the filter discards 68,549 of them —
once per child row. After a single `ANALYZE`, nothing else changed:

```
LockRows  (cost=0.42..8.45 rows=1 width=10)
  ->  Index Scan using spl_label_pkey on spl_label x  (cost=0.42..8.44 …)
        Index Cond: ((ingest_run = …) AND (source = …) AND (set_id = …) AND (version = …))
```

⇒ **THE TWO PLANS COST AN IDENTICAL `8.44`, AND THAT IS THE WHOLE DEFECT.** With
`relpages = 0` and `reltuples = -1` the planner has nothing to tell the two
apart, so it is a coin toss that happens to land on the catastrophic one. This
is not a bad estimate being punished; it is *no* estimate, and the two candidates
tying.

### The ablation: one variable, full scale

Both variants run in their own freshly cloned database — so B does not inherit
the dead tuples, and therefore the non-zero `relpages`, that A's `ROLLBACK`
leaves behind — and load byte-identical dumps of the real rows in one
transaction:

| variant | the only difference | `COPY` of 73,867 subject rows |
|---|---|---|
| **A** — as shipped | — | **493,539 ms** (8 min 13.5 s) |
| **B** | `ANALYZE spl_wording; ANALYZE spl_label` | **1,352 ms** |

The two `ANALYZE`s cost **11.8 ms + 99.8 ms = 111.6 ms** and bought a **365×**
reduction.

---

## 4. The fix, and what it is measured to do

`spl_evidence.analyze_loaded_table`, called from the orchestrator immediately
after each parent is loaded and **before anything that references it** is
loaded. The plan is cached for the session at first use, so this is the only
moment it can be got right; `analyze_source_tables` at the end of the run — which
exists for the read-backs and is still needed for them — is far too late.

End to end on the real releases, `drugref_spl160fix`, same machine, same day:

| | before | after |
|---|---|---|
| `COPY spl_label_subject` | 630 s | **~2 s** |
| whole ingest | **12 min 51 s** | **2 min 09 s** |

**Nothing that had no licence to move, moved.** `spl_wording` 27,406,
`spl_label` 68,550, `spl_label_subject` 73,867, `spl_entity_occurrence`
1,297,944, `spl_wording_quote` 138,187, `spl_ddi_pair` 29,952 (26,598 novel),
`source_checksum` `5d6a894b30ce…`, and all five route tallies — `openfda_unii`
27,494, `dailymed_active_moiety` 10,555, `dailymed_active_substance` 23,
`absent_from_dailymed` 30,386, `unresolved` 92 — identical to 2026-08-27.

Pinned by `test_a_FK_PARENT_is_ANALYZED_BEFORE_THE_CHILD_THAT_REFERENCES_IT_is_loaded`,
which asserts the **cause** rather than a duration (a fixture corpus of three
wordings cannot show a 630-second stall), exactly as the read-back `ANALYZE` is
pinned by `reltuples >= 0`. Removing **either** `ANALYZE` fails it — both mutants
were run and both were killed.

### The census, so a second one cannot arrive quietly

An FK is exposed when the parent carries an index whose leading columns are a
**proper** subset of the referenced columns. Over all **138 foreign keys in
schema `drugref`**, measured 2026-09-01: **exactly one is exposed**, and it is
this one.

| child | parent | index the planner may pick | columns pinned |
|---|---|---|---|
| `spl_label_subject` | `spl_label` | `spl_label_by_wording` | 2 of 4 |

Every other parent in the schema has **only its primary key**, which is why no
other feed has ever shown this and why the mitigation is made for *all* parents
rather than for the one that failed: the exposure is created by adding an index
to a parent, an edit nowhere near the orchestrator. Pinned by
`test_ONE_foreign_key_in_the_schema_can_be_planned_onto_a_LOOSE_index`.

---

## 5. ⇒ The correction: the refutation that closed the right door

`analyze_source_tables`'s docstring stated, as measured fact:

> The obvious suspect was the foreign-key checks on the child `COPY` — freshly
> loaded parent, no stats, RI seq scans. That was measured and REFUTED: 20,000
> child rows against an unanalyzed 68,550-row parent insert in 175 ms, **because
> PostgreSQL's RI triggers use a plan pinned to the parent's primary key rather
> than a re-planned query.**

The measurement was real. **The reason given for it is false**, and it is the
half that got quoted forward: the plan is *pinned*, but to whatever was chosen at
**first use**, which is inside the load and before any `ANALYZE`. Pinned is not
the same as pinned to the primary key. The 175 ms result did not generalise
because a parent whose only index is its primary key — every other parent in this
schema — has no wrong plan available to pin.

⇒ **A REFUTATION IS A MEASUREMENT PLUS AN EXPLANATION, AND ONLY THE MEASUREMENT
WAS TAKEN.** The explanation was reasoning written in the voice of the
measurement beside it, it closed the door on the true cause for a whole round,
and it did so in a docstring — the most durable place in the repo to put a wrong
sentence. This is the same failure the round that wrote it flagged in its own
`+13%` paragraph and removed there: *reasoning presented as measurement.*

⇒ **AND THE INSTRUMENT MATTERED MORE THAN THE HYPOTHESES.** Issue 160 listed
three untried causes — `COPY` vs `INSERT`, ICU collation on `set_id`/`version`,
drop-and-rebuild indexes. **All three are wrong**, and no amount of ablating them
would have found the fourth. One 8-second stack sample of a running backend named
it outright. Where a cost is concentrated in one statement, sample the process
before designing an experiment about it.
