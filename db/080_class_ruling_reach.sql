-- db/080 -- what a class ruling reached, written down when the ruling is made (#258).
--
-- THE DEFECT. `drugref.class_rule_member` (db/079) says which drugs a LIVE class ruling
-- reaches, but class membership is delete-and-rebuild per ingest, so the answer changes
-- under a ruling that has not. A re-ingest that moves a drug out of a class erases the
-- evidence of what the curator's ruling covered when they made it. This table keeps it:
-- one row per (ruling, side, drug), written beside the ruling by
-- `class_ruling_reach.snapshot_reach` and never rewritten.
--
-- A CORRECTION SNAPSHOTS AGAIN under its own id; the superseded ruling keeps its rows,
-- so "what did version 1 reach" stays answerable after version 2 replaces it.
CREATE TABLE IF NOT EXISTS drugref.curated_class_interaction_reach (
    curated_class_interaction_id bigint NOT NULL
        REFERENCES drugref.curated_class_interaction (curated_class_interaction_id),
    side        text NOT NULL CHECK (side IN ('subject', 'partner')),
    moiety_uuid uuid NOT NULL REFERENCES drugref.substance_moiety (moiety_uuid),
    captured    text NOT NULL CHECK (captured IN ('at_ruling', 'backfill_db080')),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (curated_class_interaction_id, side, moiety_uuid)
);

CREATE TRIGGER curated_class_interaction_reach_insert_only
    BEFORE UPDATE OR DELETE ON drugref.curated_class_interaction_reach
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

COMMENT ON TABLE drugref.curated_class_interaction_reach IS
    'The drugs a class ruling reached on each side, captured when the ruling was written '
    '(captured = at_ruling). Insert-only. captured = backfill_db080 rows are membership '
    'AT MIGRATION TIME for rulings that predate this table, NOT membership at ruling '
    'time: they are the best available, and honest about it (db/080, #258).';

-- Rulings that predate this table: take live membership now, and say so.
-- backfill:begin
INSERT INTO drugref.curated_class_interaction_reach
    (curated_class_interaction_id, side, moiety_uuid, captured)
SELECT curated_class_interaction_id, side, moiety_uuid, 'backfill_db080'
FROM   drugref.class_rule_member
ON CONFLICT DO NOTHING;
-- backfill:end
