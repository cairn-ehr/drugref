# drugref — issue 159: `finished_at − started_at` was not a duration, for any feed

Measurement record for the round that closes
[#159](https://github.com/cairn-ehr/drugref/issues/159). `db/053`, no new
projection, no figure moved. Every number below was read off a run on this
machine on 2026-09-02; the verification database is `drugref_dur159`.

---

## 1. How to reproduce it

```bash
# A FRESH database, not a TEMPLATE: the point is to measure nine feeds from
# nothing, and a template carries nine ingest_run rows written under the old
# meaning.
createdb -h localhost -p 5532 -U postgres drugref_dur159
DSN='host=localhost port=5532 dbname=drugref_dur159 user=postgres'
uv run drugref --dsn "$DSN" migrate

/usr/bin/time -p uv run drugref --dsn "$DSN" ingest chain --downloads downloads \
    --unii-release 26Feb2026 --medrt-release 2026.07.06 \
    --mesh-release 2026 --mesh-relations-release 2026.07.06 \
    --gsrs-release 2026-02-26
/usr/bin/time -p uv run drugref --dsn "$DSN" ingest onchigh --release ONCHigh-2015
/usr/bin/time -p uv run drugref --dsn "$DSN" ingest fda-cyp \
    --page downloads/FDA/fda_cyp_2026-05-29.html
/usr/bin/time -p uv run drugref --dsn "$DSN" ingest drugcentral \
    --dump downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz --release 11012023
/usr/bin/time -p uv run drugref --dsn "$DSN" ingest spl \
    --openfda downloads/OPENFDA \
    --dailymed downloads/DAILYMED/dm_spl_release_human_rx_part*.zip \
    --release 'openfda-2026-08-22+dailymed-2026-08-21'

uv run drugref --dsn "$DSN" status
```

---

## 2. What was wrong, and why every feed was wrong at once

`provenance.open_run` took `started_at DEFAULT now()`; `provenance.finish_run`
wrote `finished_at = now()`. **`now()` is `transaction_timestamp()`**, fixed for
the whole transaction. `open_run` COMMITS — that is db/025's whole design, so a
crash leaves the run row standing — so the two stamps belong to two *different*
transactions, and the subtraction measured **the gap between two transaction
start times**: the interval between `open_run`'s INSERT and the work
transaction's first statement.

That is not a duration. It is the time the orchestrator spent **not touching the
database**, which for most feeds is nothing at all.

| writer | `drugref_spl051` (2026-08-27) | `drugref_spl160fix` (2026-09-01) |
|---|---|---|
| `unii_run` | 0.0018 s | 0.0018 s |
| `medrt_run` | 0.0013 s | 0.0013 s |
| `mesh_run` | 0.0019 s | 0.0019 s |
| `mesh_rel_run` | **48.32 s** | **48.32 s** |
| `gsrs_run` | 0.0238 s | 0.0238 s |
| `drugcentral_run` | 0.0015 s | 0.0015 s |
| `fda_cyp_run` | 0.0022 s | 0.0022 s |
| `onchigh_run` | 0.0018 s | 0.0018 s |
| `spl_run` | **49.85 s** | **0.0026 s** |

### ⇒ THE ISSUE'S OWN HEADLINE EXAMPLE EVAPORATED UNDER IT, AND NOBODY LOOKED

#159 was written from `drugref_spl051`, where `spl_run` reported 49.85 s and the
issue explained it correctly: the DailyMed scan and the 19.3 GB checksum sat
between `open_run` and the first write, so the gap happened to be a real
(partial) duration there. **The COPY-cost round then put a `conn.rollback()` in
front of that scan** ([spl_run.py:313](../../../src/drugref/ingest/spl_run.py))
to close a ~50 s `idle in transaction` window — which moved `open_run` onto the
far side of the scan and collapsed the gap to **2.6 ms**. Measured above on both
databases that round built. Nothing in the suite, the issue or the round's own
verification noticed that a fix had silently rewritten the number a filed issue
was reasoning from.

⇒ **A NUMBER IN A FILED ISSUE IS A MEASUREMENT WITH NO OWNER.** The round that
moves it will not be the round that reads it. Re-measure an issue's premise
before designing against it — this round did, and found eight of the nine
figures unchanged and the ninth gone.

The one survivor, `mesh_rel_run`'s 48.32 s, is the diagnosis stated plainly: that
orchestrator parses 750 MB of MeSH between `open_run` and its first write, and
48.32 s is exactly how long that parse takes — see §4, where the same parse now
appears **inside** a 56.81 s total.

---

## 3. What shipped

**`provenance.RunClock` / `start_clock()`** — a frozen dataclass over
`time.monotonic()`. Monotonic, so an NTP step part-way through a twelve-minute
ingest cannot produce a negative duration. **A type rather than a `float`**,
because `open_run` cannot tell a `time.monotonic()` reading from a `time.time()`
one by looking, Python enforces no annotation at runtime, and the two differ by
about 56 years — a wrong duration, silently, which is the failure class this
issue is about.

**`open_run` takes a required keyword-only `clock`** and writes

```sql
started_at = clock_timestamp() - make_interval(secs => %s)
```

Only the **elapsed interval** crosses from the client, never a client timestamp,
so both ends of the subtraction are read off the server's clock: an ingest driven
from a host whose clock is minutes out still records a true duration. Required
for the same reason `writer` is — a new orchestrator cannot forget an argument it
cannot omit.

**`finish_run` writes `clock_timestamp()`.** Its no-commit contract is untouched:
the stamp still lands in the work's transaction so that the two publish
atomically, which means the duration **excludes the caller's final COMMIT**. That
exclusion is stated in the column comment rather than left to be discovered — and
§4 shows it is 3.8 s for SPL, not nothing.

**Eleven orchestrators take the clock on their first line.** Eleven is derived,
not counted by hand: `test_every_module_that_opens_a_run_takes_a_clock` greps the
tree for `provenance.open_run(` and requires `provenance.start_clock()` in the
same module. Five of the eleven have a private `_ingest` body, and the clock is
taken in the **public** entry and threaded in, because the public function is
where the command begins.

**`db/053`** — the `started_at` default (the direct-INSERT path `open_run` does
not cover: a `curation` run, and 47 test modules), a
`CHECK (finished_at IS NULL OR finished_at >= started_at)`, both column comments,
and a re-issue of db/025's `ingest_run_incomplete` comment.

**`drugref status`** prints the runtime, through the pure
`provenance.format_run_duration`.

---

## 4. The verification: nine feeds, recorded against wall clock

`/usr/bin/time -p` on the command, against `finished_at - started_at` read out of
the database afterwards. The five chain feeds share one command, so they are
compared as a sum.

| command | recorded | wall clock | recorded ÷ wall |
|---|---|---|---|
| `ingest chain` (five feeds) | **137.46 s** | 137.82 s | **99.7 %** |
| `ingest drugcentral` | 19.64 s | 20.00 s | 98.2 % |
| `ingest onchigh` | 3.87 s | 4.26 s | 90.8 % |
| `ingest fda-cyp` | 4.11 s | 4.44 s | 92.6 % |
| `ingest spl` | **135.86 s** | 140.06 s | **97.0 %** |

Per writer, as `drugref status` now prints it:

| writer | recorded | was |
|---|---|---|
| `unii_run` | 7.84 s | 0.0018 s |
| `medrt_run` | 14.69 s | 0.0013 s |
| `mesh_run` | 45.65 s | 0.0019 s |
| `mesh_rel_run` | 56.81 s | 48.32 s |
| `gsrs_run` | 12.47 s | 0.0238 s |
| `drugcentral_run` | 19.64 s | 0.0015 s |
| `onchigh_run` | 3.87 s | 0.0018 s |
| `fda_cyp_run` | 4.11 s | 0.0022 s |
| `spl_run` | **135.86 s** | **0.0026 s** — 51,657× |

### The residuals are accounted for, not waved at

The interpreter start plus argparse plus `db.connect` is **0.29–0.34 s**,
measured directly (two mis-quoted invocations that died in argparse took 0.34 s
and 0.29 s real). That is the whole residual for four of the five commands:
0.36 s, 0.36 s, 0.39 s, 0.33 s.

**SPL's residual is 4.20 s, and ~3.8 s of it is the final COMMIT** — which is
where the DEFERRED quote-budget trigger fires over 138,187 windows. The stamp
cannot cover it without breaking `finish_run`'s no-commit contract, so it is
named in the `finished_at` comment instead. It is the one feed where the
exclusion is worth knowing about.

### `mesh_rel_run` is the internal cross-check

The old number, 48.32 s, is now a **subset** of the new one, 56.81 s: the parse
is 48 s and the writes are 9 s. If the diagnosis in §2 were wrong the two numbers
would have no such relationship. Nothing about that orchestrator changed.

### The SPL run reproduced every published figure

68,550 labels of 262,032 records · 27,406 wordings · 29,952 pairs (26,598 novel)
· subjects `openfda_unii` 27,494, `dailymed_active_moiety` 10,555,
`dailymed_active_substance` 23, `absent_from_dailymed` 30,386, `unresolved` 92 ·
1,297,944 occurrences over 26,760 wordings · 138,187 quoted windows using
22,954,172 of 26,106,268 budgeted characters (87.9 %). Identical to the COPY-cost
round's, on a database built from nothing rather than from a template.

---

## 5. Rows already on disk, and the refusal that protects them

Nothing rewrites them and nothing could: what would be needed was never recorded.
So `drugref status` **refuses to print a runtime** for a run that started before
db/053 was applied on that database, reading the watershed out of the migration
ledger (`db.migration_applied_at`). Subtracting two transaction stamps still
yields a number — 0.0026 s for a 2 min 20 s ingest — and a number is what an
operator believes.

Both paths verified, neither by patching a verification database:

- **db/053 not applied** (`drugref_spl160fix`, read-only): all nine rows print
  `pre-db/053`. An unknown watershed means nothing can be a duration, which is
  the safe direction.
- **the production upgrade path** (`drugref_dur159mixed`, a fresh clone of
  `drugref_spl160fix` then `migrate`): db/053 applies cleanly over nine
  pre-existing rows — **the CHECK validated all nine**, as it had to, since
  `open_run`'s transaction always commits before the work's begins — and all nine
  still print `pre-db/053`.

---

## 6. What the CHECK caught on its first full run, which was the suite

Five tests failed the moment `ingest_run_finishes_after_it_starts` existed, all
with the same shape:

```
CheckViolation: new row for relation "ingest_run" violates check constraint
DETAIL: Failing row contains (18, UNII, …, 07:26:43.421257+08, 07:26:43.417461+08, …)
```

**Finished 3.8 ms before it started.** Two test helpers
(`tests/test_ingest_observability.py`'s `_run`, and one INSERT in
`tests/test_releases.py`) simulated a finished run with
`finished_at = now()` while letting `started_at` take its default — which db/053
had just changed to `clock_timestamp()`. `now()` is the transaction's start and
therefore *earlier* than a `clock_timestamp()` taken a statement later.

⇒ **MIXING `now()` AND `clock_timestamp()` IN ONE TRANSACTION PRODUCES A NEGATIVE
DURATION**, and without the constraint those two helpers would have gone on
producing one silently. A CHECK added "for completeness" found two live
occurrences of the exact idiom the round was removing, in the suite that was
supposed to be verifying the removal.

---

## 7. The rules this round leaves behind

⇒ **A NUMBER IN A FILED ISSUE IS A MEASUREMENT WITH NO OWNER.** #159's headline
figure was rewritten by an unrelated fix five days after it was filed, and the
issue, the suite and that fix's own review all read past it. Re-measure the
premise before designing against it.

⇒ **`now()` IS NOT A CLOCK.** It is `transaction_timestamp()`. Any two `now()`s
in one transaction are equal by definition, and any two across a commit boundary
measure the boundary rather than the work. This project had the wrong one in the
one place a duration was being computed, for nine feeds and nine migrations.

⇒ **A DERIVED COVERAGE CHECK OUTLIVES A HAND-LISTED ONE, BUT ONLY FOR WHAT IT
DERIVES.** The grep contract proves every module that opens a run also starts a
clock. It passed unchanged against the mutant that moved `start_clock()` down to
the line above `open_run` — which measures nothing. Only
`test_a_run_records_the_work_done_before_it_opened`, which injects a delay into
work the orchestrator does *before* `open_run`, kills it: recorded 59.5 ms
against the 250 ms required.

⇒ **A FIX THAT LEAVES OLD ROWS BEHIND MUST SAY SO WHERE THEY ARE READ.** The
column comment is necessary and not sufficient — the operator surface is
`drugref status`, and it is the surface that has to refuse.


---

## 8. What the review of this round found, and what it changed

The specs under `docs/superpowers/specs/` are immutable once merged; this section was
added while the PR was still open, and records the round's own corrections rather than
leaving the account above standing as if it had shipped clean.

**Three defects shipped in the first pass, all now fixed.**

1. **`drugref status` crashed mid-output on a database with no ledger.** The watershed
   read (`db.migration_applied_at`) was unguarded on the happy path, so a database
   built by replaying `db/*.sql` by hand — a shape `migration_guard`'s own docstring
   names as reachable — printed `loaded releases:` and then a psycopg traceback, with
   five of the command's six blocks never running. Reproduced end-to-end, fixed with a
   `to_regclass` probe (NOT `db.missing_relations`, which rolls back and would have
   silently discarded a caller's transaction on a happy path).
2. **The `started_at` column comment refuted itself in nine words** — *"every one of
   the nine feeds reported between 1.3 ms and 24 ms … and the one that reported
   anything else"* — while HANDOVER, ROADMAP, PROJECT-NOTES and §2 of this document all
   said *eight of nine*. It shipped into `pg_description`, where a `db/054` would have
   been needed to correct it had the PR merged first.
3. **`format_run_duration` printed impossible clock readings.** `0m60s`, `1m60s`,
   `60m60s` for any duration in `[N·60 − 0.5, N·60)` — 0.83 % of runs over a minute —
   because the `< 60` branch was decided on `round(seconds, 1)` while the minutes
   branch re-rounded the *unrounded* remainder. ⇒ **TWO ROUNDINGS OF ONE QUANTITY IS
   ONE RULE KEPT IN TWO PLACES**, the defect this project keeps finding, in arithmetic
   rather than in vocabulary.

**The derived contract was weaker than its own docstring claimed.**
`test_every_module_that_opens_a_run_takes_a_clock` said it caught a contract "satisfied
uselessly"; it greps for the *presence* of `start_clock()` and cannot. The one
behavioural killer drives `ingest_unii` — whose pre-open work is a crosswalk, an
allowlist and a checksum — so moving `start_clock()` down in `spl_run` (108 lines and a
17.6 GB scan above its `open_run`) left the suite green while dropping the entire figure
this round exists to publish. The grep also matched a **comment** in `onchigh_run.py`.
Replaced by an AST test asserting `start_clock()` is the first executable statement of
all eleven entry points, mutation-verified against `spl_run`.
⇒ **A GREP DERIVES TEXT, NOT STRUCTURE.**

**`RunClock` guarded the wrapper, not the value.** `RunClock(time.time())` — one
keystroke from `start_clock()`, and exactly the confusion `open_run`'s error message
describes — passed the `isinstance` check, committed a run dated 2083, and then threw
the whole ingest away when `finish_run` tripped the CHECK on its last statement before
the commit. `__post_init__` now rejects a reading in the future, which is the general
predicate and makes `elapsed()`'s "never negative" true by construction.

**Two claims in the catalog were false and are corrected.** The CHECK's *"can only be
violated by a caller inventing one"* omitted a hand-built `RunClock` and a **backward
server clock** — `RunClock`'s monotonic guarantee covers only the client-side window
before `open_run`; the long span between the two stamps is two reads of a wall clock,
and a backward step now costs the whole ingest rather than recording a negative
interval. All three causes, and what an operator should do, are now in a
`COMMENT ON CONSTRAINT` — the only new object here that reaches a human as an error
message, and the only one that shipped without catalog text.

**Stale hand-listed counts removed**, several of them written by this round: "the six
orchestrators" (six live copies, in files this diff edits), "unlike medrt_run, mesh_run
and mesh_rel_run" (seven writers now do pre-open work), "two dozen tests" (47 modules),
"drugref's nine feeds" (nine is what was measured; eleven writers exist), and a cited
grep whose stated output (one hit) was wrong (two — the file self-matches).

**Deferred, with the reason.** [#176](https://github.com/cairn-ehr/drugref/issues/176):
the watershed decides by **when** a row was written when the question is **which code**
wrote it. An older client against a migrated database takes the new `clock_timestamp()`
default for `started_at` and old `finish_run`'s `now()` for `finished_at`, so neither
the CHECK nor the refusal fires and a two-second run publishes as `0.0s` — issue 159's
own failure mode, reproduced. A boolean set by `open_run` would make each row
self-identifying. Not done here because it rewrites this round's central mechanism after
its measurements were verified; what was done instead is to stop anything claiming the
failure cannot happen.
