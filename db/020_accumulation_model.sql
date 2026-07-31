-- db/020_accumulation_model.sql
-- Plan C: the ACCUMULATION model -- many drugs, one effect that adds up.
--
-- WHAT THE CURRENT MODEL CANNOT SAY. class_contraindication and ddi_candidate_pair
-- are PAIRWISE: drug A against drug B. The hazard a prescriber actually meets is
-- often not pairwise at all -- three drugs each mildly serotonergic, or four each
-- mildly bleeding-prone, none of which pairs into a warning while the regimen as a
-- whole is dangerous. This migration adds the two shapes that expresses:
--
--   * ACCUMULATION (the primary mechanism) -- an effect that ADDS UP, with a
--     threshold at which it is worth saying. Its leverage comes from data drugref
--     ALREADY holds: has_PE membership answers "which drugs produce this effect"
--     for 18,639 gated rows, so the only missing ingredient is judgement, and
--     judgement is small enough to hand-curate.
--   * GROUPS (the deliberate minority) -- role-based combinations where the members
--     play DIFFERENT parts and a count is meaningless. The triple whammy (NSAID +
--     RAAS blocker + diuretic) is one group with three roles; counting "three
--     nephrotoxic drugs" would fire on three NSAIDs, which is a different and much
--     less specific claim.
--
-- BOTH ARE CURATED, so both live in the append-only signed overlay (slice 5c's
-- tier), never in a rebuildable projection. Neither duplicates ingested data:
-- effect_contribution does not LIST contributors (membership already does), it only
-- promotes their grade.
--
-- THIS MIGRATION SHIPS THE TABLES EMPTY. Spec 11 step 7 asks for the schema, the
-- read contract and the gap views with an empty curation set -- curation itself is
-- step 8, continuous work bound by spec 12-H's precondition (audit every file and
-- every predicate of a source before curating a gap it may already cover).
--
-- CANDIDATE TIER, restated because this migration widens what drugref SAYS: a
-- threshold being met is an input to review, never a rendered warning; grades and
-- thresholds are drugref's OWN clinical judgements rather than upstream facts, which
-- is why they are attributed to source 'DRUGREF'; and absence carries no information
-- (MED-RT has no nephrotoxicity concept at all, so "no finding" may simply mean "not
-- modelled anywhere").

-- ============================================================================
-- 1. THE SOURCE TRIO -- drugref becomes an authority in its own registry
-- ============================================================================
-- Spec 6: three places pin an authority's spelling and they are a TRIO, not a pair.
-- ids._SOURCE_CANONICAL is the third and is edited in Python; the two CHECKs are
-- here. Missing the ingest_run one is what actually stops everything: every curated
-- row below carries `ingest_run`, so a 'DRUGREF' class with no run to attribute it
-- to cannot be written at all.
--
-- NOTE the constraint is named `ingest_run_source`, NOT `ingest_run_source_check` --
-- that suffix is only what Postgres auto-generates for an unnamed CHECK, and db/005
-- named this one explicitly. db/009 records the same trap.
ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_source;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_source
    CHECK (source IN ('UNII', 'CHEBI', 'MED-RT', 'MeSH', 'PBS', 'DRUGREF'));

ALTER TABLE drugref.substance_class DROP CONSTRAINT IF EXISTS substance_class_source;
ALTER TABLE drugref.substance_class ADD CONSTRAINT substance_class_source
    CHECK (source IN ('MED-RT', 'MeSH', 'DRUGREF'));

COMMENT ON COLUMN drugref.substance_class.source IS
    'The authority that DEFINES this class. Since Plan C that may be drugref itself '
    '(''DRUGREF''), for an effect no ingested vocabulary names -- nephrotoxicity has no '
    'MED-RT class, and MED-RT defines no disease concepts at all. A consumer must '
    'always be able to ask WHICH authority asserted something, and this column is the '
    'answer. Extend it together with ingest_run''s own CHECK and ids._SOURCE_CANONICAL: '
    'they are a trio, and a source admitted to one but not the others is stored under '
    'a spelling some per-source rebuild will silently miss.';

-- ============================================================================
-- 2. THE OVERLAY FLOOR -- one trigger function, not four near-copies
-- ============================================================================
-- Every assertion table below has the same skeleton (spec 5.0) and therefore the
-- same floor: DELETE forbidden, only `superseded_by` may change, set once, never
-- unset, always pointing at a LATER row carrying the SAME natural key.
--
-- WRITTEN ONCE AND PARAMETERISED, deliberately. db/005 wrote forbid_claim_rewrite by
-- hand for one table and db/007 wrote two more; four more hand copies is four places
-- for one rule to drift, which is exactly the defect the interaction debt round found
-- (a reach measure stated twice where only one copy learned a correction). The
-- trigger arguments are the primary-key column followed by the natural-key columns.
--
-- The "only superseded_by may change" test compares the WHOLE ROW minus that one key
-- rather than naming columns. That is what makes it generic -- and it also means a
-- column added to any of these tables in a later migration is protected automatically,
-- with no edit here to forget.
CREATE OR REPLACE FUNCTION drugref.forbid_overlay_rewrite() RETURNS trigger AS $$
DECLARE
    pk_col   text  := TG_ARGV[0];
    old_j    jsonb := to_jsonb(OLD);
    new_j    jsonb := to_jsonb(NEW);
    target_j jsonb;
    col      text;
    row_id   bigint;
    points_at bigint;
    i        int;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drugref.% is append-only: DELETE forbidden', TG_TABLE_NAME;
    END IF;

    IF (old_j - 'superseded_by') <> (new_j - 'superseded_by') THEN
        RAISE EXCEPTION
            'drugref.% is append-only: only superseded_by may change', TG_TABLE_NAME;
    END IF;

    -- ONE-WAY: NULL -> an id, exactly once. Un-setting would resurrect a
    -- corrected-away clinical statement as live; re-pointing would rewrite history
    -- that a consumer may already have been alerted against.
    IF OLD.superseded_by IS NOT NULL
       AND NEW.superseded_by IS DISTINCT FROM OLD.superseded_by THEN
        RAISE EXCEPTION 'drugref.%.superseded_by is one-way: row % is already superseded by %',
            TG_TABLE_NAME, old_j ->> pk_col, OLD.superseded_by;
    END IF;

    IF NEW.superseded_by IS NOT NULL THEN
        row_id    := (new_j ->> pk_col)::bigint;
        points_at := NEW.superseded_by;
        -- The correction is always the LATER row (insert-new-then-point-old-at-new),
        -- so the chain strictly increases and can never close into a cycle. A cycle
        -- would make BOTH statements vanish from every `superseded_by IS NULL` read
        -- at once, with nothing anywhere reporting it.
        IF points_at <= row_id THEN
            RAISE EXCEPTION
                'drugref.%: superseded_by must reference a LATER row (% <= %)',
                TG_TABLE_NAME, points_at, row_id;
        END IF;

        EXECUTE format('SELECT to_jsonb(t) FROM drugref.%I t WHERE %I = $1',
                       TG_TABLE_NAME, pk_col)
            INTO target_j USING points_at;

        -- db/005's same-moiety rule, generalised to whatever this table's natural key
        -- is: a correction replaces a statement about THIS subject, not a different
        -- one. Pointing across subjects is not a correction, it is a merge, and there
        -- are no merge semantics here.
        FOR i IN 1 .. TG_NARGS - 1 LOOP
            col := TG_ARGV[i];
            IF (target_j -> col) IS DISTINCT FROM (new_j -> col) THEN
                RAISE EXCEPTION
                    'drugref.%: a correction must keep the same %  (% vs %)',
                    TG_TABLE_NAME, col, new_j ->> col, target_j ->> col;
            END IF;
        END LOOP;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION drugref.forbid_overlay_rewrite() IS
    'The append-only floor shared by every curated assertion table (spec 5.0). '
    'Trigger arguments: the primary-key column, then the natural-key columns a '
    'correction must preserve. Modelled on db/005''s forbid_claim_rewrite and written '
    'ONCE rather than copied per table, because one rule in four places is one rule '
    'that will drift.';

-- ---- at most one LIVE row per natural key, checked at COMMIT -----------------
--
-- SPEC 5.0 PRESCRIBES A PARTIAL UNIQUE INDEX HERE AND IT CANNOT WORK ON THESE FOUR
-- TABLES. The spec reasons from db/005's identity_claim, where it does work -- but
-- only because a correction there carries a DIFFERENT value, so the corrected row
-- lands on a different natural key and never collides. A correction here keeps the
-- same key by definition (a revised threshold is still a statement about THE SAME
-- effect), so the two rows are momentarily live under one key and an immediate index
-- rejects the INSERT -- re-creating, by a different route, exactly the trap the
-- surrogate primary key was introduced to escape.
--
-- db/007 already met this on question_state and its answer is the one adopted here: a
-- DEFERRED constraint trigger. It cannot be a deferred unique CONSTRAINT either --
-- those cannot be partial, and "unique among live rows" is inherently partial.
--
-- Generic over the natural key by comparing a jsonb projection of it, so one function
-- serves all four tables. The trigger arguments are the natural-key columns.
CREATE OR REPLACE FUNCTION drugref.forbid_multiple_live_assertions() RETURNS trigger AS $$
DECLARE
    new_j jsonb := to_jsonb(NEW);
    key_j jsonb := '{}'::jsonb;
    live  int;
    i     int;
BEGIN
    FOR i IN 0 .. TG_NARGS - 1 LOOP
        key_j := key_j || jsonb_build_object(TG_ARGV[i], new_j -> TG_ARGV[i]);
    END LOOP;

    EXECUTE format(
        'SELECT count(*) FROM drugref.%I t WHERE t.superseded_by IS NULL '
        'AND to_jsonb(t) @> $1', TG_TABLE_NAME) INTO live USING key_j;

    IF live > 1 THEN
        RAISE EXCEPTION
            'drugref.%: % live rows for natural key %; at most one row per key may '
            'have superseded_by IS NULL', TG_TABLE_NAME, live, key_j;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION drugref.forbid_multiple_live_assertions() IS
    'At most one LIVE row per natural key, checked at COMMIT. Deferred rather than a '
    'partial unique index (which spec 5.0 asks for) because a correction to these '
    'tables preserves the natural key, so both rows are briefly live and an immediate '
    'check would reject the only sequence that can express a correction. Same '
    'reasoning and same shape as db/007''s forbid_multiple_live_states.';

-- ============================================================================
-- 3. additive_effect -- which effects accumulate, and when it matters
-- ============================================================================
-- Expected cardinality: tens of rows, ever.
--
-- WHY THE NATURAL KEY IS NOT THE PRIMARY KEY, since this is the table most likely to
-- be "simplified" back: correction-by-overlay means INSERTING the new row and THEN
-- pointing the old one at it. Both rows carry the same effect_class_uuid, so a
-- primary key on it rejects the correction outright and in-place mutation becomes the
-- only possible implementation -- precisely what the overlay exists to prevent.
-- db/001 shipped that defect on identity_claim and db/005 had to repair it.
CREATE TABLE IF NOT EXISTS drugref.additive_effect (
    additive_effect_id bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    effect_class_uuid  uuid        NOT NULL REFERENCES drugref.substance_class(class_uuid),
    -- Two smallints express the realistic rules: "any two contributors" = (0,2);
    -- "a major plus anything else" = (1,2); "a major alone is worth saying" = (1,1).
    threshold_major    smallint    NOT NULL,
    threshold_total    smallint    NOT NULL,
    severity           text        NOT NULL,
    clinical_note      text,
    source             text        NOT NULL,
    ingest_run         bigint      NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    asserted_at        timestamptz NOT NULL DEFAULT now(),
    superseded_by      bigint      REFERENCES drugref.additive_effect(additive_effect_id),
    -- threshold_major = 0 is LEGAL AND LOAD-BEARING. With every uncurated member
    -- defaulting to `minor` (spec 5.2), an effect at (0,2) fires on any two members of
    -- a subtree most of which nobody has looked at. The schema cannot forbid it --
    -- (0,2) is the CORRECT encoding for a genuinely curated effect where every member
    -- really does count -- so gap_uncurated_threshold surfaces the risky combination
    -- instead of prohibiting the legitimate one.
    CONSTRAINT additive_effect_thresholds
        CHECK (threshold_major >= 0
               AND threshold_total >= 1
               AND threshold_total >= threshold_major),
    -- The same four levels a prescriber-facing consumer already expects, CHECKed
    -- rather than free text so it cannot drift one curator at a time.
    CONSTRAINT additive_effect_severity
        CHECK (severity IN ('contraindicated', 'major', 'moderate', 'minor')),
    CONSTRAINT additive_effect_source CHECK (source IN ('DRUGREF'))
);

DROP TRIGGER IF EXISTS additive_effect_single_live ON drugref.additive_effect;
CREATE CONSTRAINT TRIGGER additive_effect_single_live
    AFTER INSERT OR UPDATE ON drugref.additive_effect
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions(
        'effect_class_uuid');

DROP TRIGGER IF EXISTS additive_effect_append_only ON drugref.additive_effect;
CREATE TRIGGER additive_effect_append_only
    BEFORE UPDATE OR DELETE ON drugref.additive_effect
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'additive_effect_id', 'effect_class_uuid');

