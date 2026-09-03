-- db/054_ingest_run_duration_measured.sql
--
-- Issue 176: the watershed dated a row by TIME when the question was WHICH CODE WROTE IT.
--
-- ⇒ WHAT db/053 LEFT BEHIND. That migration changed what `started_at` and `finished_at`
-- MEAN, and gave a reader no way to tell the two meanings apart except the clock:
-- `provenance.format_run_duration` compared `started_at` against the moment db/053 was
-- applied on this database (`db.migration_applied_at`). The mechanism is sound for the
-- case it was designed for -- rows genuinely written before the migration -- and it is
-- decidable only because, in the normal case, old code and an unmigrated database
-- coincide. When they come apart the guard is silent, and it comes apart in BOTH
-- directions:
--
--   1. AN OLDER CLIENT AGAINST A MIGRATED DATABASE. An unupgraded node or a
--      previously-installed wheel INSERTs without naming `started_at`, so the stamp is
--      db/053's `clock_timestamp()` DEFAULT -- the real insert time, comfortably AFTER
--      the watershed -- while the old `finish_run` writes its work transaction's
--      `now()` into `finished_at`. The two land milliseconds apart in the right order,
--      so db/053's CHECK does not fire and the watershed does not fire, and two seconds
--      of work is published as `0.0s`. That is issue 159's own failure mode, reproduced
--      with the guard built to prevent it standing beside it saying nothing. Nothing in
--      db.py refuses a database whose schema is ahead of the code, so there is no other
--      barrier.
--
--   2. A GENUINELY NEW ROW, REFUSED. `open_run` backdates `started_at` by the elapsed
--      time the orchestrator reports from its first line, so any run whose pre-open
--      phase began before the migration lands before the watershed although both its
--      stamps are correct server clock readings. Concretely: apply db/053 in one shell
--      while `drugref ingest spl` -- which reads openFDA, scans 17.6 GB of DailyMed and
--      checksums 19.3 GB before `open_run` -- is already well into that phase in
--      another. The first real measurement the fix exists to produce is discarded, and
--      the operator is pointed at a column comment describing a defect their row does
--      not have. This direction is safe (it says less rather than something false) and
--      is still a wrong statement about that row.
--
-- ⇒ THE FIX IS TO MAKE THE ROW SAY WHAT IT IS. A boolean written beside `finished_at`
-- is self-identifying: pre-db/053 rows, old-client rows and direct INSERTs all come out
-- false, a row this code finished comes out true, and no clock comparison is involved.
-- The Python half is `provenance.finish_run` (which sets it, in the same UPDATE as the
-- stamp it vouches for -- see that function for why not `open_run`) and
-- `provenance.format_run_duration` (which reads it and no longer takes a watershed);
-- `db.migration_applied_at`, which existed only to answer the question this column now
-- answers, goes with it.
--
-- ⇒ NO ROW ON DISK IS BACKFILLED, and the temptation is worth naming because it looks
-- free. `UPDATE ... SET duration_measured = true WHERE started_at >= <db/053 applied_at>`
-- would preserve every runtime `drugref status` prints today -- and it would do so by
-- STORING the exact inference this column exists to remove, permanently, for rows an
-- older client may well have written. Computed wrongly, a wrong answer can be corrected
-- by the next round; written into a column, it becomes a fact nobody can distinguish
-- from a measured one. The cost of not backfilling is bounded and self-healing: each
-- writer's next ingest records a measured duration. (Project owner's decision, taken
-- with both options on the table.)
--
-- WHAT THIS FILE DOES: the column and its comment (S1), the view a consumer actually
-- reads (S2), a RE-ISSUE of db/025's view comment (S2 as well, and the RISKIEST edit
-- here -- overwriting an ancestor's catalog text is the one class this file spends
-- lines warning about, so it is named rather than left to be found), and a RE-ISSUE of
-- db/053's two column comments, one sentence of which this round makes false (S3).

