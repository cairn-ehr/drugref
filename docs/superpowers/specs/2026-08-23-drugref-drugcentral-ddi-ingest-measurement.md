# DrugCentral DDI ingest — measurement on the real release (Task 13, issue #101)

> Every command below was run against `drugref_dc049` (`drugref_dc101` TEMPLATE-copied, then
> migrated to `db/049`) on 2026-08-23. Outputs are transcribed verbatim from the terminal, not
> paraphrased. Nothing here was adjusted to make a figure match its prediction — the standing rule
> is that a mismatch is a finding, and (as recorded below) none was found.

## 0. Source integrity

```
$ shasum -a 256 downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz
055904d152d6c8eef4ee872b25f6476019682df8b5f49bcdf7cc018204f3e04f  downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz
```

Matches the digest recorded on `ingest_run.source_checksum` (§4) and the digest the 2026-08-23
re-measurement (`2026-08-23-drugref-drugcentral-ddi-remeasurement-results.md`) recorded. Same
bytes; every figure below describes the same dump that produced the predictions.

## 1. Build the reference database

Protected databases (`drugref_dc101` and the other kept controls) were left untouched; no other
session held a connection to `drugref_dc101` when the template copy was made.

```
$ psql "host=localhost port=5532 dbname=postgres user=postgres" -c \
  "CREATE DATABASE drugref_dc049 TEMPLATE drugref_dc101"
CREATE DATABASE

$ export DSN="host=localhost port=5532 dbname=drugref_dc049 user=postgres"
$ uv run drugref --dsn "$DSN" migrate
migrations applied

$ psql "$DSN" -Atc "SELECT max(filename) FROM drugref.schema_migration"
049_drugcentral_ddi.sql
```

Matches the expected head. `drugref_dc101` remains at `048_unknown_signature_status.sql` (confirmed
in §5), unmodified — it is the pre-`db/049` baseline used for the hot-path comparison.

## 2. Before-state — the two numbers that must not move, plus two more for context

```
$ psql "$DSN" -Atc "SELECT count(*) FROM drugref.ddi_candidate_pair"
21664
$ psql "$DSN" -Atc "SELECT count(*) FROM drugref.substance_moiety"
19438
$ psql "$DSN" -Atc "SELECT count(*) FROM drugref.moiety_contraindication"
1442
$ psql "$DSN" -Atc "SELECT count(*) FROM drugref.open_question WHERE is_current"
21842
```

Both guarded totals (21,664 / 19,438) match the reference database's known state before this
migration's ingest runs. `open_question` before-state is also captured at ROW level (all 21,842
`question_uuid`s, sorted) for the post-ingest reconciliation in §6 — count alone is not evidence
per issue #104.

## 3. Run the ingest and time it

```
$ time uv run drugref --dsn "$DSN" ingest drugcentral \
    --dump downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz --release 11012023
INFO drugref.ingest.drugcentral_run: drugcentral: 7571 bundleable of 7621 rows (50 excluded by rule 6) -> 7501 pairs; 37 unresolved, 0 self-pairs, 43 colliding registry keys
drugcentral: 7571 bundleable of 7621 rows (50 excluded by rule 6) -> 7501 pairs; 37 unresolved, 0 self-pairs, 43 colliding registry keys

uv run drugref --dsn "$DSN" ingest drugcentral --dump ... --release 11012023  15.53s user 0.48s system 79% cpu 20.183 total
```

**Wall-clock: 20.183 s total** (15.53 s user, 0.48 s system, 79% CPU) — almost all of it spent
decompressing and parsing the 1.4 GB gzip dump in the pure/streaming parser, which runs before any
database connection opens (architecture invariant: parsers are pure, orchestrators own the
transaction). Confirmed by `ingest_run.started_at`/`finished_at` in §4: the write transaction itself
took ~1.5 ms.

"43 colliding registry keys" is not one of the six predicted figures but is internally consistent
with the independent cross-check in §7: 14 duplicate InChIKeys + 29 duplicate CAS numbers = 43,
exactly the collision count the read-only spike measurement reports against the same registry.

## 4. Every figure, read back

