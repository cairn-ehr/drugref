-- db/065_class_property_pushable.sql -- a one-class read costs one class (#202), and
-- what a subclass-only drug inherits is a view (#209).
--
-- db/058-064 are applied, so they are never edited; class_property is CREATE OR REPLACE
-- with its column list UNCHANGED, so the views built on it survive.
--
-- 1. class_property, same rows, new shape. db/064's CTEs were each referenced more than
--    once, so Postgres MATERIALISED them, and a `WHERE class_uuid = X` was applied only
--    at the end: a one-class read joined every class's members to every property they
--    have (~450k rows) and counted them all. Now no subquery is referenced twice, so
--    nothing is materialised and the planner pushes `class_uuid = X` down to each of the
--    three scans of class_membership (members, member count, self-partner):
--      - asserted rows and carried rows meet in ONE UNION ALL (`via`/`src` filled on
--        the asserted rows, `carrier` on the carried ones) and ONE GROUP BY, instead of
--        a keys UNION followed by LEFT JOINs back to each CTE;
--      - the self-partner test (#206) is a LEFT JOIN, not an EXISTS (an EXISTS in a
--        select list becomes a hashed subplan over every member of every class).
--    What still runs in full on a filtered read: member_property and class_subtree (a
--    recursive view), each a few tens of ms on drugref_profiles. Verified on
--    drugref_profiles: identical rows (EXCEPT both ways, all 9,668), and identical
--    class_property_member, class_member_peculiarity and gap_unconfirmed_class_property.
-- 2. class_subclass_only_property (#209): the subtraction class_profile_read did in
--    Python, so a front end reading the views gets it without re-implementing it.

-- ---------------------------------------------------------------------------
-- 1. class_property (same columns and rows as db/064)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW drugref.class_property AS
SELECT g.class_uuid, g.relationship, g.object_kind, g.object_uuid,
       g.is_asserted, g.asserted_via, g.asserted_by,
       (g.members >= 2 AND g.carriers * 4 >= g.members * 3) AS is_observed,
       g.carriers, g.members
FROM (
    SELECT u.class_uuid, u.relationship, u.object_kind, u.object_uuid,
           bool_or(u.via IS NOT NULL) AS is_asserted,
           COALESCE(array_agg(DISTINCT u.via ORDER BY u.via)
                    FILTER (WHERE u.via IS NOT NULL), '{}'::uuid[]) AS asserted_via,
           COALESCE(array_agg(DISTINCT u.src ORDER BY u.src)
                    FILTER (WHERE u.src IS NOT NULL), '{}'::text[]) AS asserted_by,
           count(u.carrier)::int AS carriers,
           -- ELIGIBLE members (#206): less the object when it is itself a direct member.
           COALESCE(max(n.members), 0) - bool_or(sp.class_uuid IS NOT NULL)::int AS members
    FROM (
        -- ASSERTED: one row per (class, property, asserting class at or above it, source).
        -- MED-RT PK classes are excluded as the class and as the asserting ancestor.
        SELECT st.class_uuid, a.relationship, a.object_kind, a.object_uuid,
               a.class_uuid AS via, s AS src, NULL::uuid AS carrier
        FROM   drugref.class_subtree st
        JOIN   drugref.class_asserted_property a ON a.class_uuid = st.root_uuid
        CROSS  JOIN LATERAL unnest(a.asserted_by) s
        JOIN   drugref.substance_class sc ON sc.class_uuid = st.class_uuid
        JOIN   drugref.substance_class sr ON sr.class_uuid = st.root_uuid
        WHERE  NOT (sc.source = 'MED-RT' AND sc.concept_type = 'PK')
          AND  NOT (sr.source = 'MED-RT' AND sr.concept_type = 'PK')
        UNION ALL
        -- CARRIED: one row per (direct member, property it has). class_profile_member
        -- is DISTINCT and member_property one row per property, so count = carriers.
        SELECT m.class_uuid, p.relationship, p.object_kind, p.object_uuid,
               NULL, NULL, m.moiety_uuid
        FROM   drugref.class_profile_member m
        JOIN   drugref.member_property p USING (moiety_uuid)
    ) u
    LEFT JOIN (SELECT class_uuid, count(*)::int AS members
               FROM   drugref.class_profile_member GROUP BY class_uuid) n
           ON n.class_uuid = u.class_uuid
    -- At most one match per row (class_profile_member is distinct), so no fan-out.
    LEFT JOIN drugref.class_profile_member sp
           ON sp.class_uuid = u.class_uuid AND u.object_kind = 'moiety'
          AND sp.moiety_uuid = u.object_uuid
    GROUP BY u.class_uuid, u.relationship, u.object_kind, u.object_uuid
) g
-- Observed = at least two eligible members, and at least 75% of them carry it.
WHERE g.is_asserted OR (g.members >= 2 AND g.carriers * 4 >= g.members * 3);

-- CREATE OR REPLACE VIEW keeps db/064's COMMENT, which is still true word for word.

-- ---------------------------------------------------------------------------
-- 2. class_subclass_only_property (#209)
-- ---------------------------------------------------------------------------
-- Spec section 4: a drug reaching class C only through a subclass inherits what is
-- ASSERTED on C (or its ancestors) -- never what is observed over C's own members --
-- less its confirmed exceptions, and never an interaction with itself. The same
-- precedence as class_property_member, with no carries/not_confirmed: the drug is not
-- one of C's members, so C's count says nothing about it.
CREATE OR REPLACE VIEW drugref.class_subclass_only_property AS
SELECT so.class_uuid, so.moiety_uuid, so.via_class_uuid,
       cp.relationship, cp.object_kind, cp.object_uuid,
       CASE
         WHEN EXISTS (SELECT 1 FROM drugref.member_property_negation n
                      WHERE n.moiety_uuid = so.moiety_uuid
                        AND n.relationship = cp.relationship
                        AND n.object_kind = cp.object_kind
                        AND n.object_uuid = cp.object_uuid)
              THEN 'confirmed_exception'
         ELSE 'inherits'
       END AS standing
FROM   drugref.class_subclass_only_member so
JOIN   drugref.class_property cp ON cp.class_uuid = so.class_uuid AND cp.is_asserted
WHERE  NOT (cp.object_kind = 'moiety' AND cp.object_uuid = so.moiety_uuid);

COMMENT ON VIEW drugref.class_subclass_only_property IS
    'What a SUBCLASS-ONLY drug (class_subclass_only_member: reaches the class only through '
    'a descendant) has of the class''s profile: one row per (class, drug, via class, '
    'ASSERTED property), standing inherits or confirmed_exception (a curator says it does '
    'not hold for this drug). OBSERVED properties are NOT here: they are a count over the '
    'class''s own direct members and never pass down. No row for an interaction with the '
    'drug itself. A drug in two subclasses has one row per via class. A drug with no row '
    'here inherits nothing from this class, which is NOT "safe".';
