-- db/060_class_profile_class_property.sql -- class profiles, task 3: the class-level views.
--
-- Design: docs/superpowers/specs/2026-11-07-drugref-class-profiles-design.md (sections 2-5).
-- Four READ-ONLY views on top of db/059, no new tables:
--   class_property             what a class has: asserted and/or observed, with counts
--   class_property_member      each direct member's standing against each class property
--   class_member_peculiarity   what a member has that its class does not
--   class_subclass_only_member drugs that reach a class only through a subclass
--
-- Two routes to "the class has property P", deliberately asymmetric:
--   ASSERTED  a source/curator states P about the class or any ANCESTOR. It passes DOWN, so a
--             drug that sits only in a subclass still inherits it.
--   OBSERVED  at least 75% of the class's own DIRECT members (and at least two of them) carry P.
--             Computed from the class's own members only and NEVER inherited: a statistic over
--             beta-blockers says nothing certain about the subclass it was not computed over.
-- MED-RT PK classes (not drug classes, see db/059) are excluded everywhere, including as an
-- ancestor an assertion would arrive from.

-- ---------------------------------------------------------------------------
-- 1. class_property
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW drugref.class_property AS
WITH profiled AS (            -- the classes we profile at all
    SELECT class_uuid FROM drugref.substance_class
    WHERE  NOT (source = 'MED-RT' AND concept_type = 'PK')
), n_members AS (             -- DISTINCT direct members (class_profile_member is distinct)
    SELECT class_uuid, count(*)::int AS members
    FROM   drugref.class_profile_member GROUP BY class_uuid
), carried AS (               -- how many direct members hold each property
    SELECT m.class_uuid, p.relationship, p.object_kind, p.object_uuid,
           count(*)::int AS carriers
    FROM   drugref.class_profile_member m
    JOIN   drugref.member_property p USING (moiety_uuid)
    GROUP  BY m.class_uuid, p.relationship, p.object_kind, p.object_uuid
), asserted AS (              -- on the class itself or any proper-or-equal ancestor
    SELECT st.class_uuid, a.relationship, a.object_kind, a.object_uuid,
           array_agg(DISTINCT a.class_uuid ORDER BY a.class_uuid) AS asserted_via,
           (SELECT array_agg(DISTINCT s ORDER BY s)
              FROM drugref.class_asserted_property a2, unnest(a2.asserted_by) s
             WHERE a2.relationship = a.relationship AND a2.object_kind = a.object_kind
               AND a2.object_uuid = a.object_uuid
               AND a2.class_uuid IN (SELECT root_uuid FROM drugref.class_subtree
                                      WHERE class_uuid = st.class_uuid)) AS asserted_by
    FROM   drugref.class_subtree st
    JOIN   drugref.class_asserted_property a ON a.class_uuid = st.root_uuid
    WHERE  st.class_uuid IN (SELECT class_uuid FROM profiled)
      AND  st.root_uuid  IN (SELECT class_uuid FROM profiled)
    GROUP  BY st.class_uuid, a.relationship, a.object_kind, a.object_uuid
), observed AS (
    SELECT c.*, true AS is_observed
    FROM   carried c JOIN n_members n USING (class_uuid)
    WHERE  n.members >= 2 AND c.carriers * 4 >= n.members * 3
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
       COALESCE(n.members, 0)                      AS members
FROM   keys k
LEFT   JOIN asserted a USING (class_uuid, relationship, object_kind, object_uuid)
LEFT   JOIN observed o USING (class_uuid, relationship, object_kind, object_uuid)
LEFT   JOIN carried  c USING (class_uuid, relationship, object_kind, object_uuid)
LEFT   JOIN n_members n USING (class_uuid);

COMMENT ON VIEW drugref.class_property IS
    'What a class has, one row per (class, property) that is asserted, observed, or both. '
    'ASSERTED: stated about the class or any ancestor (asserted_via lists which, sorted; the '
    'class itself counts), so it passes DOWN to subclass-only drugs. OBSERVED: members >= 2 and '
    'carriers * 4 >= members * 3 over the class''s own DIRECT members only; never inherited. '
    'carriers/members are filled on every row (0 where none). Exact object match: no condition '
    'hierarchy pooling. MED-RT PK classes excluded. A property absent here is NOT CONFIRMED, '
    'never "safe".';

-- ---------------------------------------------------------------------------
-- 2. class_property_member -- standing of each direct member
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
JOIN   drugref.class_profile_member m ON m.class_uuid = cp.class_uuid;

COMMENT ON VIEW drugref.class_property_member IS
    'Each DIRECT member''s standing against each class property: confirmed_exception (a curator '
    'says it does not hold) > carries (its own stated record) > inherits (asserted on the class '
    'or an ancestor) > not_confirmed (observed, but nothing for this drug; NOT "safe"). Subclass-'
    'only drugs have no rows here (see class_subclass_only_member). RENDERING A DRUG D THROUGH '
    'LENS L (one of D''s class_profile_member classes): SHARED = rows here for (L, D) with '
    'standing carries or inherits; PECULIAR = class_member_peculiarity rows for (L, D).';

-- ---------------------------------------------------------------------------
-- 3. class_member_peculiarity
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW drugref.class_member_peculiarity AS
SELECT m.class_uuid, m.moiety_uuid, p.relationship, p.object_kind, p.object_uuid
FROM   drugref.class_profile_member m
JOIN   drugref.member_property p USING (moiety_uuid)
WHERE  NOT EXISTS (SELECT 1 FROM drugref.class_property cp
                   WHERE cp.class_uuid = m.class_uuid AND cp.relationship = p.relationship
                     AND cp.object_kind = p.object_kind AND cp.object_uuid = p.object_uuid);

COMMENT ON VIEW drugref.class_member_peculiarity IS
    'What a direct member has that its class does not: the member''s effective properties that '
    'are not a class_property of that class. RENDERING A DRUG D THROUGH LENS L: PECULIAR = the '
    'rows here for (L, D); SHARED = class_property_member rows for (L, D) with standing carries '
    'or inherits.';

-- ---------------------------------------------------------------------------
-- 4. class_subclass_only_member
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW drugref.class_subclass_only_member AS
SELECT DISTINCT st.root_uuid AS class_uuid, m.moiety_uuid, st.class_uuid AS via_class_uuid
FROM   drugref.class_subtree st
JOIN   drugref.class_profile_member m ON m.class_uuid = st.class_uuid
WHERE  st.class_uuid <> st.root_uuid
  AND  st.root_uuid IN (SELECT class_uuid FROM drugref.substance_class
                        WHERE NOT (source = 'MED-RT' AND concept_type = 'PK'))
  AND  NOT EXISTS (SELECT 1 FROM drugref.class_profile_member d
                   WHERE d.class_uuid = st.root_uuid AND d.moiety_uuid = m.moiety_uuid);

COMMENT ON VIEW drugref.class_subclass_only_member IS
    'Drugs that are direct members of a proper descendant of the class but not of the class '
    'itself, with the descendant they came through (one row per route). Listed, never counted '
    'in class_property.members, and never given an observed property; they inherit only what is '
    'ASSERTED on the class or an ancestor.';
