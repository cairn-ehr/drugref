-- db/082 -- a class ruling's snapshot was TAKEN, even when it reached no one (#266).
--
-- THE DEFECT. db/080's reach table holds one row per drug a ruling reached. A ruling
-- whose class had no members when it was written therefore leaves NO rows -- exactly
-- what a ruling that was never snapshotted leaves (one superseded before db/080, whose
-- backfill covered live rulings only). `drugref audit class-ruling` printed
-- "captured: none" for both, and an auditor could not tell "reached nobody" from
-- "never recorded": opposite answers to "who did this ruling cover?".
--
-- THE FIX. One row per ruling, written by `class_ruling_reach.snapshot_reach` beside
-- the reach rows (both sides are snapshotted in that one call, so one row covers
-- both). A ruling with this row and no reach rows on a side reached no one on it.
-- A recorded fact, not an inference from timestamps, so it stays true after a
-- release is replayed into a fresh database (#259) -- it travels in the release's
-- curated export like the reach rows do.
CREATE TABLE IF NOT EXISTS drugref.curated_class_interaction_snapshot (
    curated_class_interaction_id bigint PRIMARY KEY
        REFERENCES drugref.curated_class_interaction (curated_class_interaction_id),
    -- Same vocabulary as the reach table's `captured`: at_ruling = taken when the
    -- ruling was written; backfill_db080 = reconstructed by db/080's migration.
    captured    text NOT NULL CHECK (captured IN ('at_ruling', 'backfill_db080')),
    recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER curated_class_interaction_snapshot_insert_only
    BEFORE UPDATE OR DELETE ON drugref.curated_class_interaction_snapshot
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

COMMENT ON TABLE drugref.curated_class_interaction_snapshot IS
    'One row per class ruling whose reach was snapshotted (db/080 table). A ruling with '
    'this row but no reach rows on a side reached no one on that side; a ruling '
    'without it was never snapshotted. Insert-only (db/082, #266).';

-- Rulings snapshotted before this table: their reach rows prove a snapshot was taken.
-- Backfill wins when a ruling mixes both kinds, as `audit_ruling` decides per side.
-- shortcut: a ruling written between db/080 and db/082 whose class was EMPTY left no
-- reach rows, so it gets no marker and reads "never recorded"; no named database
-- carries db/080 yet (HANDOVER), so none exists -- revisit if one ever does.
-- backfill:begin
INSERT INTO drugref.curated_class_interaction_snapshot
    (curated_class_interaction_id, captured)
SELECT curated_class_interaction_id,
       CASE WHEN bool_or(captured = 'backfill_db080') THEN 'backfill_db080'
            ELSE 'at_ruling' END
FROM   drugref.curated_class_interaction_reach
GROUP  BY curated_class_interaction_id
ON CONFLICT DO NOTHING;
-- backfill:end
