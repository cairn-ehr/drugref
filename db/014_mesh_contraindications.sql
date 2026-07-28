-- db/014_mesh_contraindications.sql
-- Slice 5b's two contraindication relations, plus the worklist for what is withheld.
--
-- TWO RELATIONS, NOT ONE, because the objects are different kinds of thing:
--   * moiety_condition_contraindication -- CI_with. "Do not give drug X to a patient
--     in state C." 9,482 rows against the real release.
--   * moiety_contraindication           -- CI_ChemClass's moiety arm. "Do not
--     co-administer drug X with drug Y." 1,443 rows, and drugref's FIRST genuinely
--     pairwise DDI content: both endpoints are moieties, so nothing expands.
--
-- Both are REBUILDABLE PROJECTIONS and CANDIDATE TIER, exactly as
-- class_contraindication is: MED-RT does not track label updates, so rows here feed
-- review and must never auto-alert.

-- ---- 1. the condition-contraindication vocabulary ---------------------------
--
-- A SEPARATE TABLE FROM ci_axis, because the two map to different things:
-- ci_axis.membership_relationship names the class_membership axis a rule expands
-- over, and a condition has no membership axis -- nothing is a member of pregnancy.
CREATE TABLE IF NOT EXISTS drugref.condition_ci_axis (
    relationship        text    PRIMARY KEY,
    -- NO DEFAULT, deliberately. db/012 finding 5 recorded that ci_axis's comment
    -- claimed db/006's force-a-declaration discipline while supplying a DEFAULT
    -- that quietly answered the question for you. This column implements the
    -- discipline: a predicate added later MUST state whether it expands, because
    -- MeSH's tree is a different shape from MED-RT's and the recall-safe answer is
    -- not automatically the correct one.
    expands_descendants boolean NOT NULL
);

INSERT INTO drugref.condition_ci_axis (relationship, expands_descendants)
VALUES ('CI_with', true)
ON CONFLICT (relationship) DO NOTHING;

COMMENT ON TABLE drugref.condition_ci_axis IS
    'Admissible drug-condition contraindication predicates, and whether each expands '
    'down the condition DAG. moiety_condition_contraindication.relationship is a '
    'foreign key into this table, so adding a predicate is ONE insert and cannot '
    'leave the read path silently returning nothing.';
COMMENT ON COLUMN drugref.condition_ci_axis.expands_descendants IS
    'Declared per predicate, with NO default. CI_with is true on Plan B''s argument: '
    'a rule on Epilepsy must reach a patient coded Temporal Lobe Epilepsy, and for a '
    'contraindication FEWER ROWS IS THE HARM DIRECTION.';

-- ---- 2. drug -> condition ----------------------------------------------------

CREATE TABLE IF NOT EXISTS drugref.moiety_condition_contraindication (
    subject_moiety_uuid   uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    object_condition_uuid uuid   NOT NULL REFERENCES drugref.condition(condition_uuid),
    relationship          text   NOT NULL REFERENCES drugref.condition_ci_axis(relationship),
    source                text   NOT NULL,
    ingest_run            bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    -- SOURCE IS IN THE KEY (db/006 finding 2). Without it, a second authority
    -- asserting what MED-RT already recorded is swallowed by ON CONFLICT DO NOTHING
    -- -- and the next routine MED-RT rebuild, which deletes by ingest_run, takes the
    -- shared row away with it, destroying the other source's independent assertion.
    -- Slice 5c plans exactly that second source.
    PRIMARY KEY (subject_moiety_uuid, object_condition_uuid, relationship, source)
);

CREATE INDEX IF NOT EXISTS moiety_condition_ci_by_condition
    ON drugref.moiety_condition_contraindication (object_condition_uuid);

COMMENT ON TABLE drugref.moiety_condition_contraindication IS
    'Drug-CONDITION contraindications: the subject moiety is contraindicated in a '
    'patient who has the object condition. A REBUILDABLE PROJECTION, CANDIDATE TIER '
    '-- MED-RT does not track label updates, so rows feed review and must not '
    'auto-alert. NOT AN ABSOLUTE CONTRAINDICATION: MED-RT asserts the association, '
    'never its severity nor whether benefit-risk may override it, so a consumer must '
    'not render "contraindicated in pregnancy" as a hard stop. The curated overlay '
    '(slice 5c) adds severity, mechanism and management.';
