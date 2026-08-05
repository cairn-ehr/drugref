# drugref — the policy-surface debt round (#59, #60, #61, #63)

**Date:** 2026-08-05 · **Status:** design, approved · **Issues:**
[#59](https://github.com/cairn-ehr/drugref/issues/59),
[#60](https://github.com/cairn-ehr/drugref/issues/60),
[#61](https://github.com/cairn-ehr/drugref/issues/61),
[#63](https://github.com/cairn-ehr/drugref/issues/63)

A debt round, not a slice. **No new source, no migration, no clinical claim, and no published figure may
move.** It settles the four follow-ups the expansion-policy history round (#35) filed rather than fixed, and
they belong together because three of them are one sentence in different registers: *the append-only overlay
now has a rule, a table and a warning, but no shared primitive, no operator surface, and a chain that
refuses its own documented invocation.*

## 1. Why these four, and why now

#35 moved `class_expansion_policy` onto Plan C's append-only floor and, in doing so, left three things
standing:

* the insert-then-point rule now has a **third** hand-written copy (#59);
* `medrt_run`'s "re-key or withdraw" warning names two verbs the console script cannot perform, and raw SQL
  can no longer perform either, because the floor refuses both (#61);
* `drugref ingest chain` cannot run the four-source invocation that HANDOVER, the ingest-operability spec
  and #35's own plan all document (#60) — a guard that landed *after* the measurement it invalidates.

#63 rides along because this round rewrites the two documents anyway, and because the round's own findings
are exactly the kind of standing claim whose history the current churn destroys.

**Not in this round:** #36 (needs a curator ruling on a new metric and its own re-measure), #37 (not urgent
at a 2.876 ms filtered lookup), #48 (still structurally unreachable — it goes live when a *class-side*
predicate stops expanding), and any curation. Slice 5c owns #51, #52 and #55.

### 1.1 One finding this round starts with

`#61 was closed by commit 92baaea while its own commit body declared it unfixed.` GitHub's linker accepts
`fixed:` as a closing keyword, so the sentence *"Filed rather than fixed: #61 gains the index question this
round leaves open"* closed the issue it was declaring open. Verified against the code before reopening:
`build_parser` registers `migrate`, `status` and `ingest`, and nothing else.

**This is the fourth occurrence of the sweep-closed-but-unfixed pattern** (#31, #35, #40, #61), and the first
where the author was deliberately writing prose to avoid it. The standing rule as HANDOVER states it — *when
filing rather than fixing, use prose no closing keyword can be parsed out of* — is therefore **not sufficient
as written**, and §6 restates it: the hazard is **token adjacency**, `fixed: #61`, not the sentence's meaning.

## 2. #60 — a step declares which inputs it does not date

### 2.1 The tension, restated exactly

`mesh_rel_run` reads **two authorities**: MED-RT states the rule, MeSH defines its object. It writes **one**
`ingest_run` row, under `source='MED-RT'`, `writer='mesh_rel_run'`, carrying MED-RT's release tag. So the
step's `--mesh-relations-release` describes MED-RT, while the same step's `desc*.gz`/`supp*.gz` inputs are
dated by the `mesh` step as `2026`.

`check_release_agreement` compares tags **per resolved path across steps**, and so reads that as one file
claimed to be two releases. It is not: it is one file **dated once**, by `mesh`, and merely *read* by
`mesh-relations`.

### 2.2 The change

`IngestStep` gains one optional field:

```python
IngestStep("mesh-relations",
           inputs=(("medrt", "MEDRT/Core_MEDRT_*_XML.xml"),
                   ("desc",  "mesh/desc*.gz"),
                   ("supp",  "mesh/supp*.gz")),
           secondary=("desc", "supp"),
           runner=_run_mesh_relations)
```

`check_release_agreement` records a claim **only for primary inputs** — those not named in `step.secondary`.
A path claimed solely as secondary enters the map not at all, and so conflicts with nothing.

`secondary` names inputs, not paths, because the declaration belongs beside the glob it qualifies and must
survive a glob's filename changing between releases.

### 2.3 What stays refused, and what deliberately stops being refused

**Still refused** — the case `check_release_agreement`'s docstring calls uncorrectable:

```
--medrt-release 2026.07.06 --mesh-relations-release 2026.05.04
```

`MEDRT/Core_MEDRT_*_XML.xml` is **primary for both** steps, so two different tags for identical bytes are
still a pre-flight error. This is the case that matters: `db/025` added `writer` precisely so an operator
could see one half of MED-RT running a release behind the other, and letting the two halves disagree on
purpose makes that signal report staleness that does not exist.

**Deliberately allowed now** — `mesh` and `mesh-relations` disagreeing about `desc`/`supp`. That is a
behaviour change, not a side effect, so it gets its **own test asserting it passes**, not merely the absence
of a test asserting it fails. A guard that quietly stops guarding is worse than one that never existed.

### 2.4 The typo that would exempt nothing

`secondary=("dsc", "supp")` would silently exempt one input and leave the chain refusing. `IngestStep`
therefore validates in `__post_init__`: every name in `secondary` must be a declared input name, or
`ValueError` at import, where `STEPS` is built. A test pins it. *A convention that silently matches nothing
is worse than none* — the rule `resolve_inputs` and `selected_steps` already state, applied to the third
place it can now bite.

## 3. #61 — `drugref policy`, three subcommands

### 3.1 The surface

```
drugref policy record   --source MED-RT --code N0000175655 --decision deny \
                        --class-name "…" --rationale "…" \
                        --reviewed-by "…" --reviewed-against "…"
drugref policy withdraw --source MED-RT --code N0000175655 \
                        --rationale "…" --reviewed-by "…" --reviewed-against "…"
drugref policy show     [--source MED-RT --code N0000175655]
```

`show` with no arguments prints what currently **binds** (from `class_expansion_policy_current`) and the
count of unresolved rows; with `--source`/`--code` it prints the full history for one class in `policy_id`
order — decision, rationale, reviewer, release reviewed against, and what superseded it.

**`show` is not decoration.** An operator following `medrt_run`'s warning must write a rationale. Without
sight of the one they are replacing they are writing blind, and the history they cannot see is the whole of
what #35 built.

### 3.2 Where the SQL lives

`cli.py` writes **no SQL** — it calls `interactions.py`, as every other handler calls an orchestrator. This
is load-bearing: `test_only_the_current_view_reads_the_policy_table_directly` reads `pg_rewrite`, so it sees
views and matviews and **cannot see SQL embedded in Python**. A `policy` handler writing its own query would
be a fifth reader of the base table that no test could ever notice.

`show` needs two new readers in `interactions.py`:

```
live_decisions(conn)                        -> the binding set, via class_expansion_policy_current
decision_history(conn, source, source_code) -> every row for one class, superseded ones included
```

`decision_history` **names the base table deliberately** — history is precisely what the `_current` view
filters out. The #62 review's finding was that *nothing mechanical distinguishes a fourth added by accident*,
so:

> **A grep contract test pins WHERE `drugref.class_expansion_policy` appears under `src/drugref`, per file.**

Same shape as the two `provenance.py` contract tests and `test_source_clear_contract.py`, whose table tuples
are each restated independently so a drop fails.

**The arithmetic is not what it looks like, and the test must be written against the measured truth rather
than the intuitive one.** Today there are **three** qualified namings in `interactions.py` — the INSERT
(115), the supersession UPDATE (124) and `withdraw`'s `SELECT class_name` (150) — and this round both removes
one and adds one:

| | before | after |
|---|---:|---:|
| `interactions.py` INSERT / `SELECT class_name` | 2 | 2 |
| `interactions.py` supersession UPDATE | 1 | **0** — becomes an `overlay.supersede` call (§4) |
| `interactions.py` `decision_history` SELECT | 0 | **1** — new, §3.1 |
| **`interactions.py` total** | **3** | **3** |
| `ingest/medrt_run.py` | 1 | 1 |

**`medrt_run.py:254` is the trap.** It names the table in an operator **warning message** — prose, not SQL —
and a grep that counted it as a reader would be counting the sentence that tells an operator the table is
append-only. It stays, and the pin records it *as prose*, per file, so the distinction is written down rather
than rediscovered.

**`overlay.supersede` never names the table at all**: it takes the bare `"class_expansion_policy"` as an
argument and composes `drugref.{table}` with `sql.Identifier`. So the qualified string does not appear in
`overlay.py`, and the bare name at the call site is not a naming in the sense the pin measures — which is
itself worth stating, because it is the one way a future reader could satisfy the letter of the test while
adding a real fifth reader.

The standing rule *one reader, one clear, one checksum, one run record* gains its fifth member: **one place
that names the policy table.**

### 3.3 Why `--decision` has no `choices`

The decision vocabulary lives in `db/027`'s CHECK, which is the one place it lives; restating it as argparse
`choices` would be a second list to disagree with the first — exactly what `db/006` replaced a
comment-enforced CHECK↔CASE coupling to prevent, and what `record_expansion_decision`'s docstring already
refuses to do. An unrecognised value therefore reaches the database and raises `CheckViolation`.

**`record` does refuse `withdrawn`**, pointing at `policy withdraw`. This is not the vocabulary in a second
place: `withdraw_expansion_decision` already hardcodes that string once, and it becomes the module constant
`interactions.WITHDRAWN` that both uses read. The count of places holding the string stays **one**.

The reason to refuse it is the hazard the #62 review documented: `record_expansion_decision` accepts
`withdrawn` and bypasses both guarantees `withdraw_expansion_decision` exists to provide — the
`NoLiveDecisionError` that catches a caller believing something false, and carrying `class_name` forward so a
withdrawal cannot introduce a name nobody reviewed. The library keeps that door open by design. **An
operator surface should not.**

### 3.4 Transactions and errors

The library functions do not commit — the caller owns the transaction, as everywhere in these modules. The
CLI **is** the caller, so `policy record` and `policy withdraw` commit; `policy show` writes nothing.

`NoLiveDecisionError` is a `LookupError`, and `main` currently catches `(RuntimeError, ChainError)`. It joins
the caught family, so withdrawing a decision nobody made prints `drugref: no live expansion decision for …`
and exits 2, rather than a traceback.

## 4. #59 — one insert-then-point primitive

New `src/drugref/overlay.py`:

```
supersede(conn, table, pk_column, new_id, key_columns, key_values) -> None
```

This is `accumulation._supersede` **moved verbatim**, including its `psycopg.sql` composition of the
natural-key predicate. The issue's own assessment holds: it is already generic over table and pk, so this is
an import and a rename, not a refactor.

Three owners move onto it:

| caller | table | natural key |
|---|---|---|
| `accumulation` ×4 | `additive_effect`, `effect_contribution`, `interaction_group_assertion`, `interaction_group_member` | as today |
| `questions.set_state` | `question_state` | `(question_uuid,)` |
| `interactions.record_expansion_decision` | `class_expansion_policy` | `(source, source_code)` |

The module docstring states the rule **once** — insert, then point; both rows are briefly live; that is
exactly why single-live is a DEFERRED constraint trigger and not a partial unique index — where three module
docstrings restate it today.

**`claims.add_claim` is not a fourth copy** and does not move. It uses `ON CONFLICT DO NOTHING` scoped to
live rows: idempotent re-assertion, not correction. Conflating the two would put a supersession where
`db/005` wants a no-op.

A grep contract test pins `UPDATE … SET superseded_by` to `overlay.py` alone. That test **fails first** and
is what drives the move; behaviour is otherwise identical and the existing suite is the regression net.

## 5. #63 — split the two documents by volatility

### 5.1 The measurement

#62 replaced ~80% of both files in one round, and that is the pattern rather than the exception. Both sit
deliberately just under the `CLAUDE.md` < 500-line bound (499 and 496), and it is that bound which forces a
compression pass, and the compression pass which turns an edit into a rewrite. Round 1 of #62 lost content
that way and needed commit `06640a6` to restore it — the cost, already paid once.

### 5.2 The split

| file | role | bound |
|---|---|---|
| `docs/HANDOVER.md` | **volatile**: where we are now, what is in flight, next candidates, DSNs and measurement databases, tracker hygiene | ~120 lines, regenerated freely |
| `docs/PROJECT-NOTES.md` | **new, stable**: traps per round, current state by layer, how to run/test, schema and code map, upstream errata, repo facts | edited in place, no bound |
| `docs/ROADMAP.md` | unchanged role: slice ordering and per-slice records | no bound |

The volatile part is genuinely small — HANDOVER's `⇒ NEXT` block and the open-follow-ups list. Everything
else churns only because of the bound.

`PROJECT-NOTES.md` rather than `TRAPS.md`: most of that content is traps, but how-to-run, the schema/code map
and repo facts are not, and a name that promises less ages better.

**Its git history starts now.** That is the honest cost of the split and is worth stating: the split buys a
readable history going *forward*, not retroactively.

### 5.3 The two rule changes

`CLAUDE.md` is edited — genuinely, not as routine wrap-up (nextsession rule 9): a rule and a command change.

* *"Session state lives in `docs/HANDOVER.md`"* becomes the three-file split, saying which file takes what.
* The < 500-line instruction is scoped to `HANDOVER.md` and stated as ~120 lines; `PROJECT-NOTES.md` and
  `ROADMAP.md` are explicitly **not** bounded, with the reason — *a bound that forces recompression trades a
  readable history for a line count.*

`.claude/skills/nextsession/SKILL.md` rules 8 and 9 learn the third file.

## 6. The standing rule this round corrects

HANDOVER states: *when filing rather than fixing, use prose no closing keyword can be parsed out of.* §1.1
shows that is not enough. Restated:

> **Keep the issue number away from any of close/fix/resolve, in any inflection, regardless of what the
> sentence means.** GitHub's linker matches on token adjacency and accepts a colon: `fixed: #61` closes #61
> inside a sentence declaring it unfixed. Write `issue 61`, or a bare URL.

## 7. Testing

TDD throughout — failing test first.

**Pure, no database** (`#60`, and the argument layer generally):
* the documented four-source invocation passes `check_release_agreement`;
* two steps disagreeing on a **primary** shared path still raise `ReleaseError`;
* `mesh`/`mesh-relations` disagreeing on `desc`/`supp` now passes, asserted explicitly;
* `secondary` naming an undeclared input raises `ValueError` at construction.

**Contract tests (grep over the tree)**, which fail before the work and pin it after:
* `UPDATE … SET superseded_by` appears in exactly one file under `src/drugref` — `overlay.py`;
* `drugref.class_expansion_policy` appears under `src/drugref` in exactly the two files and counts §3.2
  tabulates: `interactions.py` **3** (SQL) and `ingest/medrt_run.py` **1** (an operator warning, prose). Both
  numbers are restated independently in the test, so either a new reader or a deleted one fails.

**Database-gated** (`#61`): `policy record` writes and commits a revision that supersedes the live row;
`policy withdraw` returns the class to `gap_unreviewed_expansion_root`; `policy withdraw` against a class
with no live decision exits 2 with the message rather than a traceback; `policy record --decision withdrawn`
is refused and names the other subcommand; `policy show` prints the binding set and one class's history.

Every one of these is a branch **no release-derived database can exercise** — no such database holds a
superseded or withdrawn row — so, per the standing rule (#42), they are pinned on controlled input and the
key ones verified by mutation.

### 7.1 The measurement that closes #60's loop

The round ends by running **the exact invocation #60 says is refused**, against a fresh database, from the
real releases already on disk:

```bash
uv run drugref --dsn "$DSN" ingest chain --downloads downloads \
  --unii-release 26Feb2026 --medrt-release 2026.07.06 \
  --mesh-release 2026 --mesh-relations-release 2026.07.06
```

Two things must hold: it runs, and **every published figure reproduces** — `ddi_candidate_pair` **21,664**,
`open_question` **18,834**, `gap_dead_by_expansion_policy` **1**, `gap_unreviewed_expansion_root` **0**,
`class_expansion_policy` **14** rows all binding, `loaded_release` **4** rows with both MED-RT writers.

This is not optional thoroughness. #60 exists *because* the guard landed after the measurement and the
documented command was never re-run against it; a round that fixed the guard without re-running the command
would reproduce the defect it is closing.
