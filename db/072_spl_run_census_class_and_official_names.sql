-- db/072_spl_run_census_class_and_official_names.sql
--
-- Issues 215 and 229: two more groups of SPL counts, stored per run beside
-- db/057's (never edit db/057: applied migrations are checksum-immutable).
--
-- ⇒ WHY. Both groups were only ever PRINTED (SplSummary's line, or the progress
-- `say()` that is a no-op when `progress` is None). A number that is only printed
-- cannot be compared from one release to the next -- the same gap db/057 closed for
-- issue 163's counts.
--
--   * #215, the class pass's deliberate drops: class rows dropped at a sentence
--     boundary (the design's "sentence-splitter failure" signal, R17), class rows
--     dropped on a non-drug head word (R19/P2), class keys dropped as moiety names
--     (R16) and as UNII official names (R19/P1).
--   * #229, what became of each UNII official name the drug pass could adopt as a
--     moiety key (#213): adopted, already the display name, shadowed, ambiguous,
--     excluded, folding to no tokens, plus exclusion entries matching no name.
--     New names arrive with each UNII release, which is why these drift.
--
-- ⇒ NULL MEANS "NOT MEASURED", NEVER ZERO. Rows written before this migration
-- carry NULL in every new column: those counts were never stored and there is no
-- backfill. A run given no UNII_Names file (the CLI requires one; the library does
-- not) never ran #213 or R19/P1, so the official-name group -- including
-- `class_keys_shadowed_by_official_names` -- is NULL while the class drops are not.
-- A 0 there would read as "measured, nothing adopted" in the next comparison.
--
-- ⇒ EACH GROUP IS MEASURED WHOLE OR NOT AT ALL (`num_nulls`), so no INSERT can
-- store half a group; and every count has its own `>= 0` (#187). The CHECK groups
-- are NOT the two bullets above: R19/P1's drop runs only with a UNII_Names file,
-- so it sits with the seven official-name counts (3 + 8 columns).
-- The column names equal SplSummary's field names: `spl_census.write_run_census`
-- inserts by that one list, so a column cannot be filled from a neighbour's field.

ALTER TABLE drugref.spl_run_census
    ADD COLUMN class_spans_dropped_at_a_boundary      bigint
        CHECK (class_spans_dropped_at_a_boundary >= 0),
    ADD COLUMN class_spans_dropped_on_a_non_drug_head bigint
        CHECK (class_spans_dropped_on_a_non_drug_head >= 0),
    ADD COLUMN class_keys_shadowed_by_moieties        bigint
        CHECK (class_keys_shadowed_by_moieties >= 0),
    ADD COLUMN class_keys_shadowed_by_official_names  bigint
        CHECK (class_keys_shadowed_by_official_names >= 0),
    ADD COLUMN official_names_adopted                 bigint
        CHECK (official_names_adopted >= 0),
    ADD COLUMN official_names_already_named           bigint
        CHECK (official_names_already_named >= 0),
    ADD COLUMN official_names_shadowed                bigint
        CHECK (official_names_shadowed >= 0),
    ADD COLUMN official_names_ambiguous               bigint
        CHECK (official_names_ambiguous >= 0),
    ADD COLUMN official_names_excluded                bigint
        CHECK (official_names_excluded >= 0),
    ADD COLUMN official_names_unfoldable              bigint
        CHECK (official_names_unfoldable >= 0),
    ADD COLUMN official_name_exclusions_unused        bigint
        CHECK (official_name_exclusions_unused >= 0),
    ADD CONSTRAINT spl_run_census_class_drops_together CHECK (
        num_nulls(class_spans_dropped_at_a_boundary,
                  class_spans_dropped_on_a_non_drug_head,
                  class_keys_shadowed_by_moieties) IN (0, 3)),
    ADD CONSTRAINT spl_run_census_official_names_together CHECK (
        num_nulls(official_names_adopted, official_names_already_named,
                  official_names_shadowed, official_names_ambiguous,
                  official_names_excluded, official_names_unfoldable,
                  official_name_exclusions_unused,
                  class_keys_shadowed_by_official_names) IN (0, 8));

COMMENT ON COLUMN drugref.spl_run_census.class_spans_dropped_at_a_boundary IS
    'Class (span, class) rows dropped because the span held a sentence boundary '
    '(R17): a jump reads as the sentence splitter failing on a new release. NULL: '
    'run predates db/072.';
COMMENT ON COLUMN drugref.spl_run_census.class_spans_dropped_on_a_non_drug_head IS
    'Class (span, class) rows dropped because the class word qualifies a non-drug '
    'head word, e.g. "anticoagulant effect" (R19/P2). NULL: run predates db/072.';
COMMENT ON COLUMN drugref.spl_run_census.class_keys_shadowed_by_moieties IS
    'Class match keys dropped because they equal a moiety display name (R16). '
    'NULL: run predates db/072.';
COMMENT ON COLUMN drugref.spl_run_census.class_keys_shadowed_by_official_names IS
    'Class match keys dropped because they equal a UNII official name (R19/P1). '
    'NULL: no UNII_Names file was given (not checked), or the run predates db/072.';
COMMENT ON COLUMN drugref.spl_run_census.official_names_adopted IS
    'UNII official names adopted as drug-pass moiety match keys (#213). NULL: no '
    'UNII_Names file was given (nothing adopted, not checked), or the run predates '
    'db/072.';
-- The other six official-name counts share official_names_adopted's NULL meaning.
-- Units differ: `unfoldable` counts UNII_Names ROWS; the rest count folded names.
COMMENT ON COLUMN drugref.spl_run_census.official_names_already_named IS
    'UNII official names folding onto their own substance''s display name (#213). '
    'NULL as for official_names_adopted.';
COMMENT ON COLUMN drugref.spl_run_census.official_names_shadowed IS
    'UNII official names not adopted because they fold onto ANOTHER moiety''s name '
    '(#213). NULL as for official_names_adopted.';
COMMENT ON COLUMN drugref.spl_run_census.official_names_ambiguous IS
    'UNII official names not adopted because several moieties claim them (#213, '
    '#230). NULL as for official_names_adopted.';
COMMENT ON COLUMN drugref.spl_run_census.official_names_excluded IS
    'UNII official names not adopted because the shipped exclusion list names them '
    '(#213). NULL as for official_names_adopted.';
COMMENT ON COLUMN drugref.spl_run_census.official_names_unfoldable IS
    'UNII_Names ROWS (not folded names) whose name folds to no tokens, so never a '
    'key. NULL as for official_names_adopted.';
COMMENT ON COLUMN drugref.spl_run_census.official_name_exclusions_unused IS
    'Shipped exclusion entries no official name folded onto (a typo, or a renamed '
    'name). NULL as for official_names_adopted.';
