-- db/084 -- a ruling's commit refreshes only the ruling's own key (#281 review).
--
-- THE DEFECT. db/083's ruling triggers ran `refresh_graded_pairs()`, which reads
-- ALL of `curated_ddi_pair`. On a full registry copy with NO rulings that took
-- 5.65 s, all of it in the view's MOIETY half: `ddi_candidate_pair` hash-joins
-- class_membership x class_contraindication on relationship alone (estimated
-- 60,655 rows, actual 3,822,329), then sorts and de-duplicates them to merge-join
-- an empty curated_interaction. (The class half never executed.) So every save in
-- the reviewer GUI, and every curator ruling, blocked ~5 s at COMMIT, and a replay
-- paid ~5 s per distinct origin timestamp.
--
-- THE FIX. A ruling row changes grades only for its OWN natural key: both halves
-- of `curated_ddi_pair` join rulings on exactly (subject, object class,
-- relationship). So the two ruling triggers now refresh that one key, and the
-- key's filter pushes down into the view: 9 ms for a moiety key under a generic
-- plan (as plpgsql runs it), 3-12 ms per call for a prototype of this function.
-- Each grain gets its own branch with a LITERAL rule_grain filter, so the planner
-- prunes the other half of the UNION ALL. (A LATERAL join driven from the rulings
-- does NOT push down -- 4.2 s -- hence a per-key function.)
--
-- WHAT STAYS A FULL REFRESH. class_expansion_policy (an allow/deny can change any
-- class key; rare) and ingest_run (an ingest can change any key; ~5 s per ingest is
-- noise beside the ingest itself). Their triggers are untouched.
--
-- The two ruling triggers are dropped and recreated UNDER THE SAME NAMES, so
-- `graded_pairs.REFRESH_TRIGGERS`, the tests' `commit_point` and the replay's
-- `_commit_point` name them unchanged.

-- The full refresh, for one key. Same open-episode rule as db/083's
-- `refresh_graded_pairs`: a (key, pair) with an unacknowledged observation is not
-- recorded again. Returns rows inserted.
CREATE OR REPLACE FUNCTION drugref.refresh_graded_pairs_for_key(
    p_rule_grain text, p_subject_key uuid, p_object_class uuid,
    p_relationship text, p_captured text DEFAULT 'observed')
RETURNS integer AS $$
DECLARE
    inserted integer;
BEGIN
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

COMMENT ON FUNCTION drugref.refresh_graded_pairs_for_key(text, uuid, uuid, text, text) IS
    'refresh_graded_pairs() for ONE ruling key (rule_grain, subject key, object '
    'class, relationship); returns rows inserted. Run at COMMIT by the two ruling '
    'triggers (db/084).';

-- The ruling triggers' function: the inserted row names its own key.
CREATE OR REPLACE FUNCTION drugref.refresh_graded_pairs_for_key_at_commit()
RETURNS trigger AS $$
BEGIN
    IF TG_TABLE_NAME = 'curated_interaction' THEN
        PERFORM drugref.refresh_graded_pairs_for_key(
            'moiety_rule', NEW.subject_moiety_uuid, NEW.object_class_uuid,
            NEW.relationship);
    ELSE   -- curated_class_interaction
        PERFORM drugref.refresh_graded_pairs_for_key(
            'class_rule', NEW.subject_class_uuid, NEW.object_class_uuid,
            NEW.relationship);
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Same names, same timing as db/083 (see its WHY DEFERRED); only the function
-- changes.
DROP TRIGGER IF EXISTS curated_interaction_graded_pairs ON drugref.curated_interaction;
CREATE CONSTRAINT TRIGGER curated_interaction_graded_pairs
    AFTER INSERT ON drugref.curated_interaction
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.refresh_graded_pairs_for_key_at_commit();

DROP TRIGGER IF EXISTS curated_class_interaction_graded_pairs
    ON drugref.curated_class_interaction;
CREATE CONSTRAINT TRIGGER curated_class_interaction_graded_pairs
    AFTER INSERT ON drugref.curated_class_interaction
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.refresh_graded_pairs_for_key_at_commit();