```
$ psql "$DSN" -Atc "SELECT count(*) FROM drugref.drugcentral_ddi_assertion"
7571

$ psql "$DSN" -Atc "SELECT route_1, count(*) FROM drugref.drugcentral_ddi_assertion GROUP BY 1 ORDER BY 2 DESC"
display_name|7233
inchikey|297
cas|21
unresolved|20

$ psql "$DSN" -Atc "SELECT count(*) FROM drugref.drugcentral_ddi_pair"
7501

$ psql "$DSN" -Atc "SELECT severity, count(*) FROM drugref.drugcentral_ddi_pair GROUP BY 1"
moderate|5207
contraindicated|2294

$ psql "$DSN" -Atc "SELECT candidate_source, count(*) FROM drugref.exact_ddi_pair GROUP BY 1"
MED-RT|1442
DRUGCENTRAL|7501

$ psql "$DSN" -Atc "SELECT count(*) FROM drugref.gap_unresolved_ddi_endpoint"
10

$ psql "$DSN" -Atc "SELECT count(*) FROM drugref.open_question WHERE gap_kind = 'unresolved_ddi_endpoint' AND is_current"
10

$ psql "$DSN" -Atc "SELECT count(*) FROM drugref.ddi_candidate_pair"     # MUST still be 21664
21664

$ psql "$DSN" -Atc "SELECT count(*) FROM drugref.substance_moiety"       # MUST still be 19438
19438

$ psql "$DSN" -Atc "SELECT source_checksum FROM drugref.ingest_run WHERE source = 'DRUGCENTRAL'"
055904d152d6c8eef4ee872b25f6476019682df8b5f49bcdf7cc018204f3e04f
```

`ingest_run` full row (`\x` output):

```
ingest_run_id    | 6
source           | DRUGCENTRAL
upstream_release | 11012023
source_checksum  | 055904d152d6c8eef4ee872b25f6476019682df8b5f49bcdf7cc018204f3e04f
started_at       | 2026-08-23 19:05:11.939885+08
finished_at      | 2026-08-23 19:05:11.941419+08
writer           | drugcentral_run
```

`moiety_contraindication` after ingest, confirmed unmoved (this ingest never writes it):

```
$ psql "$DSN" -Atc "SELECT count(*) FROM drugref.moiety_contraindication"
1442
```

Note on `severity`: `drugcentral_ddi_pair.severity` (moderate/contraindicated) is drugref's own
`severity_kind` mapping, read AFTER the both-order collapse to 7,501 pairs (5,207 moderate + 2,294
contraindicated). The re-measurement's 2,307/5,264 prediction was stated over the pre-collapse
DrugCentral labels (`Critical`/`Significant`), which is a different, wider row set — reconciled
directly against source in §4a below.

### 4a. Pre-collapse severity — the direct check against the prediction

`drugcentral_ddi_assertion` carries DrugCentral's own `severity_label` before the both-order
collapse into `drugcentral_ddi_pair`, which is the correct denominator for the 2,307/5,264
prediction:

```
$ psql "$DSN" -Atc "SELECT severity_label, count(*) FROM drugref.drugcentral_ddi_assertion GROUP BY 1 ORDER BY 2 DESC"
Significant|5264
Critical|2307

$ psql "$DSN" -Atc "SELECT count(*) FROM drugref.drugcentral_ddi_assertion WHERE moiety_1_uuid IS NULL OR moiety_2_uuid IS NULL"
37
```

Matches the prediction exactly: 5,264 `Significant` + 2,307 `Critical` = 7,571 bundleable rows.

### 4b. `gap_unresolved_ddi_endpoint` contents

```
$ psql "$DSN" -c "SELECT * FROM drugref.gap_unresolved_ddi_endpoint ORDER BY 1"
   source    |        endpoint_name         | row_count | upstream_release
-------------+------------------------------+-----------+------------------
 DRUGCENTRAL | aluminium chlorohydrate      |         2 | 11012023
 DRUGCENTRAL | amyl nitrite                 |         1 | 11012023
 DRUGCENTRAL | atracurium                   |         7 | 11012023
 DRUGCENTRAL | doxacurium                   |         7 | 11012023
 DRUGCENTRAL | glycopyrronium bromide       |         2 | 11012023
 DRUGCENTRAL | mivacurium                   |         7 | 11012023
 DRUGCENTRAL | pentosan polysulfate         |         1 | 11012023
 DRUGCENTRAL | phytomenadione               |         4 | 11012023
 DRUGCENTRAL | sodium polystyrene sulfonate |         2 | 11012023
 DRUGCENTRAL | vitamin e                    |         4 | 11012023
(10 rows)
```

`row_count` sums to 2+1+7+7+2+7+1+4+2+4 = **37**, matching the 37 unresolvable-row count exactly.
These are the same 10 names the 2026-08-23 re-measurement's spike tool named (§6).

## 5. Hot-path check — `db/049` must not change `ddi_candidate_pair`'s plan

