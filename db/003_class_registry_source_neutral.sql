-- db/003_class_registry_source_neutral.sql
-- Make the class registry hold classes from MORE THAN ONE upstream authority.
--
-- Slice 2a built the registry around a single source and named its columns after
-- it (medrt_nui / medrt_code). Slice 2b adds MeSH Pharmacological Actions, whose
-- concepts carry a MeSH descriptor UI ("D000894"), not a MED-RT NUI. Rather than
-- bolt a second pair of source-specific columns on, the registry becomes
-- source-neutral: WHICH authority defined the class (source) and WHAT that
-- authority calls it (source_code).
--
-- Why a separate migration rather than an edit to 002: db/002 creates the table
-- with CREATE TABLE IF NOT EXISTS, so on a database that already ran it the
-- create is skipped and an edit there would never reach the existing table. The
-- change has to be expressed as ALTERs to land on both fresh and existing
-- databases. Every statement below is guarded so that replaying the whole db/
-- directory (which is what drugref.db.apply_migrations does, every time) stays
-- idempotent.
--
-- Class IDENTITY is deliberately untouched: class_uuid stays a pure function of
-- (source, code), and src/drugref/ids.py keeps minting MED-RT classes with the
-- same "MEDRT:" key prefix they were minted with. A rebuild after this migration
-- must re-derive byte-identical UUIDs, or every class_parent/class_membership
-- edge would be orphaned with no error anywhere.

-- 1. Which authority defined the class. Backfilled to MED-RT because that is the
--    only source that existed when these rows were written.
ALTER TABLE drugref.substance_class ADD COLUMN IF NOT EXISTS source text;
UPDATE drugref.substance_class SET source = 'MED-RT' WHERE source IS NULL;
ALTER TABLE drugref.substance_class ALTER COLUMN source SET NOT NULL;

-- 2. Rename the two source-specific columns to their neutral equivalents.
--    source_code    = the authority's identity key (was medrt_nui)
--    published_code = the code as published, which associations reference by
--                     (was medrt_code); still nullable, still allowed to differ
--                     from the identity key.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'drugref' AND table_name = 'substance_class'
                 AND column_name = 'medrt_nui') THEN
        ALTER TABLE drugref.substance_class RENAME COLUMN medrt_nui TO source_code;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'drugref' AND table_name = 'substance_class'
                 AND column_name = 'medrt_code') THEN
        ALTER TABLE drugref.substance_class RENAME COLUMN medrt_code TO published_code;
    END IF;
END $$;

-- 3. Uniqueness becomes per-authority. The old global UNIQUE on medrt_nui would
--    reject a MeSH code that happened to equal a MED-RT one -- and, worse, would
--    have made the two look like the same class.
ALTER TABLE drugref.substance_class
    DROP CONSTRAINT IF EXISTS substance_class_medrt_nui_key;
ALTER TABLE drugref.substance_class
    DROP CONSTRAINT IF EXISTS substance_class_source_code_unique;
ALTER TABLE drugref.substance_class
    ADD CONSTRAINT substance_class_source_code_unique UNIQUE (source, source_code);

-- 4. Widen the axis vocabularies for MeSH.
--    'PA' is MeSH's Pharmacological Action -- a classification axis in exactly the
--    sense the MED-RT six are, so it joins them rather than getting its own table.
--    Still deliberately EXCLUDED: MED-RT's HC (the 26 alphabetical navigation
--    bins) and EXT. See db/002 for why.
ALTER TABLE drugref.substance_class
    DROP CONSTRAINT IF EXISTS substance_class_concept_type;
ALTER TABLE drugref.substance_class
    ADD CONSTRAINT substance_class_concept_type
    CHECK (concept_type IN ('MoA', 'PE', 'TC', 'PK', 'EPC', 'APC', 'PA'));

-- 'has_PA' is drugref's label for MeSH pharmacological-action membership, kept
-- symmetric with the has_* labels the MED-RT axes use. Indication /
-- contraindication relations (may_treat, CI_with, ...) remain NOT membership --
-- they are curated-overlay data for a later slice.
ALTER TABLE drugref.class_membership
    DROP CONSTRAINT IF EXISTS class_membership_relationship;
ALTER TABLE drugref.class_membership
    ADD CONSTRAINT class_membership_relationship
    CHECK (relationship IN ('has_MoA', 'has_PE', 'has_TC', 'has_PK', 'has_EPC', 'has_PA'));

-- 5. "Which classes does this authority define?" is now a real question, asked on
--    every per-source rebuild.
CREATE INDEX IF NOT EXISTS substance_class_by_source
    ON drugref.substance_class (source);
