-- db/089 -- drug effects on laboratory values, part 1: the vocabulary (#294 step 2).
--
-- WHAT THIS IS FOR. ReportTriage ranks incoming laboratory reports partly by the
-- patient's medicines, and asks drugref: for this ANALYTE and DIRECTION, which drugs
-- matter, and in which role? (Design spec 2026-11-27-drugref-lab-effects-design.md.)
-- This file holds the three small authored tables everything else keys to:
--
--   lab_analyte        -- drugref's own analytes (potassium, INR, ...)
--   lab_effect_section -- the label sections a statement can sit in, with a grade
--   lab_effect_term    -- every way a label names an effect, mapped to analyte + direction
--
-- WHY drugref's OWN ANALYTES, AND WHY FROM MeSH (owner ruling 2026-11-27, spec section 2).
-- Each analyte is derived from a MeSH descriptor, NEVER from LOINC: LOINC's licence
-- forbids using its content "for the purpose of developing or promulgating a different
-- standard", and a drugref analyte list built from LOINC material could be read as one.
-- No LOINC code appears in any drugref table; the consumer maps analytes to its own
-- LOINC-coded results.
--
-- WHY STATEMENTS KEY TO AN ANALYTE, NOT TO A MeSH CONDITION (owner ruling, spec 2.4).
-- "INR high" has no MeSH condition (INR exists only as a test, D019934), and the only
-- condition for a creatinine rise is Acute Kidney Injury -- which would record
-- "trimethoprim causes kidney injury" when it raises creatinine by blocking its
-- secretion. So the condition the label named is kept as WHAT THE LABEL SAID
-- (lab_effect_term.condition_mesh_ui), and the statement's object is the analyte.

-- ============================================================================
-- 1. lab_analyte -- one row per analyte drugref can make statements about
-- ============================================================================
CREATE TABLE IF NOT EXISTS drugref.lab_analyte (
    -- drugref's own stable slug. IMMORTAL: consumers key on it, so it is never renamed
    -- or reused (the floor trigger below refuses UPDATE and DELETE outright).
    analyte_code text PRIMARY KEY,
    -- The MeSH descriptor the analyte is derived from (Potassium = D011188).
    mesh_ui      text NOT NULL CHECK (mesh_ui ~ '^D[0-9]+$'),
    -- Interference is usually specimen-specific (cephalosporins upset URINE glucose
    -- tests, not blood glucose), so one descriptor may give one analyte per specimen.
    specimen     text NOT NULL CHECK (specimen IN ('blood', 'urine', 'any')),
    display_name text NOT NULL,
    CONSTRAINT lab_analyte_one_per_specimen UNIQUE (mesh_ui, specimen)
);

-- A vocabulary consumers code against: add rows, never change or remove one.
DROP TRIGGER IF EXISTS lab_analyte_insert_only ON drugref.lab_analyte;
CREATE TRIGGER lab_analyte_insert_only
    BEFORE UPDATE OR DELETE ON drugref.lab_analyte
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

-- The seed is #294's test set. Each mesh_ui was checked against MeSH 2026
-- (desc2026.gz) when this file was written.
INSERT INTO drugref.lab_analyte (analyte_code, mesh_ui, specimen, display_name) VALUES
    ('potassium',   'D011188', 'blood', 'Potassium'),
    ('sodium',      'D012964', 'blood', 'Sodium'),
    ('creatinine',  'D003404', 'blood', 'Creatinine'),
    ('haemoglobin', 'D006454', 'blood', 'Haemoglobin'),
    ('inr',         'D019934', 'blood', 'INR (international normalised ratio)'),
    ('neutrophils', 'D009504', 'blood', 'Neutrophils'),
    ('platelets',   'D001792', 'blood', 'Platelets')
ON CONFLICT DO NOTHING;

COMMENT ON TABLE drugref.lab_analyte IS
    'drugref''s own laboratory analytes, each derived from a MeSH descriptor (mesh_ui) and '
    'NEVER from LOINC (no LOINC code is stored anywhere; the consumer maps). analyte_code '
    'is immortal: insert-only (lab_analyte_insert_only). One descriptor may give one '
    'analyte per specimen, because assay interference is specimen-specific (#294).';

-- ============================================================================
-- 2. lab_effect_section -- the grade: which label section a statement sits in
-- ============================================================================
-- A lookup table rather than a CHECK because the views need the ORDER (min(rank) =
-- strongest section). Owner ruling A (2026-11-27): record every statement with this
-- grade and let the consumer filter. Changing the grade is a one-row migration.
CREATE TABLE IF NOT EXISTS drugref.lab_effect_section (
    -- The openFDA field name the section arrives under.
    section      text PRIMARY KEY,
    -- 1 = strongest. UNIQUE, so "the section with the lowest rank" is one section.
    section_rank integer NOT NULL UNIQUE CHECK (section_rank > 0)
);

-- The survey's order (source evaluation 3.1), with the interference section placed
-- after precautions: ranked last it would vanish under any consumer filter, and it is
-- the main home of the fourth role, "interferes" (spec 6.3).
INSERT INTO drugref.lab_effect_section (section, section_rank) VALUES
    ('boxed_warning', 1), ('warnings_and_cautions', 2), ('warnings', 3),
    ('precautions', 4), ('drug_and_or_laboratory_test_interactions', 5),
    ('laboratory_tests', 6), ('drug_interactions', 7), ('adverse_reactions', 8),
    ('adverse_reactions_table', 9)
ON CONFLICT DO NOTHING;

COMMENT ON TABLE drugref.lab_effect_section IS
    'The label sections a lab-effect statement can sit in, with section_rank (1 = '
    'strongest) as the statement''s grade. Owner ruling 2026-11-27: record and grade, the '
    'consumer filters. The interference section ranks 5, after precautions (#294 design '
    'spec 6.3).';

-- ============================================================================
-- 3. lab_effect_term -- label wording -> (analyte, direction)
-- ============================================================================
-- The owner's "condition -> (analyte, direction) map", widened to test wording
-- ("serum potassium increased"), which labels use as often as the condition name.
CREATE TABLE IF NOT EXISTS drugref.lab_effect_term (
    term_id           text PRIMARY KEY,
    -- The MeSH CONDITION when the term is one (Hyperkalemia = D006947); NULL for test
    -- wording. A MeSH UI and deliberately NOT a foreign key into drugref.condition:
    -- that table is a rebuildable projection holding only the conditions MED-RT
    -- happens to cite, and an authored map must neither block its rebuild nor depend
    -- on what MED-RT cites.
    condition_mesh_ui text CHECK (condition_mesh_ui ~ '^D[0-9]+$'),
    analyte_code      text NOT NULL REFERENCES drugref.lab_analyte(analyte_code),
    -- 'unspecified' = the wording gives no direction, or "makes the result unreliable".
    direction         text NOT NULL CHECK (direction IN ('high', 'low', 'unspecified')),
    -- TRUE when the analyte moves only THROUGH A PATHWAY (bleeding -> haemoglobin
    -- low), so a consumer can weigh it below a direct effect.
    via_pathway       boolean NOT NULL
);

INSERT INTO drugref.lab_effect_term
    (term_id, condition_mesh_ui, analyte_code, direction, via_pathway) VALUES
    ('hyperkalemia',               'D006947', 'potassium',   'high', false),
    ('serum_potassium_increased',  NULL,      'potassium',   'high', false),
    ('hypokalemia',                'D007008', 'potassium',   'low',  false),
    ('serum_potassium_decreased',  NULL,      'potassium',   'low',  false),
    ('hyponatremia',               'D007010', 'sodium',      'low',  false),
    ('siadh',                      'D007177', 'sodium',      'low',  false),
    ('acute_kidney_injury',        'D058186', 'creatinine',  'high', false),
    ('serum_creatinine_increased', NULL,      'creatinine',  'high', false),
    ('bleeding',                   'D006470', 'haemoglobin', 'low',  true),
    ('inr_increased',              NULL,      'inr',         'high', false),
    ('neutropenia',                'D009503', 'neutrophils', 'low',  false),
    ('agranulocytosis',            'D000380', 'neutrophils', 'low',  false),
    ('thrombocytopenia',           'D013921', 'platelets',   'low',  false),
    ('platelet_count_decreased',   NULL,      'platelets',   'low',  false)
ON CONFLICT DO NOTHING;

COMMENT ON TABLE drugref.lab_effect_term IS
    'Every way a label names a lab effect (a MeSH condition, condition_mesh_ui, or test '
    'wording, NULL there), mapped to drugref''s analyte and a direction. via_pathway marks '
    'an analyte reached only through a pathway (bleeding -> haemoglobin low). '
    'condition_mesh_ui is deliberately not an FK into the rebuildable condition '
    'projection (#294 design spec 6.2).';
