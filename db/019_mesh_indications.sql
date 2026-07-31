-- db/019_mesh_indications.sql
-- Slice 5b.2: MED-RT's MeSH-keyed INDICATIONS, over slice 5b's condition registry.
--
-- TWO RELATIONS, NOT ONE, AND THE TEST IS NOT "ARE THE ENDPOINTS ALIKE" (they are:
-- moiety -> condition). It is WHAT DOES A ROW SAY IF NOBODY FILTERS IT.
--   * moiety_condition_indication -- "this drug is USED FOR this condition", true of
--     may_treat, may_prevent and may_diagnose alike. 18,144 upstream assertions.
--   * moiety_induced_condition    -- "this drug can CAUSE this condition". 170.
-- A consumer who forgets a relationship filter on a shared table would read
-- "carbamazepine treats agranulocytosis" off an induces row. db/010 chose `is_direct`
-- so that a forgetful consumer errs toward RECALL, which is safe for a
-- contraindication; here the same forgetfulness asserts a therapy, so the split is
-- structural rather than a WHERE clause.
--
-- Both are REBUILDABLE PROJECTIONS and CANDIDATE TIER, exactly as slice 5b's relations
-- are: MED-RT does not track label updates, so rows feed review and must not
-- auto-alert. And an indication is not a RECOMMENDATION -- MED-RT asserts that a drug
-- may treat a condition, never that it is appropriate for a given patient, first-line,
-- or safe in combination.

-- ---- 1. the indication vocabulary --------------------------------------------
CREATE TABLE IF NOT EXISTS drugref.condition_indication_axis (
    relationship               text    PRIMARY KEY,
    -- NO DEFAULT, deliberately -- db/014's discipline after db/012 finding 5 found a
    -- comment claiming it while a DEFAULT quietly answered the question. A predicate
    -- added later MUST state its own answer.
    --
    -- DELIBERATELY NOT NAMED expands_descendants, because it licenses something
    -- WEAKER. condition_ci_axis.expands_descendants = true says the rule FIRES for the
    -- descendant: a patient coded Temporal Lobe Epilepsy IS a patient with epilepsy, so
    -- a contraindication on Epilepsy holds. Applied to an indication the same walk
    -- distributes over the OBJECT's subclasses, which the release never asserted -- one
    -- may_treat rule on Neoplasms would manufacture 702 therapeutic claims, Infections
    -- 785, and the whole set inflates 13x/41x/75x. So nothing derived is ever STORED;
    -- this column governs only whether indications_for_condition may OFFER a rule from
    -- an ancestor, LABELLED as a generalisation.
    generalises_to_descendants boolean NOT NULL
);

INSERT INTO drugref.condition_indication_axis (relationship, generalises_to_descendants)
VALUES ('may_treat', true), ('may_prevent', true), ('may_diagnose', true)
ON CONFLICT (relationship) DO NOTHING;

COMMENT ON TABLE drugref.condition_indication_axis IS
    'Admissible drug-condition INDICATION predicates, and whether a rule on one may be '
    'offered for a more specific condition as a labelled generalisation. '
    'moiety_condition_indication.relationship is a foreign key into this table. '
    'induces is deliberately ABSENT: it is not an indication, it licenses no walk, and '
    'it lives in its own table.';
COMMENT ON COLUMN drugref.condition_indication_axis.generalises_to_descendants IS
    'True for all three therapeutic predicates: a drug indicated for Epilepsy is worth '
    'OFFERING for Temporal Lobe Epilepsy, as the weaker statement "indicated for a more '
    'general form of this diagnosis". It is NOT expands_descendants -- nothing derived '
    'is stored, and a derived row is a weaker claim rather than a wider one.';

-- ---- 2. drug -> condition, therapeutic ---------------------------------------
CREATE TABLE IF NOT EXISTS drugref.moiety_condition_indication (
    subject_moiety_uuid   uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    object_condition_uuid uuid   NOT NULL REFERENCES drugref.condition(condition_uuid),
    relationship          text   NOT NULL
        REFERENCES drugref.condition_indication_axis(relationship),
    -- SOURCE IS IN THE KEY and CHECK-constrained, for db/006 finding 2's reason as
    -- restated by db/014: without it a second authority's independent assertion is
    -- swallowed by ON CONFLICT DO NOTHING and then deleted by the next MED-RT rebuild.
    -- The CHECK is not decoration -- db/012 finding 3: an unconstrained source once let
    -- 'MEDRT' insert cleanly and match nothing, ever.
    source                text   NOT NULL
        CONSTRAINT moiety_condition_indication_source CHECK (source IN ('MED-RT')),
    ingest_run            bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (subject_moiety_uuid, object_condition_uuid, relationship, source)
);

