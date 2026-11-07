-- db/059_class_profile_member_property.sql -- class profiles, task 2: the base views.
--
-- Design: docs/superpowers/specs/2026-11-07-drugref-class-profiles-design.md (sections 3-5).
-- Six READ-ONLY views, no new tables:
--   class_profile_member          direct members of a profiled class
--   member_property_record        (helper) every stated record, flagged if a curator negated it
--   member_ruling_oriented        (helper) moiety rulings, interactions seen from both drugs
--   member_property               the effective properties of a drug
--   member_property_negation      the confirmed exceptions
--   class_asserted_property       what a source or curator states about a class ITSELF
--
-- A member's properties are what a source STATES for that member. ddi_candidate_pair and
-- curated_ddi_pair fan one class rule out to every member of a class AND its subclasses;
-- counting those would make the "observed" route re-derive the "asserted" one. spl_ddi_pair
-- is a label co-mention, not an interaction. None of the three is read below.

-- ---------------------------------------------------------------------------
-- 1. class_profile_member -- direct membership only
-- ---------------------------------------------------------------------------
-- DISTINCT because a drug may be tagged to one class through several relationships
-- (has_EPC and has_MoA). MED-RT's PK axis is excluded (#155: 97.2% of its occurrences name
-- an empty class; "Clearance" is a property, not a drug class). FDA-CYP classes reuse the
-- PK concept type but are fully populated, hence the source test.
CREATE OR REPLACE VIEW drugref.class_profile_member AS
SELECT DISTINCT cm.class_uuid, cm.moiety_uuid
FROM   drugref.class_membership cm
JOIN   drugref.substance_class sc ON sc.class_uuid = cm.class_uuid
WHERE  NOT (sc.source = 'MED-RT' AND sc.concept_type = 'PK');

COMMENT ON VIEW drugref.class_profile_member IS
    'The DIRECT members of each profiled class: a drug tagged to a subclass is NOT a member '
    'of its parent. Excludes MED-RT classes of concept type PK (empty, and not drug classes); '
    'FDA-CYP PK classes are kept. No subtree expansion -- that is class_subtree''s job.';

-- ---------------------------------------------------------------------------
-- 2. member_property_record -- one row per stated record, with its negation flag
-- ---------------------------------------------------------------------------
-- `negated` is RECORD-level: a curated_interaction with applies = false on the same
-- (drug, class, relationship) kills only that class_contraindication record. Another record
-- on a different axis (CI_MoA vs CI_PE) still states the property, which is why negation is
-- decided per record and only then aggregated.
CREATE OR REPLACE VIEW drugref.member_property_record AS
SELECT subject_moiety_uuid AS moiety_uuid, relationship, 'condition'::text AS object_kind,
       object_condition_uuid AS object_uuid, source, false AS negated
FROM   drugref.moiety_condition_indication
UNION ALL
SELECT subject_moiety_uuid, relationship, 'condition', object_condition_uuid, source, false
FROM   drugref.moiety_condition_contraindication
UNION ALL
SELECT subject_moiety_uuid, relationship, 'condition', object_condition_uuid, source, false
FROM   drugref.moiety_induced_condition
UNION ALL
-- Exact drug pairs (MED-RT moiety arm + DrugCentral), unordered, so BOTH drugs get it.
-- Self-pairs excluded, as everywhere on the read path.
SELECT e.a, 'interacts_with', 'moiety', e.b, e.candidate_source, false
FROM  (SELECT moiety_lo AS a, moiety_hi AS b, candidate_source
         FROM drugref.exact_ddi_pair WHERE moiety_lo <> moiety_hi
       UNION ALL
       SELECT moiety_hi, moiety_lo, candidate_source
         FROM drugref.exact_ddi_pair WHERE moiety_lo <> moiety_hi) e
UNION ALL
SELECT cc.subject_moiety_uuid, 'interacts_with', 'class', cc.object_class_uuid, cc.source,
       EXISTS (SELECT 1 FROM drugref.curated_interaction ci
               WHERE ci.superseded_by IS NULL AND NOT ci.applies
                 AND ci.subject_moiety_uuid = cc.subject_moiety_uuid
                 AND ci.object_class_uuid   = cc.object_class_uuid
                 AND ci.relationship        = cc.relationship)
FROM   drugref.class_contraindication cc;

