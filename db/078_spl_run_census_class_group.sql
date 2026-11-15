-- db/078: spl_run_census counts unused class exclusions (#248) and holds the
-- class-pass group whole (#249).
--
-- ⇒ #248, WHY. `spl_class_vocab.EXCLUDED_CLASSES` (3 names) and `KEPT_PE_CLASSES`
-- (1 name) are keyed by exact class name. A MED-RT or MeSH release that renames
-- one leaves its entry matching nothing, and nothing says so: a renamed
-- 'Electrolyte [EPC]' brings back ~2,349 false class spans, a renamed 'Antacid
-- [PE]' drops antacid advice for its 3 non-MeSH members. The class floor cannot
-- see a change that small. `official_name_exclusions_unused` (db/072) already
-- counts the same staleness for the official-name exclusions; this is its twin.
--
-- ⇒ #249, WHY. db/072's rule is "a group is measured whole or not at all", but
-- db/077's 'non-' drop was added with only its `>= 0`, so a NULL there beside
-- measured class drops was accepted. One CHECK now spans db/072's three class
-- drops, db/077's and this migration's column (R19/P1's drop stays in db/072's
-- official-name group: it needs a UNII_Names file, these do not).
--
-- ⇒ NOT VALID, DELIBERATELY (db/043 refused it, for a different table). Every row
-- written between db/072 and this migration has db/072's drops measured and this
-- migration's column NULL (db/077's too, if written before db/077): an honest
-- "not measured", which this CHECK would refuse. (Rows from before db/072 have
-- all five NULL and pass.) NOT VALID leaves those rows standing and checks every
-- INSERT from here on, so this gate still fires on each run; db/043's objection
-- was a constraint tolerating rows that were WRONG. Never `VALIDATE CONSTRAINT`
-- this one: the history it would reject is correct. The name sorts after
-- `spl_run_census_class_drops_together`, and Postgres checks CHECKs by name, so a
-- half-measured db/072 group still reports db/072's name.

ALTER TABLE drugref.spl_run_census
    ADD COLUMN class_exclusions_unused bigint
        CHECK (class_exclusions_unused >= 0);

ALTER TABLE drugref.spl_run_census
    ADD CONSTRAINT spl_run_census_class_group_whole CHECK (
        num_nulls(class_spans_dropped_at_a_boundary,
                  class_spans_dropped_on_a_non_drug_head,
                  class_keys_shadowed_by_moieties,
                  class_spans_dropped_after_non,
                  class_exclusions_unused) IN (0, 5)) NOT VALID;

COMMENT ON COLUMN drugref.spl_run_census.class_exclusions_unused IS
    'Named class exclusions (EXCLUDED_CLASSES, KEPT_PE_CLASSES) matching no '
    'substance_class row: a renamed or mistyped class (#248). NULL: run predates '
    'db/078.';