COMMENT ON TABLE drugref.additive_effect IS
    'CURATED, APPEND-ONLY (spec 5.1): the effects drugref judges to ACCUMULATE across '
    'a regimen, and the threshold at which that is worth telling a prescriber. Fires '
    'when majors >= threshold_major AND contributors >= threshold_total, counted over '
    'additive_effect_contributor. CANDIDATE TIER: meeting a threshold is an input to '
    'review, never an auto-rendered warning, and ABSENCE CARRIES NO INFORMATION -- an '
    'effect with no row here may be one nothing upstream models at all. The thresholds '
    'and severity are drugref''s OWN clinical judgement (source = ''DRUGREF''), not an '
    'upstream fact, and are traceable to evidence through question_evidence wherever '
    'they rest on any. CURATED IS NOT VERIFIED: a grade with no evidence behind it is '
    'an opinion, and the question registry is what makes that visible.';
COMMENT ON COLUMN drugref.additive_effect.threshold_major IS
    'Minimum `major` contributors. ZERO IS LEGAL AND DANGEROUS: uncurated members '
    'default to `minor`, so (0,2) fires on any two members of a subtree nobody has '
    'reviewed. It is also the correct encoding for a fully curated effect, so it is '
    'surfaced by gap_uncurated_threshold rather than forbidden. Prefer >= 1 when '
    'curating a new effect.';