COMMENT ON VIEW drugref.member_property_record IS
    'Helper for member_property and member_property_negation: every STATED record of a drug '
    'property, one row per upstream record, flagged negated when a live curated_interaction '
    'with applies = false rules on exactly that record. Reads the five stated sources only -- '
    'never ddi_candidate_pair, curated_ddi_pair or spl_ddi_pair.';

-- ---------------------------------------------------------------------------
-- 2b. member_ruling_oriented -- live moiety rulings, interactions made symmetric
-- ---------------------------------------------------------------------------
-- Interactions are clinically symmetric (spec section 3), so a ruling on (X, interacts_with,
-- moiety, Y) governs BOTH orientations: each such ruling also appears here as (Y, ..., X).
-- If the two orientations are ruled differently, does_not_hold wins (the consumers test
-- does_not_hold independently of holds, and removal beats addition).
CREATE OR REPLACE VIEW drugref.member_ruling_oriented AS
SELECT subject_uuid AS moiety_uuid, relationship, object_kind, object_uuid, verdict
FROM   drugref.curated_property_ruling
WHERE  superseded_by IS NULL AND subject_kind = 'moiety'
UNION ALL
SELECT object_uuid, relationship, 'moiety', subject_uuid, verdict
FROM   drugref.curated_property_ruling
WHERE  superseded_by IS NULL AND subject_kind = 'moiety'
  AND  relationship = 'interacts_with' AND object_kind = 'moiety';

COMMENT ON VIEW drugref.member_ruling_oriented IS
    'Helper: live moiety-subject property rulings, with every drug-to-drug interaction ruling '
    'also listed from the partner''s side so a ruling governs both orientations.';

-- ---------------------------------------------------------------------------
-- 3. member_property -- the effective properties
-- ---------------------------------------------------------------------------
-- Curated overrides candidate: a live does_not_hold moiety ruling removes the property even
-- where records state it; a live holds ruling adds it (source DRUGREF).
CREATE OR REPLACE VIEW drugref.member_property AS
SELECT u.moiety_uuid, u.relationship, u.object_kind, u.object_uuid,
       array_agg(DISTINCT u.source ORDER BY u.source) AS sources
FROM  (SELECT moiety_uuid, relationship, object_kind, object_uuid, source
         FROM drugref.member_property_record WHERE NOT negated
       UNION ALL
       SELECT moiety_uuid, relationship, object_kind, object_uuid, 'DRUGREF'
         FROM drugref.member_ruling_oriented WHERE verdict = 'holds') u
WHERE  NOT EXISTS (
        SELECT 1 FROM drugref.member_ruling_oriented n
        WHERE  n.verdict = 'does_not_hold' AND n.moiety_uuid = u.moiety_uuid
          AND  n.relationship = u.relationship AND n.object_kind = u.object_kind
          AND  n.object_uuid = u.object_uuid)
GROUP  BY u.moiety_uuid, u.relationship, u.object_kind, u.object_uuid;

COMMENT ON VIEW drugref.member_property IS
    'The effective stated properties of a drug, one row per (drug, relationship, object) with '
    'the distinct contributing sources sorted. Two sources for one fact count once. Records a '
    'live curated applies = false negates are dropped one by one; a live does_not_hold ruling '
    'removes the whole property; a live holds ruling adds it. Excludes class-expanded pairs '
    '(ddi_candidate_pair, curated_ddi_pair) and SPL co-mentions (spl_ddi_pair).';

-- ---------------------------------------------------------------------------
-- 4. member_property_negation -- the confirmed exceptions
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW drugref.member_property_negation AS
SELECT moiety_uuid, relationship, object_kind, object_uuid
FROM   drugref.member_ruling_oriented
WHERE  verdict = 'does_not_hold'
UNION
-- ALL records negated, and no curator holds-ruling putting it back.
SELECT r.moiety_uuid, r.relationship, r.object_kind, r.object_uuid
FROM   drugref.member_property_record r
GROUP  BY r.moiety_uuid, r.relationship, r.object_kind, r.object_uuid
HAVING bool_and(r.negated)
   AND NOT EXISTS (
        SELECT 1 FROM drugref.member_ruling_oriented h
        WHERE  h.verdict = 'holds' AND h.moiety_uuid = r.moiety_uuid
          AND  h.relationship = r.relationship AND h.object_kind = r.object_kind
          AND  h.object_uuid = r.object_uuid);

COMMENT ON VIEW drugref.member_property_negation IS
    'Confirmed exceptions: a drug property a curator affirmatively says does NOT hold -- a live '
    'does_not_hold moiety ruling, or an interacts_with/class property every one of whose '
    'class_contraindication records is negated by curated_interaction applies = false. One '
    'surviving record keeps the property out of this view. Absence here means nobody has '
    'said no, never that the property is safe.';

