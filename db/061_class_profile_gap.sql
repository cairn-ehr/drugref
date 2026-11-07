-- db/061: the nineteenth question kind, unconfirmed_class_property.
--
-- WHY: class_property_member (db/060) says 'not_confirmed' when a property is common in a
-- member's class but nothing is recorded for that drug. That is absence of evidence, NOT
-- safety, so it goes on the curators' worklist. One question per (member, property): the
-- fact is about the DRUG, not the class, so two classes raising it still ask once.
-- ANY live curated_property_ruling closes it. 'cannot_establish' deliberately leaves the
-- member 'not_confirmed' (a real answer: "looked, sources do not settle it") but off the
-- worklist, so curators are not asked the same thing forever.

CREATE OR REPLACE VIEW drugref.gap_unconfirmed_class_property AS
SELECT pm.moiety_uuid, pm.relationship, pm.object_kind, pm.object_uuid,
       array_agg(DISTINCT pm.class_uuid ORDER BY pm.class_uuid) AS raising_classes
FROM   drugref.class_property_member pm
WHERE  pm.standing = 'not_confirmed'
  AND  NOT EXISTS (SELECT 1 FROM drugref.curated_property_ruling r
                   WHERE r.superseded_by IS NULL
                     AND r.subject_kind = 'moiety' AND r.subject_uuid = pm.moiety_uuid
                     AND r.relationship = pm.relationship
                     AND r.object_kind = pm.object_kind AND r.object_uuid = pm.object_uuid)
GROUP  BY pm.moiety_uuid, pm.relationship, pm.object_kind, pm.object_uuid;

COMMENT ON VIEW drugref.gap_unconfirmed_class_property IS
    'Gap kind unconfirmed_class_property: one row per (member drug, property) that is '
    'not_confirmed in at least one class and has no live curated_property_ruling of any '
    'verdict. raising_classes (sorted) lists the classes that raise it. "Not confirmed" is '
    'NOT "safe". FROZEN GAP KEY, forever once released: '
    'CLASSPROP:<moiety_uuid>:<relationship>:<object_kind>:<object_uuid>.';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%unconfirmed_class_property%') THEN
        ALTER TABLE drugref.open_question DROP CONSTRAINT IF EXISTS open_question_gap_kind;
        ALTER TABLE drugref.open_question ADD CONSTRAINT open_question_gap_kind
            CHECK (gap_kind IN (
                -- COPIED VERBATIM from the live catalog (18 kinds), then extended by one.
                'unpopulated_contraindication', 'unclassified_moiety',
                'unmatched_ingredient', 'unreviewed_expansion_root',
                'unresolved_ci_object', 'dead_by_expansion_policy',
                'condition_without_indication', 'uncurated_additive_effect',
                'uncurated_threshold', 'ineffective_contribution',
                'ungraded_contribution', 'unruled_composition_activity',
                'uncurated_condition_contradiction', 'uncurated_interaction_rule',
                'unresolved_onc_endpoint', 'uncurated_class_interaction_rule',
                'fda_cyp_unadjudicated', 'unresolved_ddi_endpoint',
                'unconfirmed_class_property'));
    END IF;
END $$;
