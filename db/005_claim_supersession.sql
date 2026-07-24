-- db/005_claim_supersession.sql
-- Make the correction overlay a real one-way overlay, and stop it being a trapdoor.
--
-- `superseded_by` is the ONLY mutable column in the whole identity spine, so it is
-- the only way the append-only floor can be subverted without an INSERT. db/001
-- constrained it barely at all: it could be un-set, re-pointed, or aimed at a claim
-- belonging to a DIFFERENT moiety, and two claims could point at each other -- a
-- cycle that makes BOTH identifiers vanish from every `superseded_by IS NULL` join
-- at once, with nothing anywhere reporting it.
--
-- The other half of the same problem ran the opposite way. identity_claim_unique
-- covered superseded rows as well as live ones, so once a (moiety, scheme, value)
-- had ever been superseded it could never be asserted again: the next release's
-- INSERT hit the index, claims.add_claim's ON CONFLICT DO NOTHING swallowed it and
-- reported "already present", and the identifier stayed invisible forever. Upstream
-- corrections DO get reverted, so a re-assertion has to be able to land.
--
-- Both are fixed here: uniqueness now covers only LIVE claims, and the trigger
-- enforces that supersession is set once, never unset, and always points at a
-- LATER claim on the SAME moiety (which also makes a cycle unrepresentable, since
-- the id must strictly increase along the chain).

-- 1. Uniqueness applies to the LIVE claim only.
--    A superseded row is history; it must not block the value coming back.
DROP INDEX IF EXISTS drugref.identity_claim_unique;
CREATE UNIQUE INDEX IF NOT EXISTS identity_claim_live_unique
    ON drugref.identity_claim (moiety_uuid, scheme, value)
    WHERE superseded_by IS NULL;

-- 2. The floor, restated with supersession actually constrained.
--    CREATE OR REPLACE so this supersedes db/001's definition of the same function
--    (the trigger keeps pointing at it, no re-attach needed).
CREATE OR REPLACE FUNCTION drugref.forbid_claim_rewrite() RETURNS trigger AS $$
DECLARE
    target_moiety uuid;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drugref.identity_claim is append-only: DELETE forbidden';
    END IF;
    IF NEW.moiety_uuid  <> OLD.moiety_uuid
       OR NEW.scheme     <> OLD.scheme
       OR NEW.value      <> OLD.value
       OR NEW.ingest_run <> OLD.ingest_run
       OR NEW.asserted_at <> OLD.asserted_at THEN
        RAISE EXCEPTION 'drugref.identity_claim is append-only: only superseded_by may change';
    END IF;

    -- Supersession is a ONE-WAY transition: NULL -> a claim id, exactly once.
    -- Un-setting would resurrect a corrected-away identifier as live; re-pointing
    -- would rewrite history that other rows may already have been read against.
    IF OLD.superseded_by IS NOT NULL
       AND NEW.superseded_by IS DISTINCT FROM OLD.superseded_by THEN
        RAISE EXCEPTION
            'drugref.identity_claim.superseded_by is one-way: claim % is already superseded by %',
            OLD.identity_claim_id, OLD.superseded_by;
    END IF;

    IF NEW.superseded_by IS NOT NULL THEN
        -- A correction replaces one moiety's identifier with another of ITS OWN.
        -- Pointing across moieties is not a correction, it is a merge -- and the
        -- registry has no merge semantics (moiety_uuid is immortal).
        SELECT moiety_uuid INTO target_moiety FROM drugref.identity_claim
            WHERE identity_claim_id = NEW.superseded_by;
        IF target_moiety <> NEW.moiety_uuid THEN
            RAISE EXCEPTION
                'drugref.identity_claim: a claim may only be superseded by another claim on the SAME moiety (% vs %)',
                NEW.moiety_uuid, target_moiety;
        END IF;
        -- The correction is always the LATER row (insert-new-then-point-old-at-new),
        -- so the chain strictly increases and can never close into a cycle.
        IF NEW.superseded_by <= NEW.identity_claim_id THEN
            RAISE EXCEPTION
                'drugref.identity_claim: superseded_by must reference a LATER claim (% <= %)',
                NEW.superseded_by, NEW.identity_claim_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 3. substance_moiety: first_seen_ingest is write-once provenance.
--    identity_claim's floor already guards its own ingest_run column; this closes
--    the same hole on the registry, where "when did drugref FIRST see this moiety"
--    was silently rewritable by any UPDATE.
CREATE OR REPLACE FUNCTION drugref.forbid_moiety_rewrite() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drugref.substance_moiety is append-only: DELETE forbidden';
    END IF;
    IF NEW.moiety_uuid <> OLD.moiety_uuid THEN
        RAISE EXCEPTION 'drugref.substance_moiety.moiety_uuid is immortal: it may not change';
    END IF;
    IF NEW.first_seen_ingest <> OLD.first_seen_ingest THEN
        RAISE EXCEPTION 'drugref.substance_moiety.first_seen_ingest is write-once provenance';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 4. ingest_run.source is the key every per-source rebuild joins through
--    (classes.clear_source_edges, interactions.clear_source_contraindications),
--    yet it was the one `source` column in the schema with no CHECK. A row written
--    under a variant spelling ('medrt') would leave its edges permanently invisible
--    to the rebuild that is supposed to replace them -- stale clinical data
--    coexisting with current data, undetectably. Extend this together with
--    ids._SOURCE_CANONICAL and substance_class's own CHECK.
ALTER TABLE drugref.ingest_run
    ADD CONSTRAINT ingest_run_source
    CHECK (source IN ('UNII', 'CHEBI', 'MED-RT', 'MeSH'));

-- 5. Index the rebuild-delete path. Every re-ingest deletes this source's rows
--    from three tables by ingest_run; without these it is a sequential scan of
--    each table plus one of ingest_run, on every run, forever.
CREATE INDEX IF NOT EXISTS ingest_run_by_source
    ON drugref.ingest_run (source);
CREATE INDEX IF NOT EXISTS class_parent_by_ingest_run
    ON drugref.class_parent (ingest_run);
CREATE INDEX IF NOT EXISTS class_membership_by_ingest_run
    ON drugref.class_membership (ingest_run);
CREATE INDEX IF NOT EXISTS class_contraindication_by_ingest_run
    ON drugref.class_contraindication (ingest_run);
