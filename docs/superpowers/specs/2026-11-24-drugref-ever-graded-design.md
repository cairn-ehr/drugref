# Ever-graded pairs: a reviewed grade never vanishes silently (#281, #279)

Date: 2026-11-24. Status: design, awaiting owner approval. Owner rulings: #281 option (c) "track what was
ever graded", applied to both grains (so it also closes #279); a departure banner ends by an explicit,
recorded acknowledgement; curators find open departures with `drugref audit departures` (2026-11-25).
Calls marked "my call" are the designer's and open to correction.

## 1. Intent

`drugref interactions` is decision support. When a clinician has seen a reviewed grade for a drug pair
(contraindicated, avoid, ...), that grade must not disappear from a later answer without the answer saying
so. #256 made this true for one case: a live class ruling whose **ruling-time reach snapshot** (db/080) held
the pair. Three ways remain silent:

1. **A correction after the drug left** (#281 §1): the correction re-snapshots against today's membership, so
   the drug is in no live snapshot.
2. **A drug that joined after the ruling and later left** (#281 §2): it was never in any snapshot.
3. **The moiety grain** (#279): a `curated_interaction` (subject moiety × object class) has no reach
   snapshot at all; a partner leaving the object class, or a policy deny cutting the subclass it was reached
   through, drops the grade silently.

The fix records, append-only, **every pair `curated_ddi_pair` has ever graded**, per ruling key, and banners
any recorded pair the view no longer grades while its ruling still stands. The banner stops for a pair only
when a curator records that they accepted the departure (who, when, why), so the audit trail
([[decision audit]] spec, 2026-11-16) shows the acceptance.

## 2. Goals, non-goals, anti-goals

**Goals**
- Any pair that `curated_ddi_pair` graded at any write point since the migration is detected when it stops
  being graded, for both grains, whatever the cause (membership change, policy deny, the drug joining late).
- A curator can acknowledge a departure; the acknowledgement is append-only, names its reviewer and
  rationale, and silences that pair until the pair is graded again and departs again.
- The record travels in the release export and is compared on replay (owner preference: a recorded fact
  that survives replay beats an inference, see memory "decision audit trail", #266).

**Non-goals**
- Recovering history from before the migration. The backfill is the best available evidence and says so.
- Signing acknowledgements. They carry `reviewed_by` / `reviewed_against` like every curated row; signing
  can follow the existing `curated_signature_status` path later if wanted (lodge an issue).
- Detecting a grade that **changed** (severity text, mechanism) — only grades that stopped reaching a pair.
- Counting departures in `drugref status`. The banner reaches the person at risk; `drugref audit
  departures` (5.4b) serves the operator (#256's reasoning stands).
- Acknowledging from the reviewer GUI (Rust). The CLI is the only acknowledgement path this round; lodge an
  issue for the GUI.

**Anti-goals (failure even if it technically works)**
- A banner a curator cannot end: this teaches clinicians to ignore `CHANGED SINCE REVIEW`.
- A correction made for an unrelated reason (wording) silently ending a banner (#281 option (b)'s flaw).
- Two banners for one cause: a ruling whose candidate is gone stays #121's orphan banner only.
- A writer that changes what `curated_ddi_pair` grades without refreshing the record. That is the silent
  gap again, one level down.

## 3. Constraints (from the repo)

- Migrations are ledger-backed and immutable; next file is `db/083`. Schema ends at `db/082`.
- Insert-only tables use the `forbid_any_rewrite()` trigger (db/080 precedent).
- Ingest parsers are pure; orchestrators own the transaction. Every ingest orchestrator (14) calls
  `provenance.finish_run` **inside** its final transaction, before its commit.
- Ruling writes: `curation.record_interaction_judgement`, `curation.record_class_interaction_judgement`,
  **and the Rust reviewer-service** (`reviewer-service/src/decisions.rs` inserts and supersedes
  `curated_interaction` through sqlx, never calling Python). Policy writes:
  `interactions.record_expansion_decision` / `withdraw_expansion_decision`. Callers own the transaction.
- The replay (`release_replay._Loader`) inserts exported curated rows as-is; it does not call the Python
  writers. It does run every ingest orchestrator, so `finish_run` executes.
- `release_replay.py` is at 498 lines (cap ~500): split before growing it. Every new curated table must be
  in `release_archive._EXPORT_TABLES` (a guard test fails otherwise) and accounted for by
  `release_replay` (`assert set(_TIMED) | {...} == set(_EXPORT_TABLES)`).
- No named database carries db/079+ yet; everything is measured on `drugref_test` only.
- `ruff format` is not applied; match the hand style. Licensing: no new dependencies.

## 4. Approaches considered

**A. A ledger refreshed at every commit that can change grades (chosen).** One insert-only table of graded
pairs. At the commit of any transaction that can change what `curated_ddi_pair` grades, insert whatever
the view grades now that has no open observation. A grade that is added is recorded at the commit that
added it; one that is removed was recorded at an earlier commit. The refresh is a **SQL function fired by
deferred constraint triggers** (5.2), so the Rust service, hand-written SQL and the replay loader are all
covered by construction rather than by remembering to call Python.
*Cost:* one full read of `curated_ddi_pair` per finished ingest run and per ruling/policy row inserted.

**A′. The same ledger, refreshed by explicit Python calls** (`finish_run`, the curation writers).
Rejected after the builder check: the Rust reviewer-service writes moiety-grain rulings without Python, and
the replay loader inserts rulings directly, so both would escape; a grade added from the GUI and removed by
the next ingest would never be recorded — the anti-goal.

**B. Database triggers on the membership tables.** Statement-level triggers with transition tables on
`class_membership`, `class_parent`, policy and ruling tables, computing the graded pairs about to be lost.
Rejected: `curated_ddi_pair` depends on half a dozen tables through a recursive view, so the trigger set is
large, fires mid-rebuild (delete-and-rebuild deletes everything first, so "lost" is everything), and hides
behaviour the repo keeps explicit in orchestrators.

**C. Reconstruct past reach from archived inputs.** The archive (#258) can rebuild any release, so "was ever
graded" could be computed by replaying history. Rejected for the banner: a CLI read cannot replay releases,
and pairs graded between releases would be invisible. (#260's diff-based membership history is the same
idea at lower cost and stays deferred.)

## 5. Design (decided)

### 5.1 Schema (`db/083_graded_pair.sql`)

`drugref.graded_pair_observation` — one row per *episode* in which a ruling key graded a pair:

| column | meaning |
|---|---|
| `observation_id` bigserial PK | |
| `rule_grain` | `'moiety_rule'` or `'class_rule'` (the view's own vocabulary) |
| `subject_key` uuid | moiety-grain: subject moiety; class-grain: subject class |
| `object_class_uuid` uuid | the ruling's object class |
| `relationship` text | the ruling's relationship |
| `subject_moiety`, `partner_moiety` uuid | the graded pair |
| `first_seen_at` timestamptz | when this episode was first observed |
| `captured` text | `'observed'` (refresh) or `'backfill_db083'` (migration-time, see 5.5) |

Keyed on the **ruling's natural key**, not its version id: a correction inherits the key's history, which
is what closes #281 §1. Insert-only (`forbid_any_rewrite`). Unique on (key, pair, `first_seen_at`).

`drugref.graded_pair_departure_ack` — one row per acknowledged pair:

| column | meaning |
|---|---|
| `ack_id` bigserial PK | |
| `observation_id` FK → `graded_pair_observation` | the episode being accepted as ended |
| `rationale` text NOT NULL, `reviewed_by`, `reviewed_against` text NOT NULL | as on every curated row |
| `acknowledged_at` timestamptz default now() | |

Unique on `observation_id`; insert-only. Acknowledging an **episode**, not the pair, is what makes "graded
again, then departed again" fire a fresh banner (5.2).

### 5.2 Refresh (`drugref.refresh_graded_pairs()`, SQL function in db/083)

Inserts, for every row `curated_ddi_pair` returns now, an observation unless the (key, pair) already has
an **unacknowledged** episode. In words: one open episode per pair; a new episode starts only when an
acknowledged pair is graded again. Returns rows inserted.

Fired by `DEFERRABLE INITIALLY DEFERRED` constraint triggers, so it runs at COMMIT, after every
append-then-point supersede in the transaction has landed:
- `AFTER INSERT` on `curated_interaction`, `curated_class_interaction`, `class_expansion_policy`;
- `AFTER UPDATE OF finished_at` on `ingest_run` when `finished_at` goes from NULL to a value (this is
  `finish_run`'s statement, inside each orchestrator's final transaction, whichever language wrote it).

Class membership, parents and candidates are written only by ingest orchestrators, which all end in
`finish_run`, so those tables need no trigger of their own. (Constraint triggers are per row; a
transaction inserting N rulings refreshes N times. Rulings are written one per transaction in practice;
if that changes, a transaction-local "already refreshed" flag is the fix — builder's call.)

A thin Python wrapper (`graded_pairs.refresh(conn)`) calls the function for tests and for any caller that
wants the observation before commit.

`curated_ddi_pair` filters `applies` and requires the candidate, so withdrawn rulings and orphans are never
observed.

### 5.3 Departure read (replaces `class_ruling_reach.departed_reach`'s body)

**Per ruling, not per pair** (my call, as #256 does today): a departure is judged against the same ruling
key. If a class rule stops reaching a pair that a moiety rule still grades, that is a departure: a
clinician who saw "contraindicated" from the class rule may now see only the moiety rule's weaker grade,
and must be told. The per-pair reading would hide exactly that.

A **departure** is an open (unacknowledged) episode whose pair `curated_ddi_pair` does not grade **under
that ruling key** now, where
the key's live version exists, `applies`, and its candidate still exists (otherwise #121's orphan banner
owns it). Same call shape as today: `departed(conn, subject, partner=None)`, directional, most recent
ruling first, grouped per ruling with its pairs; each entry says whether any of its pairs is
backfill-captured. It covers both grains; the moiety-grain entry names the `curated_interaction_id`.

db/080's reach snapshot stays as the "what did the curator see when ruling" audit (`drugref audit
class-ruling`); the banner no longer reads it.

### 5.4 Acknowledgement command

`drugref curate acknowledge-departure --grain {class,moiety} RULING_ID DRUG --rationale TEXT
--reviewed-by NAME --reviewed-against REF`: acknowledges every open, currently-departed episode of that
ruling's key in which DRUG is the subject or the partner, in one transaction; prints what it acknowledged;
refuses (non-zero exit, nothing written) if none is departed. The banner line for each ruling ends with
the exact command to run.

### 5.4b Operator listing (owner ruling, 2026-11-25)

`drugref audit departures` lists every open departure across all rulings and both grains: grain, ruling
id, its classes / subject, the departed drug(s) and pairs, `first_seen_at` of the episode, whether it is
backfill-captured, and the exact acknowledge command. Exit 0 when the list is empty, 3 otherwise (the
`EXIT_INCOMPLETE` convention), so it can gate a post-ingest checklist. Curators run it after each ingest.
The departure read of 5.3 gets a no-subject form for it (same SQL, subject filter optional).

### 5.5 Backfill (in `db/083`, between `backfill:begin/end` markers)

Two sources, both `captured = 'backfill_db083'`, `first_seen_at` = migration time:
1. every row `curated_ddi_pair` grades at migration time;
2. for class rulings: every (subject, partner) pair in any **applying** version's db/080 reach snapshot of a
   key, minus self-pairs — this keeps #256's banner for drugs that left before db/083 and also covers #281
   §1 retroactively.

The moiety grain has no earlier evidence; its history starts at db/083. The banner prints "recorded at
db/083's migration, not when graded" for backfilled entries (as #256 does for db/080).

### 5.6 Export and replay

Both tables join `release_archive._EXPORT_TABLES`. On replay the triggers regenerate observations as the
loader inserts rulings and the runs finish (interleaved as today), so the exported observations are
**compared, not loaded**: at the end, a (key, pair) in the export's observations absent from the replayed
ones, or vice versa, is a difference. Acknowledgements are loaded (they are curator acts), interleaved by
`acknowledged_at`, attached to the replayed episode with the same (key, pair) open at that moment; if none
is open, that is a difference, not a failure.

**History before db/083 (my call).** The replay migrates an empty database, so db/083's backfill writes
nothing, and every run is replayed under db/083's triggers, so the replay records pairs the origin never
observed. Such a release cannot reproduce its observations. When the export holds any `backfill_db083`
observation, the replay therefore **skips the observation comparison and warns** ("observations predate
db/083: not reproducible"), which already keeps it short of `reproduced` (the existing warning rule).
Acknowledgements still load; one with no matching open episode is reported as a difference. This costs
nothing in production: STANDING already requires production to be built from nothing with the archive set,
which means with db/083 from its first run. Split `release_replay.py` first (the reach/snapshot comparison
is the natural piece to move out).

### 5.7 Banner text

`cli_interactions._report_departures` keeps #256's shape and exit 3, adds the grain to each entry, and ends
each entry with the acknowledge command. Without db/083 it falls back to the stderr "cannot tell" note
(the existing `migration_guard` path), exit code unchanged.

## 6. Left to the builder

1. Exact SQL for the refresh and departure reads; whether `curated_ddi_pair` is read once per refresh via
   a `MATERIALIZED` CTE; indexes on the observation table (at least (key, pair)).
2. Trigger names and ordering relative to the existing deferred single-live triggers (the refresh only
   reads, so order does not change its result, but say so in the migration).
3. Whether the db/080-based `departed_reach` name is kept (rewritten) or renamed with callers updated.
4. The CLI's argument parsing style (match `cli_curate.py`).
5. Size (my call on the threshold). Observations scale with curated rulings × expansion; rulings are
   human-made and no curated content exists in production yet. A class ruling such as NSAIDs ×
   anticoagulants is ~10³ pairs, so hundreds of rulings give ~10⁵ rows, the same order as db/080's
   exported reach. Measure on the test fixtures, record the figure in PROJECT-NOTES, and lodge an issue
   (beside #260) to move to a narrower per-key refresh or a delta export if a real database passes 10⁶ rows.
6. **A guard test** that db/083's triggers exist on every table named in 5.2, plus a Rust test in
   reviewer-service that a GUI-recorded moiety ruling produces an observation at commit.
7. Tests (TDD), at least: #281 §1 (correction after leaving now banners); #281 §2 (join-then-leave
   banners); #279 (moiety-grain partner leaves; deny cuts subclass); ack silences; ack then re-join then
   leave banners again; `applies = false` correction does not banner; orphan not double-bannered;
   acknowledge refuses when nothing departed; backfill marking; the audit listing (empty / non-empty exit codes); replay compares observations and loads
   acks. The existing `test_a_correction_after_the_drug_left_is_not_a_departure` flips to a departure.
8. Docs: PROJECT-NOTES section, ROADMAP line, HANDOVER, a docs-site *Design decisions* record (living),
   suite-count line.