COMMENT ON COLUMN drugref.additive_effect.threshold_total IS
    'Minimum contributors of ANY grade. >= 1 always: a rule firing on zero '
    'contributors would fire on every regimen.';
COMMENT ON COLUMN drugref.additive_effect.superseded_by IS
    'One-way, set once, always a LATER row on the SAME effect class. A superseded row '
    'is history and is never deleted: what drugref believed, and when, stays '
    'answerable -- which matters most for exactly the rows that fired an alert.';

-- ============================================================================
-- 4. effect_contribution -- grade, not enumeration
-- ============================================================================
-- THIS TABLE DOES NOT LIST CONTRIBUTORS. Membership already does. It only PROMOTES:
--
--   contributor set = members of effect_class_uuid, INCLUDING DAG descendants;
--   grade defaults to `minor`; a row here promotes a contributor class to `major`.
--
-- PROMOTION REGRADES; IT NEVER RECRUITS. A row changes the grade of moieties that are
-- ALREADY contributors -- formally the INTERSECTION of the promoted class's membership
-- with the effect class's membership-plus-descendants. It cannot add a moiety to the
-- contributor set. The opposite reading is the one an implementer reaches for, so
-- db/021's read view is built to make it structurally impossible.
--
-- KEYED ON CLASS so a grade inherits to every member -- the "curate once, apply
-- widely" lever doing real work. Curating bleeding means promoting the handful of
-- classes whose members are the serious bleeders and leaving ~100 other members at
-- the default: a few rows, not a hundred.
CREATE TABLE IF NOT EXISTS drugref.effect_contribution (
    effect_contribution_id bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    effect_class_uuid      uuid        NOT NULL REFERENCES drugref.substance_class(class_uuid),
    contributor_class_uuid uuid        NOT NULL REFERENCES drugref.substance_class(class_uuid),
    magnitude              text        NOT NULL,
    source                 text        NOT NULL,
    ingest_run             bigint      NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    asserted_at            timestamptz NOT NULL DEFAULT now(),
    superseded_by          bigint      REFERENCES drugref.effect_contribution(effect_contribution_id),
    -- Two values, deliberately (tension E): a finer scale invites precision the
    -- evidence cannot support, and DOSE -- the thing that would actually justify a
    -- scale -- is unavailable until slice 4. Widening this later is additive.
    CONSTRAINT effect_contribution_magnitude CHECK (magnitude IN ('major', 'minor')),
    CONSTRAINT effect_contribution_source    CHECK (source IN ('DRUGREF'))
);

