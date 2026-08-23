-- db/050_drugcentral_ddi_integrity.sql
-- =============================================================================
-- The constraints and the question text db/049 left implicit.
--
-- db/049 is APPLIED and therefore immutable; everything here is additive. Five
-- findings from this branch's review round, each of which shares one shape: a
-- rule that db/049 states in a COMMENT and enforces nowhere, or a view whose
-- published text claims more than the view itself will admit.
-- =============================================================================

-- ============================================================================
-- 1. upstream_key must be PRESENT
-- ============================================================================
-- MEASURED, not hypothesised: with one `ddi.source_id` set to NULL, the ingest
-- published a row keyed by the empty string and reported clean success. The
-- orchestrator's read-back only notices when TWO rows collide on that empty key,
-- because it compares counts -- one blank is a valid row as far as a count is
-- concerned.
--
-- WHY ONE BLANK IS ALREADY A DEFECT. db/049 calls this column "the upstream
-- AUTHORITY's identifier ... which is what a key anything downstream might cite
-- has to be", and drugcentral_ddi_pair uses it as the total-order tie-break that
-- makes most-severe-wins REPRODUCIBLE between two orientations carrying the same
-- band. The empty string sorts before every real key, so a blank silently wins
-- every tie it takes part in.
--
-- This also retires the last live path to the two-blank collision the read-back
-- was written for: the first blank now aborts, so a second can never arrive.
ALTER TABLE drugref.drugcentral_ddi_assertion
    DROP CONSTRAINT IF EXISTS drugcentral_ddi_assertion_key_present;
ALTER TABLE drugref.drugcentral_ddi_assertion
    ADD CONSTRAINT drugcentral_ddi_assertion_key_present
    CHECK (upstream_key <> '');

-- ============================================================================
-- 2. blank_endpoint -- a malformed row gets its own route
-- ============================================================================
-- A NULL or blank `drug_class1`/`drug_class2` used to resolve to
-- `not_a_substance`, whose own docstring calls it "A CORRECT miss, not a failure
-- of the cascade" -- so a structurally broken row was labelled as a correct
-- reading of an upstream class name. It was then invisible at every other layer
-- too: excluded from drugcentral_ddi_pair by the NULL-uuid filter, excluded from
-- gap_unresolved_ddi_endpoint by the `<> ''` filter that has to be there (a blank
-- is not a question anyone can answer), and summed into `rows_unresolved` beside
-- genuine misses.
--
-- The same argument db/049 already made for `missing_keys_row`: "counted apart so
-- a corrupt extract does not pass for a difficult one". The route now exists, the
-- orchestrator counts it in its own summary bucket, and this CHECK admits it.
-- Measured 2026-08-23: 0 of 7,571.
ALTER TABLE drugref.drugcentral_ddi_assertion
    DROP CONSTRAINT IF EXISTS drugcentral_ddi_assertion_route_1;
ALTER TABLE drugref.drugcentral_ddi_assertion
    ADD CONSTRAINT drugcentral_ddi_assertion_route_1 CHECK (route_1 IN
        ('display_name', 'inchikey', 'cas',
         'not_a_substance', 'no_structural_key', 'missing_keys_row',
         'blank_endpoint', 'unresolved'));
ALTER TABLE drugref.drugcentral_ddi_assertion
    DROP CONSTRAINT IF EXISTS drugcentral_ddi_assertion_route_2;
ALTER TABLE drugref.drugcentral_ddi_assertion
    ADD CONSTRAINT drugcentral_ddi_assertion_route_2 CHECK (route_2 IN
        ('display_name', 'inchikey', 'cas',
         'not_a_substance', 'no_structural_key', 'missing_keys_row',
         'blank_endpoint', 'unresolved'));