CREATE INDEX IF NOT EXISTS moiety_condition_indication_by_condition
    ON drugref.moiety_condition_indication (object_condition_uuid);

COMMENT ON TABLE drugref.moiety_condition_indication IS
    'Drug-condition INDICATIONS: the subject moiety is used for the object condition '
    '(may_treat / may_prevent / may_diagnose). A REBUILDABLE PROJECTION, CANDIDATE TIER '
    '-- rows feed review and must not auto-alert. NOT A RECOMMENDATION: MED-RT asserts '
    'that a drug may treat a condition, never that it is appropriate for a given '
    'patient, first-line, correctly dosed, or safe in combination, and it asserts no '
    'ordering among the drugs that treat one condition. NOTHING HERE IS DERIVED -- '
    'every row is an assertion the release makes; generalisation happens at read time '
    'in indications_for_condition and is labelled there.';
COMMENT ON COLUMN drugref.moiety_condition_indication.object_condition_uuid IS
    'The condition treated, prevented or diagnosed. Usually a disease, but MED-RT also '
    'names the ORGANISM for prevention (Influenza A virus carries 76 may_prevent '
    'assertions -- these are the vaccines) and, rarely, a treatment target such as LDL '
    'Cholesterol. condition.tree_numbers is what lets a consumer tell them apart.';

-- ---- 3. drug -> condition, caused --------------------------------------------
CREATE TABLE IF NOT EXISTS drugref.moiety_induced_condition (
    subject_moiety_uuid   uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    object_condition_uuid uuid   NOT NULL REFERENCES drugref.condition(condition_uuid),
    -- A CHECK, NOT AN FK, and the asymmetry with the table above is db/014's own
    -- argument: an FK exists to keep a predicate list in step with a SECOND list held
    -- elsewhere (a view's CASE, a walk's gate). Nothing walks this table -- induces has
    -- no axis row and licenses no generalisation -- so there is no second list, and an
    -- FK would copy the form of that fix while its cause is absent.
    relationship          text   NOT NULL
        CONSTRAINT moiety_induced_condition_relationship
        CHECK (relationship IN ('induces')),
    source                text   NOT NULL
        CONSTRAINT moiety_induced_condition_source CHECK (source IN ('MED-RT')),
    ingest_run            bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (subject_moiety_uuid, object_condition_uuid, relationship, source)
);

CREATE INDEX IF NOT EXISTS moiety_induced_condition_by_condition
    ON drugref.moiety_induced_condition (object_condition_uuid);

COMMENT ON TABLE drugref.moiety_induced_condition IS
    'States a drug CAUSES: Unconsciousness (32 rules -- the anaesthetics), Mydriasis '
    '(14), Diarrhea (8). NEITHER an indication NOR a contraindication, which is why it '
    'has its own table: sometimes the induced state is the therapeutic point and '
    'sometimes it is the adverse effect, and MED-RT does not say which. A REBUILDABLE '
    'PROJECTION, CANDIDATE TIER.';

-- ---- 4. the cached SCR class -------------------------------------------------
--
-- Stored AS PUBLISHED and with no CHECK, exactly as condition.tree_numbers is: it is
-- opaque source data. supp2026 publishes SIX values (1: 249,245 · 4: 65,236 ·
-- 3: 6,542 · 5: 1,763 · 2: 1,236 · 6: 23) while the documentation describes four, so a
-- CHECK would abort an ingest the first time NLM adds a seventh. Drift is caught by a
-- COUNT instead -- the run summary reports registered conditions per scr_class, the
-- same posture skipped_predicates takes -- so a renumbering shows up as a number that
-- moved rather than as a gap view going quiet.
ALTER TABLE drugref.condition ADD COLUMN IF NOT EXISTS scr_class text;

COMMENT ON COLUMN drugref.condition.scr_class IS
    'MeSH SCRClass as published, NULL for a descriptor (which carries DescriptorClass, '
    'a different vocabulary). Only value 3 (rare disease) is load-bearing, and only in '
    'gap_condition_without_indication: an SCR bears no tree numbers, so nothing else '
    'can tell "Short QT Syndrome" from "aliskiren". drugref asserts no meaning for 5 '
    'and 6, which are published but undocumented.';
