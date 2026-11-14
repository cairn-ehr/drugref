-- db/077: spl_run_census counts the class pass's 'non-' drop (#214).
--
-- ⇒ WHY. #214's rule drops a class match that follows 'non-' ("non-depolarizing
-- neuromuscular blockers" names atracurium and vecuronium, the COMPLEMENT of the
-- depolarizing class the words match). Like the boundary and head drops db/072
-- stores, it is counted, so a jump between releases is visible in `drugref status`.
--
-- ⇒ NULL IS "NOT MEASURED": every run before this migration. The writer
-- (`spl_census.write_run_census`) fills it from `SplSummary`, whose field has no
-- default, so a run from here on cannot store a forgotten 0.

ALTER TABLE drugref.spl_run_census
    ADD COLUMN class_spans_dropped_after_non bigint
        CHECK (class_spans_dropped_after_non >= 0);

COMMENT ON COLUMN drugref.spl_run_census.class_spans_dropped_after_non IS
    'Class (span, class) rows dropped because "non-" precedes the class words, '
    'which then name the complement group (#214). NULL: run predates db/077.';
