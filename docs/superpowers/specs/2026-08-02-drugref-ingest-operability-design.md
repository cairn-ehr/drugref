# drugref — the ingest-operability round (#16, #47)

**Date:** 2026-08-02 · **Status:** design, approved · **Issues:**
[#16](https://github.com/cairn-ehr/drugref/issues/16),
[#47](https://github.com/cairn-ehr/drugref/issues/47)

A debt round, not a slice. It adds no source, no clinical claim and no new kind of knowledge. It makes an
ingest **observable and runnable**, and persists the one identity set an orchestrator still counts and throws
away.

## 1. Why these two, and why now

Six orchestrators — `run.py` (UNII), `chebi.py`, `medrt_run.py`, `mesh_run.py`, `mesh_rel_run.py`,
`pbs_run.py` — hand-write the same four lines of provenance SQL, and every one of them writes its
`ingest_run` row **inside the transaction that does the work**. A crashed run therefore rolls its own
provenance away: `ingest_run.finished_at` is nullable, which asserts that "started, never finished" is an
observable state, and it never can be. #16 has been open since the foundation review; each slice since has
copied the shape once more. Six copies is the point at which the pattern gets settled rather than copied a
seventh time.

#47 rides along because it lives inside `medrt_run`, which #16 touches anyway, and because db/018's own
comments name it as the change that turns a latent non-determinism into a bug. Measuring it (§5) showed that
prediction was right and its stated remedy was resting on a coincidence.

**Not in this round:** #35, #36, #37, #48. #35 (moving `class_expansion_policy` onto Plan C's append-only
shape) is a read-path change to a table that gates recall and deserves its own measurement of
`ddi_candidate_pair`; #36 needs a curator ruling on a new metric; #37 is explicitly not urgent at a 3.1 ms
filtered lookup; #48 remains structurally unreachable.

## 2. The provenance boundary

### 2.1 One module, two halves

New: `src/drugref/provenance.py`.

```
open_run(conn, *, source, upstream_release, source_checksum, writer) -> int
finish_run(conn, run_id) -> None
```

* **`open_run` INSERTs the row and COMMITS it, in its own transaction.** That commit *is* the feature: the
  row has to outlive the rollback of the work it describes. A run that dies half-way then leaves exactly the
  evidence `finished_at IS NULL` has always promised.
* **`finish_run` stamps `finished_at` and does NOT commit.** The stamp must land in the same transaction as
  the work, so the orchestrator's existing final `conn.commit()` publishes both atomically. Otherwise
  `finished` could become true about data that is not there — the failure the whole round exists to remove,
  re-introduced one line further down.

### 2.2 The connection-ownership decision #16 asks for

**An orchestrator now takes TWO transactions on ONE connection**: a short provenance transaction, then the
work. Restated in all six docstrings, because it tightens a rule callers already follow ("commit your own
pending work before calling"): *a caller with pending work has it committed at the provenance boundary.*

The alternative — an autonomous provenance transaction on a **second connection** — was rejected. It would
keep the caller's contract untouched, but `psycopg`'s `conn.info.dsn` omits the password, so reconstructing a
connection from an existing one fails under password authentication. A credential-dependent failure mode on
the very path whose job is to record failures is worse than a documented contract tightening.

### 2.3 Two things that ride along

* **`chebi.enrich_from_chebi` is the one orchestrator with no `try`/rollback and no logging.** It predates
  the foundation review that gave the other five that shape and was missed. It gets it here, because this
  round rewrites those exact lines anyway.
* **A contract test.** `INSERT INTO drugref.ingest_run` and `UPDATE … SET finished_at` must appear in
  **exactly one file** under `src/drugref`. Same pin `tests/test_source_clear_contract.py` puts on
  `db.clear_source_tables`. The standing rule — *one reader, one clear, one checksum* (#40, #43) — gains a
  fourth member: **one run record**.

## 3. What a run record has to say

### 3.1 `ingest_run.writer` — because one source has two writers

`loaded_release` keyed on `source` alone is **not one true sentence**. Source `MED-RT` has two writers:
`medrt_run` (the classification/CI half) and `mesh_rel_run` (the MeSH-keyed half). Their `source_checksum`s
legitimately differ — `ingest/checksum.py`'s `checksum(*paths)` covers one file for the first and three for
the second — so re-running one and not the other leaves the view reporting the newer as *the* MED-RT release
while the other half is a release behind, with nothing saying so. **That is #39 one layer up, on the table
#39's own fix could not reach.**

`db/025` therefore adds `ingest_run.writer`: `text NOT NULL`, **no DEFAULT**, CHECKed vocabulary — exactly
db/018's `reason` posture, for the same reason (a writer that does not declare itself must fail, not inherit
somebody else's identity).

**Historical rows cannot be attributed and are not guessed.** Nothing in an existing row distinguishes the
two MED-RT writers, so the migration backfills the literal `'unattributed'`, a real value in the CHECK whose
`COMMENT ON` says what it means: *written before db/025, when two orchestrators shared a source and nothing
told them apart.* `ingest_run` is **history, not a rebuildable projection**, so it cannot heal itself the way
db/018's table did; later re-ingests age these rows out of `loaded_release` naturally, by being newer.

### 3.2 The two views

Complementary filters on one column, so they cannot disagree — the shape db/018 adopted for
`ci_rule_partner_reach` after finding the same measure stated twice with only one copy corrected:

* **`drugref.ingest_run_incomplete`** — `finished_at IS NULL`. **Before this round this view could only ever
  be empty**, because the row rolled back with the work. That sentence belongs in its `COMMENT ON`.
* **`drugref.loaded_release`** — `DISTINCT ON (source, writer)` over `finished_at IS NOT NULL`, newest first:
  which release each writer last landed, from which bytes, when.

`loaded_release`'s `COMMENT ON` must state what it does **not** mean: it is the release a per-source rebuild
last replaced its projection from, **not** a claim that every row attributed to that source carries this
run's id. Accumulating tables (`substance_moiety`, `identity_claim`) hold rows from many runs by design.

## 4. The CLI

One console script — `[project.scripts] drugref = "drugref.cli:main"` — with `--dsn` (falling back to
`DRUGREF_DSN`) and `--log-level` global to every command.

```
drugref migrate
drugref status
drugref ingest {unii,chebi,medrt,mesh,mesh-relations,pbs} --release R <paths…>
drugref ingest chain --downloads DIR --unii-release R --medrt-release R …
```

### 4.1 The chain

* **A source is included iff its `--<source>-release` flag is supplied.** No skip-list, no default set. The
  release tag is **stated, never parsed out of a filename**: it is provenance, and this project does not
  guess provenance. (Two of the four tags are not in the filenames anyway — MeSH's `2026` and PBS's
  `2026-07`.)
* **Order is a module constant, not an argument.** UNII → ChEBI → MED-RT → MeSH → MeSH-relations → PBS. The
  dependency order is a property of the data, not a choice a caller should be able to get wrong.
* **Inputs resolve from `--downloads` by documented globs**, because the real layout is irregular and a tidy
  invented convention would match nothing: **`UNII_Records_*.txt`** at the root — **NOT `UNII_Names_*.txt`,
  which is a real file sitting right beside it carrying none of the four membership signals the moiety GATE
  reads** (`INN_ID`, `USAN_ID`, `RXCUI`, `SUBSTANCE_TYPE`; Names holds one row per synonym) —
  `MEDRT/Core_MEDRT_*_XML.xml`, `mesh/{pa,desc,supp}*`, `tables_as_csv/items.csv`. A pattern matching
  **zero or more than one** file is a loud failure naming the pattern and the directory searched — never a
  silent skip.
  *Those four are the **gate's**, not the parser's:* `unii._REQUIRED_COLUMNS` is a **six**-tuple — the four
  plus `UNII` and `Display Name` — and it is what the parser refuses a file for lacking. Two different sets,
  and "the four columns the parser requires" conflates them.
* **Every selected step's inputs are validated before any step runs**, so a typo fails in a second rather
  than sixty.
* **MED-RT ships as a zip and is extracted by hand today; the chain requires the extracted XML and says so.**
  Teaching the CLI to open archives would make it feed-aware for one feed's convenience.

### 4.2 Shape

Pure and testable without a database: the `STEPS` table, `build_parser()`, `resolve_inputs()` — which raises
`InputResolutionError` when a step's glob matches **zero or several** files, both being errors — and
`selected_steps()`, which returns the `(step, release)` pairs a chain invocation includes **in `STEPS` order**,
so the dependency order cannot be broken from the command line. `main(argv=None) -> int` is the thin impure
shell, so tests drive it by call rather than by subprocess. Target under ~300 lines; if the chain outgrows
that, it splits into its own module rather than being compressed.

## 5. #47 — the CI subjects, and two things measuring it turned up

`medrt_run` builds two sets of RxCUIs no moiety carries. The membership-derived set is persisted under
`reason = 'classification'`; the **contraindication-derived** set (the subjects of `CI_MoA`/`CI_PE` rules) is
reported as the summary integer `unmatched_ci_rxcuis` and discarded. That is precisely the shape db/008
exists to prevent: a count answers *how many drugs can we not speak about*, only the identities answer
*which ones*.

The fix is a **fourth `reason` value with its own writer** — never a shared bucket, because db/018's
invariant is EXACTLY ONE WRITER PER `(source, reason)` and sharing re-creates #39 with nothing to notice it —
plus `medrt_run` clearing both of its buckets and writing the set it already holds. `db/026` carries the
CHECK extension and the re-cut view of §5.3; `classes.py` gains the constant beside the other three.
`MedrtSummary.unmatched_ci_rxcuis` stays exactly as it is: the set is now persisted **as well as** counted,
and removing the count would break the summary contract five test modules assert on.

### 5.1 Finding 1 — the issue's own suggested name inverts db/018's tie-break

db/018 widened `gap_unmatched_ingredient` to `ORDER BY rxcui, ingest_run DESC, reason` **explicitly
anticipating #47**, and its comment says `classification` wins the tie "alphabetically". Checked on the live
database:

```
class_contraindication
classification
contraindication
indication
```

`class_contraindication` — the value the issue proposes — is the one string that sorts **before**
`classification` and so inverts the rule the comment was written to protect. (True under both C and
`en_US.UTF-8` collation: `_` precedes `i` under the first, and the second's punctuation-insensitive pass
compares `classc…` against `classi…`.)

### 5.2 Finding 2 — that comment's other justification is already false

The same comment says `classification` also wins "by being the bucket with a `name`". Measured on
`drugref_planc` (the real releases):

| bucket | rows | rows carrying a name |
|---|---:|---:|
| `classification` | 2,137 | **0** |
| `contraindication` | 826 | **0** |
| `indication` | 1,426 | **0** |

`medrt_run` passes no `names` mapping — `classes.add_unmatched_ingredients`'s own docstring says MED-RT's
membership assertions carry none — so **no row in any bucket has ever had a name**. And **1,430 RxCUIs
already sit in more than one bucket**, so this tie-break is live on real data today; it is simply
unobservable, because every candidate row is identical in every projected column.

### 5.3 What §5 therefore does

1. **Names the value so it sorts after `classification`** — `contraindication_class`, `medrt_run`'s own
   class-keyed CI subjects, beside `contraindication`, which is `mesh_rel_run`'s MeSH-keyed ones.
2. **Re-cuts the tie-break to state its intent rather than depend on a coincidence**: prefer the row that
   carries a `name`, then the reason. A view whose correctness rests on the alphabet is a view the next
   `reason` value breaks silently.
3. Pins both on **controlled input, verified by mutation** — the #42 shape, because the release cannot
   exercise a branch in which any row has a name.

## 6. Verification

TDD throughout: the failing test first, in every task.

**The load-bearing tests.**

* **The crash test.** Raise mid-run and assert the `ingest_run` row survives with `finished_at IS NULL` and
  no projection rows. It cannot pass before §2 and is the whole of #16 in one assertion.
* **The provenance contract test** (§2.3).
* **Pure CLI tests** — parser, chain ordering, glob resolution, missing/ambiguous input — plus one DB-gated
  end-to-end `main(["ingest", "unii", …])` against the committed fixtures.
* **The tie-break test** (§5.3), pinned by mutation.

**Then the full re-measure against the real releases, run through the new `chain`** — which dogfoods it, and
is the reason the chain is in scope at all. Predictions, each of which is a finding if it fails:

| | expected |
|---|---:|
| `ingest_unmatched_ingredient`, new bucket | **99** rows — #47's own measurement, re-measured here rather than assumed |
| `gap_unmatched_ingredient` | **2,150**, unchanged — all 99 are already covered by another writer |
| `register_from_gaps`, 11 kinds | **18,834**, unchanged |
| `ddi_candidate_pair` | **21,664**, unchanged |
| every other figure in HANDOVER | reproduces exactly |

If the gap count moves, the issue's premise ("nothing is lost today") stopped holding, and that is the
finding rather than a regression.

## 7. Traps this round leaves for the next change

* **`open_run` commits; `finish_run` does not.** Making them symmetric — by committing in both — would let
  `finished_at` be true about work that later rolls back.
* **`writer` is NOT NULL with no DEFAULT and `'unattributed'` is not a writer**; it is a historical marker. A
  new orchestrator adds its own value to the CHECK and to the module constant, exactly as a new `reason`
  does.
* **`loaded_release` is per `(source, writer)`, and that is not cosmetic.** Folding it back onto `source`
  re-hides the MED-RT staleness split that §3.1 exists to expose.
* **The chain's globs are a convention, and a convention that silently matches nothing is worse than none.**
  Zero matches and several matches are both errors.
* **`gap_unmatched_ingredient`'s tie-break must express its own reason.** After §5 it prefers a named row;
  a future `reason` value must not be assumed to sort anywhere in particular.
