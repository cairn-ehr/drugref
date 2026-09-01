-- db/053_ingest_run_duration.sql
--
-- Issue 159: `finished_at - started_at` was not a duration, for ANY feed.
--
-- ⇒ WHAT WAS MEASURED. `started_at DEFAULT now()` and `provenance.finish_run`'s
-- `now()` are both `transaction_timestamp()`, which is fixed for the whole
-- transaction. `open_run` COMMITS (db/025, and see its docstring on why that is
-- the feature), so the stamp `finish_run` writes belongs to a DIFFERENT, LATER
-- transaction -- and the subtraction therefore measured the gap between two
-- transaction START times, never the work between them. Read on the project's own
-- verification databases:
--
--   writer            drugref_spl051   drugref_spl160fix   real runtime
--   spl_run                  49.85 s            0.0026 s   2 min 09 s
--   mesh_rel_run             48.32 s            48.32 s    --
--   the other seven     1.3 - 24 ms         1.3 - 24 ms    --
--
-- Every one of those is wrong, including the two that look plausible. The 48.3 s
-- is the time mesh_rel_run spends parsing 750 MB of MeSH BETWEEN `open_run` and
-- its first write -- the time it spent NOT touching the database, which is the
-- complement of what the column was read as. And spl_run's 49.85 s became 0.0026 s
-- when the COPY-cost round put a `conn.rollback()` in front of the DailyMed scan,
-- which is to say the issue's own headline example evaporated under it and nobody
-- looked.
--
-- ⇒ THE FIX IS IN provenance.py, AND THIS FILE IS THE HALF THE CATALOG OWNS.
-- `finish_run` now writes `clock_timestamp()`, and `open_run` writes
-- `clock_timestamp()` minus the elapsed time its caller reports from the
-- orchestrator's FIRST LINE -- so both ends are read off the SERVER's clock (an
-- ingest driven from a host whose clock is minutes out still records a true
-- duration) while the window covers the parse, scan and checksum an orchestrator
-- does before any run row exists. What this file adds is the direct-INSERT path's
-- default, a constraint, and the catalog's own account of what the two columns
-- mean.
--
-- ⇒ ROWS ALREADY ON DISK KEEP THE OLD MEANING. Nothing here rewrites them and
-- nothing could: the information needed to correct them was never recorded. The
-- column comments say so, in the one place a consumer reading `\d+` will see it.

-- ---------------------------------------------------------------------------
-- 1. The default: the path `open_run` is NOT on
-- ---------------------------------------------------------------------------
-- A `curation` run (a curator writing to Plan C's overlay tier) and every test
-- that INSERTs a row directly take the default. Under `now()` such a row is dated
-- from the START of whatever transaction happened to be open -- for a curator
-- holding one across a review, that is not when anything began.
ALTER TABLE drugref.ingest_run
    ALTER COLUMN started_at SET DEFAULT clock_timestamp();

-- ---------------------------------------------------------------------------
-- 2. The constraint
-- ---------------------------------------------------------------------------
-- Both stamps are now clock readings taken in that order, so this can only be
-- violated by a caller inventing one. That is exactly the mistake a duration
-- column invites -- and a negative interval printed as a runtime is the kind of
-- wrong answer that gets believed. Every row written before this migration
-- satisfies it: `open_run`'s transaction commits before the work's begins.
--
-- NULL is admitted, and must be: it is the normal state of a run in flight, and a
-- constraint that refused it would abort every ingest at `open_run`.
ALTER TABLE drugref.ingest_run
    ADD CONSTRAINT ingest_run_finishes_after_it_starts
    CHECK (finished_at IS NULL OR finished_at >= started_at);

-- ---------------------------------------------------------------------------
-- 3. What the two columns mean
-- ---------------------------------------------------------------------------
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
    'databases every one of the nine feeds reported between 1.3 ms and 24 ms for a '
    'load, and the one that reported anything else was reporting the 48 s it spent '
    'NOT touching the database. ROWS WRITTEN BEFORE db/053 KEEP THAT OLD MEANING '
    'and are not durations; this migration does not rewrite them, and could not -- '
    'what would be needed was never recorded.';

COMMENT ON COLUMN drugref.ingest_run.finished_at IS
    'When the ingest''s WORK finished, read off the server clock -- '
    'clock_timestamp(), not now(), since db/053 (issue 159). NULL means started and '
    'never finished: a crash, a kill, or a run still in flight; ingest_run_incomplete '
    'is the view for that half and loaded_release for this one. WHAT THE STAMP DOES '
    'NOT COVER: the caller''s final COMMIT, which lands after it by construction. '
    'provenance.finish_run deliberately does not commit -- the stamp has to be '
    'published atomically with the work it describes, or a consumer could read '
    '"finished" about rows that were then rolled back -- so the duration this column '
    'closes is the WORK, accurate to the commit, and is a real duration only for '
    'rows written since db/053.';

-- ---------------------------------------------------------------------------
-- 4. db/025's view comment, RE-ISSUED -- half of one sentence is now false
-- ---------------------------------------------------------------------------
-- `COMMENT ON` OVERWRITES, it does not merge, so this is rebuilt from db/025's
-- text VERBATIM (the only ancestor: `grep -rn "COMMENT ON VIEW
-- drugref.ingest_run_incomplete" db/` returns one hit) with two edits and nothing
-- else. Rebuilding from an older file is how db/038 silently reverted db/036's
-- correction; tests/test_class_grain_comment.py is that whole story.
--
-- EDIT 1. "the window starts at open_run, not at the command" was true of both
-- halves of what it described and is now true of only one. Since db/053 the
-- TIMESTAMP on a row here is backdated to the orchestrator's first line and does
-- cover the parse; what still begins at `open_run` is the row's EXISTENCE. The
-- asymmetry is worth stating because it is surprising: a row here can be dated
-- before it could possibly have been written.
--
-- EDIT 2. "THREE of the six orchestrators (medrt_run, mesh_run, mesh_rel_run)"
-- was a hand-listed count of a population that has since grown to eleven writers,
-- and spl_run -- the largest -- was never in it. Replaced with the structural
-- reason and the extreme case rather than a fresh tally: a hand-listed "every" is
-- the defect this project has now found five times, most recently one round ago.
COMMENT ON VIEW drugref.ingest_run_incomplete IS
    'Runs that started and never finished -- a crash, a kill, or a run still in '
    'flight. EMPTY BY CONSTRUCTION BEFORE db/025: the run row used to roll back '
    'with the work it described, so a crashed ingest was indistinguishable from one '
    'that never started. A row here is not itself an error: check it against '
    'loaded_release, which reports the last run that DID finish. '
    'WHAT AN EMPTY VIEW DOES NOT PROVE: that nothing crashed. The row is created by '
    'open_run, and most orchestrators do real work first -- a release parse, a '
    'corpus scan, a checksum -- because the parsers are pure and take no '
    'connection; spl_run reads openFDA, scans 17.6 GB of DailyMed and checksums '
    '19.3 GB before its run exists. A crash in that work leaves no row here, '
    'exactly as before db/025. What this view makes observable is a crash during '
    'the WRITES, which is where the projection can be left half-rebuilt and where '
    'the question "did this ingest land?" actually bites. '
    'NOTE THE ASYMMETRY SINCE db/053: started_at IS backdated over that earlier '
    'work, so a row here is dated before the moment it could have been written, '
    'while the row''s EXISTENCE still begins at open_run. Reordering the parse is a '
    'real design question, not a wording one, and is deliberately not settled '
    'here.';