```
$ psql "$DSN" -c "EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM drugref.exact_ddi_pair
                WHERE moiety_lo = (SELECT moiety_lo FROM drugref.exact_ddi_pair LIMIT 1)"
 Append  (cost=0.81..391.76 rows=43 width=193) (actual time=0.171..1.493 rows=11.00 loops=1)
   Buffers: shared hit=33 read=214 written=36
   ...
 Planning Time: 2.203 ms
 Execution Time: 1.639 ms
```

(Full 100-line plan captured in the task's working notes; the top-level `Append`/`Planning`/
`Execution` lines above are its shape — this query is not the regression check, `ddi_candidate_pair`
below is.)

**Regression check — `ddi_candidate_pair`, on `drugref_dc049` (post-`db/049`):**

```
$ psql "$DSN" -c "EXPLAIN ANALYZE SELECT * FROM drugref.ddi_candidate_pair LIMIT 1"
 Limit  (cost=3975.32..4062.53 rows=1 width=126) (actual time=1.654..1.656 rows=1.00 loops=1)
   Buffers: shared hit=25 read=61 written=24
   ->  Unique  (cost=3975.32..479193.10 rows=5449 width=126) (actual time=1.654..1.655 rows=1.00 loops=1)
         ->  Incremental Sort  (cost=3975.32..479124.99 rows=5449 width=126) ...
 Planning:
   Buffers: shared hit=783 read=31 written=6
 Planning Time: 8.443 ms
 Execution Time: 1.831 ms
(96 rows)
```

**Baseline — the identical query on `drugref_dc101` (`db/048`, no `db/049` applied at all):**

```
$ psql "host=localhost port=5532 dbname=drugref_dc101 user=postgres" -Atc \
    "SELECT max(filename) FROM drugref.schema_migration"
048_unknown_signature_status.sql

$ psql "host=localhost port=5532 dbname=drugref_dc101 user=postgres" -c \
    "EXPLAIN ANALYZE SELECT * FROM drugref.ddi_candidate_pair LIMIT 1"
 Limit  (cost=3975.32..4062.53 rows=1 width=126) (actual time=1.395..1.396 rows=1.00 loops=1)
   Buffers: shared hit=28 read=58
   ->  Unique  (cost=3975.32..479193.10 rows=5449 width=126) (actual time=1.394..1.395 rows=1.00 loops=1)
         ->  Incremental Sort  (cost=3975.32..479124.99 rows=5449 width=126) ...
 Planning:
   Buffers: shared hit=764 read=50
 Planning Time: 36.502 ms
 Execution Time: 1.549 ms
(96 rows)
```

**Verdict: unchanged.** The planner's cost estimates are byte-for-byte identical between the two
databases at every node: top `Limit` cost `3975.32..4062.53`, `Unique`/`Incremental Sort` cost
`3975.32..479124.99`, `rows=5449 width=126` throughout, same join order, same node types, same
`Sort Key`/`Presorted Key`. The only differences are `Buffers` (page-cache state, not plan shape)
and `Planning Time`/`Execution Time` (both single-digit milliseconds either way — normal run-to-run
noise, not a cost-model change). `db/049` added no arm to this view's plan, which is exactly what
db/034's 3.6× regression makes worth checking on every migration that touches a shared view.

## 6. Independent cross-check — re-running the 2026-08-23 spike tool read-only against `drugref_dc101`

