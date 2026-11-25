-- db/086 -- the ledger's writers take one lock, so READ COMMITTED cannot lose an
-- episode (#287).
--
-- THE DEFECT. Under READ COMMITTED each writer of graded_pair_observation reads
-- the world from its own snapshot, so two concurrent commits can each miss what
-- the other is about to commit:
--   1. A ruling on key K commits while an ingest's full refresh runs at COMMIT.
--      The ruling's per-key refresh cannot see the ingest's uncommitted new
--      members; the ingest's refresh started before the ruling existed. The
--      (K x new member) pairs are never observed.
--   2. A curator acknowledges departed episode E while an ingest brings the pair
--      back. The ingest's refresh sees E still open (the ack is uncommitted) and
--      opens nothing; the ack then closes E. The pair is graded with no open
--      episode, so its next departure never banners.
-- If the pair departs in the very next ingest, the departure is silent: the
-- failure db/083 exists to prevent.
--
-- THE FIX. One transaction-scoped advisory lock, taken by both refresh functions
-- (this file) and by `graded_pairs.acknowledge` (Python) BEFORE they read. The
-- later transaction waits until the earlier one COMMITS (an xact lock is released
-- only after the commit is visible), and its next statement takes a fresh READ
-- COMMITTED snapshot that includes the earlier one's rows. One lock for the whole
-- ledger, not per key: the full refresh touches every key, and the per-key refresh
-- costs ~16 ms, so the only serialisation is between commits that write here.
-- Advisory locks are per database, so concurrent test databases never contend.

CREATE OR REPLACE FUNCTION drugref.lock_graded_pair_ledger() RETURNS void AS $$
    SELECT pg_advisory_xact_lock(hashtextextended('drugref.graded_pair_observation', 0));
$$ LANGUAGE sql VOLATILE;

COMMENT ON FUNCTION drugref.lock_graded_pair_ledger() IS
    'Takes the transaction-scoped advisory lock every writer of '
    'graded_pair_observation and graded_pair_departure_ack holds before reading '
    '(db/086, #287).';

-- db/083's and db/084's refresh bodies, restated VERBATIM with the lock as their
-- first statement (both are applied, hence immutable). Every caller -- the
-- trigger functions, `graded_pairs.refresh`, the replay -- calls these names, so
-- all of them get the lock. (A rename-and-wrap would avoid restating the bodies,
-- but db/083's body qualifies its parameter as `refresh_graded_pairs.captured`.)
CREATE OR REPLACE FUNCTION drugref.refresh_graded_pairs(captured text DEFAULT 'observed')
RETURNS integer AS $$
DECLARE
    inserted integer;
BEGIN
    -- db/086 (#287): wait for any other ledger writer to COMMIT, so the INSERT
    -- below (a separate statement, hence a fresh snapshot) sees its rows.
    PERFORM drugref.lock_graded_pair_ledger();
    INSERT INTO drugref.graded_pair_observation
        (rule_grain, subject_key, object_class_uuid, relationship,
         subject_moiety, partner_moiety, captured)
    SELECT g.rule_grain, g.subject_key, g.via_class, g.relationship,
           g.subject_moiety, g.partner_moiety, refresh_graded_pairs.captured
    FROM  (SELECT DISTINCT p.rule_grain,
                  CASE p.rule_grain WHEN 'class_rule' THEN p.via_subject_class
                                    ELSE p.subject_moiety END AS subject_key,
                  p.via_class, p.relationship, p.subject_moiety, p.partner_moiety
           FROM   drugref.curated_ddi_pair p) g
    WHERE NOT EXISTS (
        SELECT 1 FROM drugref.graded_pair_observation o
        WHERE  o.rule_grain = g.rule_grain AND o.subject_key = g.subject_key
        AND    o.object_class_uuid = g.via_class AND o.relationship = g.relationship
        AND    o.subject_moiety = g.subject_moiety
        AND    o.partner_moiety = g.partner_moiety
        AND    NOT EXISTS (SELECT 1 FROM drugref.graded_pair_departure_ack a
                           WHERE a.observation_id = o.observation_id));
    GET DIAGNOSTICS inserted = ROW_COUNT;
    RETURN inserted;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION drugref.refresh_graded_pairs_for_key(
    p_rule_grain text, p_subject_key uuid, p_object_class uuid,
    p_relationship text, p_captured text DEFAULT 'observed')
RETURNS integer AS $$
DECLARE
    inserted integer;
BEGIN
    -- db/086 (#287): wait for any other ledger writer to COMMIT, so the INSERT
    -- below (a separate statement, hence a fresh snapshot) sees its rows.
    PERFORM drugref.lock_graded_pair_ledger();
    IF p_rule_grain = 'moiety_rule' THEN
        INSERT INTO drugref.graded_pair_observation
            (rule_grain, subject_key, object_class_uuid, relationship,
             subject_moiety, partner_moiety, captured)
        SELECT DISTINCT 'moiety_rule', p.subject_moiety, p.via_class, p.relationship,
               p.subject_moiety, p.partner_moiety, p_captured
        FROM   drugref.curated_ddi_pair p
        WHERE  p.rule_grain = 'moiety_rule'
        AND    p.subject_moiety = p_subject_key AND p.via_class = p_object_class
        AND    p.relationship = p_relationship
        AND    NOT EXISTS (
            SELECT 1 FROM drugref.graded_pair_observation o
            WHERE  o.rule_grain = 'moiety_rule' AND o.subject_key = p_subject_key
            AND    o.object_class_uuid = p_object_class
            AND    o.relationship = p_relationship
            AND    o.subject_moiety = p.subject_moiety
            AND    o.partner_moiety = p.partner_moiety
            AND    NOT EXISTS (SELECT 1 FROM drugref.graded_pair_departure_ack a
                               WHERE a.observation_id = o.observation_id));
    ELSIF p_rule_grain = 'class_rule' THEN
        INSERT INTO drugref.graded_pair_observation
            (rule_grain, subject_key, object_class_uuid, relationship,
             subject_moiety, partner_moiety, captured)
        SELECT DISTINCT 'class_rule', p.via_subject_class, p.via_class, p.relationship,
               p.subject_moiety, p.partner_moiety, p_captured
        FROM   drugref.curated_ddi_pair p
        WHERE  p.rule_grain = 'class_rule'
        AND    p.via_subject_class = p_subject_key AND p.via_class = p_object_class
        AND    p.relationship = p_relationship
        AND    NOT EXISTS (
            SELECT 1 FROM drugref.graded_pair_observation o
            WHERE  o.rule_grain = 'class_rule' AND o.subject_key = p_subject_key
            AND    o.object_class_uuid = p_object_class
            AND    o.relationship = p_relationship
            AND    o.subject_moiety = p.subject_moiety
            AND    o.partner_moiety = p.partner_moiety
            AND    NOT EXISTS (SELECT 1 FROM drugref.graded_pair_departure_ack a
                               WHERE a.observation_id = o.observation_id));
    ELSE
        RAISE EXCEPTION 'refresh_graded_pairs_for_key: unknown rule_grain %',
            p_rule_grain;
    END IF;
    GET DIAGNOSTICS inserted = ROW_COUNT;
    RETURN inserted;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION drugref.refresh_graded_pairs(text) IS
    'Records every (ruling key, pair) curated_ddi_pair grades now that has no open '
    'episode in graded_pair_observation; returns rows inserted. Takes '
    'lock_graded_pair_ledger() first (db/086). Run at COMMIT by the '
    'ingest_run_graded_pairs and class_expansion_policy_graded_pairs constraint '
    'triggers (db/083); the ruling triggers run refresh_graded_pairs_for_key '
    '(db/084).';

COMMENT ON FUNCTION drugref.refresh_graded_pairs_for_key(text, uuid, uuid, text, text) IS
    'refresh_graded_pairs() for ONE ruling key (rule_grain, subject key, object '
    'class, relationship); returns rows inserted. Takes lock_graded_pair_ledger() '
    'first (db/086). Run at COMMIT by the two ruling triggers (db/084).';
