-- db/068_allergy_check.sql
-- =============================================================================
-- The allergy read: given a PRESCRIBED moiety and a recorded ALLERGEN (a moiety
-- or an allergy group's class_uuid), answer match / no-match / unplaced, with the
-- path. Design: docs/superpowers/specs/2026-11-08-drugref-allergy-groups-design.md
-- section 4.3. cairn (same Postgres) calls drugref.allergy_check directly;
-- src/drugref/allergy_read.py is a thin Python wrapper over it.
--
-- THE HARM DIRECTION IS `no-match`. A wrong no-match is a missed allergy warning,
-- so every path that cannot actually be checked answers `unplaced` (cairn shows
-- "not checked"), and `no-match` is reserved for "both sides placed in MeSH,
-- nothing shared".
-- =============================================================================

-- ============================================================================
-- 1. The fold -- EVERY component, whatever is_active_component says
-- ============================================================================
-- NOT moiety_active_in_composite (db/028), which keeps only components GSRS marks
-- ACTIVE. GSRS's "active" is PHARMACOLOGICAL activity, not what triggers an
-- allergy: clemizole penicillin marks penicillin G FALSE, and an active-only fold
-- would answer a penicillin allergen with a silent no-match (owner, 2026-11-09).
-- The accepted trade: a salt's partner matches too (penicillin G procaine warns a
-- procaine-allergic patient), and the path names the component that matched.
--
-- UNION, not UNION ALL: a component reached by two relations is one fold member.
CREATE OR REPLACE VIEW drugref.moiety_allergy_fold AS
SELECT moiety_uuid, moiety_uuid AS component_moiety      -- every moiety folds to itself
FROM drugref.substance_moiety
UNION
SELECT ic.moiety_uuid, sc.component_moiety               -- ...and to every component
FROM drugref.identity_claim ic                           -- of a substance it IS (by
JOIN drugref.substance_composition sc                    -- a live UNII claim)
     ON sc.substance_unii = ic.value
WHERE ic.scheme = 'UNII'
  AND ic.superseded_by IS NULL;

COMMENT ON VIEW drugref.moiety_allergy_fold IS
    'The allergy fold: each moiety, paired with itself and with EVERY '
    'substance_composition component of any live UNII claim it holds, whatever '
    'is_active_component says (TRUE, FALSE or NULL). Deliberately NOT '
    'moiety_active_in_composite: GSRS "active" is pharmacological, not what '
    'triggers an allergy (clemizole penicillin marks penicillin G FALSE). Read by '
    'drugref.allergy_check.';

