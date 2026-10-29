-- db/056_ingest_run_finished_at_not_withdrawn.sql
--
-- Issue 182, second half: db/055's "first finished_at" was only "NULL -> value".
--
-- ⇒ WHAT db/055 LEFT OPEN (found by the PR 184 review, reproduced on drugref_test).
-- db/055 lets duration_measured turn true only in the UPDATE where OLD.finished_at is
-- NULL and NEW.finished_at is set, and calls that "the row's FIRST finished_at". It
-- was not: nothing stopped a finished row going back to NULL, and that UPDATE
-- involves no true flag, so db/055's WHEN gate never ran the function. A backfill
-- therefore took two statements instead of one:
--
--   UPDATE drugref.ingest_run SET finished_at = NULL WHERE ...;
--   UPDATE drugref.ingest_run SET finished_at = <old>, duration_measured = true ...;
--
-- The second statement is indistinguishable from finish_run's, and the row came out
-- measured. db/055's own header and COMMENT say "a backfill of finished rows cannot
-- do" this; they are wrong as written, and this file is the correction (055 is
-- applied, so its text stays as it was -- see the ledger rule in db.py).
--
-- ⇒ THE NEW RULE: a written finished_at never returns to NULL. Nothing in the
-- project writes NULL there (grepped), and a finished run reopened would also read as
-- a crash in ingest_run_incomplete, so the rule refuses nothing anyone does. With it,
-- "the UPDATE that writes the first finished_at" is literally true, and db/055's
-- claims about backfills hold.
--
-- ⇒ WHAT STILL IS NOT STOPPED, unchanged from db/055: a hand-written UPDATE that sets
-- the flag together with the first finished_at on a run that really is still open
-- (a forgery of finish_run, not a backfill), and a table owner's DISABLE TRIGGER.
--
-- ⇒ THE FUNCTION ASSUMES ITS WHEN GATES. Its INSERT branch raises unconditionally and
-- it does not re-test "is any flag true" before every branch; attach it only with
-- the WHEN clauses below. The gates run it on: an INSERT claiming a measurement; any
-- UPDATE setting the flag; any UPDATE of an already-measured row (most of which pass
-- -- only the flag and the two stamps are frozen); and any UPDATE withdrawing a
-- finished_at. An ordinary UPDATE of an unmeasured row, such as an operator's
-- hand-finish of a crashed run, never reaches it.

CREATE OR REPLACE FUNCTION drugref.forbid_duration_measured_rewrite()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        RAISE EXCEPTION 'drugref.ingest_run.duration_measured may not be inserted '
                        'true: only provenance.finish_run sets it (db/055)';
    END IF;
    IF OLD.duration_measured THEN
        IF NOT NEW.duration_measured
           OR NEW.started_at IS DISTINCT FROM OLD.started_at
           OR NEW.finished_at IS DISTINCT FROM OLD.finished_at THEN
            RAISE EXCEPTION 'drugref.ingest_run row % has duration_measured true: the '
                            'flag and the two stamps it vouches for are frozen (db/055)',
                            OLD.ingest_run_id;
        END IF;
    ELSIF OLD.finished_at IS NOT NULL AND NEW.finished_at IS NULL THEN
        RAISE EXCEPTION 'drugref.ingest_run row %: finished_at may not return to NULL '
                        'once written -- a reopened run could then be flagged as if '
                        'finish_run had measured it (db/056)', OLD.ingest_run_id;
    ELSIF NEW.duration_measured
          AND (OLD.finished_at IS NOT NULL OR NEW.finished_at IS NULL) THEN
        RAISE EXCEPTION 'drugref.ingest_run row %: duration_measured may become true '
                        'only in the UPDATE that writes its first finished_at, i.e. '
                        'provenance.finish_run -- a backfill is forbidden (db/054, '
                        'db/055)', OLD.ingest_run_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION drugref.forbid_duration_measured_rewrite() IS
    'The floor under ingest_run.duration_measured (issue 182, db/055 + db/056). No '
    'INSERT may set it; a written finished_at never returns to NULL; false -> true '
    'only in the UPDATE writing the row''s first finished_at (what provenance.finish_run '
    'does, and -- with the previous rule -- what a backfill of finished rows cannot '
    'do); once true, the flag, started_at and finished_at are frozen. Relies on the '
    'WHEN clauses of its two triggers. Does NOT distinguish finish_run from a '
    'hand-written UPDATE forging it on a run that is really still open, and a table '
    'owner can DISABLE TRIGGER: a floor against accident, not an adversary.';

CREATE OR REPLACE TRIGGER forbid_duration_measured_rewrite
    BEFORE UPDATE ON drugref.ingest_run
    FOR EACH ROW
    WHEN (OLD.duration_measured OR NEW.duration_measured
          OR (OLD.finished_at IS NOT NULL AND NEW.finished_at IS NULL))
    EXECUTE FUNCTION drugref.forbid_duration_measured_rewrite();
