-- db/064_class_property_eligible_members.sql -- the observed denominator (#206).
--
-- db/062's class_property counted an interaction property "interacts_with moiety Y"
-- over ALL direct members of the class. When Y is itself a direct member it can never
-- carry "interacts with Y" (db/063: no self-pairs), so it sat in the denominator and
-- never in the numerator: 3 of the 4 others interacting with Y read as 3 of 5 (60%, not
-- observed) instead of 3 of 4 (75%, observed).
--
-- Now `members` is the ELIGIBLE count for the row: the direct members minus Y when the
-- object is a moiety that is itself a direct member. The >= 2 floor and the 75% bar
-- both use it, and so does every row (asserted-only rows too), so `carriers of members`
-- always reads as a fraction of the drugs that could carry the property.
--
-- CREATE OR REPLACE with the column list UNCHANGED, so the views built on it survive.
-- Body otherwise identical to db/062.

CREATE OR REPLACE VIEW drugref.class_property AS
WITH profiled AS (            -- the classes we profile at all
    SELECT class_uuid FROM drugref.substance_class
    WHERE  NOT (source = 'MED-RT' AND concept_type = 'PK')
), n_members AS (             -- DISTINCT direct members (class_profile_member is distinct)
    SELECT class_uuid, count(*)::int AS members
    FROM   drugref.class_profile_member GROUP BY class_uuid
), self_partner AS (          -- (class, property object) pairs where the object IS a member
    SELECT class_uuid, 'moiety'::text AS object_kind, moiety_uuid AS object_uuid
    FROM   drugref.class_profile_member
), carried AS (               -- how many direct members hold each property
    SELECT m.class_uuid, p.relationship, p.object_kind, p.object_uuid,
           count(*)::int AS carriers
    FROM   drugref.class_profile_member m
    JOIN   drugref.member_property p USING (moiety_uuid)
    GROUP  BY m.class_uuid, p.relationship, p.object_kind, p.object_uuid
), asserted AS (              -- on the class itself or any proper-or-equal ancestor
    -- One row per (class, property, asserting ancestor, source); both arrays aggregate
    -- over the SAME filtered rows, so a PK ancestor can reach neither.
    SELECT st.class_uuid, a.relationship, a.object_kind, a.object_uuid,
           array_agg(DISTINCT a.class_uuid ORDER BY a.class_uuid) AS asserted_via,
           array_agg(DISTINCT s ORDER BY s)                       AS asserted_by
    FROM   drugref.class_subtree st
    JOIN   drugref.class_asserted_property a ON a.class_uuid = st.root_uuid
    CROSS  JOIN LATERAL unnest(a.asserted_by) s
    WHERE  st.class_uuid IN (SELECT class_uuid FROM profiled)
      AND  st.root_uuid  IN (SELECT class_uuid FROM profiled)
    GROUP  BY st.class_uuid, a.relationship, a.object_kind, a.object_uuid
), eligible AS (              -- carried, with the members that COULD carry it (#206)
    SELECT c.*, n.members - (sp.class_uuid IS NOT NULL)::int AS members
    FROM   carried c
    JOIN   n_members n USING (class_uuid)
    LEFT   JOIN self_partner sp USING (class_uuid, object_kind, object_uuid)
), observed AS (
    SELECT class_uuid, relationship, object_kind, object_uuid, true AS is_observed
    FROM   eligible
    WHERE  members >= 2 AND carriers * 4 >= members * 3
), keys AS (
    SELECT class_uuid, relationship, object_kind, object_uuid FROM asserted
    UNION
    SELECT class_uuid, relationship, object_kind, object_uuid FROM observed
)
SELECT k.class_uuid, k.relationship, k.object_kind, k.object_uuid,
       a.class_uuid IS NOT NULL                    AS is_asserted,
       COALESCE(a.asserted_via, '{}'::uuid[])      AS asserted_via,
       COALESCE(a.asserted_by,  '{}'::text[])      AS asserted_by,
       o.is_observed IS NOT NULL                   AS is_observed,
       COALESCE(c.carriers, 0)                     AS carriers,
       -- The SAME eligible count as `eligible`, for rows no member carries too.
       COALESCE(n.members, 0) - (sp.class_uuid IS NOT NULL)::int AS members
FROM   keys k
LEFT   JOIN asserted a USING (class_uuid, relationship, object_kind, object_uuid)
LEFT   JOIN observed o USING (class_uuid, relationship, object_kind, object_uuid)
LEFT   JOIN carried  c USING (class_uuid, relationship, object_kind, object_uuid)
LEFT   JOIN n_members n USING (class_uuid)
LEFT   JOIN self_partner sp USING (class_uuid, object_kind, object_uuid);

COMMENT ON VIEW drugref.class_property IS
    'What a class has, one row per (class, property) that is asserted, observed, or both. '
    'ASSERTED: stated about the class or any ancestor (asserted_via lists which, sorted; the '
    'class itself counts; asserted_by the sources of exactly those), so it passes DOWN to '
    'subclass-only drugs. OBSERVED: members >= 2 and carriers * 4 >= members * 3 over the '
    'class''s own DIRECT members only; never inherited. members is the ELIGIBLE count for '
    'the row: the direct members, less the object itself when it is a moiety that is a '
    'direct member (a drug never interacts with itself, #206). carriers/members are filled '
    'on every row (0 where none). Exact object match: no condition hierarchy pooling. MED-RT '
    'PK classes excluded, as a class and as an ancestor. A property absent here is NOT '
    'CONFIRMED, never "safe".';
