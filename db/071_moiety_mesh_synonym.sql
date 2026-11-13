-- db/071_moiety_mesh_synonym.sql
-- =============================================================================
-- BAN/AAN names for Cairn's allergy pick (#231). db/070 made the UNII official
-- names searchable ('acetaminophen' finds paracetamol), but a clinician typing
-- 'lignocaine', 'frusemide', 'adrenaline' or 'amoxycillin' still found nothing:
-- UNII files those only as common names (TYPE cn). MeSH has them as entry terms
-- of each drug's preferred concept.
--
-- Owner's decision on #231 (2026-11-14): these are SEARCH-ONLY names. They
-- include misspellings (Acetominophen) and abbreviations (APAP), so they are
-- never SPL label-match keys (nothing on the SPL side reads this table), and a
-- name that two moieties share is kept for both (search shows a list).
-- =============================================================================

-- ============================================================================
-- 1. Vocabulary -- the writer CHECK gains one value; the source is 'MeSH'
-- ============================================================================
-- COPIED FROM THE LIVE CATALOG (db/070) AND EXTENDED BY ONE VALUE, never retyped.
-- provenance.WRITERS gains 'mesh_synonym_run' in the SAME commit.
ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_writer;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_writer
    CHECK (writer IN ('unii_run', 'chebi', 'medrt_run', 'mesh_run', 'mesh_rel_run',
                      'pbs_run', 'curation', 'unattributed', 'gsrs_run',
                      'onchigh_run', 'fda_cyp_run', 'drugcentral_run', 'spl_run',
                      'allergy_group_run', 'moiety_name_run', 'mesh_synonym_run'));

-- ============================================================================
-- 2. The table
-- ============================================================================
CREATE TABLE IF NOT EXISTS drugref.moiety_mesh_synonym (
    moiety_uuid uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    -- As MeSH spells it (mixed case). Search case-insensitively.
    name        text   NOT NULL,
    -- The MeSH record the term came from (D... descriptor or C... supplementary).
    mesh_ui     text   NOT NULL,
    ingest_run  bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (moiety_uuid, name)
);
COMMENT ON TABLE drugref.moiety_mesh_synonym IS
    'MeSH entry terms of a record''s PREFERRED concept (unpermuted), linked to a '
    'moiety by that concept''s own UNII: BAN/AAN names such as Frusemide, '
    'Lignocaine, Adrenaline (#231). SEARCH ONLY: never an SPL label-match key. A '
    'name two moieties share is kept for both. The shipped official-name exclusions '
    'apply. A REBUILDABLE PROJECTION: delete-and-rebuild per source (MeSH, writer '
    'mesh_synonym_run). Search drugref.moiety_name, not this.';

-- ============================================================================
-- 3. The view consumers search -- db/070's two branches unchanged, plus a third
-- ============================================================================
-- The third branch hides a synonym the same moiety already answers to as its
-- display or official name (case-insensitively), so a picker does not list one
-- moiety twice under one name. Checked HERE rather than at ingest because the
-- display names change with every `ingest unii`; the view cannot go stale.
CREATE OR REPLACE VIEW drugref.moiety_name AS
SELECT moiety_uuid, display_name AS name, 'display'::text AS name_kind
FROM drugref.substance_moiety
UNION ALL
SELECT moiety_uuid, name, 'unii_official'::text
FROM drugref.moiety_official_name
UNION ALL
SELECT s.moiety_uuid, s.name, 'mesh_synonym'::text
FROM drugref.moiety_mesh_synonym s
WHERE NOT EXISTS (SELECT 1 FROM drugref.substance_moiety m
                  WHERE m.moiety_uuid = s.moiety_uuid
                    AND lower(m.display_name) = lower(s.name))
  AND NOT EXISTS (SELECT 1 FROM drugref.moiety_official_name o
                  WHERE o.moiety_uuid = s.moiety_uuid
                    AND lower(o.name) = lower(s.name));

COMMENT ON VIEW drugref.moiety_name IS
    'Every name a moiety can be found by: its display_name (name_kind display), '
    'its UNII official names (unii_official, from moiety_official_name) and its '
    'MeSH synonyms (mesh_synonym, from moiety_mesh_synonym: BAN/AAN names such as '
    'lignocaine, frusemide; #231). A mesh_synonym equal to the same moiety''s '
    'display or official name is not repeated. Display names may collide, and a '
    'mesh_synonym may name several moieties (salts, isomers): search shows a list. '
    'Only unii_official names are SPL label-match keys.';
