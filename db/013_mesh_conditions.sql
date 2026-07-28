-- db/013_mesh_conditions.sql
-- The MeSH CONDITION registry: the object side of a drug-condition contraindication.
--
-- WHY CONDITIONS ARE NOT substance_class ROWS (spec §2, tension A). MED-RT's CI_with
-- names the patient state a drug must not be given in. Measured against the real
-- 2026.07.06 release, that is a disease 10,091 times -- but also PREGNANCY and
-- LACTATION (786 assertions), a procedure (105), and the check tag "Female". Three
-- things follow, and each on its own is decisive:
--   * class_membership (moiety IS-A-MEMBER-OF class) is meaningless here. Nothing is
--     a member of pregnancy.
--   * substance_class's axis vocabulary (MoA/PE/TC/PK/EPC/APC/PA) is entirely
--     pharmacological. Filing "Coronary Artery Bypass" under it needs either a lie or
--     an axis meaning "not actually a substance class".
--   * substance_class currently MEANS "a class of substances", and that meaning is
--     load-bearing for the licence-scoping argument in ingest/medrt.py.
--
-- REBUILDABLE PROJECTION, like substance_class and deliberately outside slice 1's
-- append-only floor: a condition withdrawn upstream must be able to disappear.
-- Condition IDENTITY survives a rebuild by determinism -- condition_uuid is a pure
-- function of (source, source_code) -- so no pin table is needed.

CREATE TABLE IF NOT EXISTS drugref.condition (
    -- UUIDv5(CONDITION_NAMESPACE, source || ':' || source_code), minted by
    -- ids.mint_condition_uuid. Immortal, externally citable, and the join key of
    -- condition_parent -- so the derivation is frozen and pinned by a test literal.
    condition_uuid    uuid    PRIMARY KEY,
    -- Constrained for the reason db/003 constrains substance_class.source: the
    -- stored spelling and the UUID key derive from ONE canonicalisation
    -- (ids.canonical_source), and a source the CHECK admits but that function does
    -- not know would be stored under a spelling a per-source rebuild cannot find.
    -- Widen this CHECK and _SOURCE_CANONICAL together, never one alone.
    source            text    NOT NULL CHECK (source IN ('MeSH')),
    -- The authority's stable record id: a MeSH DescriptorUI (D004827) or a
    -- SupplementalRecordUI (C536778). NOT the ConceptUI (M0004868) MED-RT points at
    -- -- see mesh_concepts.py: many concepts resolve to one record, so keying on the
    -- concept would split one condition into several.
    source_code       text    NOT NULL,
    name              text    NOT NULL,
    record_kind       text    NOT NULL CHECK (record_kind IN ('DESCRIPTOR', 'SCR')),
    -- MeSH tree numbers, AS PUBLISHED. Stored because they are SOURCE data, not
    -- derived: they are the input condition_parent is built from, and they are what
    -- lets a consumer tell a disease (C) from a physiological state (G) from a
    -- procedure (E) without drugref inventing a taxonomy of its own. SCRs carry none.
    tree_numbers      text[]  NOT NULL DEFAULT '{}',
    first_seen_ingest bigint  NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    UNIQUE (source, source_code)
);

COMMENT ON TABLE drugref.condition IS
    'Patient states a drug may be contraindicated in, from MeSH: diseases, but also '
    'physiological states (pregnancy, lactation), procedures and demographics. A '
    'REBUILDABLE PROJECTION -- re-ingest replaces this source''s rows. NOT a '
    'substance_class: nothing is a MEMBER of a condition, so no membership table '
    'exists or should be added.';
COMMENT ON COLUMN drugref.condition.source_code IS
    'MeSH DescriptorUI or SupplementalRecordUI -- the RECORD, never the ConceptUI '
    'MED-RT references. Several concepts resolve to one record.';
COMMENT ON COLUMN drugref.condition.tree_numbers IS
    'MeSH tree numbers as published. Source data, not derived: condition_parent is '
    'built from their nesting, and the leading letter distinguishes a disease (C) '
    'from a physiological state (G) from a procedure (E).';

CREATE TABLE IF NOT EXISTS drugref.condition_parent (
    child_condition_uuid  uuid   NOT NULL REFERENCES drugref.condition(condition_uuid),
    parent_condition_uuid uuid   NOT NULL REFERENCES drugref.condition(condition_uuid),
    ingest_run            bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (child_condition_uuid, parent_condition_uuid),
    -- Self-parenting is the ONE cycle a UNION-over-(root,node) walk cannot survive,
    -- so it is forbidden structurally. Longer cycles are tolerated by the walk
    -- itself (db/012's ci_class_subtree explains why), not by this constraint.
    CONSTRAINT condition_parent_not_self
        CHECK (child_condition_uuid <> parent_condition_uuid)
);

CREATE INDEX IF NOT EXISTS condition_parent_by_parent
    ON drugref.condition_parent (parent_condition_uuid);

COMMENT ON TABLE drugref.condition_parent IS
    'The condition DAG, derived from MeSH tree-number nesting exactly as slice 2b '
    'derived the PA DAG. MANY-TO-MANY: a descriptor bears several tree numbers, so '
    '1,690 of the 5,190 conditions in the 2026 release have more than one parent. '
    'A REBUILDABLE PROJECTION -- cleared and rebuilt per source on every ingest.';
