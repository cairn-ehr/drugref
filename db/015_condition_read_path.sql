-- db/015_condition_read_path.sql
-- Read-time descendant expansion for drug-condition contraindications.
--
-- The same shape as db/012's ci_class_subtree + ddi_candidate_pair, over a different
-- DAG. Deliberately NOT the same view: this walks condition_parent (MeSH conditions),
-- not class_parent (substance classes), so it is a second walk over a second graph --
-- not the duplication db/012 removed, which was three copies of ONE walk.

CREATE OR REPLACE VIEW drugref.condition_subtree AS
WITH RECURSIVE subtree(root_uuid, condition_uuid) AS (
    SELECT DISTINCT ci.object_condition_uuid, ci.object_condition_uuid
    FROM   drugref.moiety_condition_contraindication ci
  UNION
    SELECT s.root_uuid, cp.child_condition_uuid
    FROM   subtree s
    JOIN   drugref.condition_parent cp ON cp.parent_condition_uuid = s.condition_uuid
)
SELECT root_uuid, condition_uuid FROM subtree;

COMMENT ON VIEW drugref.condition_subtree IS
    'For every condition a contraindication NAMES: that condition and every one '
    'below it in the condition DAG. THE ROOT IS INCLUDED IN ITS OWN SUBTREE -- '
    'condition_contraindication_expanded''s `is_direct` depends on it. Deduped on '
    '(root, condition) rather than on paths, so it terminates under a cycle (db/013 '
    'forbids only self-parenting) and stays linear in a multi-parent DAG, where '
    '1,690 of the registry''s 5,203 conditions (5,190 descriptors + 13 tree-less '
    'SCRs) have several parents. Scoped to CONTRAINDICATED '
    'conditions: a condition no rule names is ABSENT, not present with only itself.';
COMMENT ON COLUMN drugref.condition_subtree.root_uuid IS
    'The contraindicated condition the walk started from -- what a rule NAMES.';
COMMENT ON COLUMN drugref.condition_subtree.condition_uuid IS
    'A condition at or below root_uuid. Equal to root_uuid for exactly one row per root.';

CREATE OR REPLACE VIEW drugref.condition_contraindication_expanded AS
SELECT ci.subject_moiety_uuid    AS subject_moiety,
       ci.object_condition_uuid  AS object_condition,
       s.condition_uuid          AS member_condition,
       s.condition_uuid = ci.object_condition_uuid AS is_direct,
       ci.relationship,
       ci.source
FROM   drugref.moiety_condition_contraindication ci
JOIN   drugref.condition_ci_axis a ON a.relationship = ci.relationship
JOIN   drugref.condition_subtree s ON s.root_uuid    = ci.object_condition_uuid
-- Expansion is per predicate. When a predicate does not expand, only the named
-- condition survives -- so switching it off is ONE UPDATE and needs no view edit.
WHERE  a.expands_descendants
   OR  s.condition_uuid = ci.object_condition_uuid;

COMMENT ON VIEW drugref.condition_contraindication_expanded IS
    'Drug-condition contraindications, expanded down the condition DAG: the subject '
    'moiety is contraindicated in a patient whose condition is member_condition, '
    'because a rule named object_condition at or above it. DIRECTIONAL and CANDIDATE '
    'TIER -- rows feed review and must not auto-alert, and MED-RT asserts no severity, '
    'so this is never a hard stop on its own. `WHERE is_direct` reproduces the '
    'unexpanded row set exactly, so a precision-sensitive consumer opts out '
    'EXPLICITLY and a consumer who forgets errs toward recall. EXPANSION WIDENS '
    'RECALL, NOT CERTAINTY: a row with is_direct = false was written against an '
    'ancestor of the patient''s coded condition, which is why member_condition and '
    'is_direct are columns rather than an internal detail.';
COMMENT ON COLUMN drugref.condition_contraindication_expanded.object_condition IS
    'The condition the RULE named -- provenance for a non-direct match.';
COMMENT ON COLUMN drugref.condition_contraindication_expanded.member_condition IS
    'The condition actually matched: at or below object_condition.';
