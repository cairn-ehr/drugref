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
    -- may_treat rule on Neoplasms would manufacture 708 therapeutic claims, Infections
    -- 812, Cardiovascular Diseases 478, and the whole set inflates 14x/47x/77x
    -- (may_treat / may_prevent / may_diagnose): 14,674 stored rows would become 276,343.
    -- So nothing derived is ever STORED; this column governs only whether
    -- indications_for_condition may OFFER a rule from an ancestor, LABELLED as a
    -- generalisation.
    --
    -- MEASURED ON 5b.2's REGISTRY, not the spec's. The spec said 702/785 and 13x/41x/75x
    -- over slice 5b's 5,203-condition registry; this slice's closure widened it to 5,963
    -- (spec 3.6), so every descendant count below a root moved up with it. The argument
    -- is unchanged and slightly stronger -- which is the point of re-measuring rather
    -- than carrying a figure forward.
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
    'ordering among the drugs that treat one condition. '
    'NOTHING HERE IS EXPANDED -- no row is derived by walking the condition DAG; '
    'generalisation happens at read time in indications_for_condition and is labelled '
    'there. But rows here CAN sit on a broader condition than the release named, '
    'because MED-RT keys on a MeSH ConceptUI while a condition is keyed on the RECORD '
    'that owns it: 422 of the 2026.07.06 release''s 18,314 assertions (2.3%) name one '
    'of 90 subordinate concepts, which collapse onto 85 broader records. THAT 422 IS A '
    'RELEASE-GRAIN COUNT, taken above the moiety gate -- it is what MED-RT asserts, not '
    'how many rows landed here, and the row figure has never been measured. The row '
    'carries no concept_ui, so a consumer CANNOT yet tell which rows are affected '
    '(#52); the per-run count is the only evidence. '
    'A DRUG MAY BE BOTH INDICATED AND CONTRAINDICATED FOR ONE CONDITION: 168 pairs are '
    'in this table AND in moiety_condition_contraindication on the same release '
    '(carvedilol/Heart Failure, alteplase/Stroke, budesonide/Asthma). MED-RT asserts '
    'no qualifier distinguishing them, so a consumer MUST NOT read such a pair as a '
    'contradiction to be resolved automatically -- it is a real clinical distinction '
    '(stable chronic HFrEF vs acute decompensation) that the MeSH descriptor grain '
    'cannot carry. #51.';
COMMENT ON COLUMN drugref.moiety_condition_indication.object_condition_uuid IS
    'The condition treated, prevented or diagnosed. Usually a disease, but MED-RT also '
    'names the ORGANISM for prevention (Influenza A virus carries 76 may_prevent '
    'assertions -- these are the vaccines) and, rarely, a treatment target such as LDL '
    'Cholesterol. condition.tree_numbers is what lets a consumer tell them apart.';

-- ---- 2a. the same warning, on the OTHER side of the collision ------------------
--
-- RE-ISSUED HERE RATHER THAN EDITED INTO db/014, which created this table and is
-- MERGED and therefore immutable. A catalog comment has exactly ONE live value, so
-- there is no way to APPEND to db/014's text; the whole comment has to be restated by
-- whichever migration last has something to add. The first four sentences below are
-- db/014's verbatim -- do not "tidy" them, they are load-bearing there too.
--
-- IT MUST BE ON BOTH TABLES, not merely on the indication side, because the failure is
-- symmetrical and a consumer meets it from whichever side they queried first: someone
-- reading \d+ moiety_condition_contraindication to find out what a CI_with row means
-- gets no hint that the same drug may be sitting in moiety_condition_indication for the
-- same condition. 5b.2 is when that became possible -- before it there was no
-- indication table to collide with -- which is why a 5b.2 migration is what says so.
COMMENT ON TABLE drugref.moiety_condition_contraindication IS
    'Drug-CONDITION contraindications: the subject moiety is contraindicated in a '
    'patient who has the object condition. A REBUILDABLE PROJECTION, CANDIDATE TIER '
    '-- MED-RT does not track label updates, so rows feed review and must not '
    'auto-alert. NOT AN ABSOLUTE CONTRAINDICATION: MED-RT asserts the association, '
    'never its severity nor whether benefit-risk may override it, so a consumer must '
    'not render "contraindicated in pregnancy" as a hard stop. The curated overlay '
    '(slice 5c) adds severity, mechanism and management. '
    'A DRUG MAY BE BOTH CONTRAINDICATED AND INDICATED FOR ONE CONDITION: 168 pairs are '
    'in this table AND in moiety_condition_indication on the 2026.07.06 release '
    '(carvedilol/Heart Failure, alteplase/Stroke, budesonide/Asthma). MED-RT asserts '
    'no qualifier distinguishing them, so a consumer MUST NOT read such a pair as a '
    'contradiction to be resolved automatically -- it is a real clinical distinction '
    '(stable chronic HFrEF vs acute decompensation) that the MeSH descriptor grain '
    'cannot carry. The two read paths walk in OPPOSITE directions, so the collision '
    'multiplies below the object rather than staying at 168. #51.';

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