DROP TRIGGER IF EXISTS effect_contribution_single_live ON drugref.effect_contribution;
CREATE CONSTRAINT TRIGGER effect_contribution_single_live
    AFTER INSERT OR UPDATE ON drugref.effect_contribution
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions(
        'effect_class_uuid', 'contributor_class_uuid');

DROP TRIGGER IF EXISTS effect_contribution_append_only ON drugref.effect_contribution;
CREATE TRIGGER effect_contribution_append_only
    BEFORE UPDATE OR DELETE ON drugref.effect_contribution
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'effect_contribution_id', 'effect_class_uuid', 'contributor_class_uuid');

COMMENT ON TABLE drugref.effect_contribution IS
    'CURATED, APPEND-ONLY (spec 5.2): which CLASSES contribute to an additive effect '
    'strongly enough to count as `major`. IT DOES NOT LIST CONTRIBUTORS -- '
    'class_membership already does, and every member of the effect class is a '
    'contributor at `minor` by default. A row here REGRADES members it already '
    'reaches and can never RECRUIT one: a promoted class sharing no member with the '
    'effect is a silent no-op, which gap_ineffective_contribution reports.';
COMMENT ON COLUMN drugref.effect_contribution.magnitude IS
    'AN EXPLICIT `minor` ROW IS NOT REDUNDANT. It records "a curator looked at this '
    'class and it really is minor", which is a different fact from "nobody has '
    'looked" even though both grade to minor. That distinction is what keeps the '
    'review queue finite: gap_ungraded_contribution lists classes with NO row here, '
    'not classes graded minor -- reading it the other way would re-earn the same '
    'curator attention forever.';

