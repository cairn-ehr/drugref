# Allergy groups — measured results

**Run:** 2026-11-09 · **Database:** `drugref_allergy` (`host=localhost port=5532 dbname=drugref_allergy user=postgres`) ·
**Design:** [`2026-11-08-drugref-allergy-groups-design.md`](2026-11-08-drugref-allergy-groups-design.md) (§6 is the
pre-build table compared against below) · **Status:** measured; a measurement, not a gate (spec §6).

A moiety that answers `unplaced` means *not checked*, never *safe*.

## 1. The build

`drugref_allergy` was made with `createdb -T drugref_profiles` (19,438 moieties, schema at `db/065`), so
`drugref_profiles` itself stayed untouched. It was then migrated to `db/068` with `db.apply_migrations`.

| step | command | wall |
|---|---|---|
| migrate | `db.apply_migrations` (067, 068) | under 1 s |
| ingest | `drugref ingest allergy-groups --release 2026 --desc downloads/mesh/desc2026.gz --supp downloads/mesh/supp2026.gz` | 19.9 s |
| measure | `uv run python -m tools.allergy_group_measure --medrt downloads/MEDRT/Core_MEDRT_2026.07.06_XML.xml --timing 200` | seconds |

Sources (`downloads/` is gitignored, so the hashes are recorded here):

| file | SHA-256 |
|---|---|
| `downloads/mesh/desc2026.gz` (MeSH 2026 descriptors; 31,110 records) | `9fe35b3170652376a592daf69e91a80d6c693ecaf9c571ceb701d04204cb357d` |
| `downloads/mesh/supp2026.gz` (MeSH 2026 supplementary records; 324,045 records) | `2140276db950375efac5ddc5c9993debcc8301ec8f654b3b66edf315ee889ab2` |
| `downloads/MEDRT/Core_MEDRT_2026.07.06_XML.xml` (MED-RT 2026.07.06; cross-check only) | not hashed (release name is the identity) |

Allow-list: the packaged `src/drugref/data/allergy_groups.toml`. Ingest summary as printed:
`groups=1 members=84 placed_moieties=10739 members_no_key=27 members_key_not_in_registry=98 includes_unresolved=()`.

## 2. Figures against the pre-build table (spec §6)

| | pre-build | built | note |
|---|---:|---:|---|
| penicillin group members | 78 | **83** distinct moieties (84 member rows) | see below |
| moieties placed in MeSH, of 19,438 | 9,930 | **10,739** (55.2%) | see below |
| `-cillin` moieties not reached, of 104 | 22 | **17** | see below |
| `has_SC` misses | 2 | **0** | the two became `include` entries |
| prescribable moieties placed | not measured | **6,200 of 8,861 (70.0%)**; 2,661 answer `unplaced` | denominator: moieties with any unsuperseded `RXNORM_IN` claim |

**Deltas, explained.** The pre-build script bridged by UNII only, with no CAS fallback and no includes. Re-running
the real parser with a UNII-only bridge on this database reproduces the pre-build numbers exactly (78 members,
9,930 placed), so every difference is the three rules the build adds:

- **Members 78 to 83.** +3 from the CAS fallback (`fenbenicillin`, `clometocillin`, `tameticillin`: MeSH carries
  a CAS number the registry holds but not their UNII) and +2 from the allow-list includes (`piperacillin
  anhydrous`, `carbenicillin indanyl sodium`). The 84th row is `propicillin potassium`, which is a member twice
  over (via `scr:C006801` and `scr:C100135`): rows are `(group, moiety, via)`, moieties are distinct.
  By route: 45 moieties via a descriptor, 36 via a supplementary record, 2 via include; these three sum to 83
  distinct moieties, so no moiety is counted twice. The 84th row is not an overlap between routes: it is
  `propicillin potassium`'s two `scr` rows (37 supplementary rows give 36 moieties).
  The UNII-only re-run was a throwaway run of the build's own bridging code restricted to UNII keys (empty CAS
  index, no includes); the script was not kept.
- **Placed 9,930 to 10,739.** +807 from the CAS fallback (two-key bridge gives 10,737 on its own) and +2 from the
  two include moieties, which no MeSH record carried a key for.
- **`-cillin` 22 to 17.** Exactly the five new members above (3 CAS + 2 includes). The remaining 17 are all
  `unplaced / prescribed-not-in-mesh` except `xantocillin`, which is placed and correctly `no-match` (an
  antibiotic isonitrile, not a penicillin). `penicillinase` is an enzyme and is correctly out.

The 17: almecillin, amantocillin, azidocillin sodium, fibracillin, fumoxicillin, furbucillin, isopropicillin,
levopropylcillin, oxetacillin, penicillinase, phenyracillin, piridicillin, piroxicillin, rotamicillin,
thiphencillin, tobicillin, xantocillin.

