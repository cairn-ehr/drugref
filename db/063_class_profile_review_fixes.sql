-- db/063_class_profile_review_fixes.sql -- class profiles, pre-merge review fixes.
--
-- db/058-062 are applied, so they are never edited; the view below is CREATE OR REPLACE
-- with its column list UNCHANGED, so the views built on it survive.
--
-- 1. curated_property_ruling: a drug-to-drug ruling is stored with the LOWER uuid as the
--    subject (curation_property orders the pair). Interactions are symmetric, so without
--    this (X, Y) and (Y, X) are two natural keys that can both be live: a later "holds"
--    recorded from Y's side would silently lose to an earlier "does_not_hold" from X's.
--    The comparison is strict, so a drug ruled to interact with ITSELF is refused too
--    (self-pairs are excluded everywhere else on the read path).
-- 2. class_property_member: a member has no standing against "interacts_with <itself>".
--    "C interacts with x" is a fair class property when x is one of C's members (the
--    others interact with x), but asking whether x interacts with x is meaningless; it
--    showed up as "inherits" in x's profile and as self-questions on the worklist.
-- 3. COMMENTs: class_asserted_property names both ways a curator withdraws a row.

-- ---------------------------------------------------------------------------
-- 1. Drug pairs in one orientation, never a self-pair
-- ---------------------------------------------------------------------------
ALTER TABLE drugref.curated_property_ruling
    DROP CONSTRAINT IF EXISTS curated_property_ruling_drug_pair_ordered;
ALTER TABLE drugref.curated_property_ruling
    ADD CONSTRAINT curated_property_ruling_drug_pair_ordered CHECK (
        subject_moiety_uuid IS NULL OR object_moiety_uuid IS NULL
        OR subject_moiety_uuid < object_moiety_uuid);

-- ---------------------------------------------------------------------------
-- 2. class_property_member (body otherwise identical to db/060)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW drugref.class_property_member AS
SELECT cp.class_uuid, cp.relationship, cp.object_kind, cp.object_uuid,
       m.moiety_uuid,
       CASE
         WHEN EXISTS (SELECT 1 FROM drugref.member_property_negation n
                      WHERE n.moiety_uuid = m.moiety_uuid AND n.relationship = cp.relationship
                        AND n.object_kind = cp.object_kind AND n.object_uuid = cp.object_uuid)
              THEN 'confirmed_exception'
         WHEN EXISTS (SELECT 1 FROM drugref.member_property p
                      WHERE p.moiety_uuid = m.moiety_uuid AND p.relationship = cp.relationship
                        AND p.object_kind = cp.object_kind AND p.object_uuid = cp.object_uuid)
              THEN 'carries'
         WHEN cp.is_asserted THEN 'inherits'
         ELSE 'not_confirmed'
       END AS standing
FROM   drugref.class_property cp
JOIN   drugref.class_profile_member m ON m.class_uuid = cp.class_uuid
-- A drug is never asked about an interaction with itself.
WHERE  NOT (cp.object_kind = 'moiety' AND cp.object_uuid = m.moiety_uuid);

-- CREATE OR REPLACE VIEW keeps the comment; re-issued with the self-pair rule added.
COMMENT ON VIEW drugref.class_property_member IS
    'Each DIRECT member''s standing against each class property: confirmed_exception (a curator '
    'says it does not hold) > carries (its own stated record) > inherits (asserted on the class '
    'or an ancestor) > not_confirmed (observed, but nothing for this drug; NOT "safe"). Subclass-'
    'only drugs have no rows here (see class_subclass_only_member), and no member has a row for '
    'an interaction with itself. RENDERING A DRUG D THROUGH '
    'LENS L (one of D''s class_profile_member classes) is a FOUR-WAY split: SHARED = rows here '
    'for (L, D) with standing carries or inherits; NOT CONFIRMED FOR THIS DRUG = standing '
    'not_confirmed; CONFIRMED EXCEPTION = standing confirmed_exception; PECULIAR = '
    'class_member_peculiarity rows for (L, D). A consumer MUST render the not_confirmed rows, '
    'never drop them: dropped, the gap reads as "does not apply" (spec section 9).';

-- ---------------------------------------------------------------------------
-- 3. Re-issued COMMENT
-- ---------------------------------------------------------------------------
COMMENT ON VIEW drugref.class_asserted_property IS
    'Properties a source or curator states for a class ITSELF, one row per (class, property) '
    'with the contributing sources: class x class rules on both classes, drug -> class rules on '
    'the object class (interacts with that drug), and live holds class rulings. A curator '
    'withdraws a row two ways: a live curated applies = false drops the matching source record '
    '(one record at a time; another source may still state it), and a drug''s live '
    'does_not_hold ruling on (interacts_with, class C) removes C''s row for that drug. Excludes '
    'anything inferred from member counts (the observed route) and every expanded pair view. A '
    'class-subject does_not_hold ruling only WITHDRAWS that class''s own earlier holds: it does '
    'not suppress an ancestor''s assertion passing down, nor an observed property. '
    'curated_property_ruling interaction rulings change these views only and do NOT reach the '
    'prescribing-alert path (exact_ddi_pair, ddi_candidate_pair, curated_ddi_pair) -- #204.';