To verify the 50-row rule-6 exclusion breakdown (13 Stockley's + 37 Lexicomp), which is not stored
in any table (the ingest parser is pure/streaming and never persists the excluded rows or their
`ddi_ref_id`), the same tool that produced the original 2026-08-23 re-measurement was re-run,
read-only, against the untouched `drugref_dc101`:

```
$ time uv run python -m tools.drugcentral_ddi_spike \
    --dump downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz \
    --dsn "host=localhost port=5532 dbname=drugref_dc101 user=postgres" \
    --out "<scratchpad>/dc101-crosscheck.md"
using cached extract in downloads/DRUGCENTRAL/extracted
wrote <scratchpad>/dc101-crosscheck.md
uv run python -m tools.drugcentral_ddi_spike --dump ... --dsn ... --out ...  0.80s user 0.14s system 13% cpu 7.233 total
```

The tool connects with `conn.read_only = True` and issues only `SELECT`s (verified by reading
`load_registry()` in `tools/drugcentral_ddi_spike.py` before running it) — safe against a protected
database. Relevant excerpts from its output:

```
| `ddi_ref_id` | rows | what the dump says it is | rule 6 |
|---|---:|---|---|
| `2` | 7,571 | Veterans Health Administration (VHA) National Drug File - Reference Terminology (NDF-RT) — Veterans Health Administration | **clean — bundle** |
| `3` | 37 | Lexicomp Online — Wolters Kluwer Health | **out** |
| `1` | 13 | Stockley's Drug Interactions — Karen Baxter (ISBN 0853699143, 2010) | **out** |

Row accounting, cascade run: 7,571 rows = 37 unresolvable + 0 self-pair + 7,534 pair-yielding, and
those 7,534 rows collapse to 7,501 distinct unordered pairs.

| risk label | whole table | bundleable subset |
|---|---:|---:|
| `Critical` | 2,307 | 2,307 |
| `Significant` | 5,264 | 5,264 |

### The 10 endpoint names the cascade does not resolve
- `aluminium chlorohydrate` — `unresolved`
- `amyl nitrite` — `unresolved`
- `atracurium` — `unresolved`
- `doxacurium` — `unresolved`
- `glycopyrronium bromide` — `unresolved`
- `mivacurium` — `unresolved`
- `pentosan polysulfate` — `unresolved`
- `phytomenadione` — `unresolved`
- `sodium polystyrene sulfonate` — `unresolved`
- `vitamin e` — `unresolved`
```

Every figure and every name in this independent, read-only, differently-coded path (dump parsing +
registry SELECTs, no `drugref ingest` code involved) matches what the actual ingest wrote to
`drugref_dc049` in §3/§4/§4b exactly, name for name. Also confirmed from the same cache manifest
(`downloads/DRUGCENTRAL/extracted/manifest.json`, `dump_sha256` matching §0): `"ddi": 7621` — the
7,621 total row count independently from the extract, not just the ingest's log line.

## 7. `open_question` reconciliation at ROW level (not just count)

Per issue #104, a bare count is not evidence — question counts depend on which unrelated ingest ran
last. `question_uuid` was dumped, sorted, to `open_question_before.txt` in §2 (before the ingest ran)
via `SELECT question_uuid FROM drugref.open_question WHERE is_current ORDER BY 1`; the identical
query was re-run to `open_question_after.txt` after the ingest, and the two files were compared by
set difference:

```
$ psql "$DSN" -Atc "SELECT count(*) FROM drugref.open_question WHERE is_current"   # after
21852

$ comm -13 open_question_before.txt open_question_after.txt | wc -l    # added
10
$ comm -23 open_question_before.txt open_question_after.txt | wc -l    # removed
0
```

Net delta: 21,852 − 21,842 = **+10**, and the row-level diff confirms it is exactly 10 additions and
0 removals (no unrelated churn). The 10 added `question_uuid`s:

```
23bdbf27-c7ab-5b6d-97c6-0f37c199ec37
24600192-331d-582d-a973-466ec1b62b37
3d8dfbc9-6f8e-50ef-a718-f139b166a5b7
57353ce2-7c86-5a0b-bdce-84af778869ce
59de7ede-9e10-589e-a843-60622b2e5a46
7431fd3d-7213-5504-939b-82c9ae39c035
9ecaad94-b6c0-5536-ba7d-3d7fb27731a2
e756ca52-996c-5b99-be41-381b1c5a8b3d
f957df87-344e-5dd6-8201-39cfa7790844
f976f3d5-df56-5cf1-af20-b99c8ce7d7e7
```

```
$ psql "$DSN" -Atc "SELECT gap_kind, count(*) FROM drugref.open_question
    WHERE question_uuid IN (<the 10 above>) GROUP BY 1"
unresolved_ddi_endpoint|10
```

All 10 additions carry `gap_kind = 'unresolved_ddi_endpoint'` — exactly the 10 rows `register_from_gaps`
seeded from `gap_unresolved_ddi_endpoint` (§4b). Every pre-existing `gap_kind`'s count (11 kinds,
21,842 rows total before) is byte-identical after ingest:

```
Before                                  After
condition_without_indication|97         condition_without_indication|97
dead_by_expansion_policy|1              dead_by_expansion_policy|1
unclassified_moiety|16089               unclassified_moiety|16089
uncurated_additive_effect|381           uncurated_additive_effect|381
uncurated_condition_contradiction|168   uncurated_condition_contradiction|168
uncurated_interaction_rule|595          uncurated_interaction_rule|595
unmatched_ingredient|2150               unmatched_ingredient|2150
unpopulated_contraindication|13         unpopulated_contraindication|13
unresolved_ci_object|103                unresolved_ci_object|103
                                         unresolved_ddi_endpoint|10   <- new
unruled_composition_activity|2245       unruled_composition_activity|2245
```

No unrelated gap kind moved — this ingest's `register_from_gaps` call touched only the grain it
owns.

## 8. Predictions — MATCHED / MISMATCHED