-- ============================================================================
-- 3. ddi_source_severity.source -- the CHECK db/049's own rule asked for
-- ============================================================================
-- db/049 gave drugcentral_ddi_assertion.source a CHECK twelve lines below this
-- table and wrote the rule down there -- source columns are "widened per source as
-- authorities land, not left open" -- then left this one open. A typo'd mapping
-- row is inert rather than harmful (the assertion FK rejects any label it cannot
-- reach), so this is symmetry rather than a defect; the asymmetry was unintended
-- and the rule is the repo's.
ALTER TABLE drugref.ddi_source_severity
    DROP CONSTRAINT IF EXISTS ddi_source_severity_source;
ALTER TABLE drugref.ddi_source_severity
    ADD CONSTRAINT ddi_source_severity_source
    CHECK (source IN ('DRUGCENTRAL'));

-- ============================================================================
-- 4. gap_unresolved_ddi_endpoint -- carry the ROUTE, and fold the way Python does
-- ============================================================================
-- TWO CORRECTIONS, both in the fold and the projection rather than the filter.
-- The filter itself was right and is unchanged: on a NULL uuid, NEVER on the
-- route vocabulary, for db/006's reason.
--
-- (a) THE ROUTE IS NOW PUBLISHED, because the question text derived from this view
--     asserted something only one route supports: "DrugCentral resolves it to a
--     structure with an InChIKey or a CAS number, and no live identity_claim in
--     drugref carries either". For `not_a_substance` DrugCentral has no struct_id
--     at all; for `no_structural_key` it has one carrying neither key; for
--     `missing_keys_row` the extract is broken. The view deliberately admits all
--     of them, so the text has to be able to tell them apart -- and question_uuid
--     is IMMORTAL and externally cited, so a question minted under the wrong
--     story cannot be quietly reworded away. It measures 0 on this release only
--     because every one of the 10 names happens to land on `unresolved`: the
--     guard was the data, not the code.
--
--     Routes stay one-per-name here, which is what GROUP BY assumes: resolution is
--     a pure function of the FOLDED name, and this view groups by exactly that
--     folded name, so every row contributing to a group resolved identically.
--     min() is therefore an identity over the group, not a choice -- stated
--     because a reader is entitled to ask why an aggregate is safe here.
--
-- (b) THE FOLD NOW MATCHES `fold_name`. db/049's comment says the fold "is
--     drugcentral_resolve.fold_name's rule -- restated here", and it was not:
--     one-argument btrim() strips SPACES ONLY, while Python's str.strip() also
--     strips tab, newline, CR, form feed and vertical tab. Two homes for one rule
--     that were not the same rule, feeding an immortal question_uuid. Measured
--     2026-08-23: all 7,621 endpoint values are clean, so nothing minted under the
--     old fold is wrong on THIS release -- the divergence was latent, and it is
--     cheaper to close than to keep re-verifying.
-- DROP then CREATE, not CREATE OR REPLACE: `route` lands in the MIDDLE of the
-- column list and PostgreSQL's REPLACE may only APPEND columns. Nothing else in
-- the schema selects from this view -- questions._GAP_SOURCES reads it from
-- Python -- so there is nothing to cascade to.
DROP VIEW IF EXISTS drugref.gap_unresolved_ddi_endpoint;
CREATE VIEW drugref.gap_unresolved_ddi_endpoint AS
SELECT e.source,
       e.endpoint_name,
       min(e.route)            AS route,
       count(*)                AS row_count,
       max(r.upstream_release) AS upstream_release
FROM  (SELECT a.source, a.ingest_run, a.route_1 AS route,
              lower(btrim(a.endpoint_1_name, E' \t\n\r\f\v')) AS endpoint_name
         FROM drugref.drugcentral_ddi_assertion a
        WHERE a.moiety_1_uuid IS NULL
        UNION ALL
       SELECT a.source, a.ingest_run, a.route_2 AS route,
              lower(btrim(a.endpoint_2_name, E' \t\n\r\f\v')) AS endpoint_name
         FROM drugref.drugcentral_ddi_assertion a
        WHERE a.moiety_2_uuid IS NULL) e