-- ---------------------------------------------------------------------------
-- 5. class_asserted_property -- facts stated about a class itself
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW drugref.class_asserted_property AS
SELECT u.class_uuid, u.relationship, u.object_kind, u.object_uuid,
       array_agg(DISTINCT u.source ORDER BY u.source) AS asserted_by
FROM  (
        -- class x class candidate rule: both classes, unless a curator ruled it out.
        SELECT p.subject_class_uuid AS class_uuid, 'interacts_with' AS relationship,
               'class' AS object_kind, p.object_class_uuid AS object_uuid, p.source
          FROM drugref.class_pair_contraindication p
         WHERE NOT EXISTS (SELECT 1 FROM drugref.curated_class_interaction n
                WHERE n.superseded_by IS NULL AND NOT n.applies
                  AND n.subject_class_uuid = p.subject_class_uuid
                  AND n.object_class_uuid = p.object_class_uuid
                  AND n.relationship = p.relationship)
        UNION ALL
        SELECT p.object_class_uuid, 'interacts_with', 'class', p.subject_class_uuid, p.source
          FROM drugref.class_pair_contraindication p
         WHERE NOT EXISTS (SELECT 1 FROM drugref.curated_class_interaction n
                WHERE n.superseded_by IS NULL AND NOT n.applies
                  AND n.subject_class_uuid = p.subject_class_uuid
                  AND n.object_class_uuid = p.object_class_uuid
                  AND n.relationship = p.relationship)
        UNION ALL
        -- a curator's own class x class assertion, both sides.
        SELECT subject_class_uuid, 'interacts_with', 'class', object_class_uuid, source
          FROM drugref.curated_class_interaction WHERE superseded_by IS NULL AND applies
        UNION ALL
        SELECT object_class_uuid, 'interacts_with', 'class', subject_class_uuid, source
          FROM drugref.curated_class_interaction WHERE superseded_by IS NULL AND applies
        UNION ALL
        -- drug X -> class C states a fact about C as a whole: C interacts with X.
        SELECT cc.object_class_uuid, 'interacts_with', 'moiety', cc.subject_moiety_uuid,
               cc.source
          FROM drugref.class_contraindication cc
         WHERE NOT EXISTS (SELECT 1 FROM drugref.curated_interaction n
                WHERE n.superseded_by IS NULL AND NOT n.applies
                  AND n.subject_moiety_uuid = cc.subject_moiety_uuid
                  AND n.object_class_uuid = cc.object_class_uuid
                  AND n.relationship = cc.relationship)
        UNION ALL
        -- a drug-subject holds ruling on a class is the same fact seen from the class.
        SELECT object_uuid, 'interacts_with', 'moiety', subject_uuid, 'DRUGREF'
          FROM drugref.curated_property_ruling
         WHERE superseded_by IS NULL AND subject_kind = 'moiety'
           AND relationship = 'interacts_with' AND object_kind = 'class'
           AND verdict = 'holds'
        UNION ALL
        -- the only route to a condition property for a class today.
        SELECT subject_uuid, relationship, object_kind, object_uuid, 'DRUGREF'
          FROM drugref.curated_property_ruling
         WHERE superseded_by IS NULL AND subject_kind = 'class' AND verdict = 'holds'
      ) u
-- A drug's does_not_hold on (interacts_with, class C) withdraws C's side of it too.
WHERE  NOT (u.object_kind = 'moiety' AND EXISTS (
        SELECT 1 FROM drugref.curated_property_ruling n
        WHERE  n.superseded_by IS NULL AND n.subject_kind = 'moiety'
          AND  n.verdict = 'does_not_hold' AND n.relationship = 'interacts_with'
          AND  n.object_kind = 'class' AND n.object_uuid = u.class_uuid
          AND  n.subject_uuid = u.object_uuid))
GROUP  BY u.class_uuid, u.relationship, u.object_kind, u.object_uuid;

COMMENT ON VIEW drugref.class_asserted_property IS
    'Properties a source or curator states for a class ITSELF, one row per (class, property) '
    'with the contributing sources: class x class rules on both classes, drug -> class rules on '
    'the object class (interacts with that drug), and live holds class rulings. Rules a curator '
    'rules applies = false are dropped record by record. Excludes anything inferred from member '
    'counts (the observed route) and every expanded pair view.';
