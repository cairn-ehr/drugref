-- db/092 -- drug effects on laboratory values, part 4: the views (#294).
--
-- THE CONTRACT ReportTriage reads (design spec 2026-11-27-drugref-lab-effects-design.md
-- sections 7-9):
--
--   lab_effect_label_evidence  -- EVIDENCE grain: one row per citing label occurrence
--   lab_effect_label_statement -- one row per machine-read (drug, analyte, direction,
--                                 role, co-medication) statement
--   lab_effect_open_label_statement -- those no moiety ruling has answered (rule 1)
--   lab_effect                 -- THE READ VIEW: what drugref currently states
--   lab_effect_coverage        -- THE COVERAGE VIEW: "no statement" vs "no effect"
--   gap_unconfirmed_label_lab_effect -- the curator worklist
--
-- GRAIN IN THE NAME, as db/051 and db/066 insist: count(*) on the evidence view is
-- never a statement count.
--
-- THE PRECEDENCE RULES (spec 6.5 as amended by sections 12 and 13), in one place
-- because every view below obeys them:
--   1. WHICH READINGS A MOIETY RULING ANSWERS. A live moiety ruling answers a machine
--      reading of the same drug and analyte when ALL of these hold:
--        - the same co-medication (none = none, drug E = drug E): "not more dangerous
--          with E" is no review of a standalone "causes" (spec 13.2);
--        - the same direction, or the RULING is 'unspecified'. A ruling on potassium
--          LOW does not answer a HIGH reading (12.1), and a ruling on HIGH answers an
--          'unspecified' reading only under HIGH: its LOW half stays listed (13.3);
--        - the ruling is a `holds` (any role: a curator corrects a misread role by
--          ruling the right one, without refuting the machine's guess), OR it is in
--          the reading's own role, OR the reading's role is not_read. So "does not
--          interfere with the assay" leaves "causes hyperkalaemia" standing (13.1).
--      An answered reading leaves the read view (it stays in the evidence view).
--   2. A class `holds` reaches every member (lab_effect_class_member, db/090).
--   3. A moiety does_not_hold on the class `holds`'s key, the subject apart (same
--      analyte, direction, role and co-medication), carves that member out (an
--      exception). cannot_establish does not: "the sources do not settle it for this
--      drug" is no exception to the class (12.3).
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
       -- Pathway-ONLY: one direct term among the wording makes the statement direct.
       bool_and(via_pathway)                          AS via_pathway
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
-- 2b. lab_effect_open_label_statement -- rule 1, stated ONCE
-- ============================================================================
-- Each statement with the query directions no live moiety ruling answers yet. An
-- 'unspecified' statement is asked about under high AND low, and each half is
-- answered on its own. Both the read view (3d) and the worklist (5) read this, so the
-- two cannot disagree about what is answered. It deliberately does NOT read
-- lab_effect, which joins current_release: a database whose current_release is gone
-- (one predating db/081) must keep its worklist.
CREATE OR REPLACE VIEW drugref.lab_effect_open_label_statement AS
SELECT st.*, od.open_directions
FROM   drugref.lab_effect_label_statement st
CROSS  JOIN LATERAL (
    SELECT ARRAY(
        SELECT q FROM unnest(CASE WHEN st.direction = 'unspecified'
                                  THEN ARRAY['high', 'low']
                                  ELSE ARRAY[st.direction] END) AS q
        WHERE  NOT EXISTS (
                   SELECT 1 FROM drugref.curated_lab_effect x
                   WHERE  x.superseded_by IS NULL
                     AND  x.subject_kind = 'moiety'
                     AND  x.subject_uuid = st.subject_moiety
                     AND  x.analyte_code = st.analyte_code
                     AND  x.co_medication_kind = st.co_medication_kind
                     AND  x.co_medication_uuid = st.co_medication_uuid
                     AND  x.direction IN (q, 'unspecified')
                     AND  (x.verdict = 'holds' OR x.role = st.role_read
                           OR st.role_read = 'not_read'))) AS open_directions
) od
WHERE  cardinality(od.open_directions) > 0;

COMMENT ON VIEW drugref.lab_effect_open_label_statement IS
    'lab_effect_label_statement rows no live moiety ruling has fully answered (db/092 '
    'rule 1), with open_directions = the query directions (high / low) still '
    'unanswered. What the read view lists as label rows and the worklist asks about. '
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
           r.curated_lab_effect_id, r.onset_min_days, r.onset_max_days, r.method,
           -- Only a label row (3d) narrows the query directions it is listed under.
           NULL::text[] AS open_directions
    FROM   live r
    WHERE  r.subject_kind = 'moiety' AND r.verdict = 'holds'
    UNION ALL
    -- 3b. A class holds ruling, as a class row (the consumer asked "which classes").
    SELECT r.analyte_code, r.direction, 'class', r.subject_class_uuid, NULL,
           r.role, r.co_medication_kind, r.co_medication_uuid,
           'curated', true, false, NULL, NULL, NULL, NULL,
           r.curated_lab_effect_id, r.onset_min_days, r.onset_max_days, r.method, NULL
    FROM   live r
    WHERE  r.subject_kind = 'class' AND r.verdict = 'holds'
    UNION ALL
    -- 3c. ... and its members (rule 2), minus a member with its own does_not_hold on
    -- the same key, the subject apart (rule 3). A member's holds on that key is
    -- already in 3a, so it is left out here too rather than listed twice.
    SELECT r.analyte_code, r.direction, 'moiety', m.moiety_uuid, r.subject_class_uuid,
           r.role, r.co_medication_kind, r.co_medication_uuid,
           'curated', true, false, NULL, NULL, NULL, NULL,
           r.curated_lab_effect_id, r.onset_min_days, r.onset_max_days, r.method, NULL
    FROM   live r
    JOIN   drugref.lab_effect_class_member m ON m.class_uuid = r.subject_class_uuid
    WHERE  r.subject_kind = 'class' AND r.verdict = 'holds'
      AND  NOT EXISTS (
               SELECT 1 FROM live x
               WHERE  x.subject_kind = 'moiety' AND x.subject_uuid = m.moiety_uuid
                 AND  x.analyte_code = r.analyte_code AND x.direction = r.direction
                 AND  x.role = r.role AND x.co_medication_kind = r.co_medication_kind
                 AND  x.co_medication_uuid = r.co_medication_uuid
                 AND  x.verdict IN ('does_not_hold', 'holds'))
    UNION ALL
    -- 3d. Machine-read label statements, in each query direction no moiety ruling
    -- answers (rule 1, in 2b; class rulings never do, rule 4).
    SELECT st.analyte_code, st.direction, 'moiety', st.subject_moiety, NULL,
           st.role_read, st.co_medication_kind, st.co_medication_uuid,
           'label', false, st.via_pathway, st.strongest_section, st.section_rank,
           st.label_count, st.stated_as, NULL, NULL, NULL, NULL, st.open_directions
    FROM   drugref.lab_effect_open_label_statement st
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
-- An 'unspecified' direction answers BOTH questions, so it is listed under both --
-- except a label row's half that a ruling already answered (3d's open_directions).
CROSS  JOIN LATERAL unnest(COALESCE(a.open_directions,
                                    CASE WHEN a.direction = 'unspecified'
                                         THEN ARRAY['high', 'low']
                                         ELSE ARRAY[a.direction] END))
       AS qd(query_direction)