-- ============================================================================
-- 2. allergy_check -- three outcomes, never two
-- ============================================================================
-- Evaluated in THIS order (spec 4.3; the order is the contract):
--   1. prescribed not a moiety                      -> unplaced / unknown-prescribed
--   2. allergen is a class:
--        not an allergy group                       -> unplaced / allergen-not-a-group
--        one same-group row per prescribed fold component that is a member;
--        with none: no-match if any prescribed component is placed,
--                   else                            -> unplaced / prescribed-not-in-mesh
--   3. allergen is neither class nor moiety         -> unplaced / unknown-allergen
--   4. allergen is a moiety: same-drug rows for every shared fold component, and
--      same-group rows for every group holding an allergen AND a prescribed
--      component. With no rows:
--        no allergen component in any group         -> unplaced / allergen-in-no-group
--        no prescribed component placed             -> unplaced / prescribed-not-in-mesh
--        else                                       -> no-match
-- "Placed" = in allergy_mesh_placement (the ingest puts every member there too).
--
-- SHAPE: match rows carry the path and a NULL reason; otherwise exactly ONE row
-- with NULL path columns and reason NULL iff outcome is no-match.
CREATE OR REPLACE FUNCTION drugref.allergy_check(p_prescribed uuid, p_allergen uuid)
RETURNS TABLE (outcome              text,
               match_kind           text,
               reason               text,
               group_class_uuid     uuid,
               group_name           text,
               group_mesh_ui        text,
               review_status        text,
               allergen_component   uuid,
               prescribed_component uuid)
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $$
    WITH
    -- The two folds. The allergen fold is EMPTY unless the allergen is a moiety,
    -- because the view's rows all start from substance_moiety / identity_claim.
    pf AS (SELECT component_moiety AS c FROM drugref.moiety_allergy_fold
           WHERE moiety_uuid = p_prescribed),
    af AS (SELECT component_moiety AS c FROM drugref.moiety_allergy_fold
           WHERE moiety_uuid = p_allergen),
    facts AS (
        SELECT
          EXISTS (SELECT 1 FROM drugref.substance_moiety
                  WHERE moiety_uuid = p_prescribed)                   AS p_known,
          EXISTS (SELECT 1 FROM drugref.substance_class
                  WHERE class_uuid = p_allergen)                      AS a_is_class,
          EXISTS (SELECT 1 FROM drugref.allergy_group
                  WHERE class_uuid = p_allergen)                      AS a_is_group,
          EXISTS (SELECT 1 FROM drugref.substance_moiety
                  WHERE moiety_uuid = p_allergen)                     AS a_is_moiety,
          EXISTS (SELECT 1 FROM pf JOIN drugref.allergy_mesh_placement pl
                  ON pl.moiety_uuid = pf.c)                           AS p_placed,
          EXISTS (SELECT 1 FROM af JOIN drugref.allergy_group_member gm
                  ON gm.moiety_uuid = af.c)                           AS a_in_group
    ),
    -- DISTINCT because a moiety can be a member through several `via` rows.
    hits AS (
        -- Rule 2: the allergen IS a group (allergen_component NULL: no fold).
        SELECT 'same-group' AS kind, gm.class_uuid, NULL::uuid AS a_c, pf.c AS p_c
        FROM pf JOIN drugref.allergy_group_member gm
             ON gm.moiety_uuid = pf.c AND gm.class_uuid = p_allergen
        UNION
        -- Rule 4a: same drug -- the two folds share a moiety.
        SELECT 'same-drug', NULL::uuid, af.c, pf.c
        FROM af JOIN pf ON pf.c = af.c
        UNION
        -- Rule 4b: same group -- a group holds a component from each side.
        SELECT 'same-group', ga.class_uuid, af.c, pf.c
        FROM af
        JOIN drugref.allergy_group_member ga ON ga.moiety_uuid = af.c
        JOIN drugref.allergy_group_member gp ON gp.class_uuid = ga.class_uuid
        JOIN pf ON pf.c = gp.moiety_uuid
    ),
    -- The no-hit answer, in the order above. reason NULL means no-match.
    verdict AS (
        SELECT CASE
                 WHEN NOT p_known                    THEN 'unknown-prescribed'
                 WHEN a_is_class AND NOT a_is_group  THEN 'allergen-not-a-group'
                 WHEN a_is_group AND NOT p_placed    THEN 'prescribed-not-in-mesh'
                 WHEN a_is_group                     THEN NULL
                 WHEN NOT a_is_moiety                THEN 'unknown-allergen'
                 WHEN NOT a_in_group                 THEN 'allergen-in-no-group'
                 WHEN NOT p_placed                   THEN 'prescribed-not-in-mesh'
               END AS reason
        FROM facts
    )
    SELECT 'match', h.kind, NULL::text, h.class_uuid, sc.class_name,
           ag.mesh_root_ui, ag.review_status, h.a_c, h.p_c
    FROM hits h
    LEFT JOIN drugref.substance_class sc ON sc.class_uuid = h.class_uuid
    LEFT JOIN drugref.allergy_group  ag ON ag.class_uuid = h.class_uuid
    UNION ALL
    SELECT CASE WHEN v.reason IS NULL THEN 'no-match' ELSE 'unplaced' END,
           NULL, v.reason, NULL, NULL, NULL, NULL, NULL, NULL
    FROM verdict v
    WHERE NOT EXISTS (SELECT 1 FROM hits)
    ORDER BY 2, 5, 8, 9
$$;

COMMENT ON FUNCTION drugref.allergy_check(uuid, uuid) IS
    'Allergy check for cairn: prescribed moiety vs recorded allergen (a moiety or '
    'an allergy group class_uuid). outcome is match | no-match | unplaced, never '
    'two: unplaced (with a reason: unknown-prescribed, unknown-allergen, '
    'allergen-not-a-group, allergen-in-no-group, prescribed-not-in-mesh) means '
    '"not checked" and must never be shown as safe. match rows (one per path) give '
    'match_kind same-drug | same-group, the group (class_uuid, name, MeSH root UI) '
    'and its review_status, and the '
    'fold components that matched on each side. Both sides are folded through '
    'moiety_allergy_fold (every composition component). KNOWN GAP: no '
    'penicillin-cephalosporin cross-allergy rule yet, so cephalexin vs penicillins '
    'answers no-match; that is not a statement that cephalosporins are safe.';
