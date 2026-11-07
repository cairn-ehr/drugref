-- tools/class_profile_census.sql -- the class-profiles read-cost census (results record §2, §4, §6).
--
-- READ-ONLY. Run against a database built from the full chain (results record §1):
--   psql "host=localhost port=5532 dbname=drugref_profiles user=postgres" -f tools/class_profile_census.sql
--
-- Two figures need more than psql and are run by hand beside it:
--   CLI wall time (results §6), for the class the results record uses and a drug in it:
--     /usr/bin/time -p uv run drugref --dsn "<dsn>" profile class b0723faa-540c-5d7a-870f-4466c1b351e5
--     /usr/bin/time -p uv run drugref --dsn "<dsn>" profile drug  d1c60bfd-af53-5e58-ae5e-ca17075ff875
--   (beta-Adrenergic Blocker [EPC]; propranolol, no --lens.)
--   register_from_gaps (results §4), timed in a transaction that is rolled back:
--     uv run python -c "import time, psycopg; from drugref import questions as q
--     c = psycopg.connect('<dsn>'); r = c.execute('SELECT max(ingest_run_id) FROM drugref.ingest_run').fetchone()[0]
--     t = time.perf_counter(); q.register_from_gaps(c, r); print(time.perf_counter() - t); c.rollback()"

\timing on
\pset pager off

\echo '== §2 full-scan cost (count(*), warm cache: run the file twice and read the second) =='
SELECT count(*) AS class_property           FROM drugref.class_property;
SELECT count(*) AS class_property_member    FROM drugref.class_property_member;
SELECT count(*) AS class_member_peculiarity FROM drugref.class_member_peculiarity;
SELECT count(*) AS gap_unconfirmed          FROM drugref.gap_unconfirmed_class_property;

\echo '== §4 the worklist: current questions of the kind =='
SELECT count(*) AS current_questions FROM drugref.open_question
WHERE  gap_kind = 'unconfirmed_class_property' AND is_current;

\echo '== §6 single-class read, beta-Adrenergic Blocker [EPC] (a literal uuid, as a consumer sends it) =='
SELECT class_uuid AS bb FROM drugref.substance_class
WHERE  source = 'MED-RT' AND class_name = 'beta-Adrenergic Blocker [EPC]' \gset
EXPLAIN (ANALYZE, BUFFERS, SUMMARY) SELECT * FROM drugref.class_property            WHERE class_uuid = :'bb';
EXPLAIN (ANALYZE, BUFFERS, SUMMARY) SELECT * FROM drugref.class_property_member     WHERE class_uuid = :'bb';
EXPLAIN (ANALYZE, BUFFERS, SUMMARY) SELECT * FROM drugref.class_member_peculiarity  WHERE class_uuid = :'bb';
EXPLAIN (ANALYZE, BUFFERS, SUMMARY) SELECT * FROM drugref.class_subclass_only_member WHERE class_uuid = :'bb';
EXPLAIN (ANALYZE, BUFFERS, SUMMARY) SELECT * FROM drugref.class_subclass_only_property WHERE class_uuid = :'bb';

\echo '== names: profile-view object uuids that resolve to no name (expected 0; the reader would print the raw uuid) =='
WITH objects AS (
    SELECT object_kind, object_uuid FROM drugref.class_property
    UNION
    SELECT object_kind, object_uuid FROM drugref.member_property
)
SELECT object_kind, count(*) AS unnamed
FROM   objects o
WHERE  NOT EXISTS (SELECT 1 FROM drugref.condition        WHERE condition_uuid = o.object_uuid)
  AND  NOT EXISTS (SELECT 1 FROM drugref.substance_moiety WHERE moiety_uuid    = o.object_uuid)
  AND  NOT EXISTS (SELECT 1 FROM drugref.substance_class  WHERE class_uuid     = o.object_uuid)
GROUP  BY object_kind;