-- LEFT: current_release has NO row in a database with no published release, and an
-- inner join would empty the whole read view there.
LEFT   JOIN drugref.current_release cr ON true;

COMMENT ON VIEW drugref.lab_effect IS
    'THE READ VIEW (#294): the (analyte, query_direction, subject, role, '
    'co-medication) facts drugref currently states; one fact may have both a curated '
    'and a label row, and an unspecified and a directed row. Filter on '
    'query_direction: a row whose direction is unspecified is listed under both high '
    'and low. basis = curated (confirmed = true, a live holds ruling, its class''s '
    'members included with reached_via_class_uuid) or label (confirmed = false, '
    'MACHINE-READ; role may be not_read). A moiety ruling with the same co-medication '
    'and direction replaces that drug''s label rows: a holds in any role, '
    'does_not_hold / cannot_establish only in the read role (or not_read). A '
    'member''s own does_not_hold on the same key carves it out of a class ruling; a '
    'class ruling never hides a member''s label rows. strongest_section / '
    'section_rank are the grade. release_tag / release_drifted are NULL when no '
    'release is published. Absence of a row is NOT "no effect": read '
    'lab_effect_coverage.';

-- ============================================================================
-- 4. lab_effect_coverage -- "no statement" told apart from "no effect" (spec 8)
-- ============================================================================
-- One row per (drug, analyte, direction) for every drug in the registry, with exactly
-- one state; the first that applies wins:
--   stated                 -- lab_effect has a row (any role, either basis)
--   cannot_establish       -- the drug's own curator looked and the sources do not
--                             settle it. Ahead of ruled_no_effect, so a class-level
--                             "no" never overrules it.
--   ruled_no_effect        -- a live does_not_hold with NO co-medication, the drug's
--                             own or a class's that reaches it, for EVERY ONE of the
--                             four roles. THE ONLY "NO EFFECT" drugref ever states.
--                             Ruling out one role ("does not interfere") or one
--                             combination ("not with drug B") is not "no effect"
--                             (spec 12.2): ruled_out_roles lists what was ruled out,
--                             and the state falls through.
--   examined_nothing_found -- drugref read at least one label for this drug and found
--                             nothing for this analyte. A label that does not mention
--                             hyperkalaemia is NOT evidence of no hyperkalaemia:
--                             unavailable context, NEVER reassurance.
--   not_examined           -- no label for this drug was read
-- A ruling with direction 'unspecified' speaks to both directions, as in lab_effect.
CREATE OR REPLACE VIEW drugref.lab_effect_coverage AS
WITH examined AS (
    SELECT moiety_uuid, count(DISTINCT set_id) AS label_count
    FROM   drugref.spl_lab_label_subject
    WHERE  moiety_uuid IS NOT NULL
    GROUP  BY moiety_uuid
), live AS (
    SELECT * FROM drugref.curated_lab_effect WHERE superseded_by IS NULL
)
SELECT m.moiety_uuid,
       an.analyte_code,
       d.direction,
       CASE
         WHEN EXISTS (SELECT 1 FROM drugref.lab_effect le
                      WHERE  le.subject_kind = 'moiety'
                        AND  le.subject_uuid = m.moiety_uuid
                        AND  le.analyte_code = an.analyte_code
                        AND  le.query_direction = d.direction)
           THEN 'stated'
         -- Before ruled_no_effect: a drug-specific "the sources do not settle it"
         -- is never overruled by a class-level "no".
         WHEN EXISTS (SELECT 1 FROM live x
                      WHERE  x.verdict = 'cannot_establish'
                        AND  x.subject_kind = 'moiety' AND x.subject_uuid = m.moiety_uuid
                        AND  x.analyte_code = an.analyte_code
                        AND  x.direction IN (d.direction, 'unspecified'))
           THEN 'cannot_establish'
         WHEN cardinality(ro.ruled_out_roles) = 4 THEN 'ruled_no_effect'
         WHEN e.moiety_uuid IS NOT NULL THEN 'examined_nothing_found'
         ELSE 'not_examined'
       END AS state,
       ro.ruled_out_roles,
       COALESCE(e.label_count, 0) AS label_count