**Prescribable coverage.** 2,661 of 8,861 prescribable moieties (30.0%) answer `unplaced` against the penicillin
group. The honest answer; cairn shows it as "not checked".

## 3. MED-RT `has_SC` recall cross-check

29 associations (`has_SC`, from RxNorm, to `Penicillins`); streamed with `iterparse`. Each `from_code` was looked
up against unsuperseded `RXNORM_IN` claims, then `allergy_check(moiety, penicillin group)`.

- **25 resolved to a moiety; 25 of 25 match. Misses: 0.**
- **4 RxCUIs resolve to no moiety at all:** `733` (ampicillin), `723` (amoxicillin), `9893` (nafcillin sodium),
  `70618` (Penicillin). These are not recall misses of the group: the registry holds those drugs, under other
  `RXNORM_IN` values (`ampicillin` 221058, `amoxicillin` 133008, `nafcillin sodium` 485026, `penicillin g` 7980),
  and `amoxicillin` and `ampicillin` are group members. They are an identifier-coverage observation: the
  registry's `RXNORM_IN` claims do not use MED-RT's RxCUIs for these four, so a join by MED-RT RxCUI misses them.
  No RxCUI tried was present under any `RXNORM*` scheme. Filed as [#223](https://github.com/cairn-ehr/drugref/issues/223); not acted on here.

## 4. The acceptance cases (verbatim, `drugref allergy check PRESCRIBED ALLERGEN`)

Penicillin group `55a1d1a0-66b0-5831-8bd7-b1cc29be1f80`; perindopril `eb17289c-f23d-5d06-b33d-87e4c7c99188`;
amoxicillin `da46e7b3-5e2c-5d32-b814-a991bda5cd9d`; cephalexin `4763c7f7-7f4b-52f8-ab71-54a440331432`;
flucloxacillin `a00894ac-04ce-5987-9595-d9e01a7bd5e4`.

```
$ drugref allergy check eb17289c-f23d-5d06-b33d-87e4c7c99188 55a1d1a0-66b0-5831-8bd7-b1cc29be1f80
no-match
$ drugref allergy check da46e7b3-5e2c-5d32-b814-a991bda5cd9d 55a1d1a0-66b0-5831-8bd7-b1cc29be1f80
match
same-group  Penicillins (allergy group) [structural-seed] D010406  allergen:Penicillins (allergy group) -> prescribed:amoxicillin anhydrous
same-group  Penicillins (allergy group) [structural-seed] D010406  allergen:Penicillins (allergy group) -> prescribed:amoxicillin
$ drugref allergy check 4763c7f7-7f4b-52f8-ab71-54a440331432 55a1d1a0-66b0-5831-8bd7-b1cc29be1f80
no-match
$ drugref allergy check a00894ac-04ce-5987-9595-d9e01a7bd5e4 da46e7b3-5e2c-5d32-b814-a991bda5cd9d
match
same-group  Penicillins (allergy group) [structural-seed] D010406  allergen:amoxicillin anhydrous -> prescribed:flucloxacillin
same-group  Penicillins (allergy group) [structural-seed] D010406  allergen:amoxicillin -> prescribed:flucloxacillin
```

(a) perindopril: no warning, as required. (b) amoxicillin: warning, as required. (c) cefalexin: `no-match` — the
known gap (spec §2: no penicillin-cephalosporin rule this round), so the owner's third case is **not met** by
this slice, by design. (d) flucloxacillin for an amoxicillin allergy: warns, naming amoxicillin as the allergen.
Amoxicillin's fold reaches both `amoxicillin` and `amoxicillin anhydrous`, hence two rows each way.

## 5. Read cost

`EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM drugref.allergy_check(prescribed, group)`, third run of each (warm):

| case | planning | execution |
|---|---:|---:|
| perindopril (no-match) | 1.50 ms | 0.215 ms |
| amoxicillin (match) | 1.74 ms | 0.204 ms |
| cephalexin (no-match) | 1.38 ms | 0.203 ms |
| almecillin (unplaced) | 0.66 ms | 0.080 ms |

Plan headline: the only sequential scans are on `allergy_group_member` (84 rows) and `allergy_group` (1 row);
every big table (`substance_moiety`, `identity_claim`, `substance_composition`, `allergy_mesh_placement`) is hit by
index. Most branches report `never executed` because the outcome ordering short-circuits.

Timing loop, 200 calls over 50 moieties alternating moiety-allergen and group-allergen, one connection, after a
20-call warm-up (wall time per call including the client round trip):
**median 0.14 ms, p95 0.30 ms, max 0.39 ms.** Nothing near the 20 ms line.

## 6. Not measured

- That no existing reader's output changed (spec §6): covered by the test suite, not re-measured on this database.
- A rebuild of MeSH PA then allergy groups in the other order on a real database (covered on fixtures only).
- Cold-cache timings; concurrent load.
- The sample is one group (penicillins); a second group's cost is not extrapolated.
