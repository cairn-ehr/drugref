-- db/041_fda_cyp_gap_catch_all.sql
-- Slice 5c.2g, task 7's SECOND review round: db/040's own fix introduced a new
-- defect while correcting the old one. db/040 is already applied and frozen
-- (immutable once merged -- CLAUDE.md, and db.apply_migrations enforces it by
-- checksum), so the correction is a new migration rather than an edit in place.

-- ============================================================================
-- WHAT WAS WRONG: db/040's WHERE clauses were an ALLOWLIST
-- ============================================================================
-- db/039's original view read `WHERE a.disposition <> 'member'` -- permissive:
-- every disposition except the one known-safe value was admitted. Splitting the
-- view into two grains (db/040) forced each half to name which dispositions it
-- covers, and db/040 named them POSITIVELY and EXHAUSTIVELY:
--
--   cell half:    WHERE a.disposition =  'withheld_qualified'
--   subject half: WHERE a.disposition IN ('unresolved_substance',
--                                          'combination_regimen',
--                                          'non_drug_entity')
--
-- That enumerates the same four values questions.py's CASE enumerates -- and
-- the fifth value ('member') is excluded only because it does not appear in
-- either list, not because anything says so. A SIXTH disposition -- a future
-- CHECK widening, exactly the kind db/035 landed mid-plan and db/039 landed for
-- three columns at once -- is EXCLUDED FROM BOTH HALVES rather than admitted to
-- either. It reaches neither the cell grouping nor the subject grouping, so it
-- produces ZERO gap-view rows. Verified directly by the reviewer: widen the
-- disposition CHECK with a synthetic sixth value, insert a row, and the view
-- returns nothing for it.
--
-- THAT IS SILENT, AND IT CONTRADICTS TWO WRITTEN CLAIMS AT ONCE. The view's own
-- COMMENT says "ABSENCE OF A ROW IS NOT COVERAGE" -- true only if absence means
-- "adjudicated or resolved", not "a disposition value this migration's author
-- did not anticipate". And questions.py's CASE comment claims a fifth disposition
-- "aborts the ingest loudly" -- true of the CASE itself (it has no ELSE), but
-- only for a row that REACHES the CASE, and db/040's allowlist WHERE clauses
-- were exactly what stopped it arriving. This is issues 74/66/76's shape (a gate
-- that exists and is never exercised, so it never fires) landing beside issue
-- 122's (a comment stating a property the code does not actually have).
--
-- ============================================================================
-- WHY AN ALLOWLIST WAS THE WRONG SHAPE HERE, AND WHAT THE FIX IS
-- ============================================================================
-- The cell half's `= 'withheld_qualified'` is a POSITIVE claim about which rows
-- are cell-grained -- there is exactly one such disposition today, and adding a
-- second cell-grained disposition is a decision someone has to make deliberately
-- (it would need its own reasoning, the way withheld_qualified's does in db/040's
-- header). That half is CORRECTLY an allowlist and is UNCHANGED here.
--
-- The subject half is different: it does not claim to be an exhaustive list of
-- interesting subject-grained dispositions, it claims to be "everything that
-- isn't cell-grained and isn't a resolved membership". That is a NEGATIVE
-- predicate, and stating it as a positive enumeration silently drops whatever
-- the enumeration forgets. Its WHERE clause becomes:
--
--   WHERE a.disposition NOT IN ('member', 'withheld_qualified')
--
-- A sixth disposition now lands in the subject half by construction (it is
-- excluded from neither the cell half's positive predicate nor this negative
-- one, and the two halves are no longer complements-by-coincidence but by
-- construction: the cell half takes withheld_qualified, the subject half takes
-- everything else that is not member). It reaches questions.py's CASE, matches
-- no WHEN, evaluates to SQL NULL, and the executemany INSERT into
-- open_question(question_text, ...) trips that column's NOT NULL constraint --
-- the ingest aborts with a loud, specific error naming the column, rather than
-- silently reporting success having derived zero new questions. This is the
-- SAME "force a declaration" discipline db/014 gave condition_ci_axis's
-- expands_descendants, and the same reason questions.py's unresolved_ci_object
-- entry omits ELSE from its own CASE.
--
-- ============================================================================
-- WHAT BREAKS IF THIS IS REVERTED
-- ============================================================================
-- Reverting to db/040's positive enumeration re-opens the silent hole: the next
-- disposition added to fda_cyp_assertion's CHECK (a real, foreseeable event --
-- this project has widened the CHECK on this exact column once already, from
-- five values with room reserved for a sixth per spec section 7.1's "six
-- recognisable categories, five stored") produces zero gap-view rows and zero
-- questions for every row carrying it, with `drugref ingest fda-cyp` reporting
-- success throughout. Nothing downstream would notice until a curator asks why
-- a substance they can see in fda_cyp_assertion never appears on the worklist.
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
WHERE  a.disposition NOT IN ('member', 'withheld_qualified')
GROUP  BY a.source, a.raw_substance, a.disposition;

COMMENT ON VIEW drugref.gap_fda_cyp_unadjudicated IS
    'FDA-CYP tuples awaiting a human: a footnote nobody has adjudicated, a name '
    'drugref did not resolve, a regimen, or a non-drug entity. TWO GRAINS, ONE '
    'view, joined with UNION ALL (db/040, catch-all corrected by db/041): '
    'withheld_qualified is grouped per CELL (source, raw_substance, '
    'column_heading, pathway) because each footnoted cell is its own '
    'adjudication; EVERY OTHER NON-MEMBER disposition is grouped per SUBJECT '
    '(source, raw_substance, disposition), with column_heading and pathway '
    'NULL, because the question is about the NAME, not the cell that happened '
    'to mention it -- and the subject half is a NEGATIVE predicate '
    '(NOT IN (member, withheld_qualified)) precisely so an unanticipated sixth '
    'disposition still reaches this view rather than vanishing. '
    'ABSENCE OF A ROW IS NOT COVERAGE.';
