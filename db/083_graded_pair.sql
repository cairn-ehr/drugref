-- db/083 -- every pair a reviewed grade ever reached, kept (#281, #279).
--
-- THE DEFECT. `drugref.curated_ddi_pair` only says which pairs rulings grade NOW.
-- #256's departure banner compared it with db/080's reach snapshot, which covers
-- class rulings only and only the version that was snapshotted: a correction after
-- a drug left (#281 section 1) snapshots the smaller class and the departed drug
-- vanishes; a drug that joined and then left between snapshots (#281 section 2)
-- was never recorded; and a moiety-grain ruling (#279) has no snapshot at all.
--
-- THE FIX. One observation per EPISODE in which a ruling key graded a pair,
-- written by `drugref.refresh_graded_pairs()` at every COMMIT that could change
-- the view. A departure is then "an open episode the view no longer returns"
-- (Task 3), and an acknowledgement closes one episode (Task 4).
--
-- WHY THE RULING'S NATURAL KEY, NOT ITS VERSION ID. A correction is a new version
-- row of the same key (append-then-point). Keyed on the version, a correction
-- would start empty and forget what its predecessor reached -- #281 section 1
-- again. Keyed on (grain, subject key, object class, relationship), a correction
-- inherits the key's history.
CREATE TABLE IF NOT EXISTS drugref.graded_pair_observation (
    observation_id    bigserial PRIMARY KEY,
    -- `curated_ddi_pair.rule_grain`'s own vocabulary.
    rule_grain        text NOT NULL CHECK (rule_grain IN ('moiety_rule', 'class_rule')),
    -- The ruling's subject: the subject MOIETY for a moiety_rule, the subject
    -- CLASS for a class_rule. Two kinds of id, so no foreign key.
    subject_key       uuid NOT NULL,
    object_class_uuid uuid NOT NULL REFERENCES drugref.substance_class (class_uuid),
    relationship      text NOT NULL,
    subject_moiety    uuid NOT NULL REFERENCES drugref.substance_moiety (moiety_uuid),
    partner_moiety    uuid NOT NULL REFERENCES drugref.substance_moiety (moiety_uuid),
    -- observed = written by the refresh; backfill_db083 = reconstructed by this
    -- migration for history that predates it (see the backfill below).
    captured          text NOT NULL CHECK (captured IN ('observed', 'backfill_db083')),
    first_seen_at     timestamptz NOT NULL DEFAULT now()
);

-- The refresh and the departure read both look an episode up by (key, pair).
-- Not unique: an acknowledged episode and the one that re-opens after it share it.
CREATE INDEX IF NOT EXISTS graded_pair_observation_key_pair
    ON drugref.graded_pair_observation
       (rule_grain, subject_key, object_class_uuid, relationship,
        subject_moiety, partner_moiety);

CREATE TRIGGER graded_pair_observation_insert_only
    BEFORE UPDATE OR DELETE ON drugref.graded_pair_observation
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

COMMENT ON TABLE drugref.graded_pair_observation IS
    'One row per episode in which a ruling key (rule_grain, subject_key, '
    'object_class_uuid, relationship) graded a pair, keyed on the ruling''s natural '
    'key so a correction inherits its history. Written by refresh_graded_pairs() at '
    'COMMIT. captured = backfill_db083 rows were recorded at db/083''s migration, NOT '
    'when graded. Insert-only (db/083, #281, #279).';

-- An acknowledgement closes ONE episode, not the pair: if the pair is graded again
-- later, the refresh opens a new episode, and a second departure banners again.
CREATE TABLE IF NOT EXISTS drugref.graded_pair_departure_ack (
    ack_id           bigserial PRIMARY KEY,
    observation_id   bigint NOT NULL UNIQUE
        REFERENCES drugref.graded_pair_observation (observation_id),
    rationale        text NOT NULL CHECK (btrim(rationale) <> ''),
    reviewed_by      text NOT NULL,
    reviewed_against text NOT NULL,
    acknowledged_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER graded_pair_departure_ack_insert_only
    BEFORE UPDATE OR DELETE ON drugref.graded_pair_departure_ack
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

COMMENT ON TABLE drugref.graded_pair_departure_ack IS
    'A curator''s acceptance that one graded_pair_observation episode has ended (the '
    'pair departed and that is correct). One per episode; insert-only (db/083).';


-- ============================================================================
-- The refresh
-- ============================================================================
-- One row per DISTINCT (key, pair) `curated_ddi_pair` returns, unless that key and
-- pair already has an OPEN episode (an observation with no acknowledgement). The
-- view already drops `applies = false` rulings and rulings whose candidate is gone,
-- so withdrawn rulings and orphans are never observed. Returns rows inserted.
CREATE OR REPLACE FUNCTION drugref.refresh_graded_pairs(captured text DEFAULT 'observed')
RETURNS integer AS $$
DECLARE
    inserted integer;
BEGIN
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

COMMENT ON FUNCTION drugref.refresh_graded_pairs(text) IS
    'Records every (ruling key, pair) curated_ddi_pair grades now that has no open '
    'episode in graded_pair_observation; returns rows inserted. Run at COMMIT by the '
    '*_graded_pairs constraint triggers (db/083).';

CREATE OR REPLACE FUNCTION drugref.refresh_graded_pairs_at_commit() RETURNS trigger AS $$
BEGIN
    PERFORM drugref.refresh_graded_pairs();
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- WHY DEFERRED. A correction is append-then-point: INSERT the new version, then
-- UPDATE the old one's superseded_by. Refreshed straight after the INSERT, the view
-- would still be mid-correction. Deferred to COMMIT, the refresh sees the
-- transaction's final state, whichever language (Python, the Rust reviewer
-- service, psql) wrote it.
--
-- WHY ORDER AGAINST THE EXISTING DEFERRED `*_single_live` TRIGGERS DOES NOT MATTER.
-- Those only CHECK (and raise); the refresh only READS the view and writes its own
-- table. Every statement of the transaction has run before any deferred trigger
-- fires, so whichever fires first, the refresh sees the same rows; and if a
-- single-live check raises, the whole transaction -- refresh included -- rolls back.
--
-- PER ROW, so a transaction inserting N rulings refreshes N times; the second and
-- later find every pair already open and insert nothing. Rulings are written one
-- per transaction in practice (spec 5.2).
CREATE CONSTRAINT TRIGGER curated_interaction_graded_pairs
    AFTER INSERT ON drugref.curated_interaction
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.refresh_graded_pairs_at_commit();

CREATE CONSTRAINT TRIGGER curated_class_interaction_graded_pairs
    AFTER INSERT ON drugref.curated_class_interaction
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.refresh_graded_pairs_at_commit();

CREATE CONSTRAINT TRIGGER class_expansion_policy_graded_pairs
    AFTER INSERT ON drugref.class_expansion_policy
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.refresh_graded_pairs_at_commit();

-- WHY ingest_run IS THE INGEST CHOKE POINT. Class membership, parents and rule
-- candidates are written only by ingest orchestrators, and every one ends by
-- stamping its run finished (`provenance.finish_run`) inside its final
-- transaction. One trigger on that stamp therefore covers every table an ingest
-- rebuilds, without a trigger per projection table. Only the NULL -> value
-- transition: the stamp is written once (db/055, db/056).
CREATE CONSTRAINT TRIGGER ingest_run_graded_pairs
    AFTER UPDATE OF finished_at ON drugref.ingest_run
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    WHEN (OLD.finished_at IS NULL AND NEW.finished_at IS NOT NULL)
    EXECUTE FUNCTION drugref.refresh_graded_pairs_at_commit();


-- ============================================================================
-- Backfill: history that predates this table
-- ============================================================================
-- 1. Every pair graded at migration time.
-- 2. For class rulings, every (subject, partner) pair in the db/080 reach snapshot
--    of ANY applying version of a key, minus self-pairs (as the view drops them):
--    this keeps #256's banner for drugs that left before db/083, and covers a
--    correction made after a drug left (#281 section 1) retroactively. The moiety
--    grain has no earlier evidence; its history starts here.
-- Both are marked backfill_db083 and stamped with migration time, not ruling time.
-- backfill:begin
SELECT drugref.refresh_graded_pairs('backfill_db083');
INSERT INTO drugref.graded_pair_observation
    (rule_grain, subject_key, object_class_uuid, relationship,
     subject_moiety, partner_moiety, captured)
SELECT DISTINCT 'class_rule', cci.subject_class_uuid, cci.object_class_uuid,
       cci.relationship, s.moiety_uuid, p.moiety_uuid, 'backfill_db083'
FROM   drugref.curated_class_interaction cci
JOIN   drugref.curated_class_interaction_reach s
       ON  s.curated_class_interaction_id = cci.curated_class_interaction_id
       AND s.side = 'subject'
JOIN   drugref.curated_class_interaction_reach p
       ON  p.curated_class_interaction_id = cci.curated_class_interaction_id
       AND p.side = 'partner'
WHERE  cci.applies
AND    s.moiety_uuid <> p.moiety_uuid
AND    NOT EXISTS (
    SELECT 1 FROM drugref.graded_pair_observation o
    WHERE  o.rule_grain = 'class_rule' AND o.subject_key = cci.subject_class_uuid
    AND    o.object_class_uuid = cci.object_class_uuid
    AND    o.relationship = cci.relationship
    AND    o.subject_moiety = s.moiety_uuid AND o.partner_moiety = p.moiety_uuid
    AND    NOT EXISTS (SELECT 1 FROM drugref.graded_pair_departure_ack a
                       WHERE a.observation_id = o.observation_id));
-- backfill:end
