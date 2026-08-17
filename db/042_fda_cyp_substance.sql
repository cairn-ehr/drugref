-- db/042_fda_cyp_substance.sql
-- Slice 5c.2g, the whole-branch review round: three corrections to
-- fda_cyp_assertion and its gap view, found in one pass over db/039-041.
-- db/039, db/040 and db/041 are already applied and frozen (immutable once
-- merged -- CLAUDE.md, and db.apply_migrations enforces it by checksum), so
-- every correction below is a new column or a CREATE OR REPLACE VIEW, never
-- an edit in place.

-- ============================================================================
-- 1. `substance` -- the curator-facing name FDA never printed, which db/039
--    parsed and then never stored
-- ============================================================================
-- fda_cyp.CypTuple has ALWAYS computed a clean `substance` (footnote markers
-- stripped) alongside `raw_substance` (FDA's printed form, markers and all).
-- db/039's INSERT only ever wrote raw_substance, and questions.py's gap_key
-- and question text were built from THAT column -- so a curator-facing
-- question reads "Which drugref moiety, if any, is FDA's oseltamivir
-- carboxylate 1?" and its gap_key is 'FDACYP:oseltamivir carboxylate 1||',
-- both quoting a FOOTNOTE MARKER as though it were part of the name. This is
-- the exact defect that gave this slice its headline case
-- ('ritonavir 14, 15,' -- PROJECT-NOTES "THE HEADLINE" #1) shipping again in
-- the human-readable output, and it is worse than cosmetic:
-- question_uuid = uuid5(gap_kind, gap_key) is IMMORTAL and externally
-- citable, so keying on FDA's own footnote NUMBERING means FDA renumbering a
-- footnote (or adding one) changes the identity of every open question about
-- that substance, for a reason that has nothing to do with the substance
-- itself.
--
-- db/039's own header states "29 of 337 cells carry a footnote". Measured
-- directly against the shipped parser (not the design-round probe that
-- produced 29): the real figure is 31. db/039 is frozen and cannot be
-- corrected in place, so the correction lives here rather than silently
-- staying wrong in a comment nobody reads again.
ALTER TABLE drugref.fda_cyp_assertion ADD COLUMN IF NOT EXISTS substance text;

COMMENT ON COLUMN drugref.fda_cyp_assertion.substance IS
    'FDA''s substance name with footnote markers stripped (fda_cyp.CypTuple.substance) '
    '-- the name a curator should read and the name a gap_key/question_text should '
    'quote. raw_substance is kept unchanged beside it as the raw-evidence column: the '
    'printed fact and the derived one are both stored, never one in place of the other.';

-- NO SQL BACKFILL, AND THAT IS A DELIBERATE CHOICE, NOT A SHORTCUT LEFT FOR
-- LATER. Deriving `substance` correctly means reproducing
-- fda_cyp.split_footnotes's regex (a marker is a whitespace-separated bare
-- integer or single lower-case letter, optionally repeated and
-- comma-separated, at the very end of the text -- tight enough that
-- 'peginterferon alpha-2a' and 'MATE2-K' must NOT be truncated) in SQL,
-- beside the Python original. That is a second implementation of a rule this
-- project has already lost rounds to keeping in one place (ids.mint_question_uuid's
-- own docstring makes the identical argument for NOT minting UUIDs in SQL) --
-- and a regex that drifts from its Python twin would silently mis-derive a
-- name, which is a worse outcome than a NULL. So the column ships NULLABLE:
-- the ONE authority for the derivation stays fda_cyp.split_footnotes, called
-- once by fda_cyp_run (this commit) for every row it writes. Because
-- fda_cyp_assertion is a rebuildable projection keyed by ingest_run.source
-- (CLAUDE.md's architecture invariant: "per-source rebuilds are safe:
-- projections keyed by ingest_run.source are delete-and-rebuild"), the next
-- `drugref ingest fda-cyp` clears every existing FDA-CYP row and rewrites it
-- WITH `substance` populated -- so no row on a database that has re-ingested
-- since this migration is ever NULL in practice, and no migration-time
-- backfill is needed to reach that state honestly.

-- ============================================================================
-- 2. row_footnote_markers / cell_footnote_markers -- WHERE a marker sits is
--    part of what it means, and db/039 discarded that position
-- ============================================================================
-- FDA's footnote markers live in two positions on the page: glued to the
-- SUBSTANCE NAME (a claim about the substance -- 'adefovir 1') or attached
-- inside a CELL, either trailing the whole cell or mid-cell on one pathway
-- item (a claim about that specific role/pathway -- conivaptan's
-- '3A moderate inhibitor 5', ciprofloxacin's '1A2 20 ; 3A moderate
-- inhibitor'). fda_cyp.parse_table already computed both separately and then
-- merged them into ONE footnote_markers string before db/039's INSERT ever
-- saw them, so the assertion table -- and every question text built from it
-- -- lost the ability to tell "FDA qualified this substance" from "FDA
-- qualified THIS CELL".
--
-- That is issue 122's shape (a message asserting a cause it has not
-- confirmed) applied to attachment rather than to value: bupropion's
-- withheld question reads "Does FDA's footnote on bupropion 2 (CYP Strg INH,
-- 2D6) narrow or NEGATE the membership its row states?" -- but footnote 2 is
-- glued to bupropion's NAME and is about CYP2B6 substrate status; it says
-- nothing about the 2D6 INHIBITION cell the question names. Withholding that
-- cell's membership is right (any footnote on the row is grounds to
-- withhold, per db/039's design section 3); asserting that the footnote is
-- ABOUT that cell is not. Measured: 31 of the 33 withheld gap rows carry a
-- name-level marker rather than a cell-level one, so this was the common
-- case, not an edge case.
ALTER TABLE drugref.fda_cyp_assertion ADD COLUMN IF NOT EXISTS row_footnote_markers text;
ALTER TABLE drugref.fda_cyp_assertion ADD COLUMN IF NOT EXISTS cell_footnote_markers text;

COMMENT ON COLUMN drugref.fda_cyp_assertion.row_footnote_markers IS
    'Footnote marker(s) glued to the SUBSTANCE NAME (fda_cyp.CypTuple.row_footnote_markers) '
    '-- a claim about the substance, not about any one cell. NULL for a row with no '
    'name-level marker. Nullable for the same reason `substance` is: no SQL backfill, '
    'populated by fda_cyp_run from the parser''s own computation. See db/042''s header.';

COMMENT ON COLUMN drugref.fda_cyp_assertion.cell_footnote_markers IS
    'Footnote marker(s) attached INSIDE this cell -- trailing the whole cell or mid-cell '
    'on this one pathway item (fda_cyp.CypTuple.cell_footnote_markers). NULL for a cell '
    'with no cell-level marker. When this IS present, FDA''s footnote genuinely attaches '
    'to this cell and a question may say so; when it is NULL and only '
    'row_footnote_markers is set, the footnote is about the NAME and attaching it to one '
    'cell is a claim FDA did not make. See db/042''s header.';

-- footnote_markers itself (db/039) is UNCHANGED and stays the merge of both --
-- every existing caller that only needs "is this row qualified at all"
-- (fda_cyp_run._classify's disposition check, _footnote_text's prose lookup)
-- keeps reading it exactly as before. The two new columns are additive
-- evidence about POSITION, not a replacement for the merged fact.

-- ============================================================================
-- 3. The gap view, recreated: `substance` replaces `raw_substance` in the
--    key/text-facing projection, the two footnote-scope columns are added,
--    and the subject half stops quoting an arbitrary cell's text
-- ============================================================================
-- THREE CHANGES IN ONE CREATE OR REPLACE, because all three touch the same
-- view and db/040's own precedent (splitting the grain) already established
-- that a view correction here is a whole-view replacement, not a patch:
--
--   a) `substance` is projected (max(), matching every other non-grouped
--      column here -- it is a pure function of raw_substance, so max() picks
--      the only value that can ever be present) so questions.py's key_sql and
--      text_sql can read it in place of raw_substance (task 1 above).
--   b) row_footnote_markers / cell_footnote_markers are projected for the
--      CELL half only (real per-cell facts for withheld_qualified). The
--      SUBJECT half sets both NULL::text: none of its three dispositions'
--      question text (unresolved_substance, combination_regimen,
--      non_drug_entity) ever names a footnote -- db/039's disposition order
--      checks non_drug_entity and combination_regimen BEFORE
--      withheld_qualified specifically so a row can be BOTH footnoted and one
--      of those three (grapefruit juice is marker 9 AND non_drug_entity) without
--      the footnote governing its text.
--   c) raw_cell and footnote_text are now NULL::text in the SUBJECT half.
--      db/041's version projected max(raw_cell) and max(footnote_text) across
--      EVERY row sharing a substance and disposition -- for rifampin
--      (unresolved_substance, 8 cells) that picks ONE arbitrary cell's text
--      via whichever value happens to sort highest, and attributes it to the
--      whole substance in a column a curator might reasonably read. Neither
--      column is used by any of the three subject-half branches in
--      questions.py's CASE (they name raw_substance/substance and, for
--      unresolved_substance only, registry_near_name) -- so NULLing them
--      matches db/040's own reasoning for column_heading/pathway: the honest
--      value for a fact this half is not asking about is NULL, not an
--      arbitrary survivor of max(). registry_near_name is NOT nulled here,
--      because unresolved_substance's branch DOES read it -- nulling it would
--      silently break test_a_near_name_never_upgrades_a_rows_disposition's
--      own "the row must still raise its question" assertion.
-- Column order below matches db/041's exactly for the nine pre-existing
-- columns, with the three new ones APPENDED rather than interleaved:
-- CREATE OR REPLACE VIEW may add trailing columns to an existing view but may
-- not reorder or rename the ones already there (Postgres refuses with
-- InvalidTableDefinition otherwise), and every existing caller that reads
-- this view by column NAME (never `SELECT *` -- grepped) is unaffected either
-- way, so there is no reason to fight that rule.
CREATE OR REPLACE VIEW drugref.gap_fda_cyp_unadjudicated AS
SELECT a.source,
       a.raw_substance,
       a.column_heading,
       a.pathway,
       max(a.disposition)           AS disposition,
       max(a.raw_cell)              AS raw_cell,
       max(a.footnote_text)         AS footnote_text,
       max(a.registry_near_name)    AS registry_near_name,
       max(r.upstream_release)      AS upstream_release,
       max(a.substance)             AS substance,
       max(a.row_footnote_markers)  AS row_footnote_markers,
       max(a.cell_footnote_markers) AS cell_footnote_markers
