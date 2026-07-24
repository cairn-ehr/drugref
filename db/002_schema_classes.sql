-- db/002_schema_classes.sql
-- drugref global tier, slice 2a: the classification DAG and moiety<->class membership.
--
-- This is the SECOND of the two orthogonal structures: an is-a-kind-of DAG, sitting
-- across the is-made-of composition tree that slice 1 started. Membership is
-- many-to-many -- a link table, never a parent FK on the moiety -- because a moiety
-- belongs to many classes on several axes at once (mechanism, effect, therapeutic).
--
-- SUPERSEDED IN PART by db/003_class_registry_source_neutral.sql: the columns
-- created below as medrt_nui/medrt_code are renamed there to source_code and
-- published_code, a NOT NULL (CHECK-constrained) `source` column is added, and the
-- concept_type / relationship CHECKs are widened for MeSH. This file is left as-written because
-- it uses CREATE TABLE IF NOT EXISTS -- editing it would never reach a database
-- that has already run it. Read 003 for the shape the registry actually has.
--
-- IMPORTANT -- why there is no append-only trigger floor in this file:
-- slice 1's floor (db/001) guards substance IDENTITY, which is immortal. The tables
-- here are a REBUILDABLE PROJECTION of an upstream authority (MED-RT): ingesting a
-- newer release DELETEs this source's prior edges and re-inserts them, so that a
-- class which lost a parent upstream loses it here too. A no-DELETE trigger would
-- make that impossible. Class *identity* is kept stable a different way -- class_uuid
-- is a pure UUIDv5 function of (source, code) (src/drugref/ids.py; the MED-RT NUI
-- before db/003 generalised it), so a rebuild re-derives exactly the same UUIDs it
-- had before.

-- The class registry: one row per MED-RT pharmacologic class concept.
CREATE TABLE IF NOT EXISTS drugref.substance_class (
    class_uuid        uuid   PRIMARY KEY,          -- UUIDv5(CLASS_NAMESPACE, 'MEDRT:'||nui)
    medrt_nui         text   NOT NULL UNIQUE,      -- MED-RT's stable id; class_uuid derives from it
    -- The concept's code AS PUBLISHED -- which is what associations reference by
    -- (from_code/to_code), whereas medrt_nui is the identity. The two hold the same
    -- string throughout the 2026.07.06 release, so this looks redundant today; it
    -- is stored separately because the parser resolves edge endpoints through it,
    -- and a release that let them diverge would otherwise match no edge at all.
    medrt_code        text,
    class_name        text   NOT NULL,             -- e.g. 'Calcium Channel Blocker [EPC]'
    concept_type      text   NOT NULL,             -- MED-RT CTY
    first_seen_ingest bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    -- The six MED-RT concept types that carry real classification meaning for us.
    -- Verified against the 2026.07.06 release:
    --   MoA/PE/TC/PK  reached by the has_MoA/has_PE/has_TC/has_PK associations;
    --   EPC           reached hierarchically (a 'Parent Of' from the EPC to the drug);
    --   APC           the parent type of 835 APC->EPC edges, needed or the EPC
    --                 hierarchy is truncated at its top.
    -- Deliberately EXCLUDED: HC (the 26 alphabetical navigation bins such as
    -- 'A [Preparations]' -- scaffolding, not classification) and EXT (chemical
    -- concepts staged for MeSH, with no ingredient membership).
    CONSTRAINT substance_class_concept_type
        CHECK (concept_type IN ('MoA', 'PE', 'TC', 'PK', 'EPC', 'APC'))
);

-- The subclass DAG. A class may have MANY parents, so this is an edge table and not
-- a parent column. Sourced from MED-RT 'Parent Of' relationships, which run FROM the
-- parent TO the child, and followed only where both endpoints are classes we ingest
-- (never SNOMED CT or MeSH -- see the ingest module for why that matters).
CREATE TABLE IF NOT EXISTS drugref.class_parent (
    child_class_uuid  uuid   NOT NULL REFERENCES drugref.substance_class(class_uuid),
    parent_class_uuid uuid   NOT NULL REFERENCES drugref.substance_class(class_uuid),
    ingest_run        bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (child_class_uuid, parent_class_uuid),
    CONSTRAINT class_parent_no_self_parent CHECK (child_class_uuid <> parent_class_uuid)
);

-- Many-to-many membership: which moieties belong to which classes, on which axis.
-- The axis is recorded because class-level curation inherits along it -- a consumer
-- needs "all MoA classes of moiety X", not merely "all classes of X".
CREATE TABLE IF NOT EXISTS drugref.class_membership (
    moiety_uuid  uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    class_uuid   uuid   NOT NULL REFERENCES drugref.substance_class(class_uuid),
    relationship text   NOT NULL,
    ingest_run   bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (moiety_uuid, class_uuid, relationship),
    -- Kept symmetric with substance_class.concept_type. 'has_EPC' is drugref's own
    -- label for EPC membership: MED-RT has no has_EPC association type and states it
    -- hierarchically instead, so we normalise it to look like the other four axes.
    -- Indication/contraindication relations (may_treat, CI_with, ...) are NOT
    -- membership -- they are curated-overlay data for a later slice -- and has_SC
    -- points into MeSH, which is slice 2b.
    CONSTRAINT class_membership_relationship
        CHECK (relationship IN ('has_MoA', 'has_PE', 'has_TC', 'has_PK', 'has_EPC'))
);

-- Query paths: "which moieties are in this class" and "walk the DAG upward".
CREATE INDEX IF NOT EXISTS class_membership_by_class
    ON drugref.class_membership (class_uuid);
CREATE INDEX IF NOT EXISTS class_parent_by_parent
    ON drugref.class_parent (parent_class_uuid);