FROM   drugref.substance_moiety m
CROSS  JOIN drugref.lab_analyte an
CROSS  JOIN (VALUES ('high'), ('low')) AS d(direction)
LEFT   JOIN examined e ON e.moiety_uuid = m.moiety_uuid
-- The roles a live, co-medication-free does_not_hold rules out for this drug, analyte
-- and direction (its own, or a class's reaching it), in the fixed role order.
CROSS  JOIN LATERAL (
    SELECT ARRAY(
        SELECT role.name
        FROM   unnest(ARRAY['causes', 'more_dangerous', 'monitoring', 'interferes'])
               WITH ORDINALITY AS role(name, ord)
        WHERE  EXISTS (
                   SELECT 1 FROM live x
                   WHERE  x.verdict = 'does_not_hold' AND x.role = role.name
                     AND  x.co_medication_kind = 'none'
                     AND  x.analyte_code = an.analyte_code
                     AND  x.direction IN (d.direction, 'unspecified')
                     AND  ((x.subject_kind = 'moiety' AND x.subject_uuid = m.moiety_uuid)
                           OR (x.subject_kind = 'class' AND EXISTS (
                                  SELECT 1 FROM drugref.lab_effect_class_member cm
                                  WHERE  cm.class_uuid = x.subject_class_uuid
                                    AND  cm.moiety_uuid = m.moiety_uuid))))
        ORDER  BY role.ord) AS ruled_out_roles
) ro;

COMMENT ON VIEW drugref.lab_effect_coverage IS
    'THE COVERAGE VIEW (#294): one row per (drug, analyte, direction high/low) with one '
    'state -- stated, cannot_establish, ruled_no_effect, examined_nothing_found, '
    'not_examined (first that applies). ONLY ruled_no_effect means "no effect", and it '
    'needs a co-medication-free does_not_hold for ALL FOUR roles; ruled_out_roles lists '
    'the roles ruled out so far. examined_nothing_found (a label was read and did not '
    'mention it) and not_examined are UNAVAILABLE CONTEXT, NEVER REASSURANCE. '
    'label_count = distinct labels read for the drug. Query it filtered by drug: it '
    'spans the whole registry.';

-- ============================================================================
-- 5. The worklist -- machine readings no curator has answered
-- ============================================================================
-- One question per (drug, analyte) while ANY of its label readings is unanswered: the
-- same open statements the read view lists as label rows (2b, rule 1).
CREATE OR REPLACE VIEW drugref.gap_unconfirmed_label_lab_effect AS
SELECT st.subject_moiety AS moiety_uuid,
       st.analyte_code,
       min(st.section_rank) AS section_rank,
       -- Distinct labels across the pair (summing per-statement counts would count a
       -- label once per role it was read in).
       (SELECT count(DISTINCT ev.set_id) FROM drugref.lab_effect_label_evidence ev
        WHERE  ev.subject_moiety = st.subject_moiety
          AND  ev.analyte_code = st.analyte_code
          AND  ev.role_read <> 'not_an_effect') AS label_count
FROM   drugref.lab_effect_open_label_statement st
GROUP  BY st.subject_moiety, st.analyte_code;

COMMENT ON VIEW drugref.gap_unconfirmed_label_lab_effect IS
    'Gap kind unconfirmed_label_lab_effect: one row per (drug, analyte) with a label '
    'reading no live moiety ruling answers (lab_effect_open_label_statement); section_rank '
    'is the strongest grade among them, label_count distinct labels for the pair. '
    'MACHINE-READ, NOT CONFIRMED, never an assertion, and "not confirmed" is NOT "safe". '
    'Frozen gap key: LABEFFECT:<moiety_uuid>:<analyte_code>.';

-- Widen open_question_gap_kind by one value: db/066's DO-block, the list COPIED
-- VERBATIM from db/066 (the latest migration touching this constraint; 20 kinds).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%unconfirmed_label_lab_effect%') THEN
        ALTER TABLE drugref.open_question DROP CONSTRAINT IF EXISTS open_question_gap_kind;
        ALTER TABLE drugref.open_question ADD CONSTRAINT open_question_gap_kind
            CHECK (gap_kind IN (
                'unpopulated_contraindication', 'unclassified_moiety',
                'unmatched_ingredient', 'unreviewed_expansion_root',
                'unresolved_ci_object', 'dead_by_expansion_policy',
                'condition_without_indication', 'uncurated_additive_effect',
                'uncurated_threshold', 'ineffective_contribution',
                'ungraded_contribution', 'unruled_composition_activity',
                'uncurated_condition_contradiction', 'uncurated_interaction_rule',
                'unresolved_onc_endpoint', 'uncurated_class_interaction_rule',
                'fda_cyp_unadjudicated', 'unresolved_ddi_endpoint',
                'unconfirmed_class_property', 'unconfirmed_label_class_statement',
                'unconfirmed_label_lab_effect'));
    END IF;
END $$;