FROM   drugref.fda_cyp_assertion a
JOIN   drugref.ingest_run r ON r.ingest_run_id = a.ingest_run
WHERE  a.disposition = 'withheld_qualified'
GROUP  BY a.source, a.raw_substance, a.column_heading, a.pathway
UNION ALL
SELECT a.source,
       a.raw_substance,
       NULL::text                   AS column_heading,
       NULL::text                   AS pathway,
       max(a.disposition)           AS disposition,
       NULL::text                   AS raw_cell,
       NULL::text                   AS footnote_text,
       max(a.registry_near_name)    AS registry_near_name,
       max(r.upstream_release)      AS upstream_release,
       max(a.substance)             AS substance,
       NULL::text                   AS row_footnote_markers,
       NULL::text                   AS cell_footnote_markers
FROM   drugref.fda_cyp_assertion a
JOIN   drugref.ingest_run r ON r.ingest_run_id = a.ingest_run
WHERE  a.disposition NOT IN ('member', 'withheld_qualified')
GROUP  BY a.source, a.raw_substance, a.disposition;

COMMENT ON VIEW drugref.gap_fda_cyp_unadjudicated IS
    'FDA-CYP tuples awaiting a human: a footnote nobody has adjudicated, a name '
    'drugref did not resolve, a regimen, or a non-drug entity. TWO GRAINS, ONE view, '
    'joined with UNION ALL (db/040, catch-all corrected by db/041, substance/footnote-'
    'scope corrected by db/042): withheld_qualified is grouped per CELL (source, '
    'raw_substance, column_heading, pathway) because each footnoted cell is its own '
    'adjudication, and carries real row_footnote_markers/cell_footnote_markers so a '
    'question can tell a name-level footnote from a genuine cell attachment; EVERY '
    'OTHER NON-MEMBER disposition is grouped per SUBJECT (source, raw_substance, '
    'disposition), with column_heading, pathway, raw_cell, footnote_text and both '
    'footnote-scope columns NULL, because the question is about the NAME, not a cell '
    'that happened to mention it, and none of its text reads those columns. `substance` '
    '(db/042) is the name a gap_key/question_text should quote -- raw_substance is kept '
    'as the raw-evidence column, footnote markers and all. ABSENCE OF A ROW IS NOT '
    'COVERAGE.';