JOIN  drugref.ingest_run r ON r.ingest_run_id = e.ingest_run
      -- A blank endpoint is not a question anyone can answer, and the resolver
      -- already refuses to look one up (an empty structural key would otherwise
      -- collapse every keyless substance onto one moiety). It is not lost by being
      -- dropped here: it now carries route `blank_endpoint` and the orchestrator
      -- counts it in a bucket of its own.
WHERE e.endpoint_name <> ''
GROUP BY e.source, e.endpoint_name;

COMMENT ON VIEW drugref.gap_unresolved_ddi_endpoint IS
    'Endpoint names drugref cannot key to a moiety, WHATEVER THE REASON -- the '
    'filter is on a NULL uuid and never on the route vocabulary, so `route` is '
    'published beside each name rather than assumed. ONE ROW PER FOLDED NAME, '
    'because a curator resolves a name rather than a row. Measured 2026-08-23: 37 '
    'rows over 10 names, every one on route ''unresolved'' -- DrugCentral holds an '
    'InChIKey or a CAS number that no live identity_claim carries. Those are '
    'REGISTRY-COVERAGE work, not a synonym list: ''phytomenadione'' is the INN for '
    'phytonadione and ''atracurium'' the base of the besylate drugref already '
    'holds, so an answer could change something, which is db/012''s test for '
    'whether the review gate may ask at all. A name on route ''not_a_substance'' '
    'is a different question and says so. The question retires by itself when the '
    'claim lands.';
COMMENT ON COLUMN drugref.gap_unresolved_ddi_endpoint.route IS
    'HOW the endpoint failed to reach a moiety, from drugcentral_resolve.ROUTES. '
    'One route per folded name: resolution is a pure function of the folded name, '
    'so every row in a group resolved the same way.';

-- ============================================================================
-- 5. One correction db/049 cannot carry, because db/049 is applied
-- ============================================================================
-- db/049 section 5 justifies exact_ddi_pair being a NEW view rather than an arm
-- on ddi_candidate_pair with: "db/034 then MEASURED that arm costing 3.6x with
-- the new grain EMPTY". The FIGURE is right and quoted verbatim from db/034, and
-- the CONCLUSION stands -- an additive view changes no existing query. What is
-- misattributed is the MECHANISM: per db/034's own header the ~3.6x (1.4 ms ->
-- ~5.1 ms) came from widening `ci_class_subtree`'s RECURSIVE SEED, which inflated
-- the planner's row estimate ~5x and flipped a Hash Join to a Merge Join
-- re-scanning `class_parent` once per level. db/034 fixed that by giving the class
-- grain its own walk and KEPT the arm on ddi_candidate_pair. It never measured a
-- union arm and rejected one.
--
-- Recorded here because migrations are immutable once applied and db/049 is in the
-- ledger. Anyone reusing that sentence as evidence should read db/034 first.
COMMENT ON VIEW drugref.exact_ddi_pair IS
    'Every EXACT drug-drug pair some upstream authority asserts, whatever its '
    'grain -- the read path moiety_contraindication has lacked since db/014. '
    'KEYED UNORDERED (moiety_lo, moiety_hi), because "am I about to co-prescribe '
    'these two?" is an unordered question; a source that DOES assert a direction '
    'keeps it in subject_moiety/object_moiety. CANDIDATE TIER, and DELIBERATELY '
    'NOT A SUPERSET OF ddi_candidate_pair: that view expands CLASS rules and this '
    'one does not, so a consumer wanting everything reads both. severity is NULL '
    'wherever the authority states none. NOTE for anyone reading db/049''s '
    'rationale for this view being separate: db/034 measured the ~3.6x cost of '
    'widening ci_class_subtree''s recursive seed, not of a union arm, and it kept '
    'the arm it had -- the conclusion holds, the cited mechanism did not.';