-- ============================================================================
-- 5. THE READ PATH -- one walk, in the only sound direction
-- ============================================================================
--
-- There is deliberately NO condition_indication_expanded view to mirror slice 5b's.
-- 5b needs one because its rows are stored expandable and whole-set access is a real
-- use; here nothing is stored expanded, so the base table IS whole-set access and a
-- second walk would buy nothing while creating exactly the disagreement db/006 warns
-- about. The ONE other statement of the reach rule is the view below, and a test pins
-- the two against each other.

CREATE OR REPLACE VIEW drugref.condition_indication_reach AS
WITH RECURSIVE subtree(root_uuid, condition_uuid) AS (
    SELECT DISTINCT i.object_condition_uuid, i.object_condition_uuid
    FROM   drugref.moiety_condition_indication i
  UNION
    SELECT s.root_uuid, cp.child_condition_uuid
    FROM   subtree s
    JOIN   drugref.condition_parent cp ON cp.parent_condition_uuid = s.condition_uuid
),
reached AS (
    SELECT s.condition_uuid,
           count(*) FILTER (WHERE s.condition_uuid = i.object_condition_uuid)
               AS direct_rules,
           count(*) FILTER (WHERE s.condition_uuid <> i.object_condition_uuid
                            AND   a.generalises_to_descendants) AS generalised_rules
    FROM   subtree s
    JOIN   drugref.moiety_condition_indication i
           ON i.object_condition_uuid = s.root_uuid
    JOIN   drugref.condition_indication_axis a ON a.relationship = i.relationship
    GROUP  BY s.condition_uuid
)
SELECT c.condition_uuid,
       COALESCE(r.direct_rules, 0)      AS direct_indication_rules,
       COALESCE(r.generalised_rules, 0) AS generalised_indication_rules
FROM   drugref.condition c
LEFT   JOIN reached r ON r.condition_uuid = c.condition_uuid;

COMMENT ON VIEW drugref.condition_indication_reach IS
    'For EVERY registry condition, how many indication rules reach it: directly, and '
    'by generalisation from an ancestor. One row per condition -- a condition nothing '
    'reaches is present with zeroes, never absent, which is what lets '
    'gap_condition_without_indication be a filter on this view rather than a second '
    'statement of the same walk (db/018: one quantity stated twice will disagree). '
    'induces is excluded: it holds no axis row and licenses no walk.';
COMMENT ON COLUMN drugref.condition_indication_reach.generalised_indication_rules IS
    'Rules written against an ANCESTOR of this condition. A WEAKER claim, not a wider '
    'one -- the drug is indicated for a more general form of the diagnosis, which is '
    'not the same as being indicated for the diagnosis.';

CREATE OR REPLACE FUNCTION drugref.indications_for_condition(patient_condition uuid)
RETURNS TABLE (subject_moiety   uuid,
               object_condition uuid,
               member_condition uuid,
               is_direct        boolean,
               relationship     text,
               source           text)
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $$
    WITH RECURSIVE ancestor(condition_uuid) AS (
        SELECT patient_condition
      UNION
        SELECT cp.parent_condition_uuid
        FROM   ancestor an
        JOIN   drugref.condition_parent cp
               ON cp.child_condition_uuid = an.condition_uuid
    )
    SELECT i.subject_moiety_uuid,
           i.object_condition_uuid,
           -- Always the condition asked about: this walk climbs from it, so every row
           -- returned is a rule that reaches THAT condition. Returned anyway so the
           -- shape matches contraindications_for_condition column for column.
           patient_condition,
           i.object_condition_uuid = patient_condition,
           i.relationship,
           i.source
    FROM   drugref.moiety_condition_indication i
    JOIN   drugref.condition_indication_axis a ON a.relationship = i.relationship
    JOIN   ancestor an ON an.condition_uuid = i.object_condition_uuid
    WHERE  a.generalises_to_descendants
       OR  i.object_condition_uuid = patient_condition;
