-- db/040_fda_cyp_gap_grain.sql
-- Slice 5c.2g, task 7's own review: db/039's gap view has the WRONG GRAIN for
-- three of its four dispositions. db/039 is already applied and frozen
-- (immutable once merged -- CLAUDE.md, and db.apply_migrations enforces it by
-- checksum), so the correction is a new migration rather than an edit in place.

-- ============================================================================
-- WHAT WAS WRONG, MEASURED ON THE REAL PAGE (2026-05-29 release, 419 tuples)
-- ============================================================================
-- db/039 grouped every disposition alike, on (source, raw_substance,
-- column_heading, pathway). That grain is correct for exactly ONE of the four:
--
--   withheld_qualified    -- CORRECT. Each footnoted CELL is its own
--                            adjudication -- bupropion's CYP2B6 cell and a
--                            different cell of its can carry DIFFERENT
--                            footnotes needing DIFFERENT answers, so
--                            column_heading/pathway belong in the grain.
--                            Measured: 33 gap rows, 33 facts. Unchanged here.
--   unresolved_substance  -- WRONG. "Which drugref moiety, if any, is FDA's
--                            rifampin?" is ONE fact about the NAME, asked
--                            regardless of which cell mentions it -- FDA's
--                            table lists rifampin against several pathways,
--                            and the resolution question does not vary by
--                            column. Measured: 16 gap rows, 8 distinct
--                            substances -- rifampin alone asked the identical
--                            question up to 8 times.
--   combination_regimen   -- WRONG, same shape. "Which component, if any,
--                            carries the REGIMEN's role" does not depend on
--                            which pathway the regimen was reported against.
--                            Measured: 17 gap rows, 9 distinct regimens.
--   non_drug_entity       -- WRONG, same shape: FDA's own five-substance
--                            sentence names the SUBSTANCE, not a cell.
--                            Measured: 5 gap rows, 5 distinct substances --
--                            already 1:1 on today's page (each of the five
--                            happens to appear once), so this one produced
--                            the right COUNT by accident; the view's SHAPE
--                            was still wrong, and a future release listing
--                            one of the five against two pathways would have
--                            split it exactly as rifampin split above.
--
-- 71 gap rows minted 71 question_uuids where 55 facts existed -- 16 surplus.
-- question_uuid is immortal and externally citable the moment this ships, and
-- it is a pure function of (gap_kind, gap_key), so shipping the wrong grain
-- would mean RETIRING sixteen UUIDs later (a question whose gap_key no
-- longer derives is deleted or retired by register_from_gaps) instead of
-- simply never minting them. This is issue 41's rule in its OTHER direction:
-- that issue's own title case was a view grouping too COARSELY and folding
-- two facts onto one question; this one groups too FINELY and mints several
-- questions for one fact. Nothing built on 5c.2g has merged yet, so this is
-- the cheap moment to fix it -- the whole argument db/037's header already
-- made for its own three follow-up corrections.

-- ============================================================================
-- THE FIX: two correctly-grained halves, joined with UNION ALL
-- ============================================================================
--   * the CELL half -- withheld_qualified only, grouped on (source,
--     raw_substance, column_heading, pathway) exactly as db/039 already did.
--     Byte-identical rows, byte-identical question_uuids: nothing about this
--     disposition was wrong, so nothing about it changes.
--   * the SUBJECT half -- the other three dispositions, grouped on (source,
--     raw_substance, disposition). column_heading and pathway are projected
--     as NULL::text, not omitted -- both halves must keep the same column
--     list for UNION ALL, and NULL is the honest value: those two columns
--     are not part of the fact this half is asking about.
--
-- questions.py's key_sql (same commit) COALESCEs both now-nullable columns to
-- '' before concatenating them into gap_key. That guard is not cosmetic:
-- SQL's `||` returns NULL if any operand is NULL, so an unguarded key_sql
-- would silently mint a NULL gap_key -- and so a NULL-derived question_uuid,
-- colliding every one of the subject-grain rows onto a single UUID -- for
-- every row this half produces. The CASE text branching in questions.py is
-- unchanged in shape (still one branch per disposition); only the two now-
-- redundant column_heading/pathway interpolations in the unresolved_substance
-- and combination_regimen branches are dropped, because with the grain
-- corrected the question genuinely is about the substance, not the cell.

-- ============================================================================
-- WHAT BREAKS IF THIS IS REVERTED
-- ============================================================================
-- Reverting to db/039's single grouping re-mints the sixteen surplus
-- question_uuids this fix retires: rifampin's "which moiety" question splits
-- back into up to eight identical-text rows, one per cell that happens to
-- mention it, and a curator who answers one sees the rest still open, asking
-- the identical question under different UUIDs. That is exactly the failure
-- issue 41 is filed against, reproduced in its finer direction.
CREATE OR REPLACE VIEW drugref.gap_fda_cyp_unadjudicated AS
SELECT a.source,
       a.raw_substance,
       a.column_heading,
       a.pathway,
       max(a.disposition)        AS disposition,
       max(a.raw_cell)           AS raw_cell,
       max(a.footnote_text)      AS footnote_text,
       max(a.registry_near_name) AS registry_near_name,
       max(r.upstream_release)   AS upstream_release
FROM   drugref.fda_cyp_assertion a
JOIN   drugref.ingest_run r ON r.ingest_run_id = a.ingest_run
WHERE  a.disposition = 'withheld_qualified'
GROUP  BY a.source, a.raw_substance, a.column_heading, a.pathway
UNION ALL
SELECT a.source,
       a.raw_substance,
       NULL::text                AS column_heading,
       NULL::text                AS pathway,
       max(a.disposition)        AS disposition,
       max(a.raw_cell)           AS raw_cell,
       max(a.footnote_text)      AS footnote_text,
       max(a.registry_near_name) AS registry_near_name,
       max(r.upstream_release)   AS upstream_release
FROM   drugref.fda_cyp_assertion a
JOIN   drugref.ingest_run r ON r.ingest_run_id = a.ingest_run
WHERE  a.disposition IN ('unresolved_substance', 'combination_regimen', 'non_drug_entity')
GROUP  BY a.source, a.raw_substance, a.disposition;

COMMENT ON VIEW drugref.gap_fda_cyp_unadjudicated IS
    'FDA-CYP tuples awaiting a human: a footnote nobody has adjudicated, a name '
    'drugref did not resolve, a regimen, or a non-drug entity. TWO GRAINS, ONE '
    'view, joined with UNION ALL (db/040): withheld_qualified is grouped per '
    'CELL (source, raw_substance, column_heading, pathway) because each '
    'footnoted cell is its own adjudication; the other three dispositions are '
    'grouped per SUBJECT (source, raw_substance, disposition), with '
    'column_heading and pathway NULL, because the question is about the NAME, '
    'not the cell that happened to mention it. ABSENCE OF A ROW IS NOT COVERAGE.';
