-- db/004_schema_interactions.sql
-- drugref global tier, slice 5a: ingested drug<->class CONTRAINDICATIONS.
--
-- drugref's first drug-drug interaction data. MED-RT CI_MoA / CI_PE state, in the
-- release's own words, a "contraindicated mechanism of action / physiological
-- effect of a CO-ADMINISTERED ingredient" -- i.e. "drug X is contraindicated with
-- any co-administered drug acting on class C". A class-level DDI rule: one row
-- expands at read time (ddi_candidate_pair, below) over class_membership into the
-- concrete drug pairs, so the pair explosion is never stored.
--
-- STORAGE TIER -- a rebuildable projection, like class_membership (db/002), NOT the
-- append-only signed overlay (that is the curated moat, a later slice). MED-RT is
-- an upstream authority; re-ingesting a newer release DELETEs this source's rows
-- and re-inserts, so a contraindication retracted upstream disappears here too. A
-- no-DELETE floor would make that impossible, and there is no immortal identity to
-- protect here -- the row only links two IDs that are immortal elsewhere.
--
-- Why a separate migration and not an edit to 002/003: those files use
-- CREATE TABLE IF NOT EXISTS / guarded ALTERs and are replayed whole every
-- apply_migrations, so an edit there would never reach a database that already ran
-- them. A brand-new table is a clean additive db/004, itself guarded so replay
-- stays idempotent.

CREATE TABLE IF NOT EXISTS drugref.class_contraindication (
    -- The drug the statement is ABOUT: contraindicated when co-administered with a
    -- drug of object_class_uuid. A moiety, joined from the MED-RT RxNorm subject
    -- through the same RXNORM_IN identity_claim slice-2a membership uses.
    subject_moiety_uuid uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    -- The MoA/PE class of the CO-ADMINISTERED drug. Already in substance_class (2a).
    object_class_uuid   uuid   NOT NULL REFERENCES drugref.substance_class(class_uuid),
    relationship        text   NOT NULL,
    source              text   NOT NULL,
    ingest_run          bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (subject_moiety_uuid, object_class_uuid, relationship),
    -- Only the two co-administered-ingredient predicates whose object is a MED-RT
    -- class drugref already ingests. Deliberately NOT here: has_* (membership),
    -- may_treat/may_prevent/induces (indications), and CI_with / CI_ChemClass --
    -- the last two are drug-disease / drug-drug too but MeSH-keyed, so they need
    -- MeSH descriptor ingest first and belong to slice 5b.
    CONSTRAINT class_contraindication_relationship
        CHECK (relationship IN ('CI_MoA', 'CI_PE')),
    -- Symmetric with substance_class.source; widened per source as authorities land.
    CONSTRAINT class_contraindication_source
        CHECK (source IN ('MED-RT'))
);

-- Read path: "who is contraindicated with drugs of this class" -- the object side
-- drives pair expansion, so it is the indexed direction.
CREATE INDEX IF NOT EXISTS class_contraindication_by_object
    ON drugref.class_contraindication (object_class_uuid);

-- Pair expansion: a class-level rule joined to the members of its object class,
-- reusing the class_membership drugref already builds. CI_MoA pairs with has_MoA
-- members, CI_PE with has_PE members -- never cross-wired, or the clinical meaning
-- inverts. The subject is never paired with itself. Sub-class (DAG-descendant)
-- inheritance is intentionally NOT applied here: this direct-membership view is the
-- conservative default, and its blast radius is small enough to reason about; the
-- reviewed overlay (a later slice), not this projection, decides what alerts.
CREATE OR REPLACE VIEW drugref.ddi_candidate_pair AS
SELECT ci.subject_moiety_uuid AS moiety_a,
       m.moiety_uuid          AS moiety_b,        -- the co-administered drug
       ci.relationship,
       ci.object_class_uuid   AS via_class,
       ci.source,
       ci.ingest_run
FROM   drugref.class_contraindication ci
JOIN   drugref.class_membership m
       ON m.class_uuid = ci.object_class_uuid
      AND m.relationship = CASE ci.relationship
                              WHEN 'CI_MoA' THEN 'has_MoA'
                              WHEN 'CI_PE'  THEN 'has_PE'
                           END
WHERE  m.moiety_uuid <> ci.subject_moiety_uuid;
