-- db/062_class_profile_fixes.sql -- class profiles, final review fixes.
--
-- db/058-061 are applied (drugref_profiles carries their checksums), so they are never
-- edited; every view below is CREATE OR REPLACE with its column list UNCHANGED, so the
-- views built on it survive.
--
-- 1. class_property: asserted_by is aggregated in the grouped `asserted` CTE instead of
--    by a correlated subquery. The subquery walked class_subtree WITHOUT the `profiled`
--    filter, so a MED-RT PK ancestor's sources leaked into asserted_by; it also ran once
--    per row (3.37 s of a 3.67 s single-class read on drugref_profiles, #202).
-- 2. gap_unconfirmed_class_property: closed by member_ruling_oriented, so a ruling
--    recorded from the partner's side (Y, interacts_with, moiety, D) closes D's question.
-- 3. COMMENTs re-issued: the four-way drug-through-a-lens recipe, the alert-path limit of
--    interaction rulings (#204), and what a class-subject does_not_hold does NOT do.

-- ---------------------------------------------------------------------------
-- 1. class_property (body otherwise identical to db/060)
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
    'class itself counts; asserted_by the sources of exactly those), so it passes DOWN to '
    'subclass-only drugs. OBSERVED: members >= 2 and carriers * 4 >= members * 3 over the '
    'class''s own DIRECT members only; never inherited. carriers/members are filled on every '
    'row (0 where none). Exact object match: no condition hierarchy pooling. MED-RT PK classes '
    'excluded, as a class and as an ancestor. A property absent here is NOT CONFIRMED, never '
    '"safe".';

-- ---------------------------------------------------------------------------
-- 2. gap_unconfirmed_class_property (body otherwise identical to db/061)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW drugref.gap_unconfirmed_class_property AS
SELECT pm.moiety_uuid, pm.relationship, pm.object_kind, pm.object_uuid,
       array_agg(DISTINCT pm.class_uuid ORDER BY pm.class_uuid) AS raising_classes
FROM   drugref.class_property_member pm
WHERE  pm.standing = 'not_confirmed'
  -- member_ruling_oriented is already live-only and moiety-only, and lists every
  -- drug-to-drug interaction ruling from BOTH drugs' sides.
  AND  NOT EXISTS (SELECT 1 FROM drugref.member_ruling_oriented r
                   WHERE r.moiety_uuid = pm.moiety_uuid
                     AND r.relationship = pm.relationship
                     AND r.object_kind = pm.object_kind AND r.object_uuid = pm.object_uuid)
GROUP  BY pm.moiety_uuid, pm.relationship, pm.object_kind, pm.object_uuid;

COMMENT ON VIEW drugref.gap_unconfirmed_class_property IS
    'Gap kind unconfirmed_class_property: one row per (member drug, property) that is '
    'not_confirmed in at least one class and has no live moiety ruling of any verdict, read '
    'through member_ruling_oriented so an interaction ruled from the partner''s side counts. '
    'raising_classes (sorted) lists the classes that raise it. "Not confirmed" is NOT "safe". '
    'Answering an interacts_with question does not change prescribing alerts (#204). '
    'FROZEN GAP KEY, forever once released: '
    'CLASSPROP:<moiety_uuid>:<relationship>:<object_kind>:<object_uuid>.';

-- ---------------------------------------------------------------------------
-- 3. Re-issued COMMENTs
-- ---------------------------------------------------------------------------
COMMENT ON VIEW drugref.class_property_member IS
    'Each DIRECT member''s standing against each class property: confirmed_exception (a curator '
    'says it does not hold) > carries (its own stated record) > inherits (asserted on the class '
    'or an ancestor) > not_confirmed (observed, but nothing for this drug; NOT "safe"). Subclass-'
    'only drugs have no rows here (see class_subclass_only_member). RENDERING A DRUG D THROUGH '
    'LENS L (one of D''s class_profile_member classes) is a FOUR-WAY split: SHARED = rows here '
    'for (L, D) with standing carries or inherits; NOT CONFIRMED FOR THIS DRUG = standing '
    'not_confirmed; CONFIRMED EXCEPTION = standing confirmed_exception; PECULIAR = '
    'class_member_peculiarity rows for (L, D). A consumer MUST render the not_confirmed rows, '
    'never drop them: dropped, the gap reads as "does not apply" (spec section 9).';

COMMENT ON VIEW drugref.class_member_peculiarity IS
    'What a direct member has that its class does not: the member''s effective properties that '
    'are not a class_property of that class. RENDERING A DRUG D THROUGH LENS L is a FOUR-WAY '
    'split: PECULIAR = the rows here for (L, D); and from class_property_member for (L, D): '
    'SHARED = standing carries or inherits, NOT CONFIRMED FOR THIS DRUG = not_confirmed, '
    'CONFIRMED EXCEPTION = confirmed_exception. A consumer MUST render the not_confirmed rows, '
    'never drop them: dropped, the gap reads as "does not apply" (spec section 9).';

COMMENT ON VIEW drugref.member_property IS
    'The effective stated properties of a drug, one row per (drug, relationship, object) with '
    'the distinct contributing sources sorted. Two sources for one fact count once. Records a '
    'live curated applies = false negates are dropped one by one; a live does_not_hold ruling '
    'removes the whole property; a live holds ruling adds it. Excludes class-expanded pairs '
    '(ddi_candidate_pair, curated_ddi_pair) and SPL co-mentions (spl_ddi_pair). '
    'curated_property_ruling interaction rulings change THESE VIEWS ONLY: they do NOT reach the '
    'prescribing-alert path (exact_ddi_pair, ddi_candidate_pair, curated_ddi_pair) -- #204.';

COMMENT ON VIEW drugref.class_asserted_property IS
    'Properties a source or curator states for a class ITSELF, one row per (class, property) '
    'with the contributing sources: class x class rules on both classes, drug -> class rules on '
    'the object class (interacts with that drug), and live holds class rulings. Rules a curator '
    'rules applies = false are dropped record by record. Excludes anything inferred from member '
    'counts (the observed route) and every expanded pair view. A class-subject does_not_hold '
    'ruling only WITHDRAWS that class''s own earlier holds: it does not suppress an ancestor''s '
    'assertion passing down, nor an observed property. curated_property_ruling interaction '
    'rulings change these views only and do NOT reach the prescribing-alert path '
    '(exact_ddi_pair, ddi_candidate_pair, curated_ddi_pair) -- #204.';