$$;

COMMENT ON FUNCTION drugref.indications_for_condition(uuid) IS
    'Every indication that reaches a patient coded with this condition, found by '
    'walking UP the condition DAG from it. THE DIRECTION IS THE POINT: walking DOWN '
    'from a rule''s object would distribute a therapeutic claim over the object''s '
    'subclasses, and one may_treat rule on Neoplasms would manufacture 708 claims the '
    'release never made. Walking up instead yields a WEAKER statement that is true. '
    'A row with is_direct = false MUST be rendered as "indicated for <object_condition>, '
    'a more general form of this diagnosis" and NEVER as an indication for the coded '
    'diagnosis -- object_condition is a column for exactly that reason. CANDIDATE TIER '
    'and not a recommendation: no severity, no line of therapy, no ordering. UNION over '
    'the node, not the path, so it terminates under a cycle (db/013 forbids only '
    'self-parenting).';

-- ============================================================================
-- 6. THE SEVENTH GAP KIND -- diseases drugref knows nothing to give for
-- ============================================================================
--
-- A COMPLEMENTARY FILTER ON condition_indication_reach, not a second walk: `= 0` on
-- the sum of its two columns. db/018's round is why -- the reach measure was stated
-- twice there, only one copy learned a correction, and a whole class of dead rules was
-- reported by nothing.
--
-- SCOPED, AND THE SCOPE IS A JUDGEMENT WITH NUMBERS BEHIND IT. 939 registry conditions
-- are unreached; 97 of them are gaps. The 842 excluded are, attributed by FIRST tree
-- number (totals exact, measured through the moiety gate on the 2026 releases):
--   670  E-tree SURGICAL PROCEDURES (Abdominoplasty, Ablation Techniques)
--    54  B-tree organisms · 52 D-tree chemicals · 40 G-tree phenomena (Beer, Cheese)
--    14  M-tree demographics (Adolescent, Aged) · 6 H · 1 A · 1 N
--     4  tree-less SCRs that are NOT rare diseases (aliskiren, formaldehyde-serum
--        albumin) -- see below
-- "Nothing is indicated for Abdominoplasty" is a category error, not a gap, and
-- question_uuid is EXTERNALLY CITABLE and immortal: minting 842 of them for noise
-- would bury the 97 real rows on a worklist whose whole value is that a curator can
-- work it.
--
-- EVERY FIGURE IN THIS BLOCK IS POST-GATE, and that is why it differs from the spec's.
-- The spec computed 855/66/789/669 over the objects the RELEASE references; drugref
-- stores only a rule whose subject some moiety carries, and 1,426 indication subject
-- RxCUIs match none. The spec's own breakdown ("25 N-tree · 13 M-tree · 12 J") does not
-- survive the gate at all. Both are right about different populations -- exactly the
-- concept-grain-vs-record-grain error slice 5b hit five times -- but only the post-gate
-- number is what this view returns, so only the post-gate number belongs in its comment.
--
-- TREE-LESS RECORDS ARE EXCLUDED ON A DIFFERENT GROUND, and the distinction matters:
-- an SCR holds no DAG position at all, so "no indication above it" is VACUOUSLY true
-- and says nothing. The SCRClass = 3 carve-out recovers exactly the 17 for which the
-- vacuous answer is also the clinically right one -- a rare disease with no recorded
-- indication is the most valuable row on this list (Short QT Syndrome, succinic
-- semialdehyde dehydrogenase deficiency, Familial medullary thyroid carcinoma).
CREATE OR REPLACE VIEW drugref.gap_condition_without_indication AS
SELECT c.condition_uuid,
       c.name,
       c.source_code,
       c.record_kind
FROM   drugref.condition c
JOIN   drugref.condition_indication_reach r ON r.condition_uuid = c.condition_uuid
WHERE  r.direct_indication_rules + r.generalised_indication_rules = 0
AND    (EXISTS (SELECT 1 FROM unnest(c.tree_numbers) t
                WHERE  left(t, 1) IN ('C', 'F'))
        -- 3 = rare disease. The only SCRClass value drugref reads, and the only place
        -- it is read. See condition.scr_class on why no CHECK constrains it.
        OR (c.tree_numbers = '{}' AND c.scr_class = '3'));

