-- db/070_moiety_name.sql
-- =============================================================================
-- Searchable moiety names (#231). Cairn's allergy pick (cairn_drug_search, Cairn
-- db/076) could only match substance_moiety.display_name, so a clinician typing
-- 'acetaminophen' found nothing: the registry names that moiety 'paracetamol'.
-- #227 already reads the UNII official names (UNII_Names TYPE 'of') during the
-- SPL ingest, but held them only in memory.
--
-- This migration persists those names (moiety_official_name, written by
-- ingest/moiety_name_run.py) and exposes one relation to search, the
-- moiety_name VIEW: every display name plus every stored official name.
--
-- WHY A VIEW OVER THE DISPLAY NAMES rather than copying them into the table: a
-- UNII re-ingest refreshes display_name, and a copied name would then go stale
-- until someone re-ran the names ingest. The view cannot disagree with the
-- registry.
-- =============================================================================

-- ============================================================================
-- 1. Vocabulary -- the writer CHECK gains one value; the source is 'UNII'
-- ============================================================================
-- COPIED FROM THE LIVE CATALOG (db/067) AND EXTENDED BY ONE VALUE, never retyped.
-- provenance.WRITERS gains 'moiety_name_run' in the SAME commit. No new source:
-- the names ARE the UNII release (FDA, public domain), and a release is
-- unambiguous per (source, writer), as MED-RT's two writers already are.
ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_writer;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_writer
    CHECK (writer IN ('unii_run', 'chebi', 'medrt_run', 'mesh_run', 'mesh_rel_run',
                      'pbs_run', 'curation', 'unattributed', 'gsrs_run',
                      'onchigh_run', 'fda_cyp_run', 'drugcentral_run', 'spl_run',
                      'allergy_group_run', 'moiety_name_run'));

-- ============================================================================
-- 2. The table
-- ============================================================================
CREATE TABLE IF NOT EXISTS drugref.moiety_official_name (
    moiety_uuid uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    -- As UNII_Names spells it (upper case). Search case-insensitively.
    name        text   NOT NULL,
    ingest_run  bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (moiety_uuid, name)
);
COMMENT ON TABLE drugref.moiety_official_name IS
    'UNII official names (UNII_Names TYPE of: USAN, INN ...) that name a moiety '
    'differently from its display_name, e.g. ACETAMINOPHEN for paracetamol. Holds '
    'exactly the names the SPL drug pass adopts as match keys (#213, '
    'ingest/spl_official_names.py): a name another moiety displays, a name two '
    'moieties claim, a name of a UNII two moieties claim, and the shipped '
    'exclusions are all left out. A REBUILDABLE PROJECTION: delete-and-rebuild per '
    'source (UNII, writer moiety_name_run). Search drugref.moiety_name, not this.';

-- ============================================================================
-- 3. The view consumers search
-- ============================================================================
CREATE OR REPLACE VIEW drugref.moiety_name AS
SELECT moiety_uuid, display_name AS name, 'display'::text AS name_kind
FROM drugref.substance_moiety
UNION ALL
SELECT moiety_uuid, name, 'unii_official'::text
FROM drugref.moiety_official_name;

COMMENT ON VIEW drugref.moiety_name IS
    'Every name a moiety can be found by: its display_name (name_kind display) '
    'and its UNII official names (name_kind unii_official, from '
    'moiety_official_name). One name never points at two moieties through the '
    'official half; display names may still collide (registry_read counts those). '
    'BAN/AAN names (lignocaine, frusemide, adrenaline) are NOT here: UNII files '
    'them as common names (TYPE cn), not official ones (#231).';
