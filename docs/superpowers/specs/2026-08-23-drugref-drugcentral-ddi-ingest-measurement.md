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
directly against source in §4b below.

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

**This paragraph originally claimed the post-collapse severity figures (§4, `drugcentral_ddi_pair`:
5,207 moderate / 2,294 contraindicated) were "reconciled directly against source" here. They were
not — §4a only re-verified the PRE-collapse split against the prediction and never computed the
delta to the post-collapse counts. That was a defect in this record, caught on review, and is fixed
by §4b below rather than by softening the sentence.**

### 4b. The reconciliation §4a claimed but did not do, plus the fact it uncovered

`drugcentral_ddi_pair.severity` is read AFTER `db/049`'s both-order collapse (moiety_lo/moiety_hi,
`DISTINCT ON`, most-severe-wins — see the migration's own comment above the view, quoted in §5). The
70-row gap between the pre-collapse 7,571 bundleable rows and the post-collapse 7,501 pairs has two
completely different causes, and both are checkable without a database:

```
pre-collapse   (drugcentral_ddi_assertion, all bundleable rows):  2,307 Critical + 5,264 Significant = 7,571
post-collapse  (drugcentral_ddi_pair, one row per unordered pair): 2,294 contraindicated + 5,207 moderate = 7,501
70 rows leave: 7,571 − 7,501 = 70
```

**Where the 70 go — two disjoint causes:**

```
$ psql "$DSN" -Atc "SELECT severity_label, count(*) FROM drugref.drugcentral_ddi_assertion
    WHERE moiety_1_uuid IS NULL OR moiety_2_uuid IS NULL GROUP BY 1 ORDER BY 1"
Critical|2
Significant|35
```

37 of the 70 are the unresolvable rows (§4c) — they never reach the pair view at all (2 Critical + 35
Significant = 37).

```
$ psql "$DSN" -Atc "
  SELECT count(*) FROM (
    SELECT least(a.moiety_1_uuid, a.moiety_2_uuid) AS lo, greatest(a.moiety_1_uuid, a.moiety_2_uuid) AS hi
    FROM drugref.drugcentral_ddi_assertion a
    WHERE a.moiety_1_uuid IS NOT NULL AND a.moiety_2_uuid IS NOT NULL AND a.moiety_1_uuid <> a.moiety_2_uuid
    GROUP BY 1,2 HAVING count(*) > 1
  ) dup"
33
```

The other 33 are the both-order duplicates `db/049`'s own comment describes ("the source publishes
33 pairs in both orders … and they are one pair"): each of these 33 unordered pairs has exactly 2
rows in `drugcentral_ddi_assertion` (one per orientation) and collapses to 1 row in
`drugcentral_ddi_pair`, so exactly 1 row per pair — 33 rows total — disappears in the collapse.
37 + 33 = **70**, matching the gap exactly.

**The 70 split by label — checkable arithmetic, no query required beyond what's already above:**

```
Critical:    2,307 (pre) − 2,294 (post) = 13
Significant: 5,264 (pre) − 5,207 (post) = 57
13 + 57 = 70
```

That 13/57 split itself decomposes cleanly into the two causes:

```
$ psql "$DSN" -Atc "
WITH resolved AS (
  SELECT least(a.moiety_1_uuid,a.moiety_2_uuid) AS lo, greatest(a.moiety_1_uuid,a.moiety_2_uuid) AS hi,
         a.severity_label
  FROM drugref.drugcentral_ddi_assertion a
  WHERE a.moiety_1_uuid IS NOT NULL AND a.moiety_2_uuid IS NOT NULL AND a.moiety_1_uuid <> a.moiety_2_uuid
), dup AS (SELECT lo, hi FROM resolved GROUP BY 1,2 HAVING count(*) > 1)
SELECT array_agg(r.severity_label ORDER BY r.severity_label), count(*)
FROM resolved r JOIN dup d USING (lo, hi)
GROUP BY r.lo, r.hi"
{Critical,Critical}      -- 11 pairs (22 rows, 11 removed by the collapse)
{Significant,Significant} -- 18 pairs (36 rows, 18 removed by the collapse)
{Critical,Significant}    -- 4 pairs  (8 rows, 4 removed by the collapse — see below)
```

