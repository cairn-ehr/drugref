-- db/067_allergy_groups.sql
-- =============================================================================
-- Allergy groups: MeSH-seeded drug groups for cross-reactivity-aware allergy
-- matching (design: docs/superpowers/specs/2026-11-08-drugref-allergy-groups-design.md).
-- This migration is schema and vocabulary only; the parser, orchestrator and
-- read come in later tasks.
-- =============================================================================

-- ============================================================================
-- 1. Vocabulary -- four CHECKs, one commit
-- ============================================================================
-- COPIED FROM THE LIVE CATALOG AND THEN EXTENDED BY ONE VALUE, never retyped
-- (db/039, db/051). ids._SOURCE_CANONICAL gains "DRUGREF-ALLERGY" and
-- provenance.WRITERS gains 'allergy_group_run' in the SAME commit; if they
-- drift apart a per-source rebuild silently deletes nothing.
ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_source;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_source
    CHECK (source IN ('UNII', 'CHEBI', 'MED-RT', 'MeSH', 'PBS', 'DRUGREF', 'GSRS',
                      'ONCHIGH', 'FDA-CYP', 'DRUGCENTRAL', 'SPL', 'DRUGREF-ALLERGY'));

ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_writer;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_writer
    CHECK (writer IN ('unii_run', 'chebi', 'medrt_run', 'mesh_run', 'mesh_rel_run',
                      'pbs_run', 'curation', 'unattributed', 'gsrs_run',
                      'onchigh_run', 'fda_cyp_run', 'drugcentral_run', 'spl_run',
                      'allergy_group_run'));

ALTER TABLE drugref.substance_class DROP CONSTRAINT IF EXISTS substance_class_source;
ALTER TABLE drugref.substance_class ADD CONSTRAINT substance_class_source
    CHECK (source IN ('MED-RT', 'MeSH', 'DRUGREF', 'FDA-CYP', 'DRUGREF-ALLERGY'));

-- 'AG' = allergy group.
ALTER TABLE drugref.substance_class DROP CONSTRAINT IF EXISTS substance_class_concept_type;
ALTER TABLE drugref.substance_class ADD CONSTRAINT substance_class_concept_type
    CHECK (concept_type IN ('MoA', 'PE', 'TC', 'PK', 'EPC', 'APC', 'PA', 'AG'));

-- ============================================================================
-- 2. Tables
-- ============================================================================
CREATE TABLE IF NOT EXISTS drugref.allergy_group (
    class_uuid        uuid   PRIMARY KEY REFERENCES drugref.substance_class(class_uuid),
    mesh_root_ui      text   NOT NULL,
    review_status     text   NOT NULL
        CONSTRAINT allergy_group_review_status
        CHECK (review_status IN ('structural-seed', 'reviewed')),
    reviewed_by       text,
    reviewed_against  text,
    ingest_run        bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id)
);
COMMENT ON TABLE drugref.allergy_group IS
    'One row per allergy group (a substance_class of concept_type AG): the MeSH '
    'root it was seeded from and whether a clinician has reviewed it. Exists so a '
    'structural seed is never mistaken for clinical judgement. A REBUILDABLE '
    'PROJECTION: delete-and-rebuild per source (DRUGREF-ALLERGY).';

CREATE TABLE IF NOT EXISTS drugref.allergy_group_member (
    class_uuid  uuid   NOT NULL REFERENCES drugref.allergy_group(class_uuid),
    moiety_uuid uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    -- 'descriptor:<UI>' | 'scr:<UI>' | 'include:<UNII>': why this moiety is in.
    via         text   NOT NULL,
    reason      text,
    ingest_run  bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (class_uuid, moiety_uuid, via)
);
CREATE INDEX IF NOT EXISTS allergy_group_member_by_moiety
    ON drugref.allergy_group_member (moiety_uuid);
COMMENT ON TABLE drugref.allergy_group_member IS
    'Which moieties belong to which allergy group, and VIA what. Deliberately NOT '
    'class_membership: an allergy group is a cross-reactivity hint, not a '
    'pharmacological class, and mixing them would leak it into class-based '
    'interaction and contraindication reads. A REBUILDABLE PROJECTION: '
    'delete-and-rebuild per source (DRUGREF-ALLERGY).';

CREATE TABLE IF NOT EXISTS drugref.allergy_mesh_placement (
    moiety_uuid uuid   PRIMARY KEY REFERENCES drugref.substance_moiety(moiety_uuid),
    ingest_run  bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id)
);
COMMENT ON TABLE drugref.allergy_mesh_placement IS
    'Moieties that MeSH places anywhere at all, so a moiety in no group can be '
    'told apart from one MeSH has never heard of. A REBUILDABLE PROJECTION: '
    'delete-and-rebuild per source (DRUGREF-ALLERGY).';
