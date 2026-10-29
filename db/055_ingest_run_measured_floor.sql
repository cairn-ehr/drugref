-- db/055_ingest_run_measured_floor.sql
--
-- Issue 182: db/054's no-backfill rule was prose only.
--
-- ⇒ WHAT STOOD. db/054 spends a section forbidding
-- `UPDATE ... SET duration_measured = true WHERE <some date test>`, because that stores
-- the inference the column exists to remove, as a fact nobody can tell from a measured
-- one. The #181 review closed the ACCIDENTAL path (the flag is written by
-- provenance.finish_run, so an operator's `SET finished_at = now()` leaves it false).
-- The DELIBERATE path -- a repair script, a future migration -- still worked.
--
-- ⇒ THE INVARIANT, and why it is not "may not change". finish_run legitimately flips
-- false -> true, so a blanket freeze is wrong. What is true is narrower:
--
--   1. No row is INSERTed already claiming a measurement (nothing measured it).
--   2. false -> true happens ONLY in the UPDATE that writes the row's FIRST
--      finished_at -- OLD.finished_at NULL, NEW.finished_at set. That is exactly
--      finish_run's statement, and it is never a backfill's: a backfill targets rows
--      that already finished.
--   3. Once true, the flag never changes again, and NEITHER DO started_at OR
--      finished_at: the flag vouches for those two values, and a rewritten stamp under
--      a true flag is a measured-looking duration nothing measured.
--
-- ⇒ WHAT THIS DOES NOT STOP, said here so a reader does not assume more. Rule 2 cannot
-- tell finish_run from a hand-written `SET finished_at = now(), duration_measured =
-- true` on a still-open row: both are one UPDATE writing the first stamp. That is a
-- forgery of finish_run, not a backfill, and needs a writer that names the column on
-- purpose. And as db/001 says of its own floor, a table-owning role can DISABLE
-- TRIGGER: this is a floor against accident and against a future migration, not
-- against an adversary.
--
-- THE WHEN CLAUSE keeps the trigger off every UPDATE that involves no true flag, which
-- is all of them except finish_run's.

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
    ELSIF OLD.finished_at IS NOT NULL OR NEW.finished_at IS NULL THEN
        RAISE EXCEPTION 'drugref.ingest_run row %: duration_measured may become true '
                        'only in the UPDATE that writes its first finished_at, i.e. '
                        'provenance.finish_run -- a backfill is forbidden (db/054, '
                        'db/055)', OLD.ingest_run_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION drugref.forbid_duration_measured_rewrite() IS
    'The floor under ingest_run.duration_measured (issue 182). No INSERT may set it; '
    'false -> true only in the UPDATE writing the row''s first finished_at (what '
    'provenance.finish_run does, and what a backfill of finished rows cannot do); once '
    'true, the flag, started_at and finished_at are frozen. Does NOT distinguish '
    'finish_run from a hand-written UPDATE forging it on a still-open row, and a '
    'table owner can DISABLE TRIGGER: a floor against accident, not an adversary.';

CREATE OR REPLACE TRIGGER forbid_duration_measured_insert
    BEFORE INSERT ON drugref.ingest_run
    FOR EACH ROW
    WHEN (NEW.duration_measured)
    EXECUTE FUNCTION drugref.forbid_duration_measured_rewrite();

CREATE OR REPLACE TRIGGER forbid_duration_measured_rewrite
    BEFORE UPDATE ON drugref.ingest_run
    FOR EACH ROW
    WHEN (OLD.duration_measured OR NEW.duration_measured)
    EXECUTE FUNCTION drugref.forbid_duration_measured_rewrite();