- Same-label duplicates: 11 Critical-only pairs + 18 Significant-only pairs = 29 of the 33 (matches
  `db/049`'s comment: "29 of the 33 duplicates carry the same band"). Collapsing removes exactly one
  row per pair, so **11 Critical + 18 Significant rows** disappear here.
- Critical total removed: 2 (unresolvable) + 11 (same-label collapse) + 0 (conflicting collapse,
  see below) = **13**. Matches the arithmetic above.
- Significant total removed: 35 (unresolvable) + 18 (same-label collapse) + 4 (conflicting collapse)
  = **57**. Matches the arithmetic above.

**The headline fact this arithmetic uncovers: 4 of the 33 duplicate pairs carry CONFLICTING
labels** — `db/049`'s own comment says as much ("MOST-SEVERE-WINS between two orientations that
disagree (4 of the 33 do)") but this measurement had never actually located or named them before.
Confirmed directly:

```
$ psql "$DSN" -Atc "
  SELECT count(*) FROM (
    SELECT least(a.moiety_1_uuid, a.moiety_2_uuid) AS lo, greatest(a.moiety_1_uuid, a.moiety_2_uuid) AS hi
    FROM drugref.drugcentral_ddi_assertion a
    WHERE a.moiety_1_uuid IS NOT NULL AND a.moiety_2_uuid IS NOT NULL AND a.moiety_1_uuid <> a.moiety_2_uuid
    GROUP BY 1,2 HAVING count(*) > 1 AND count(DISTINCT severity_label) > 1
  ) conflict"
4
```

Of the 7,501 final pairs, 7,497 are single-label (every row that maps to that pair agrees) and 4 are
not:

```
pairs where every row agrees:  2,290 Critical-only + 5,207 Significant-only = 7,497
pairs where rows disagree:     4 (Critical vs Significant)
7,497 + 4 = 7,501
```

`severity_rank` (rank 1 = most severe, set by db/035) resolves all 4 conflicts toward `Critical` —
confirmed by reading the actual `drugcentral_ddi_pair` rows for these 4 pairs, not inferred:

```
$ psql "$DSN" -c "
SELECT p.moiety_lo, p.moiety_hi, p.severity, p.upstream_severity_label, p.upstream_key
FROM drugref.drugcentral_ddi_pair p
WHERE (p.moiety_lo, p.moiety_hi) IN (
  ('089c94b9-d21a-5f76-b322-c64792f7fd28','553a58eb-a2fa-53e4-b48d-d2aa8f54c940'),
  ('089c94b9-d21a-5f76-b322-c64792f7fd28','e630be20-48cf-5744-8fc9-fe2d7a52b988'),
  ('3e822e77-feee-5a08-84e3-3568f81754ff','be22ef76-7bcb-5198-a1dd-e14f60398d98'),
  ('9a8b72c9-4d19-5656-bc87-b66301e0ee86','be22ef76-7bcb-5198-a1dd-e14f60398d98')
) ORDER BY 1,2"
              moiety_lo               |              moiety_hi               |    severity     | upstream_severity_label |  upstream_key
--------------------------------------+--------------------------------------+------------------+-------------------------+-----------------
 089c94b9-d21a-5f76-b322-c64792f7fd28 | 553a58eb-a2fa-53e4-b48d-d2aa8f54c940 | contraindicated | Critical                | C56.3580
 089c94b9-d21a-5f76-b322-c64792f7fd28 | e630be20-48cf-5744-8fc9-fe2d7a52b988 | contraindicated | Critical                | C56^4767^
 3e822e77-feee-5a08-84e3-3568f81754ff | be22ef76-7bcb-5198-a1dd-e14f60398d98 | contraindicated | Critical                | C56^4966^
 9a8b72c9-4d19-5656-bc87-b66301e0ee86 | be22ef76-7bcb-5198-a1dd-e14f60398d98 | contraindicated | Critical                | C23308143128526
(4 rows)
```

All four land `contraindicated`, all four carry `upstream_severity_label = Critical`. So:

```
2,290 (Critical-only) + 4 (conflicts, all won by Critical) = 2,294 contraindicated  -- matches §4 exactly
5,207 (Significant-only) + 0 (no conflict is won by Significant)  = 5,207 moderate  -- matches §4 exactly
```

**The 4 pairs, named**, with both raw (per-orientation) rows shown so a reader can check the clinical
judgement without trusting the collapse:

```
$ psql "$DSN" -Atc "SELECT moiety_uuid, display_name FROM drugref.substance_moiety
    WHERE moiety_uuid IN (
      '089c94b9-d21a-5f76-b322-c64792f7fd28','553a58eb-a2fa-53e4-b48d-d2aa8f54c940',
      'e630be20-48cf-5744-8fc9-fe2d7a52b988','3e822e77-feee-5a08-84e3-3568f81754ff',
      'be22ef76-7bcb-5198-a1dd-e14f60398d98','9a8b72c9-4d19-5656-bc87-b66301e0ee86'
    ) ORDER BY 1"
089c94b9-d21a-5f76-b322-c64792f7fd28|atazanavir
3e822e77-feee-5a08-84e3-3568f81754ff|gemfibrozil
553a58eb-a2fa-53e4-b48d-d2aa8f54c940|atorvastatin
9a8b72c9-4d19-5656-bc87-b66301e0ee86|gatifloxacin
be22ef76-7bcb-5198-a1dd-e14f60398d98|pioglitazone
e630be20-48cf-5744-8fc9-fe2d7a52b988|rifapentine
```

| pair | orientation A | orientation B | final `drugcentral_ddi_pair.severity` |
|---|---|---|---|
| atazanavir / atorvastatin | `ATAZANAVIR SO4/ATORVASTATIN CALCIUM` → **Critical** (`C56.3580`) | `ATAZANAVIR/ATORVASTATIN CALCIUM` → Significant (`C56^4090^`) | `contraindicated` |
| atazanavir / rifapentine | `ATAZANAVIR SO4/RIFAPENTINE` → Significant (`C23306845143447`) | `ATAZANAVIR/RIFAPENTINE` → **Critical** (`C56^4767^`) | `contraindicated` |
| gemfibrozil / pioglitazone | `GEMFIBROZIL/PIOGLITAZONE HCL` → Significant (`C56.3352`) | `GEMFIBROZIL/PIOGLITAZONE` → **Critical** (`C56^4966^`) | `contraindicated` |
| gatifloxacin / pioglitazone | `GATIFLOXACIN/PIOGLITAZONE HCL` → **Critical** (`C23308143128526`) | `GATIFLOXACIN/PIOGLITAZONE` → Significant (`C56^4265^`) | `contraindicated` |

Raw rows, for direct inspection (`upstream_label` verbatim from the source):

```
$ psql "$DSN" -c "
SELECT a.moiety_1_uuid, a.moiety_2_uuid, a.severity_label, a.upstream_key, a.upstream_label
FROM drugref.drugcentral_ddi_assertion a
WHERE (least(a.moiety_1_uuid,a.moiety_2_uuid), greatest(a.moiety_1_uuid,a.moiety_2_uuid)) IN (
  ('089c94b9-d21a-5f76-b322-c64792f7fd28','553a58eb-a2fa-53e4-b48d-d2aa8f54c940'),
  ('089c94b9-d21a-5f76-b322-c64792f7fd28','e630be20-48cf-5744-8fc9-fe2d7a52b988'),
  ('3e822e77-feee-5a08-84e3-3568f81754ff','be22ef76-7bcb-5198-a1dd-e14f60398d98'),
  ('9a8b72c9-4d19-5656-bc87-b66301e0ee86','be22ef76-7bcb-5198-a1dd-e14f60398d98')
) ORDER BY 1,2,3"
            moiety_1_uuid             |            moiety_2_uuid             | severity_label |  upstream_key   |                      upstream_label
--------------------------------------+--------------------------------------+-----------------+-----------------+-----------------------------------------------------------
 089c94b9-d21a-5f76-b322-c64792f7fd28 | 553a58eb-a2fa-53e4-b48d-d2aa8f54c940 | Critical        | C56.3580        | ATAZANAVIR SO4/ATORVASTATIN CALCIUM [VA Drug Interaction]
 089c94b9-d21a-5f76-b322-c64792f7fd28 | e630be20-48cf-5744-8fc9-fe2d7a52b988 | Significant     | C23306845143447 | ATAZANAVIR SO4/RIFAPENTINE [VA Drug Interaction]
 3e822e77-feee-5a08-84e3-3568f81754ff | be22ef76-7bcb-5198-a1dd-e14f60398d98 | Significant     | C56.3352        | GEMFIBROZIL/PIOGLITAZONE HCL [VA Drug Interaction]
 553a58eb-a2fa-53e4-b48d-d2aa8f54c940 | 089c94b9-d21a-5f76-b322-c64792f7fd28 | Significant     | C56^4090^       | ATAZANAVIR/ATORVASTATIN CALCIUM [VA Drug Interaction]
 9a8b72c9-4d19-5656-bc87-b66301e0ee86 | be22ef76-7bcb-5198-a1dd-e14f60398d98 | Critical        | C23308143128526 | GATIFLOXACIN/PIOGLITAZONE HCL [VA Drug Interaction]
 be22ef76-7bcb-5198-a1dd-e14f60398d98 | 3e822e77-feee-5a08-84e3-3568f81754ff | Critical        | C56^4966^       | GEMFIBROZIL/PIOGLITAZONE [VA Drug Interaction]
 be22ef76-7bcb-5198-a1dd-e14f60398d98 | 9a8b72c9-4d19-5656-bc87-b66301e0ee86 | Significant     | C56^4265^       | GATIFLOXACIN/PIOGLITAZONE [VA Drug Interaction]
 e630be20-48cf-5744-8fc9-fe2d7a52b988 | 089c94b9-d21a-5f76-b322-c64792f7fd28 | Critical        | C56^4767^       | ATAZANAVIR/RIFAPENTINE [VA Drug Interaction]
(8 rows)
```

This is real DrugCentral/NDF-RT data disagreeing with itself across the two directions it published
the same pair in — not a drugref defect — and `db/049`'s most-severe-wins rule is doing exactly what
its comment says it does, for the first time observed against real content rather than as a
hypothetical the migration's comment described in advance.

### 4c. `gap_unresolved_ddi_endpoint` contents

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

(This `exact_ddi_pair` query is the new view's own hot path, not the cross-migration regression
check — `ddi_candidate_pair` below is. Its full plan was inspected on-screen during the run; the
summary line above is what it showed. The regression check below is the one this section's verdict
rests on, and its full plan is quoted in both directions rather than pointed at.)

**Regression check — `ddi_candidate_pair`, re-run and re-captured in full on both databases:**

```
$ psql "$DSN" -c "EXPLAIN ANALYZE SELECT * FROM drugref.ddi_candidate_pair LIMIT 1"    # drugref_dc049, post-db/049
 Limit  (cost=3975.32..4062.53 rows=1 width=126) (actual time=1.606..1.607 rows=1.00 loops=1)
   Buffers: shared hit=25 read=61
   ->  Unique  (cost=3975.32..479193.10 rows=5449 width=126) (actual time=1.605..1.607 rows=1.00 loops=1)
         Buffers: shared hit=25 read=61
         ->  Incremental Sort  (cost=3975.32..479124.99 rows=5449 width=126) (actual time=1.605..1.606 rows=1.00 loops=1)
               Sort Key: ci.subject_moiety_uuid, ci.object_class_uuid, ci.relationship, ci.source, m.moiety_uuid, ((m.class_uuid = ci.object_class_uuid)) DESC, m.class_uuid
               Presorted Key: ci.subject_moiety_uuid, ci.object_class_uuid, ci.relationship, ci.source
               Full-sort Groups: 1  Sort Method: quicksort  Average Memory: 29kB  Peak Memory: 29kB
               Buffers: shared hit=25 read=61
               ->  Nested Loop Left Join  (cost=3888.14..478879.78 rows=5449 width=126) (actual time=0.244..1.556 rows=40.00 loops=1)
                     Join Filter: ((class_expansion_policy.source = oc.source) AND (class_expansion_policy.source_code = oc.source_code))
                     Rows Removed by Join Filter: 560
                     Filter: ((m.class_uuid = ci.object_class_uuid) OR (a.expands_descendants AND (COALESCE(class_expansion_policy.decision, 'allow'::text) <> 'deny'::text)))
                     Buffers: shared hit=14 read=61
                     ->  Nested Loop  (cost=3888.14..478600.99 rows=11733 width=143) (actual time=0.218..1.458 rows=40.00 loops=1)
                           Join Filter: ((m.moiety_uuid <> ci.subject_moiety_uuid) AND (m.relationship = a.membership_relationship))
                           Buffers: shared hit=14 read=60
                           ->  Nested Loop  (cost=3887.85..361313.25 rows=118789 width=159) (actual time=0.200..1.407 rows=4.00 loops=1)
                                 Join Filter: (ci.object_class_uuid = subtree.root_uuid)
                                 Rows Removed by Join Filter: 1318
                                 Buffers: shared hit=9 read=52
                                 ->  Nested Loop  (cost=0.89..216.12 rows=635 width=159) (actual time=0.064..0.099 rows=2.00 loops=1)
                                       Buffers: shared hit=6 read=10
                                       ->  Nested Loop  (cost=0.59..110.69 rows=635 width=126) (actual time=0.047..0.057 rows=2.00 loops=1)
                                             Buffers: shared hit=5 read=5
                                             ->  Nested Loop  (cost=0.44..94.35 rows=635 width=86) (actual time=0.037..0.046 rows=2.00 loops=1)
                                                   Buffers: shared hit=3 read=5
                                                   ->  Index Scan using class_contraindication_pkey on class_contraindication ci  (cost=0.28..77.77 rows=635 width=53) (actual time=0.020..0.023 rows=2.00 loops=1)
                                                         Index Searches: 1
                                                         Buffers: shared hit=1 read=3
                                                   ->  Memoize  (cost=0.16..0.25 rows=1 width=65) (actual time=0.010..0.010 rows=1.00 loops=2)
                                                         Cache Key: ci.relationship
                                                         Cache Mode: logical
                                                         Hits: 0  Misses: 2  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                                         Buffers: shared hit=2 read=2
                                                         ->  Index Scan using ci_axis_pkey on ci_axis a  (cost=0.15..0.24 rows=1 width=65) (actual time=0.008..0.008 rows=1.00 loops=2)
                                                               Index Cond: (relationship = ci.relationship)
                                                               Index Searches: 2
                                                               Buffers: shared hit=2 read=2
                                             ->  Memoize  (cost=0.16..0.25 rows=1 width=48) (actual time=0.005..0.005 rows=1.00 loops=2)
                                                   Cache Key: ci.ingest_run
                                                   Cache Mode: logical
                                                   Hits: 1  Misses: 1  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                                   Buffers: shared hit=2
                                                   ->  Index Scan using ingest_run_pkey on ingest_run r  (cost=0.15..0.24 rows=1 width=48) (actual time=0.008..0.008 rows=1.00 loops=1)
                                                         Index Cond: (ingest_run_id = ci.ingest_run)
                                                         Index Searches: 1
                                                         Buffers: shared hit=2
                                       ->  Memoize  (cost=0.29..0.86 rows=1 width=33) (actual time=0.020..0.020 rows=1.00 loops=2)
                                             Cache Key: ci.object_class_uuid
                                             Cache Mode: logical
                                             Hits: 0  Misses: 2  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                             Buffers: shared hit=1 read=5
                                             ->  Index Scan using substance_class_pkey on substance_class oc  (cost=0.28..0.85 rows=1 width=33) (actual time=0.019..0.019 rows=1.00 loops=2)
                                                   Index Cond: (class_uuid = ci.object_class_uuid)
                                                   Index Searches: 2
                                                   Buffers: shared hit=1 read=5
                                 ->  Materialize  (cost=3886.97..4822.32 rows=37414 width=32) (actual time=0.066..0.636 rows=661.00 loops=2)
                                       Storage: Memory  Maximum Storage: 84kB
                                       Buffers: shared hit=3 read=42
                                       ->  CTE Scan on subtree  (cost=3886.97..4635.25 rows=37414 width=32) (actual time=0.130..1.194 rows=1233.00 loops=1)
                                             Storage: Memory  Maximum Storage: 84kB
                                             Buffers: shared hit=3 read=42
                                             CTE subtree
                                               ->  Recursive Union  (cost=14.94..3886.97 rows=37414 width=32) (actual time=0.130..1.033 rows=1233.00 loops=1)
                                                     Storage: Memory  Maximum Storage: 59kB
                                                     Buffers: shared hit=3 read=42
                                                     ->  HashAggregate  (cost=14.94..15.98 rows=104 width=32) (actual time=0.128..0.133 rows=104.00 loops=1)
                                                           Group Key: ci_1.object_class_uuid
                                                           Batches: 1  Memory Usage: 32kB
                                                           Buffers: shared hit=3 read=4
                                                           ->  Seq Scan on class_contraindication ci_1  (cost=0.00..13.35 rows=635 width=32) (actual time=0.003..0.072 rows=635.00 loops=1)
                                                                 Buffers: shared hit=3 read=4
                                                     ->  Hash Join  (cost=139.47..349.69 rows=3731 width=32) (actual time=0.066..0.088 rows=152.50 loops=8)
                                                           Hash Cond: (s.class_uuid = cp.parent_class_uuid)
                                                           Buffers: shared read=38
                                                           ->  WorkTable Scan on subtree s  (cost=0.00..20.80 rows=1040 width=32) (actual time=0.000..0.007 rows=154.12 loops=8)
                                                           ->  Hash  (cost=83.10..83.10 rows=4510 width=32) (actual time=0.511..0.511 rows=4510.00 loops=1)
                                                                 Buckets: 8192  Batches: 1  Memory Usage: 346kB
                                                                 Buffers: shared read=38
                                                                 ->  Seq Scan on class_parent cp  (cost=0.00..83.10 rows=4510 width=32) (actual time=0.020..0.255 rows=4510.00 loops=1)
                                                                       Buffers: shared read=38
                           ->  Index Scan using class_membership_by_class on class_membership m  (cost=0.29..0.69 rows=20 width=39) (actual time=0.010..0.011 rows=10.00 loops=4)
                                 Index Cond: (class_uuid = subtree.class_uuid)
                                 Index Searches: 4
                                 Buffers: shared hit=5 read=8
                     ->  Materialize  (cost=0.00..1.18 rows=1 width=96) (actual time=0.001..0.002 rows=14.00 loops=40)
                           Storage: Memory  Maximum Storage: 17kB
                           Buffers: shared read=1
                           ->  Seq Scan on class_expansion_policy  (cost=0.00..1.18 rows=1 width=96) (actual time=0.017..0.019 rows=14.00 loops=1)
                                 Filter: ((superseded_by IS NULL) AND (decision <> 'withdrawn'::text))
                                 Buffers: shared read=1
 Planning:
   Buffers: shared hit=784 read=30
 Planning Time: 8.081 ms
 Execution Time: 1.821 ms
(96 rows)
```

**Baseline — the identical query on `drugref_dc101` (`db/048`, no `db/049` applied at all):**

```
$ psql "host=localhost port=5532 dbname=drugref_dc101 user=postgres" -Atc \
    "SELECT max(filename) FROM drugref.schema_migration"
048_unknown_signature_status.sql

$ psql "host=localhost port=5532 dbname=drugref_dc101 user=postgres" -c \
    "EXPLAIN ANALYZE SELECT * FROM drugref.ddi_candidate_pair LIMIT 1"
 Limit  (cost=3975.32..4062.53 rows=1 width=126) (actual time=1.875..1.876 rows=1.00 loops=1)
   Buffers: shared hit=23 read=63
   ->  Unique  (cost=3975.32..479193.10 rows=5449 width=126) (actual time=1.874..1.875 rows=1.00 loops=1)
         Buffers: shared hit=23 read=63
         ->  Incremental Sort  (cost=3975.32..479124.99 rows=5449 width=126) (actual time=1.874..1.875 rows=1.00 loops=1)
               Sort Key: ci.subject_moiety_uuid, ci.object_class_uuid, ci.relationship, ci.source, m.moiety_uuid, ((m.class_uuid = ci.object_class_uuid)) DESC, m.class_uuid
               Presorted Key: ci.subject_moiety_uuid, ci.object_class_uuid, ci.relationship, ci.source
               Full-sort Groups: 1  Sort Method: quicksort  Average Memory: 29kB  Peak Memory: 29kB
               Buffers: shared hit=23 read=63
               ->  Nested Loop Left Join  (cost=3888.14..478879.78 rows=5449 width=126) (actual time=0.396..1.815 rows=40.00 loops=1)
                     Join Filter: ((class_expansion_policy.source = oc.source) AND (class_expansion_policy.source_code = oc.source_code))
                     Rows Removed by Join Filter: 560
                     Filter: ((m.class_uuid = ci.object_class_uuid) OR (a.expands_descendants AND (COALESCE(class_expansion_policy.decision, 'allow'::text) <> 'deny'::text)))
                     Buffers: shared hit=12 read=63
                     ->  Nested Loop  (cost=3888.14..478600.99 rows=11733 width=143) (actual time=0.361..1.738 rows=40.00 loops=1)
                           Join Filter: ((m.moiety_uuid <> ci.subject_moiety_uuid) AND (m.relationship = a.membership_relationship))
                           Buffers: shared hit=12 read=62
                           ->  Nested Loop  (cost=3887.85..361313.25 rows=118789 width=159) (actual time=0.337..1.671 rows=4.00 loops=1)
                                 Join Filter: (ci.object_class_uuid = subtree.root_uuid)
                                 Rows Removed by Join Filter: 1318
                                 Buffers: shared hit=7 read=54
                                 ->  Nested Loop  (cost=0.89..216.12 rows=635 width=159) (actual time=0.233..0.251 rows=2.00 loops=1)
                                       Buffers: shared hit=4 read=12
                                       ->  Nested Loop  (cost=0.59..110.69 rows=635 width=126) (actual time=0.217..0.223 rows=2.00 loops=1)
                                             Buffers: shared hit=3 read=7
                                             ->  Nested Loop  (cost=0.44..94.35 rows=635 width=86) (actual time=0.202..0.207 rows=2.00 loops=1)
                                                   Buffers: shared hit=3 read=5
                                                   ->  Index Scan using class_contraindication_pkey on class_contraindication ci  (cost=0.28..77.77 rows=635 width=53) (actual time=0.182..0.183 rows=2.00 loops=1)
                                                         Index Searches: 1
                                                         Buffers: shared hit=1 read=3
                                                   ->  Memoize  (cost=0.16..0.25 rows=1 width=65) (actual time=0.011..0.011 rows=1.00 loops=2)
                                                         Cache Key: ci.relationship
                                                         Cache Mode: logical
                                                         Hits: 0  Misses: 2  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                                         Buffers: shared hit=2 read=2
                                                         ->  Index Scan using ci_axis_pkey on ci_axis a  (cost=0.15..0.24 rows=1 width=65) (actual time=0.008..0.008 rows=1.00 loops=2)
                                                               Index Cond: (relationship = ci.relationship)
                                                               Index Searches: 2
                                                               Buffers: shared hit=2 read=2
                                             ->  Memoize  (cost=0.16..0.25 rows=1 width=48) (actual time=0.008..0.008 rows=1.00 loops=2)
                                                   Cache Key: ci.ingest_run
                                                   Cache Mode: logical
                                                   Hits: 1  Misses: 1  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                                   Buffers: shared read=2
                                                   ->  Index Scan using ingest_run_pkey on ingest_run r  (cost=0.15..0.24 rows=1 width=48) (actual time=0.013..0.013 rows=1.00 loops=1)
                                                         Index Cond: (ingest_run_id = ci.ingest_run)
                                                         Index Searches: 1
                                                         Buffers: shared read=2
                                       ->  Memoize  (cost=0.29..0.86 rows=1 width=33) (actual time=0.013..0.013 rows=1.00 loops=2)
                                             Cache Key: ci.object_class_uuid
                                             Cache Mode: logical
                                             Hits: 0  Misses: 2  Evictions: 0  Overflows: 0  Memory Usage: 1kB
                                             Buffers: shared hit=1 read=5
                                             ->  Index Scan using substance_class_pkey on substance_class oc  (cost=0.28..0.85 rows=1 width=33) (actual time=0.012..0.012 rows=1.00 loops=2)
                                                   Index Cond: (class_uuid = ci.object_class_uuid)
                                                   Index Searches: 2
                                                   Buffers: shared hit=1 read=5
                                 ->  Materialize  (cost=3886.97..4822.32 rows=37414 width=32) (actual time=0.048..0.692 rows=661.00 loops=2)
                                       Storage: Memory  Maximum Storage: 84kB
                                       Buffers: shared hit=3 read=42
                                       ->  CTE Scan on subtree  (cost=3886.97..4635.25 rows=37414 width=32) (actual time=0.094..1.304 rows=1233.00 loops=1)
                                             Storage: Memory  Maximum Storage: 84kB
                                             Buffers: shared hit=3 read=42
                                             CTE subtree
                                               ->  Recursive Union  (cost=14.94..3886.97 rows=37414 width=32) (actual time=0.094..1.163 rows=1233.00 loops=1)
                                                     Storage: Memory  Maximum Storage: 59kB
                                                     Buffers: shared hit=3 read=42
                                                     ->  HashAggregate  (cost=14.94..15.98 rows=104 width=32) (actual time=0.093..0.097 rows=104.00 loops=1)
                                                           Group Key: ci_1.object_class_uuid
                                                           Batches: 1  Memory Usage: 32kB
                                                           Buffers: shared hit=3 read=4
                                                           ->  Seq Scan on class_contraindication ci_1  (cost=0.00..13.35 rows=635 width=32) (actual time=0.002..0.053 rows=635.00 loops=1)
                                                                 Buffers: shared hit=3 read=4
                                                     ->  Hash Join  (cost=139.47..349.69 rows=3731 width=32) (actual time=0.085..0.107 rows=152.50 loops=8)
                                                           Hash Cond: (s.class_uuid = cp.parent_class_uuid)
                                                           Buffers: shared read=38
                                                           ->  WorkTable Scan on subtree s  (cost=0.00..20.80 rows=1040 width=32) (actual time=0.000..0.005 rows=154.12 loops=8)
                                                           ->  Hash  (cost=83.10..83.10 rows=4510 width=32) (actual time=0.661..0.661 rows=4510.00 loops=1)
                                                                 Buckets: 8192  Batches: 1  Memory Usage: 346kB
                                                                 Buffers: shared read=38
                                                                 ->  Seq Scan on class_parent cp  (cost=0.00..83.10 rows=4510 width=32) (actual time=0.050..0.347 rows=4510.00 loops=1)
                                                                       Buffers: shared read=38
                           ->  Index Scan using class_membership_by_class on class_membership m  (cost=0.29..0.69 rows=20 width=39) (actual time=0.013..0.015 rows=10.00 loops=4)
                                 Index Cond: (class_uuid = subtree.class_uuid)
                                 Index Searches: 4
                                 Buffers: shared hit=5 read=8
                     ->  Materialize  (cost=0.00..1.18 rows=1 width=96) (actual time=0.001..0.001 rows=14.00 loops=40)
                           Storage: Memory  Maximum Storage: 17kB
                           Buffers: shared read=1
                           ->  Seq Scan on class_expansion_policy  (cost=0.00..1.18 rows=1 width=96) (actual time=0.027..0.028 rows=14.00 loops=1)
                                 Filter: ((superseded_by IS NULL) AND (decision <> 'withdrawn'::text))
                                 Buffers: shared read=1
 Planning:
   Buffers: shared hit=740 read=74
 Planning Time: 8.546 ms
 Execution Time: 2.033 ms
(96 rows)
```

**Verdict: unchanged, and checkable from the two plans above without re-running anything.** Every
`cost=...`, `rows=...`, `width=...`, node type, `Join Filter`, `Filter`, `Sort Key`, `Presorted Key`,
`Index Cond`, and `Cache Key` line is identical, in the identical order, in both plans — read them
side by side and there is nothing to reconcile. Only `actual time=`, `Buffers:` (page-cache state)
and the closing `Planning Time`/`Execution Time`/`Planning: Buffers` lines differ, which is
run-to-run noise, not a plan change.

This was also confirmed mechanically rather than just by eye: each plan was saved to a file and
passed through `sed` to blank out exactly the noise lines (`actual time=...`, `Buffers:...`,
`Planning Time:`, `Execution Time:`, the bare `Planning:` line), then diffed:

```
$ sed -E 's/\(actual time=[0-9.]+\.\.[0-9.]+ rows=[0-9.]+ loops=[0-9]+\)//; s/Buffers:.*//; \
    /^ *Planning Time:/d; /^ *Execution Time:/d; /^ *Planning:$/d' plan-dc049.txt > plan-dc049.norm.txt
$ sed -E 's/\(actual time=[0-9.]+\.\.[0-9.]+ rows=[0-9.]+ loops=[0-9]+\)//; s/Buffers:.*//; \
    /^ *Planning Time:/d; /^ *Execution Time:/d; /^ *Planning:$/d' plan-dc101.txt > plan-dc101.norm.txt
$ diff -u plan-dc101.norm.txt plan-dc049.norm.txt
$ echo "exit: $?"
exit: 0
```

Zero lines of difference once run-to-run noise is stripped: `db/049` added no arm to
`ddi_candidate_pair`'s plan, which is exactly what db/034's 3.6× regression makes worth checking on
every migration that touches a shared view. Any reader can reproduce this by saving both `psql -c
"EXPLAIN ANALYZE ..."` outputs above to files and running the same two commands.

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
`drugref_dc049` in §3/§4/§4c exactly, name for name. Also confirmed from the same cache manifest
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
seeded from `gap_unresolved_ddi_endpoint` (§4c). Every pre-existing `gap_kind`'s count (11 kinds,
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
| 5 | 37 unresolvable rows | 37 (ingest log; `drugcentral_ddi_assertion` rows with a NULL moiety UUID; §4c) | **MATCHED** |
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

- **The 4 conflicting-severity pairs (§4b) are the round's headline real-data finding.**
  `db/049`'s comment predicted 4 of the 33 both-order duplicates would disagree on severity; this
  measurement is the first time they were actually located, named, and confirmed to resolve
  `contraindicated` via most-severe-wins: atazanavir/atorvastatin, atazanavir/rifapentine,
  gemfibrozil/pioglitazone, gatifloxacin/pioglitazone. See §4b for the full row-level evidence.
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
| Record defects found on review | 2 — both in this record, not in code (§4b's unearned "reconciled" claim, fixed in place; §5's plan-shape claim under-evidenced, fixed by quoting both full plans) |
| Headline real-data finding | 4 pairs where DrugCentral's two orientations disagree on severity — named in §4b, all resolved `contraindicated` by `db/049`'s most-severe-wins rule |
