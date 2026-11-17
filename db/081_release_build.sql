-- db/081_release_build.sql -- which build a published release came from, and drift (#258).
--
-- WHY. A node restores a published DB dump (spec 2026-11-16 section 5), so "which release
-- are my answers from?" must be answerable from the database alone. release_manifest
-- (db/030) is immutable and says WHAT was published, not which code/ingests built it,
-- and it must not be altered. release_build is the sidecar: one insert-only row per tag.
--
-- run_ids are the ingest_run rows the release carried. Drift ignores a source that is
-- not among them: a node's own PBS run after a restore is local data, not drift.

CREATE TABLE IF NOT EXISTS drugref.release_build (
    release_tag text        PRIMARY KEY REFERENCES drugref.release_manifest(release_tag),
    git_commit  text        NOT NULL,
    ledger_head text        NOT NULL,
    run_ids     bigint[]    NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS release_build_insert_only ON drugref.release_build;
CREATE TRIGGER release_build_insert_only
    BEFORE UPDATE OR DELETE ON drugref.release_build
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

COMMENT ON TABLE drugref.release_build IS
    'Build provenance of a published release (#258): git commit, migration ledger head and '
    'the ingest_run ids it carried. Insert-only sidecar to the immutable release_manifest. '
    'recorded_at is the DATABASE clock, the reference point for drift in current_release.';

CREATE OR REPLACE VIEW drugref.current_release AS
SELECT m.release_tag, m.published_at, rb.git_commit, rb.ledger_head,
       array_remove(ARRAY[
           CASE WHEN rb.release_tag IS NULL
                THEN 'no build record (published before db/081)' END,
           CASE WHEN EXISTS (
                    SELECT 1 FROM drugref.ingest_run r
                    WHERE r.finished_at > rb.recorded_at
                      AND r.source IN (SELECT c.source FROM drugref.ingest_run c
                                       WHERE c.ingest_run_id = ANY (rb.run_ids)))
                THEN 're-ingested' END,
           CASE WHEN EXISTS (SELECT 1 FROM drugref.curated_interaction
                             WHERE reviewed_at > rb.recorded_at)
                  OR EXISTS (SELECT 1 FROM drugref.curated_condition
                             WHERE reviewed_at > rb.recorded_at)
                  OR EXISTS (SELECT 1 FROM drugref.curated_class_interaction
                             WHERE reviewed_at > rb.recorded_at)
                  OR EXISTS (SELECT 1 FROM drugref.curated_property_ruling
                             WHERE reviewed_at > rb.recorded_at)
                THEN 're-curated' END,
           CASE WHEN EXISTS (SELECT 1 FROM drugref.class_expansion_policy
                             WHERE reviewed_at > rb.recorded_at)
                THEN 'expansion policy changed' END,
           CASE WHEN rb.release_tag IS NOT NULL
                 AND (SELECT max(filename) FROM drugref.schema_migration)
                     IS DISTINCT FROM rb.ledger_head
                THEN 'newer migration' END
       ], NULL) AS drift_reasons
FROM (SELECT * FROM drugref.release_manifest ORDER BY recorded_at DESC LIMIT 1) m
LEFT JOIN drugref.release_build rb ON rb.release_tag = m.release_tag;

COMMENT ON VIEW drugref.current_release IS
    'Zero or one row: the latest published release and why the live database may have '
    'drifted from it (#258). CHEAP: max-timestamp probes only, it does NOT verify the '
    'manifest (use `drugref signing` for that). With no build row every probe is skipped '
    'and the single reason says so, since there is no reference time to compare with.';
