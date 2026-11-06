-- db/057_spl_run_census.sql
--
-- Issue 186: issue 163's three counts, stored once per SPL run.
--
-- ⇒ WHY. #163 added `records_field_absent`, `records_field_blank` and
-- `labels_keyed_on_document_id` to SplSummary and ruled that NONE of them may
-- refuse a run: "the ratio moving between releases is the signal". But the only
-- place they went was one INFO log line, so no release could ever be compared with
-- the last. The sharpest case: a label keyed on openFDA's per-document `id` can
-- never match DailyMed and is routed `absent_from_dailymed`. If openFDA stopped
-- sending `set_id` on 500 records, 500 labels would be published as absent from
-- DailyMed and the only trace would be that log line.
--
-- ⇒ HISTORY, NOT A PROJECTION. Keyed by the run, and deliberately NOT in
-- spl_evidence.SPL_TABLES: the per-source rebuild deletes the previous run's SPL
-- rows, and a census deleted with them would leave nothing to compare against.
-- `ingest_run` is history for the same reason (db/025). The row is written in the
-- run's own transaction, so a run that rolls back leaves no census behind.
--
-- `records_read` and `labels` are stored beside the three so the ratio is
-- computable from this table alone; they are also what the identities need.
--
-- ⇒ THE CHECKS COPY SplSummary's OWN IDENTITIES (spl_checks.py) so a hand-written
-- INSERT meets the same floor as the one writer: every record is a label, absent
-- or blank; an id-keyed label is still a label. Each count also has its own
-- `>= 0`, because a -1 and a +1 satisfy a sum (issue 187's lesson).
--
-- NOT ENFORCED: that the referenced run's source is 'SPL'. A foreign key cannot
-- say it, and the only writer (spl_run.ingest_spl) passes its own run id.

CREATE TABLE IF NOT EXISTS drugref.spl_run_census (
    ingest_run_id                bigint PRIMARY KEY
                                 REFERENCES drugref.ingest_run(ingest_run_id),
    records_read                 bigint NOT NULL CHECK (records_read >= 0),
    labels                       bigint NOT NULL CHECK (labels >= 0),
    records_field_absent         bigint NOT NULL CHECK (records_field_absent >= 0),
    records_field_blank          bigint NOT NULL CHECK (records_field_blank >= 0),
    labels_keyed_on_document_id  bigint NOT NULL
                                 CHECK (labels_keyed_on_document_id >= 0),
    CONSTRAINT spl_run_census_records_accounted CHECK (
        labels + records_field_absent + records_field_blank = records_read),
    CONSTRAINT spl_run_census_id_keyed_are_labels CHECK (
        labels_keyed_on_document_id <= labels)
);

COMMENT ON TABLE drugref.spl_run_census IS
    'One row per SPL ingest run of issue 163''s counts, which never refuse a run '
    'but whose drift between releases is the signal (issue 186). HISTORY keyed by '
    'ingest_run, not cleared by the per-source rebuild, so the previous release''s '
    'row is still here to compare with. Written in the run''s own transaction.';
COMMENT ON COLUMN drugref.spl_run_census.records_field_absent IS
    'openFDA records with no drug_interactions field at all (or an explicit null).';
COMMENT ON COLUMN drugref.spl_run_census.records_field_blank IS
    'openFDA records whose drug_interactions field is present but holds only blanks.';
COMMENT ON COLUMN drugref.spl_run_census.labels_keyed_on_document_id IS
    'Labels keyed on openFDA''s per-document id because set_id was missing. They can '
    'never match DailyMed and are routed absent_from_dailymed, so a jump here reads '
    'as labels vanishing from DailyMed when it is really a change in openFDA''s keys.';