-- ---------------------------------------------------------------------------
-- 1. The column
-- ---------------------------------------------------------------------------
-- NOT NULL with a default rather than a nullable three-state, because "maybe" has no
-- reader: `format_run_duration` would have to collapse NULL onto false anyway, and a
-- column with an unreachable third state is a vocabulary nobody keeps.
--
-- DEFAULT false is what makes this safe without anyone remembering: every path that is
-- not `finish_run` -- a `curation` row written by a curator, a direct INSERT, an ingest
-- driven by code older than db/053 -- lands false by doing nothing. The default covers
-- INSERTs; `finish_run`'s UPDATE is what covers the second stamp, which is why the flag
-- is written there and not at the INSERT (see provenance.finish_run).
ALTER TABLE drugref.ingest_run
    ADD COLUMN duration_measured boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN drugref.ingest_run.duration_measured IS
    'Whether finished_at - started_at on THIS row is a real duration (issue 176). Set '
    'true by provenance.finish_run and by nothing else, IN THE SAME UPDATE THAT WRITES '
    'finished_at, because the claim is about BOTH stamps and open_run could only ever '
    'promise it about a value not yet written. It DEFAULTS TO FALSE, so every other '
    'path -- a curation row, a direct INSERT, an ingest driven by code older than '
    'db/053, and a row written between db/053 and db/054 by a client that predates this '
    'column -- says so about itself rather than being guessed at. '
    'WHY THE WRITER IS finish_run AND NOT open_run. DEFAULT false governs INSERTs, and '
    'finished_at arrives by UPDATE, so a flag set at INSERT is not covered by the '
    'default at all: open_run commits its row so a crashed ingest leaves a trace, and '
    'an operator tidying that row by hand (UPDATE ... SET finished_at = now()) writes '
    'the second stamp with no measurement behind it. The CHECK passes, the row enters '
    'loaded_release, and a flag set at INSERT would still read true -- publishing hours '
    'of runtime for a run that never finished. Written by finish_run, the hand-rolled '
    'UPDATE does not name this column and the row keeps its false. '
    'WHY A COLUMN RATHER THAN A DATE. db/053 changed what the two stamps MEAN, and '
    'until db/054 a reader told the two meanings apart by comparing started_at against '
    'when db/053 was applied here. That asks WHEN the row was written; the question is '
    'WHICH CODE wrote it, and nothing on the row recorded that. An older client against '
    'a migrated database takes db/053''s clock_timestamp() default for started_at and '
    'writes its own now() into finished_at, so the row is dated after the watershed, '
    'satisfies the CHECK, and publishes two seconds of work as "0.0s" -- issue 159''s '
    'failure mode with the guard against it standing beside it saying nothing. In the '
    'other direction open_run backdates started_at over the orchestrator''s pre-open '
    'parse, so a run that began before the migration was refused although both its '
    'stamps were correct. '
    'FALSE IS NOT AN ERROR. It says only that nothing on this row vouches for the '
    'subtraction, so drugref status prints "unmeasured" instead of a number a reader '
    'would believe. '
    'NO ROW WAS BACKFILLED by db/054 and none could honestly be: nothing on disk '
    'records which code wrote it, so a backfill would store the very inference this '
    'column removes. Each writer''s next run records a measured duration.';

-- ---------------------------------------------------------------------------
-- 2. The view a consumer actually reads
-- ---------------------------------------------------------------------------
-- `drugref status` reads loaded_release, not ingest_run. A column added to the table and
-- left out of the view is invisible to every consumer, and the block would have to fall
-- back on the inference this migration removes -- half a feature, which is the same
-- shape as this project's twice-paid-for "a detector nobody calls is not a detector".
--
-- REBUILT FROM db/025 VERBATIM with `duration_measured` appended and NOTHING ELSE
-- changed: CREATE OR REPLACE VIEW may add columns only at the END of the list, and
-- rebuilding a view or comment from the wrong ancestor is how db/038 silently reverted
-- db/036 (tests/test_class_grain_comment.py is that whole story). db/025 is the only
-- ancestor: `grep -rn "CREATE OR REPLACE VIEW drugref.loaded_release" db/` returns
-- exactly two FILES, db/025 and this one -- on three lines, because the sentence you
-- are reading quotes the pattern and is itself a hit. db/053 disclaimed the same
-- off-by-one about its own grep; a count offered as reproducible has to survive being
-- run, and this one is the reason to say "files" rather than "hits".
--
-- The ingest_run_id tie-break is not decoration. finished_at is a timestamp, two runs
-- can share one, and a DISTINCT ON whose ORDER BY does not name a unique row keeps
-- whichever the plan happened to emit first -- the same latent non-determinism db/018
-- found in gap_unmatched_ingredient.
CREATE OR REPLACE VIEW drugref.loaded_release AS
SELECT DISTINCT ON (source, writer)
       source, writer, upstream_release, source_checksum,
       ingest_run_id, started_at, finished_at, duration_measured
FROM   drugref.ingest_run
WHERE  finished_at IS NOT NULL
ORDER  BY source, writer, finished_at DESC, ingest_run_id DESC;