COMMENT ON VIEW drugref.gap_condition_without_indication IS
    'DISEASES drugref holds no indication for -- not directly, and not from any '
    'condition above them. 97 rows against the 2026 releases: 80 carrying a C '
    '(Diseases) or F (Psychiatry) tree number, plus 17 tree-less SCRClass-3 rare '
    'diseases. Scoped DELIBERATELY: 842 further unreached conditions are excluded, 670 '
    'of them surgical procedures, because "nothing is indicated for Abdominoplasty" is '
    'a category error rather than a gap. Every figure here is POST-GATE (drugref stores '
    'only rules whose subject a moiety carries), which is why it differs from the '
    'spec''s pre-gate 66/789. Answerable from openFDA-SPL labels (tier 2) '
    'or MeDIC (tier 3) before literature.';

-- Admit the seventh question kind. Guarded on the constraint's TEXT, as db/016 and
-- db/018 are, and 'condition_without_indication' is distinctive enough to guard on.
--
-- ON "DISTINCTIVE ENOUGH", which section 7 below is the counter-example to: the guard
-- asks "has my widening already landed?" and a substring that the OLD constraint
-- already contains answers yes when the truth is no.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%condition_without_indication%') THEN
        ALTER TABLE drugref.open_question
            DROP CONSTRAINT IF EXISTS open_question_gap_kind;
        ALTER TABLE drugref.open_question
            ADD CONSTRAINT open_question_gap_kind CHECK (gap_kind IN (
                'unpopulated_contraindication', 'unclassified_moiety',
                'unmatched_ingredient', 'unreviewed_expansion_root',
                'unresolved_ci_object', 'dead_by_expansion_policy',
                'condition_without_indication'));
    END IF;
END $$;

-- ============================================================================
-- 7. A THIRD unmatched-ingredient bucket
-- ============================================================================
-- db/018's invariant is EXACTLY ONE WRITER PER (source, reason), and this preserves
-- it: mesh_rel_run owns BOTH 'contraindication' and 'indication', clearing each on its
-- own, because one orchestrator owns the whole MeSH-keyed run (spec 6.1). One writer
-- owning two buckets is fine; two writers sharing one bucket is what #39 was.
--
-- The two lists are genuinely different populations, which is why a bucket rather than
-- a wider clear: on the real 2026.07.06 release MED-RT states contraindications and
-- indications over overlapping-but-unequal ingredient sets, so a subject unmatched for
-- one may be matched (or simply absent) for the other, and a clear that took both
-- would make the answer depend on which pass ran last -- #39 exactly.
--
-- THE GUARD BELOW MUST NOT MATCH ON '%indication%': 'contraindication' CONTAINS
-- 'indication', so that pattern is satisfied by the EXISTING constraint, the widening
-- silently does not happen, and the first reason = 'indication' write fails at ingest
-- time. Verified against the live constraint before this file was written:
-- '%indication%' matches it (wrong), '%''indication''::text%' does not (right).
-- pg_get_constraintdef renders each admitted value as 'x'::text, so the quoted literal
-- is anchored on both sides and cannot match inside 'contraindication'::text.
-- Do not "simplify" it back.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'ingest_unmatched_ingredient_reason'
                   AND    conrelid = 'drugref.ingest_unmatched_ingredient'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%''indication''::text%') THEN
        ALTER TABLE drugref.ingest_unmatched_ingredient
            DROP CONSTRAINT IF EXISTS ingest_unmatched_ingredient_reason;
        ALTER TABLE drugref.ingest_unmatched_ingredient
            ADD CONSTRAINT ingest_unmatched_ingredient_reason
            CHECK (reason IN ('classification', 'contraindication', 'indication'));
    END IF;
END $$;

COMMENT ON COLUMN drugref.ingest_unmatched_ingredient.reason IS
    'Why this RxCUI is on the worklist, and therefore WHICH writer owns the row: '
    'classification (medrt_run -- an ingredient MED-RT classifies), contraindication '
    'and indication (mesh_rel_run -- the SUBJECT of a MeSH-keyed rule of each kind). '
    'Every writer clears its own (source, reason) and no other, which is what #39 '
    'cost to learn. NOT NULL with no DEFAULT, deliberately: a writer that does not say '
    'which bucket it rebuilds must fail rather than inherit one.';
