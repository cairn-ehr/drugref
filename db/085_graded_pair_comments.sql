-- db/085 -- db/083's stored comments, corrected for db/084 (#286 review).
--
-- db/084 moved the two RULING triggers onto `refresh_graded_pairs_for_key`, but
-- left db/083's COMMENTs saying every *_graded_pairs trigger runs the full
-- `refresh_graded_pairs()`. A comment is what `\d+` shows an operator, so a wrong
-- one is a wrong answer. db/083 and db/084 are applied (immutable), hence this
-- file. Comments only: no object changes.

COMMENT ON TABLE drugref.graded_pair_observation IS
    'One row per episode in which a ruling key (rule_grain, subject_key, '
    'object_class_uuid, relationship) graded a pair, keyed on the ruling''s natural '
    'key so a correction inherits its history. Written at COMMIT: by '
    'refresh_graded_pairs() for an ingest or expansion-policy commit, by '
    'refresh_graded_pairs_for_key() for a ruling commit (db/084). '
    'captured = backfill_db083 rows were recorded at db/083''s migration, NOT when '
    'graded. Insert-only (db/083, #281, #279).';

COMMENT ON FUNCTION drugref.refresh_graded_pairs(text) IS
    'Records every (ruling key, pair) curated_ddi_pair grades now that has no open '
    'episode in graded_pair_observation; returns rows inserted. Run at COMMIT by the '
    'ingest_run_graded_pairs and class_expansion_policy_graded_pairs constraint '
    'triggers (db/083); the ruling triggers run refresh_graded_pairs_for_key '
    '(db/084).';
