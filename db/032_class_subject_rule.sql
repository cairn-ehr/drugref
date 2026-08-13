-- db/032_class_subject_rule.sql -- slice 5c.2, the class-subject round: a second
-- interaction-rule GRAIN beside the moiety x class grain Tasks 1-8 built.
--
-- Spec: docs/superpowers/specs/2026-08-11-drugref-slice-5c2-onc-ddi-floor-design.md
-- section 14, which records why this file exists at all. In short: Tasks 1-8 built
-- machinery for rules shaped "moiety contraindicated with a co-administered member
-- of a drug class" (class_contraindication / curated_interaction, db/004/db/006/
-- db/029). Retrieving the ONC high-priority list (Phansalkar 2012, Table 2) then
-- showed that shape covers only 4 of its 15 entries: 8 are CLASS x CLASS (SSRIs x
-- MAOIs, statins x CYP3A4 inhibitors, ...) and 1 is a CLASS SELF-PAIR
-- (QT-prolonging agents x QT-prolonging agents). Flattening each class x class
-- entry onto the existing grain (enumerate the class's members, write one curated
-- row per member) was measured and rejected: the five MAOI entries alone would
-- flatten to ~155 curated rows for five clinical facts, each separately graded,
-- separately supersedable, and each an opportunity for two rows stating one fact to
-- disagree -- the exact defect db/029's whole design (grade the RULE, not the pair)
-- was written to prevent, reintroduced one tier up. So this file adds a rule whose
-- SUBJECT is a class, expanded on BOTH sides at read time (Task 11); one row holds
-- one ONC entry.
--
-- SCHEMA ONLY. Two tables, their floor, their indexes. No parser, no orchestrator
-- (Task 10), no read-path view (Task 11) -- this file only makes the shape exist so
-- those tasks' own tests are not also blocked on schema.
--
-- ============================================================================
-- DECISION 1 (spec 14.3): TWO TABLES, NOT A POLYMORPHIC SUBJECT COLUMN.
-- ============================================================================
-- The cheap-looking alternative is to make curated_interaction.subject_moiety_uuid
-- nullable and add a nullable subject_class_uuid beside it -- one table, one
-- natural key, half the DDL. It was rejected because it breaks the overlay floor
-- db/029 already relies on. drugref.forbid_multiple_live_assertions (db/020,
-- rewritten by db/023 to use EQUALITY PREDICATES over the natural-key columns for
-- speed -- a jsonb-containment version was measured quadratic: 5,773 ms against
-- 42 ms for a 2,000-row load) builds its "how many rows share this key" check with
-- `t.col = <value>` for every natural-key column. On a moiety-grain row
-- subject_class_uuid would be NULL, and in SQL `NULL = NULL` is never true -- so the
-- generated predicate `t.subject_class_uuid = NULL` matches ZERO rows, not "rows
-- where it's also NULL". Two class-grain rows sharing a natural key would each see
-- the OTHER as not-a-match, and the single-live guard would silently stop guarding
-- for exactly the rows it was widened to cover. db/023's own trigger function
-- already RAISEs if a natural-key column is found NULL at runtime for precisely
-- this reason -- so a polymorphic column would not fail quietly, it would fail
-- LOUDLY on every insert, which is no better.
--
-- Slice 5b met this exact fork already: a `condition` turned out not to be a
-- `substance_class` (nothing is a MEMBER of pregnancy), and db/013/014 answered it
-- with two relations rather than one relation with an optional column, because the
-- endpoints are different KINDS of thing. Same answer here: a class-subject rule
-- and a moiety-subject rule are different kinds of statement, so they get different
-- tables, and the single-live guard keeps working on both without learning
-- anything new about NULLs.
--
-- ============================================================================
-- DECISION 2 (spec 14.3): A CLASS SELF-PAIR IS LEGAL HERE; A MOIETY SELF-PAIR IS
-- NOT, AND THAT STAYS TRUE.
-- ============================================================================
-- db/014's moiety_contraindication_not_self CHECK (subject_moiety_uuid <>
-- object_moiety_uuid) forbids a drug pairing with itself -- a drug cannot be its
-- own co-administration partner, and nothing about this slice changes that. But
-- "QT-prolonging agents x QT-prolonging agents" (a real ONC entry: any two
-- QT-prolonging drugs taken together are the risk, not one drug taken with
-- itself) is a claim about the CLASS's membership, not about a moiety pairing with
-- itself. class_pair_contraindication therefore carries NO self-pair CHECK, and
-- Task 11's read-path expansion is where "exclude identical moieties" belongs
-- (mirroring class_contraindication's existing `WHERE m.moiety_uuid <>
-- ci.subject_moiety_uuid` in ddi_candidate_pair, db/004) -- at the PAIR grain, not
-- the RULE grain, since a class legitimately equals itself as a rule subject.
--
-- ============================================================================
-- 1. class_pair_contraindication -- the CANDIDATE tier
-- ============================================================================
-- Mirrors class_contraindication (db/004, widened by db/006) column for column:
-- rebuildable projection, source-scoped, PK includes `source` for db/006's own
-- reason (so a second authority's row is never swallowed by ON CONFLICT DO NOTHING
-- and then deleted by the other authority's per-source rebuild), FK to
-- ingest_run for provenance. The only structural difference is that BOTH
-- endpoints are classes instead of one moiety and one class.
CREATE TABLE IF NOT EXISTS drugref.class_pair_contraindication (
    -- The class the statement is ABOUT: every member is contraindicated when
    -- co-administered with a member of object_class_uuid. Not interchangeable
    -- with the object side -- see ddi_candidate_pair's own directionality note
    -- (db/006), which applies here unchanged once Task 11 builds the class-subject
    -- read path.
    subject_class_uuid uuid   NOT NULL REFERENCES drugref.substance_class(class_uuid),
    -- The class of the co-administered drug.
    object_class_uuid  uuid   NOT NULL REFERENCES drugref.substance_class(class_uuid),
    relationship        text   NOT NULL,
    source              text   NOT NULL,
    ingest_run          bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (subject_class_uuid, object_class_uuid, relationship, source),
    -- db/006's finding 1, mirrored rather than re-learned: a hardcoded CHECK
    -- duplicates the predicate vocabulary the read path (Task 11) also has to
    -- name to know which class_membership axis to expand over, and widening only
    -- one of the two silently produces rows that expand to zero pairs. One
    -- vocabulary, one home: ci_axis, same table class_contraindication already
    -- points at.
    CONSTRAINT class_pair_contraindication_relationship
        FOREIGN KEY (relationship) REFERENCES drugref.ci_axis(relationship),
    -- Symmetric with class_contraindication_source (db/031): the two candidate
    -- authorities this registry currently admits. Widen alongside that CHECK if a
    -- third authority ever asserts a class x class rule.
    CONSTRAINT class_pair_contraindication_source
        CHECK (source IN ('MED-RT', 'ONCHIGH'))
    -- NO self-pair CHECK here -- see DECISION 2 above. A class legitimately
    -- contraindicates its own membership (QT-prolonging x QT-prolonging); the
    -- exclusion that matters ("a drug is not its own co-administration partner")
    -- belongs in Task 11's pair-expansion view, at the moiety grain, exactly where
    -- class_contraindication's own ddi_candidate_pair already puts it.
);

-- Read path (Task 11): "who is contraindicated with drugs of this class" -- the
-- object side drives pair expansion on the OBJECT'S membership, symmetric with
-- class_contraindication_by_object (db/004). The subject side needs no index of
-- its own: it is the leading column of the primary key above, so the planner can
-- already seek on it.
CREATE INDEX IF NOT EXISTS class_pair_contraindication_by_object
    ON drugref.class_pair_contraindication (object_class_uuid);

COMMENT ON TABLE drugref.class_pair_contraindication IS
    'Class-level drug-drug contraindications where BOTH the subject and the object '
    'are drug classes: every member of subject_class_uuid is contraindicated with '
    'every co-administered member of object_class_uuid. A REBUILDABLE PROJECTION, '
    'CANDIDATE TIER, exactly like class_contraindication -- the sibling table this '
    'one mirrors for the reason spec 2026-08-11-drugref-slice-5c2-onc-ddi-floor-design.md '
    'section 14 records: a curator-originated per-member flattening of an ONC '
    'class x class entry (SSRIs x MAOIs, ...) would cost ~155 curated rows for 5 '
    'clinical facts and reintroduce the disagreeing-duplicate defect the RULE-level '
    'grain exists to prevent. A CLASS MAY LEGALLY PAIR WITH ITSELF (QT-prolonging '
    'agents x QT-prolonging agents is a real ONC entry) -- unlike moiety_contraindication, '
    'which forbids a moiety pairing with itself (db/014): these are different claims. '
    'Read-path expansion (Task 11) is where "a drug is not its own partner" is enforced, '
    'at the pair grain, not here at the rule grain.';
COMMENT ON COLUMN drugref.class_pair_contraindication.subject_class_uuid IS
    'The class the contraindication is ABOUT. Not interchangeable with the object '
    'side -- symmetric with class_contraindication.subject_moiety_uuid''s own comment.';
COMMENT ON COLUMN drugref.class_pair_contraindication.object_class_uuid IS
    'The class of the co-administered drug the subject class must not be combined '
    'with. May equal subject_class_uuid -- see the table comment.';

-- ============================================================================
-- 2. curated_class_interaction -- the OVERLAY tier
-- ============================================================================
-- Mirrors db/029's curated_interaction section as closely as the different
-- natural key allows: same grading columns, same completeness CHECK, same
-- append-only floor, same deferred single-live guard, same db/027 provenance
-- triple, same nullable question_uuid with ON DELETE CASCADE. THE FLOOR ITSELF IS
-- REUSED, NOT REWRITTEN: both trigger functions (drugref.forbid_overlay_rewrite
-- and drugref.forbid_multiple_live_assertions, db/020/db/023) are already generic
-- over the natural key, taking the column names as trigger arguments -- so this
-- section adds no new PL/pgSQL at all, following db/029's own precedent ("one
-- rule in seven places is one rule that will drift, and this project has spent
-- four rounds proving it" -- now eight places, still one rule).
--
-- WHY THE NATURAL KEY IS NOT THE PRIMARY KEY: identical reasoning to
-- curated_interaction (db/029) -- correction-by-overlay means INSERTing the new
-- row and THEN pointing the old one at it, so both rows briefly carry the same
-- natural key, and a primary key on it would reject the only sequence that can
-- express a correction.
CREATE TABLE IF NOT EXISTS drugref.curated_class_interaction (
    curated_class_interaction_id bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_class_uuid uuid        NOT NULL REFERENCES drugref.substance_class(class_uuid),
    object_class_uuid  uuid        NOT NULL REFERENCES drugref.substance_class(class_uuid),
    relationship        text        NOT NULL,
    -- THE RULING, identical meaning to curated_interaction.applies: `false` is a
    -- real answer ("a curator looked and this class x class rule is not a real
    -- interaction"), and the only thing that lets a reviewed rule leave the
    -- worklist instead of being asked about every release forever. NO DEFAULT: a
    -- ruling must be stated, never guessed.
    applies              boolean     NOT NULL,
    severity             text,
    mechanism            text,
    management           text,
    evidence_grade       text,
    -- NULLABLE, for curated_interaction's own reason: a curator may assert
    -- something no gap view asked about, and a NULL here is what makes "this
    -- grade rests on nothing recorded" visible instead of implied.
    question_uuid         uuid        REFERENCES drugref.open_question(question_uuid)
                                      ON DELETE CASCADE,
    source                text        NOT NULL,
    -- db/027's provenance triple, not an ingest_run FK -- a human curator's
    -- assertion has no ingest run at all. reviewed_against names the release (or
    -- for the ONC list, the paper/version) the judgement was formed against.
    reviewed_by           text        NOT NULL,
    reviewed_against       text        NOT NULL,
    reviewed_at            timestamptz NOT NULL DEFAULT now(),
    superseded_by          bigint      REFERENCES drugref.curated_class_interaction(curated_class_interaction_id),
    -- db/006's finding 1 again, on the overlay side this time: an FK into ci_axis
    -- rather than a CHECK, so this table cannot grade a relationship the candidate
    -- projection (section 1 above) could never produce a row for.
    CONSTRAINT curated_class_interaction_relationship
        FOREIGN KEY (relationship) REFERENCES drugref.ci_axis(relationship),
    -- Plan C's exact vocabulary, reused rather than re-minted -- the same four
    -- levels curated_interaction and curated_condition already use.
    CONSTRAINT curated_class_interaction_severity
        CHECK (severity IN ('contraindicated', 'major', 'moderate', 'minor')),
    CONSTRAINT curated_class_interaction_evidence_grade
        CHECK (evidence_grade IN ('established', 'probable', 'suspected', 'theoretical')),
    CONSTRAINT curated_class_interaction_source CHECK (source IN ('DRUGREF')),
    -- ONE CHECK, not several nullable columns nobody cross-checks -- identical
    -- shape to curated_interaction_ruling_is_complete: "real but ungraded" and
    -- "not real but graded major" both stay unrepresentable.
    CONSTRAINT curated_class_interaction_ruling_is_complete CHECK (
        (applies AND severity IS NOT NULL AND evidence_grade IS NOT NULL)
        OR
        (NOT applies AND severity IS NULL AND evidence_grade IS NULL))
);

-- ---- the floor, REUSED rather than copied ------------------------------------
-- Both functions are db/020's (rewritten for speed by db/023), generic over the
-- natural key. Trigger arguments: the primary-key column, then the natural-key
-- columns a correction must preserve -- identical calling convention to db/029's
-- curated_interaction_append_only, one grain over.
DROP TRIGGER IF EXISTS curated_class_interaction_append_only ON drugref.curated_class_interaction;
CREATE TRIGGER curated_class_interaction_append_only
    BEFORE UPDATE OR DELETE ON drugref.curated_class_interaction
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'curated_class_interaction_id', 'subject_class_uuid', 'object_class_uuid',
        'relationship');

-- DEFERRED, for curated_interaction_single_live's own reason: a correction is
-- momentarily TWO live rows -- between the INSERT and the UPDATE that
-- supersedes -- and an immediate check would reject the only sequence that can
-- express one.
DROP TRIGGER IF EXISTS curated_class_interaction_single_live ON drugref.curated_class_interaction;
CREATE CONSTRAINT TRIGGER curated_class_interaction_single_live
    AFTER INSERT OR UPDATE ON drugref.curated_class_interaction
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions(
        'subject_class_uuid', 'object_class_uuid', 'relationship');

-- PARTIAL and NOT UNIQUE, matching the trigger's predicate exactly -- the same
-- property curated_interaction_live_key carries and assert_live_key_index (the
-- shared test fixture, issue 74) checks by name across all seven existing tables
-- of this shape; this is the eighth. db/023 measured that without this index the
-- trigger is a sequential scan per row and therefore quadratic: 2,000 rows cost
-- 5,773 ms without it, 42 ms with it. NOTHING BUT THE TRIGGER READS IT.
CREATE INDEX IF NOT EXISTS curated_class_interaction_live_key
    ON drugref.curated_class_interaction
       (subject_class_uuid, object_class_uuid, relationship)
    WHERE superseded_by IS NULL;

-- THE OTHER UNREAD INDEX, for curated_interaction_by_question's own reason:
-- Postgres indexes the REFERENCED side of a foreign key automatically and the
-- REFERENCING side never, so question_uuid is bare by default. Two per-ingest
-- readers depend on it once Task 10 lands: register_from_gaps probes this table
-- with NOT EXISTS once per gap kind, and the ON DELETE CASCADE must find this
-- table's rows before the append-only trigger can refuse the delete.
CREATE INDEX IF NOT EXISTS curated_class_interaction_by_question
    ON drugref.curated_class_interaction (question_uuid);

COMMENT ON TABLE drugref.curated_class_interaction IS
    'CURATED, APPEND-ONLY: drugref''s own judgement on a CLASS x CLASS '
    'contraindication rule -- severity, mechanism, management and evidence grade -- '
    'inheriting to every pair the rule expands to once Task 11 builds the '
    'both-sides expansion. The class-subject sibling of curated_interaction '
    '(db/029), which grades a moiety-subject rule; kept as a SEPARATE table rather '
    'than a nullable column there because forbid_multiple_live_assertions (db/023) '
    'compares natural-key columns by EQUALITY and NULL = NULL is never true in SQL '
    '-- a polymorphic subject would make the single-live guard silently stop '
    'guarding for exactly the rows it exists to cover. See this file''s own preamble '
    '(DECISION 1) for the full argument. `source` is NOT in the key, for '
    'curated_interaction''s own reason: one clinical fact, one live drugref '
    'judgement, however many upstream authorities asserted it.';
COMMENT ON COLUMN drugref.curated_class_interaction.applies IS
    'The curator''s RULING, identical meaning to curated_interaction.applies. '
    'False is a real answer -- "reviewed, and this class x class rule is not a '
    'real interaction". No DEFAULT: absence of a row means NOBODY HAS LOOKED.';
COMMENT ON COLUMN drugref.curated_class_interaction.superseded_by IS
    'One-way, set once, always a LATER row on the SAME natural key. A superseded '
    'row is history and is never deleted.';