COMMENT ON COLUMN drugref.moiety_condition_contraindication.subject_moiety_uuid IS
    'The drug the contraindication is ABOUT. Not interchangeable with the object.';
COMMENT ON COLUMN drugref.moiety_condition_contraindication.object_condition_uuid IS
    'The patient state -- a disease, but also pregnancy, lactation or a procedure.';

-- ---- 3. drug -> drug ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS drugref.moiety_contraindication (
    subject_moiety_uuid uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    object_moiety_uuid  uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    -- A CHECK, NOT A FOREIGN KEY INTO AN AXIS TABLE -- and that asymmetry with
    -- db/006 is deliberate. db/006 replaced a CHECK with an FK because the predicate
    -- list was duplicated in a CASE *inside a view*: two lists in two places, where
    -- widening only one silently produced rows that expanded to nothing. Here both
    -- endpoints are moieties -- no DAG, no expansion, no membership axis, and
    -- therefore NO SECOND LIST to keep in step with. An FK would copy the form of
    -- db/006's fix while its cause is absent.
    relationship        text   NOT NULL
        CONSTRAINT moiety_contraindication_relationship
        CHECK (relationship IN ('CI_ChemClass')),
    source              text   NOT NULL,
    ingest_run          bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (subject_moiety_uuid, object_moiety_uuid, relationship, source),
    CONSTRAINT moiety_contraindication_not_self
        CHECK (subject_moiety_uuid <> object_moiety_uuid)
);

CREATE INDEX IF NOT EXISTS moiety_contraindication_by_object
    ON drugref.moiety_contraindication (object_moiety_uuid);

COMMENT ON TABLE drugref.moiety_contraindication IS
    'PAIRWISE drug-drug contraindications: the subject moiety must not be '
    'co-administered with the object moiety. drugref''s first EXACT pair data -- '
    'both endpoints are moieties, so nothing expands and no class DAG is involved. '
    'DIRECTIONAL: the subject is the drug the statement is ABOUT, and swapping the '
    'columns changes the meaning. A REBUILDABLE PROJECTION, CANDIDATE TIER.';

-- ---- 4. what is withheld, preserved as a worklist -----------------------------
--
-- CI_ChemClass's CLASS arm (405 assertions over 108 MeSH chemical classes) is NOT
-- ingested. Expanding it over MeSH's STRUCTURAL chemical tree makes a rule on
-- Sulfonamides (D013449, 36 rules) reach 61 moieties including bendroflumethiazide
-- and bosentan -- the discredited sulfa cross-reactivity inference, generated
-- automatically and shipped as a safety assertion. MeSH's chemical tree is a
-- structural taxonomy and does not mean what a clinical class means.
--
-- Plan B's precedent governs: it made a pharmacist rule on 14 expansion roots before
-- expanding over them. So the content is PRESERVED and published as a question, and
-- a curator decides. This table is what db/008 established for unmatched ingredients
-- -- keeping only a COUNT and discarding the identity is what made that gap
-- unqueryable, and the same mistake is not repeated here.
CREATE TABLE IF NOT EXISTS drugref.ingest_unresolved_ci_object (
    ingest_run      bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    source          text   NOT NULL,
    relationship    text   NOT NULL,
    object_source   text   NOT NULL,
    object_code     text   NOT NULL,
    object_name     text,
    -- How many assertions ride on this object. One row per OBJECT, not per
    -- assertion, because the question a curator answers is per class: "should a
    -- contraindication naming Sulfonamides expand over MeSH's structural tree?"
    assertion_count integer NOT NULL,
    PRIMARY KEY (ingest_run, source, relationship, object_source, object_code)
);

COMMENT ON TABLE drugref.ingest_unresolved_ci_object IS
    'Contraindication assertions whose OBJECT drugref deliberately did not ingest: '
    'one row per object, carrying how many rules ride on it. Not an error and not a '
    'drop -- it is the worklist behind gap_unresolved_ci_object. Populated by '
    'CI_ChemClass objects that name a CLASS rather than a substance, which are '
    'withheld pending curator review (see the sulfonamide case in this migration).';