-- db/025's text VERBATIM with ONE sentence appended and nothing removed. The view's
-- own meaning has not changed; what has changed is that it now carries the column
-- deciding whether its two stamps may be subtracted, and a consumer reading \d+ here
-- should be told that rather than having to find ingest_run's comment.
COMMENT ON VIEW drugref.loaded_release IS
    'Which upstream release each writer last landed, from which bytes, and when. '
    'PER (source, writer), NOT per source: MED-RT has two writers and re-ingesting one '
    'without the other is a real and otherwise invisible staleness. WHAT IT DOES NOT '
    'MEAN: this is the release a per-source rebuild last replaced its PROJECTION from, '
    'not a claim that every row attributed to that source carries this run''s id -- '
    'substance_moiety and identity_claim ACCUMULATE and hold rows from many runs by '
    'design. A run still in flight, or one that died, is in ingest_run_incomplete '
    'instead. '
    'THE RUNTIME IS NOT A SUBTRACTION A CONSUMER MAY JUST DO: finished_at - started_at '
    'is a duration only where duration_measured is true (db/054, issue 176), which is '
    'why that column is in this view.';

-- ---------------------------------------------------------------------------
-- 3. db/053's two column comments, RE-ISSUED -- one sentence is now false
-- ---------------------------------------------------------------------------
-- `COMMENT ON` OVERWRITES, it does not merge, so both are rebuilt from db/053's text
-- VERBATIM with the edits named here and nothing else. The one that must GO is
-- finished_at's "and is a real duration only for rows written since db/053": that is
-- the weaker test, and the older client of issue 176 satisfies it while writing this
-- stamp the old way. tests/test_ingest_run_duration.py checks the re-issue for what it
-- DROPPED as well as for what it says, which is the check db/038's own verification was
-- structurally blind to.

-- EDIT: the closing sentence gains the discriminator. What it already said -- that rows
-- written before db/053 keep the old meaning and are not durations -- stays true and
-- stays here; what it could not say is which rows those are.
COMMENT ON COLUMN drugref.ingest_run.started_at IS
    'When the ingest that wrote this row BEGAN, read off the SERVER clock. Since '
    'db/053 provenance.open_run backdates it: the stored value is clock_timestamp() '
    'at the INSERT minus the elapsed time the orchestrator reports from its own '
    'first line, so it covers the release parse, corpus scan and checksum an '
    'orchestrator does before any run row exists. Only the ELAPSED INTERVAL crosses '
    'from the client, never a client timestamp, so both ends of the subtraction are '
    'the server''s clock and an ingest driven from a host whose clock is out still '
    'records a true duration. BEFORE db/053 this was now(), i.e. '
    'transaction_timestamp(), which dated the INSERT''s transaction instead: '
    'finished_at - started_at was then the gap between two transaction starts and '
    'not a duration at all (issue 159) -- on this project''s own verification '
    'databases EIGHT of the nine feeds measured reported between 1.3 ms and 24 ms for '
    'a load, and the ninth was reporting the 48 s it spent NOT touching the database. '
    'ROWS WRITTEN BEFORE db/053 KEEP THAT OLD MEANING '
    'and are not durations; this migration does not rewrite them, and could not -- '
    'what would be needed was never recorded. '
    'WHICH ROWS THOSE ARE IS NOT A QUESTION ABOUT THIS STAMP: since db/054 the '
    'discriminator is duration_measured, a boolean finish_run sets, because comparing '
    'this column against a migration''s applied_at asks WHEN a row was written when '
    'the question is WHICH CODE wrote it (issue 176). Backdating is itself why: a '
    'correct new row can be dated before the migration that made it correct.';

-- EDIT: the closing clause "and is a real duration only for rows written since db/053"
-- is REPLACED, not supplemented. Leaving it beside the new one would be two tests for
-- one fact, and the older of the two is the one an out-of-date client passes.
COMMENT ON COLUMN drugref.ingest_run.finished_at IS
    'When the ingest''s WORK finished, read off the server clock -- '
    'clock_timestamp(), not now(), since db/053 (issue 159). NULL means started and '
    'never finished: a crash, a kill, or a run still in flight; ingest_run_incomplete '
    'is the view for that half and loaded_release for this one. WHAT THE STAMP DOES '
    'NOT COVER: the caller''s final COMMIT, which lands after it by construction. '
    'provenance.finish_run deliberately does not commit -- the stamp has to be '
    'published atomically with the work it describes, or a consumer could read '
    '"finished" about rows that were then rolled back -- so the duration this column '
    'closes is the WORK, accurate to the commit, and is a real duration only for rows '
    'whose duration_measured is true (db/054, issue 176). "Written since db/053" was '
    'the older and weaker test this comment used to state, and an out-of-date client '
    'passes it while writing this very stamp the old way.';
