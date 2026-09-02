# drugref — issue 174: the `ANALYZE` that reported success without running

Measurement record for the round that closes
[#174](https://github.com/cairn-ehr/drugref/issues/174) and
[#172](https://github.com/cairn-ehr/drugref/issues/172). No migration — the schema
does not change. Every number below was read off a run on this machine on
2026-09-02; the verification database is `drugref_notice174`, built from nothing.

---

## 1. How to reproduce it

```bash
# A FRESH database. The point is a full-scale ingest under two different roles,
# and a template would carry statistics written by whoever built it.
psql -h localhost -p 5532 -U postgres -c 'CREATE DATABASE drugref_notice174'
DSN='host=localhost port=5532 dbname=drugref_notice174 user=postgres'
uv run drugref --dsn "$DSN" migrate

/usr/bin/time -p uv run drugref --dsn "$DSN" ingest chain --downloads downloads \
    --unii-release 26Feb2026 --medrt-release 2026.07.06 \
    --mesh-release 2026 --mesh-relations-release 2026.07.06 \
    --gsrs-release 2026-02-26
/usr/bin/time -p uv run drugref --dsn "$DSN" ingest spl \
    --openfda downloads/OPENFDA \
    --dailymed downloads/DAILYMED/dm_spl_release_human_rx_part*.zip \
    --release 'openfda-2026-08-22+dailymed-2026-08-21'

# THE SPLIT THE ISSUE IS ABOUT: an application role that can read and write every
# table in drugref and owns none of them. `options=-c role=...` is how a run is
# made to happen AS that role without inventing an authentication story -- the
# connection authenticates as postgres and every statement runs as drugref_app.
psql -h localhost -p 5532 -U postgres -d drugref_notice174 <<'SQL'
CREATE ROLE drugref_app LOGIN;
GRANT USAGE ON SCHEMA drugref TO drugref_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA drugref TO drugref_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA drugref TO drugref_app;
SQL
SPLIT="$DSN options='-c role=drugref_app'"
/usr/bin/time -p uv run drugref --dsn "$SPLIT" ingest spl ...   # refuses, exit 2

# THE REMEDY THE MESSAGE NAMES, applied and re-measured.
psql -h localhost -p 5532 -U postgres -d drugref_notice174 \
     -c 'GRANT MAINTAIN ON ALL TABLES IN SCHEMA drugref TO drugref_app'
/usr/bin/time -p uv run drugref --dsn "$SPLIT" ingest spl ...   # succeeds
```

The bare defect, without drugref, on PostgreSQL 18.1:

```
SET ROLE probe;            -- USAGE on the schema, SELECT+INSERT on the table
ANALYZE probe_ns.t;
WARNING:  permission denied to analyze "t", skipping it
ANALYZE                    <- the command tag. No exception.
relpages | reltuples
       0 |        -1       <- and nothing was analysed
```

---

## 2. What was wrong

**`ANALYZE` does not raise when the calling role may not analyse a table it names
explicitly.** It emits a `WARNING`, **skips that table**, and returns the
`ANALYZE` command tag. And the warning went nowhere: psycopg delivers notices to
registered handlers and to nothing else, and before this round
`grep -rn add_notice_handler src/ tests/` returned nothing.

That combination is worth an ingest. Issue 160 measured what the missing
statistics cost: the `COPY` into `spl_label_subject` spent **630 s** at 96% of one
core, 100% of a stack sample inside `RI_FKey_check_ins`, because with
`relpages = 0` the foreign key's pinned plan was an index scan matching all 68,550
parent rows — and the orchestrator's own read-backs ran **25 minutes at 100% CPU**
without finishing. `analyze_loaded_table`'s docstring says in bold that the
statement is *not optional and not a tidy-up*. Under an admin-migrates /
app-ingests role split — an ordinary deployment, forbidden by nothing in this
codebase — every one of those statements silently did nothing, **and the run still
reported success**: `reconcile`, `read_pairs` and `check_floors` all count rows,
and the row counts are identical either way.

⇒ **THE PROJECT HAD ALREADY WRITTEN THE MECHANISM DOWN AS A COMMENT.**
`ingest/drugcentral_run.py` records the first time this discard cost something:
*"the server answers a mis-placed SET TRANSACTION with a NOTICE, not an error, and
psycopg discards notices unless a handler is installed, so the ingest reported
success having silently lost its atomicity."* That paragraph has sat in one module
since db/050. **A comment in one module is not a channel** — which is the whole
lesson of this round, and the same shape as the four rounds this repo has lost to
one rule kept in two places.

---

## 3. What shipped

### `server_messages.py` — the channel

* `SEVERITY_LEVEL` maps **all eight** severities the wire protocol defines to
  `logging` levels; `UNKNOWN_SEVERITY_LEVEL` is `WARNING`, deliberately the
  loudest reasonable answer. `Diagnostic.severity` is **localised**, so an
  unrecognised string is a real shape (a German server says `WARNUNG`), not a
  defensive fantasy — and `read_diagnostic` therefore prefers
  `severity_nonlocalized`, which is always English.
* `NOTICE` lands at `INFO`, not `DEBUG`, because `INFO` is the CLI's default
  `--log-level` and the cost was **measured, not guessed**: a full fresh migrate
  of all 53 `db/*.sql` files emits **35** notices in total, every one an
  "… does not exist, skipping" that an operator watching a migration wants.
  (PostgreSQL 18 no longer emits the per-table "will create implicit index"
  notice, which would have made the same run hundreds of lines and forced the
  opposite choice.)
* `collect(conn)` is the scoped collector a caller uses to *enforce* on what the
  server said. It installs **its own** handler rather than reading `db.connect`'s,
  because a guard that depended on how the connection was opened would fire on the
  CLI path and nowhere else — not in this suite, whose `conn` fixture calls
  `psycopg.connect` directly. That is issues 74/66/76's "gate that never fires",
  inside the guard meant to close 174.
* Nothing raises from a handler, and the docstring says why: psycopg calls notice
  handlers inside its result processing and **swallows** whatever they raise, so a
  handler is a reporting surface and can never be an enforcing one.

### `db.connect` — where the channel is installed

One line, at the one function every orchestrator, CLI command and migration opens
its connection through.

### `analyze.py` — the statement that proves it ran

`analyze_tables(conn, tables, schema="drugref")` builds the statement with
`sql.Identifier` (psycopg quotes and escapes; the caller's module-constant check
remains the separate *policy* about which tables a source may name), runs it inside
`server_messages.collect`, and then applies **two checks that are not one check
twice** — each shown in the suite killing a mutant the other cannot see:

| check | fires when | blind to |
| --- | --- | --- |
| the server raised a WARNING or worse | always, including a **re-ingest** | a connection whose notices go nowhere |
| `pg_class.reltuples` is still `-1` | statistics were **never** gathered | a re-ingest, where the previous run left plausible numbers |

`-1` is the sentinel and `0` is not a milder version of it: a table analysed while
empty has statistics. (The issue-160 review round already had to make exactly this
distinction — `reltuples >= 0` let two mutants live.) The postcondition is readable
inside the analysing transaction because `ANALYZE` writes `relpages`/`reltuples`
through `vac_update_relstats`, a non-transactional in-place update: measured `-1`
before and `500` immediately after, in one transaction.

Both refusal messages quote **the server's own words** rather than restating them,
and both name `current_user` — because the fix is a `GRANT` naming exactly that
role, and "permission denied" without "whose" is half a diagnosis.

---

## 4. Verified at full scale, on both sides of the role split

`drugref_notice174`, built from nothing on 2026-09-02.

| run | role | wall clock | outcome |
| --- | --- | --- | --- |
| `ingest chain` (5 feeds) | owner | **132.91 s** | every published figure reproduced |
| `ingest spl` | owner | **131.77 s** | every published figure reproduced |
| `ingest spl` (re-ingest) | `drugref_app`, no `MAINTAIN` | **81.76 s** | **REFUSED**, exit 2 |
| `ingest spl` (re-ingest) | `drugref_app`, `MAINTAIN` granted | **150.02 s** | succeeds, same figures |

The owner run reproduced the slice's published numbers exactly, as it had to —
this round changes no SQL and no parsing: **68,550** labels of **262,032** records
carrying **27,406** wordings → **29,952** pairs (**28,520** novel); **1,297,944**
occurrences over **26,760** wordings; **138,187** quoted windows using 87.9% of the
budget; **404,764** self-pair rows excluded. Against the 2 min 09 s recorded for
the same ingest after issue 160, 131.77 s is inside run-to-run noise: the guard
costs two catalogue reads per `ANALYZE`, six times per run.

⇒ **THE REFUSAL RAN AS A RE-INGEST, WHICH IS THE HALF A POSTCONDITION CANNOT
SEE.** At the moment it refused, `spl_wording.reltuples` was **27,406** — left
behind by the successful run above — so `never_analyzed` returned empty and
**only** the collected WARNING could have fired. That is not a contrived test
case; it is what the second ingest on any existing database looks like.

The refusal is clean in every way the project cares about:

* it names the cause in the server's words, the role, and the remedy —
  `ANALYZE of drugref.spl_wording reported success and the server simultaneously
  complained … Running as role drugref_app. The server said: [WARNING 01000]
  permission denied to analyze "spl_wording", skipping it. …`
* **exit code 2**, one line on stderr, via `cli.main`'s existing `RuntimeError`
  handler;
* the projection is **untouched** — 68,550 `spl_label` rows, all from one
  `ingest_run` — because a warning aborts no transaction and the orchestrator's own
  `except` rolled the run back;
* the abandoned run is visible as `ingest_run` 7 with `finished_at IS NULL`,
  exactly as every other mid-run failure leaves it.

⇒ **AND THE REMEDY THE MESSAGE NAMES ACTUALLY WORKS**, which this project checks
rather than assumes: `GRANT MAINTAIN ON ALL TABLES IN SCHEMA drugref TO
drugref_app`, and the identical command completes in **150.02 s** publishing every
figure above unchanged. A message that prescribes a no-op is a misdiagnosis loop,
and issue 122 exists because this repo shipped one. (150.02 s against the owner
run's 131.77 s is the re-ingest, not the role: that run additionally deletes the
1.3 million occurrence rows of the projection it replaces. `MAINTAIN` is the
privilege the *owner* already holds implicitly, so nothing about the analysed path
differs.)

**The channel is silent on a healthy run.** Not one `drugref.postgres` line
appeared during either successful ingest or during the chain; the only place it
speaks by default is `migrate`, at 35 lines on a fresh database.

---

## 5. Issue 172, closed on the way past

`spl_evidence.py` was **512** lines when this round started and **518** after the
guard landed — over CLAUDE.md rule 4's ~500 cap, in the module whose first
sentence is *"the SOLE writer of drugref's SPL rows"*. Issue 172 had already named
the seam: `Registry` / `load_registry`, **a READ path inside the sole writer**.

It moved to `registry_read.py` — "Reads of the IDENTITY SPINE", whose own docstring
says *"it is small on purpose; the spine's other reads can land here as they are
needed"*. It reads `substance_moiety` and `identity_claim` and nothing else, so it
was always a spine read wearing an SPL coat. Verbatim move first, whole suite
green, then nothing else: `spl_evidence.py` **518 → 428**, `registry_read.py`
**91 → 181**. Callers repointed: `ingest/spl_run.py`, `tools/spl_suppress_derive.py`,
`tools/spl_class_vocabulary_delta.py`, and two test modules.

⇒ **AND THE CAP BECAME A SWEEP INSTEAD OF THREE REMEMBERED FILES.** `500` was
written down in `test_cli.py` and again in `test_cli_signing.py`, each pinning the
one file its author had just written — three of forty-odd modules, which is how
`spl_evidence.py` reached 518 with a green suite. Both are deleted and folded into
`tests/test_module_size_cap.py`, which owns the number once and sweeps every module
under `src/drugref`. Seven modules were already over the cap (`questions.py` at 797
down to `ingest/spl_match.py` at 524); they are a **checked ledger**, not an
allow-list — a second test asserts each is *still* over, so a name left behind
after a split fails rather than becoming a permanent exemption. Filed as
[#177](https://github.com/cairn-ehr/drugref/issues/177).

---

## 6. What this round did not do

* **`has_table_privilege(…, 'MAINTAIN')` as a PRECONDITION** was considered and
  rejected. It would name the cause before spending the `ANALYZE`, but it is a
  PostgreSQL 17+ spelling (`unrecognized privilege type` on 16 and earlier aborts
  the transaction), and it would be a *third* check for a condition the two above
  already catch in every case measured — one rule in three places, to save two
  catalogue reads.
* **No repo-wide notice ASSERTION.** `db.connect`'s handler reports; only
  `analyze_tables` refuses. Making every unexpected server warning fatal
  everywhere is a much larger behaviour change and would need its own measurement
  of what the ingests actually emit — which, on the evidence above, is nothing.