-- ============================================================================
-- 5. interaction_group -- the role-based exceptions, in three tables
-- ============================================================================
-- THREE tables, not two, for the same reason the moiety spine has substance_moiety
-- beside identity_claim: the group's IDENTITY must outlive any particular assertion
-- about it, because interaction_group_member and any external citation point at it.

-- 5a. Identity only. Append-only and NEVER superseded: it holds a deterministic UUID
--     and its provenance, so there is nothing here that can be wrong. Retiring a
--     group means superseding its ASSERTION, not deleting its identity -- the same
--     discipline that keeps moiety_uuid immortal while its claims come and go.
CREATE TABLE IF NOT EXISTS drugref.interaction_group (
    group_uuid        uuid   PRIMARY KEY,
    source            text   NOT NULL,
    source_code       text   NOT NULL,
    first_seen_ingest bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    CONSTRAINT interaction_group_source      CHECK (source IN ('DRUGREF')),
    CONSTRAINT interaction_group_code_unique UNIQUE (source, source_code)
);

CREATE OR REPLACE FUNCTION drugref.forbid_group_identity_rewrite() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drugref.interaction_group is append-only: DELETE forbidden';
    END IF;
    IF NEW.group_uuid <> OLD.group_uuid OR NEW.source <> OLD.source
       OR NEW.source_code <> OLD.source_code THEN
        RAISE EXCEPTION 'drugref.interaction_group identity is immortal: it may not change';
    END IF;
    IF NEW.first_seen_ingest <> OLD.first_seen_ingest THEN
        RAISE EXCEPTION 'drugref.interaction_group.first_seen_ingest is write-once provenance';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS interaction_group_immortal ON drugref.interaction_group;
CREATE TRIGGER interaction_group_immortal
    BEFORE UPDATE OR DELETE ON drugref.interaction_group
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_group_identity_rewrite();

COMMENT ON TABLE drugref.interaction_group IS
    'Immortal identity of a ROLE-BASED interaction (spec 5.3) -- the exception to the '
    'accumulation model, for combinations whose members play DIFFERENT parts so that '
    'counting them says nothing. group_uuid = ids.mint_group_uuid(''DRUGREF'', '
    'source_code), deterministic exactly as class_uuid is, so two drugref instances '
    'curating the same group agree with no coordination. Its OWN namespace, not '
    'CLASS_NAMESPACE: a group is not a substance_class, and one UUID for two kinds of '
    'thing would silently join a group''s members to a class''s.';

-- 5b. What is CLAIMED about the group -- the part that gets corrected.
CREATE TABLE IF NOT EXISTS drugref.interaction_group_assertion (
    interaction_group_assertion_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    group_uuid    uuid        NOT NULL REFERENCES drugref.interaction_group(group_uuid),
    name          text        NOT NULL,
    severity      text        NOT NULL,
    clinical_note text,
    source        text        NOT NULL,
    ingest_run    bigint      NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    asserted_at   timestamptz NOT NULL DEFAULT now(),
    superseded_by bigint      REFERENCES drugref.interaction_group_assertion(interaction_group_assertion_id),
    CONSTRAINT interaction_group_assertion_severity
        CHECK (severity IN ('contraindicated', 'major', 'moderate', 'minor')),
    CONSTRAINT interaction_group_assertion_source CHECK (source IN ('DRUGREF'))
);