| # | Prediction | Measured | Verdict |
|---|---|---|---|
| 1 | 7,621 `ddi` rows total | 7,621 (ingest log; independently, extract manifest `"ddi": 7621`) | **MATCHED** |
| 2 | 7,571 bundleable (`ddi_ref_id = 2`) | 7,571 (`drugcentral_ddi_assertion` count; ingest log) | **MATCHED** |
| 3 | 50 excluded (13 Stockley's + 37 Lexicomp) | 50 excluded (ingest log); independently, `ddi_ref_id=1` → 13 rows (Stockley's), `ddi_ref_id=3` → 37 rows (Lexicomp) per §6 | **MATCHED** |
| 4 | 7,534 pair-yielding rows | 7,534 (7,571 bundleable − 37 unresolvable, confirmed via §6's row accounting) | **MATCHED** |
| 5 | 37 unresolvable rows | 37 (ingest log; `drugcentral_ddi_assertion` rows with a NULL moiety UUID; §4b) | **MATCHED** |
| 6 | 0 self-pairs | 0 (ingest log) | **MATCHED** |
| 7 | 7,501 distinct unordered moiety pairs | 7,501 (`drugcentral_ddi_pair` count) | **MATCHED** |
| 8 | 10 distinct unresolved endpoint names (10 `gap_unresolved_ddi_endpoint` rows) | 10 (both counts; names match §6's list exactly) | **MATCHED** |
| 9 | Severity bands before collapse: 2,307 `Critical`, 5,264 `Significant` | 2,307 `Critical`, 5,264 `Significant` (`drugcentral_ddi_assertion.severity_label`, §4a) | **MATCHED** |
| 10 | `ddi_candidate_pair` still 21,664 | 21,664 before and after | **MATCHED** |
| 11 | `substance_moiety` still 19,438 | 19,438 before and after | **MATCHED** |
| 12 | Hot path: `db/049` must not change `ddi_candidate_pair`'s plan | Cost estimates identical, node-for-node, between `drugref_dc049` (post-`049`) and `drugref_dc101` (pre-`049`) | **MATCHED** |

**Zero mismatches.** Every figure the 2026-08-23 re-measurement predicted reproduced exactly on the
next execution of the same code path (the ingest) and, independently, on a second, differently
coded execution path (the read-only spike tool against the untouched `drugref_dc101`). This is the
expected outcome directly after a re-measurement round that fixed the instrument — the earlier
seven-figure drift happened over rounds of hand-copied numbers with no re-derivation tool; here the
tool and the ingest agree with each other and with themselves.

## 9. Non-predicted but noteworthy figures

- **43 colliding registry keys** (ingest log) = 14 duplicate InChIKeys + 29 duplicate CAS numbers,
  matching the collision counts the independent spike measurement reports for the same registry
  snapshot (§6, and consistent with `PROJECT-NOTES.md`'s account of the same collision check).
  Not a defect: `first_wins`/`ORDER BY` make the collision resolve deterministically, and it was
  already known and tested before this ingest.
- **Ingest DB transaction duration**: `ingest_run.started_at` to `finished_at` spans ~1.5 ms, far
  shorter than the 20.183 s wall-clock — nearly all wall-clock time is spent in the pure/streaming
  dump parser, before the transaction (and the single writer) ever opens a connection. Consistent
  with the architecture invariant that parsers are pure and orchestrators own the transaction.
- `exact_ddi_pair` by `candidate_source`: `MED-RT` 1,442 (unchanged — this ingest never writes
  `moiety_contraindication`), `DRUGCENTRAL` 7,501 (new).
- `drugcentral_ddi_assertion.route_1` breakdown: `display_name` 7,233, `inchikey` 297, `cas` 21,
  `unresolved` 20 — the routes that resolved the FIRST endpoint of each bundleable row (not the
  585-name-level cascade figures in §6, which are per distinct folded name; this is per row and
  therefore weighted by how often each name recurs across rows).

## Summary

| | |
|---|---|
| Database | `drugref_dc049` (`TEMPLATE drugref_dc101`, migrated to head) |
| Migration head | `049_drugcentral_ddi.sql` |
| Baseline database | `drugref_dc101`, head `048_unknown_signature_status.sql` (untouched, read-only) |
| Dump | `downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz`, 1,400,714,190 bytes |
| SHA-256 | `055904d152d6c8eef4ee872b25f6476019682df8b5f49bcdf7cc018204f3e04f` — matches prediction |
| Ingest wall-clock | 20.183 s (15.53 s user, 0.48 s system, 79% CPU) |
| `ingest_run_id` | 6 |
| Predictions matched | 12 / 12 |
| Predictions mismatched | 0 / 12 |
| Code defects found | none |
