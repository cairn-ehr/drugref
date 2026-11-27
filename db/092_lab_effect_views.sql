-- db/092 -- drug effects on laboratory values, part 4: the views (#294).
--
-- THE CONTRACT ReportTriage reads (design spec 2026-11-27-drugref-lab-effects-design.md
-- sections 7-9):
--
--   lab_effect_label_evidence  -- EVIDENCE grain: one row per citing label occurrence
--   lab_effect_label_statement -- one row per machine-read (drug, analyte, direction,
--                                 role, co-medication) statement
--   lab_effect                 -- THE READ VIEW: what drugref currently states
--   lab_effect_coverage        -- THE COVERAGE VIEW: "no statement" vs "no effect"
--   gap_unconfirmed_label_lab_effect -- the curator worklist
--
-- GRAIN IN THE NAME, as db/051 and db/066 insist: count(*) on the evidence view is
-- never a statement count.
--
-- THE PRECEDENCE RULES (spec 6.5), in one place because every view below obeys them:
--   1. The REVIEW UNIT is (moiety, analyte). Any live moiety ruling on an analyte, of any
--      verdict, answers EVERY machine reading for that moiety and analyte: the readings
--      leave the read view (they stay in the evidence view). So a curator corrects a
--      misread role by ruling the right one, without refuting the machine's guess.
--   2. A class `holds` reaches every member (lab_effect_class_member, db/090).
--   3. A moiety ruling on the SAME seven-column key as a class `holds` carves that
--      member out (an exception).
--   4. A CLASS ruling NEVER hides a member's label readings: fewer rows is the harm
--      direction (the repo's standing rule: a signature never gates a read), so one
--      class does_not_hold must not silence every member's labels.

-- ============================================================================
-- 1. lab_effect_label_evidence -- one row per (subject drug, citing occurrence)
-- ============================================================================
CREATE OR REPLACE VIEW drugref.lab_effect_label_evidence AS
SELECT s.moiety_uuid      AS subject_moiety,
       s.set_id,
       s.version,
       ls.section,
       sec.section_rank,
       o.text_key,
       o.char_start,
       o.char_end,
       o.term_id,
       t.analyte_code,
       o.direction_read   AS direction,
       t.via_pathway,
       o.role_read,
       o.role_rule,
       -- The same encoding curated_lab_effect's generated columns use, so a reading
       -- and a ruling compare on one key.
       CASE WHEN o.co_medication_moiety_uuid IS NOT NULL THEN 'moiety'
            WHEN o.co_medication_class_uuid  IS NOT NULL THEN 'class'
            ELSE 'none' END AS co_medication_kind,
       COALESCE(o.co_medication_moiety_uuid, o.co_medication_class_uuid,
                '00000000-0000-0000-0000-000000000000'::uuid) AS co_medication_uuid,
       o.ingest_run
FROM   drugref.spl_lab_label_subject s
JOIN   drugref.spl_lab_label_section ls
       ON ls.ingest_run = s.ingest_run AND ls.source = s.source
      AND ls.set_id = s.set_id AND ls.version = s.version
JOIN   drugref.lab_effect_section sec ON sec.section = ls.section
JOIN   drugref.spl_lab_effect_occurrence o
       ON o.ingest_run = ls.ingest_run AND o.source = ls.source
      AND o.text_key = ls.text_key
JOIN   drugref.lab_effect_term t ON t.term_id = o.term_id
-- Resolved subjects only: an unresolved label states nothing about any drug.
WHERE  s.moiety_uuid IS NOT NULL;

COMMENT ON VIEW drugref.lab_effect_label_evidence IS
    'EVIDENCE GRAIN -- one row per (subject drug, label occurrence of a lab-effect term); '
    'count(*) is NOT a statement count, read lab_effect_label_statement. What a '
    'clinician''s "why" link reads: set_id, version, section, the term, the rule that '
    'fired. Includes not_an_effect readings. MACHINE-READ, NOT CONFIRMED (#294).';

-- ============================================================================
-- 2. lab_effect_label_statement -- one row per machine-read statement
-- ============================================================================
CREATE OR REPLACE VIEW drugref.lab_effect_label_statement AS
SELECT subject_moiety,
       analyte_code,
       direction,
       role_read,
       co_medication_kind,
       co_medication_uuid,
       -- The grade: the strongest section any citing label states it in.
       (array_agg(section ORDER BY section_rank))[1] AS strongest_section,
       min(section_rank)                              AS section_rank,
       count(DISTINCT set_id)                         AS label_count,
       array_agg(DISTINCT term_id ORDER BY term_id)   AS stated_as,
       bool_or(via_pathway)                           AS via_pathway
FROM   drugref.lab_effect_label_evidence
-- A precondition ("do not use in patients with hyperkalemia") is not an effect.
WHERE  role_read <> 'not_an_effect'
GROUP  BY subject_moiety, analyte_code, direction, role_read,
          co_medication_kind, co_medication_uuid;

COMMENT ON VIEW drugref.lab_effect_label_statement IS
    'STATEMENT GRAIN -- one row per machine-read (drug, analyte, direction, role, '
    'co-medication). strongest_section / section_rank are the grade (owner ruling A: '
    'record and grade, the consumer filters); label_count counts DISTINCT labels; '
    'stated_as is the label wording (term ids). not_an_effect readings excluded. '
    'MACHINE-READ, NOT CONFIRMED (#294).';

-- ============================================================================
-- 3. lab_effect -- THE READ VIEW
-- ============================================================================
CREATE OR REPLACE VIEW drugref.lab_effect AS
WITH live AS (
    SELECT * FROM drugref.curated_lab_effect WHERE superseded_by IS NULL
), arms AS (
    -- 3a. A drug's own holds ruling. via_pathway is FALSE on every curated row: a
    -- ruling states the analyte effect itself, not the wording that led to it.
    SELECT r.analyte_code, r.direction, 'moiety'::text AS subject_kind,
           r.subject_moiety_uuid AS subject_uuid, NULL::uuid AS reached_via_class_uuid,
           r.role, r.co_medication_kind, r.co_medication_uuid,
           'curated'::text AS basis, true AS confirmed, false AS via_pathway,
           NULL::text AS strongest_section, NULL::integer AS section_rank,
           NULL::bigint AS label_count, NULL::text[] AS stated_as,
           r.curated_lab_effect_id, r.onset_min_days, r.onset_max_days, r.method
    FROM   live r
    WHERE  r.subject_kind = 'moiety' AND r.verdict = 'holds'
    UNION ALL
    -- 3b. A class holds ruling, as a class row (the consumer asked "which classes").
    SELECT r.analyte_code, r.direction, 'class', r.subject_class_uuid, NULL,
           r.role, r.co_medication_kind, r.co_medication_uuid,
           'curated', true, false, NULL, NULL, NULL, NULL,
           r.curated_lab_effect_id, r.onset_min_days, r.onset_max_days, r.method
    FROM   live r
    WHERE  r.subject_kind = 'class' AND r.verdict = 'holds'
    UNION ALL
    -- 3c. ... and its members (rule 2), minus a member with its own ruling on the same
    -- key (rule 3: a does_not_hold carves it out; a holds is already in 3a).
    SELECT r.analyte_code, r.direction, 'moiety', m.moiety_uuid, r.subject_class_uuid,
           r.role, r.co_medication_kind, r.co_medication_uuid,
           'curated', true, false, NULL, NULL, NULL, NULL,
           r.curated_lab_effect_id, r.onset_min_days, r.onset_max_days, r.method
    FROM   live r
    JOIN   drugref.lab_effect_class_member m ON m.class_uuid = r.subject_class_uuid
    WHERE  r.subject_kind = 'class' AND r.verdict = 'holds'
      AND  NOT EXISTS (
               SELECT 1 FROM live x
               WHERE  x.subject_kind = 'moiety' AND x.subject_uuid = m.moiety_uuid
                 AND  x.analyte_code = r.analyte_code AND x.direction = r.direction
                 AND  x.role = r.role AND x.co_medication_kind = r.co_medication_kind
                 AND  x.co_medication_uuid = r.co_medication_uuid)
    UNION ALL
    -- 3d. Machine-read label statements, unless a moiety ruling of ANY verdict
    -- answers the (moiety, analyte) pair (rule 1). Class rulings do not (rule 4).
    SELECT st.analyte_code, st.direction, 'moiety', st.subject_moiety, NULL,
           st.role_read, st.co_medication_kind, st.co_medication_uuid,
           'label', false, st.via_pathway, st.strongest_section, st.section_rank,
           st.label_count, st.stated_as, NULL, NULL, NULL, NULL
    FROM   drugref.lab_effect_label_statement st
    WHERE  NOT EXISTS (
               SELECT 1 FROM live x
               WHERE  x.subject_kind = 'moiety' AND x.subject_uuid = st.subject_moiety
                 AND  x.analyte_code = st.analyte_code)
)
SELECT a.analyte_code,
       qd.query_direction,
       a.direction,
       an.specimen,
       a.via_pathway,
       a.subject_kind,
       a.subject_uuid,
       CASE a.subject_kind
            WHEN 'moiety' THEN (SELECT sm.display_name FROM drugref.substance_moiety sm
                                WHERE  sm.moiety_uuid = a.subject_uuid)
            ELSE (SELECT sc.class_name FROM drugref.substance_class sc
                  WHERE  sc.class_uuid = a.subject_uuid) END AS subject_name,
       a.reached_via_class_uuid,
       a.role,
       a.co_medication_kind,
       a.co_medication_uuid,
       a.basis,
       a.confirmed,
       a.strongest_section,
       a.section_rank,
       a.label_count,
       a.stated_as,
       a.curated_lab_effect_id,
       a.onset_min_days,
       a.onset_max_days,
       a.method,
       cr.release_tag,
       cardinality(cr.drift_reasons) > 0 AS release_drifted
FROM   arms a
JOIN   drugref.lab_analyte an ON an.analyte_code = a.analyte_code
-- An 'unspecified' direction answers BOTH questions, so it is listed under both.
CROSS  JOIN LATERAL unnest(CASE WHEN a.direction = 'unspecified'
                                THEN ARRAY['high', 'low']
                                ELSE ARRAY[a.direction] END) AS qd(query_direction)
-- LEFT: current_release has NO row in a database with no published release, and an
-- inner join would empty the whole read view there.
LEFT   JOIN drugref.current_release cr ON true;

COMMENT ON VIEW drugref.lab_effect IS
    'THE READ VIEW (#294): one row per (analyte, query_direction, subject, role, '
    'co-medication) drugref currently states. Filter on query_direction: a row whose '
    'direction is unspecified is listed under both high and low. basis = curated '
    '(confirmed = true, a live holds ruling, its class''s members included with '
    'reached_via_class_uuid) or label (confirmed = false, MACHINE-READ; role may be '
    'not_read). A moiety ruling of any verdict on an analyte replaces that drug''s '
    'label rows for it; a member''s own ruling on the same key carves it out of a '
    'class ruling; a class ruling never hides a member''s label rows. strongest_section '
    '/ section_rank are the grade. release_tag / release_drifted are NULL when no '
    'release is published. Absence of a row is NOT "no effect": read lab_effect_coverage.';