DROP TRIGGER IF EXISTS interaction_group_assertion_single_live
    ON drugref.interaction_group_assertion;
CREATE CONSTRAINT TRIGGER interaction_group_assertion_single_live
    AFTER INSERT OR UPDATE ON drugref.interaction_group_assertion
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions('group_uuid');

DROP TRIGGER IF EXISTS interaction_group_assertion_append_only
    ON drugref.interaction_group_assertion;
CREATE TRIGGER interaction_group_assertion_append_only
    BEFORE UPDATE OR DELETE ON drugref.interaction_group_assertion
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'interaction_group_assertion_id', 'group_uuid');

COMMENT ON TABLE drugref.interaction_group_assertion IS
    'CURATED, APPEND-ONLY: what drugref claims about an interaction group -- its name, '
    'severity and clinical note. Separate from interaction_group so a correction never '
    'touches the identity that members and external citations point at. CANDIDATE '
    'TIER: a covered group is an input to review, not an auto-rendered warning.';

-- 5c. Which classes satisfy which ROLE. Versioned too -- and the first draft of the
--     design got exactly this wrong: it gave the header superseded_by and left the
--     members a bare natural-key table, so the header was append-only while the part
--     that actually determines whether the group FIRES was mutable in place.
--     Correcting which classes satisfy `diuretic` would have silently rewritten the
--     record of what drugref believed when it fired an alert.
CREATE TABLE IF NOT EXISTS drugref.interaction_group_member (
    interaction_group_member_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    group_uuid    uuid        NOT NULL REFERENCES drugref.interaction_group(group_uuid),
    role          text        NOT NULL,
    class_uuid    uuid        NOT NULL REFERENCES drugref.substance_class(class_uuid),
    source        text        NOT NULL,
    ingest_run    bigint      NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    asserted_at   timestamptz NOT NULL DEFAULT now(),
    superseded_by bigint      REFERENCES drugref.interaction_group_member(interaction_group_member_id),
    CONSTRAINT interaction_group_member_source CHECK (source IN ('DRUGREF'))
);

DROP TRIGGER IF EXISTS interaction_group_member_single_live
    ON drugref.interaction_group_member;
CREATE CONSTRAINT TRIGGER interaction_group_member_single_live
    AFTER INSERT OR UPDATE ON drugref.interaction_group_member
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions(
        'group_uuid', 'role', 'class_uuid');

DROP TRIGGER IF EXISTS interaction_group_member_append_only
    ON drugref.interaction_group_member;
CREATE TRIGGER interaction_group_member_append_only
    BEFORE UPDATE OR DELETE ON drugref.interaction_group_member
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'interaction_group_member_id', 'group_uuid', 'role', 'class_uuid');

COMMENT ON TABLE drugref.interaction_group_member IS
    'CURATED, APPEND-ONLY: which classes satisfy which ROLE in a group. A group fires '
    'when the regimen covers EVERY DISTINCT role among its LIVE members. There is no '
    'separate roles table on purpose -- the required roles are SELECT DISTINCT role '
    'WHERE superseded_by IS NULL -- so a role cannot exist without a live member that '
    'satisfies it, and superseding the last member of a role REMOVES the role rather '
    'than leaving a group that can never fire again.';
COMMENT ON COLUMN drugref.interaction_group_member.role IS
    'The part this class plays, e.g. ''NSAID'' / ''RAAS blocker'' / ''diuretic''. Two '
    'drugs satisfying the SAME role do not cover a second one, which is the whole '
    'reason groups exist beside accumulation: counting three nephrotoxic drugs would '
    'fire on three NSAIDs, a much weaker claim than the triple whammy.';

CREATE INDEX IF NOT EXISTS additive_effect_by_class
    ON drugref.additive_effect (effect_class_uuid);
CREATE INDEX IF NOT EXISTS effect_contribution_by_effect
    ON drugref.effect_contribution (effect_class_uuid);
CREATE INDEX IF NOT EXISTS interaction_group_member_by_group
    ON drugref.interaction_group_member (group_uuid);
