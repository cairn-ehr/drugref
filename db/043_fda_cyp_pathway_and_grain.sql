-- db/043 — FDA-CYP: the closed pathway vocabulary in SQL, and the gap view's
--          grain realigned with the gap_key db/042 moved.
--
-- Two corrections from the slice 5c.2g review. Both are cases of a rule that
-- was reasoned about carefully in one language and left unstated in the other.
--
-- WHY A NEW FILE rather than an edit to db/039–db/042: those four are recorded
-- in drugref.schema_migration with their checksums, and db.apply_migrations
-- refuses a body that no longer hashes to what it applied ("Migrations are
-- immutable once applied: add a new db/*.sql file instead of editing").
--
-- ============================================================================
-- 1. fda_cyp_pathway — the closed vocabulary, as a table the assertion FKs to
-- ============================================================================
-- db/039 constrained `source`, `role`, `potency` and `disposition` with CHECKs
-- and left `pathway text NOT NULL` with no constraint at all — the ONE axis the
-- module's own headline argument is about. fda_cyp.py's docstring puts it
-- plainly: a lenient parse mints 'cyp:1a2 20' and 'transporter:oatp1b1
-- inhibitor' as classes with real immortal UUIDs, and "those four are what this
-- module's strictness is for". That strictness lived only in Python, so
--
--     INSERT INTO drugref.fda_cyp_assertion (..., pathway) VALUES (..., 'oatp1b1 inhibitor')
--
-- succeeded, and any future writer — a backfill script, a repair query, a
-- second orchestrator — could widen the vocabulary by accident.
--
-- A TABLE, NOT A CHECK, and not because a CHECK could not hold 17 values. The
-- pairing is the reason: `system` and `pathway` are only meaningful TOGETHER.
-- 'OATP1B1' is a real transporter, and a CYP row naming it would mint a class
-- under the wrong system — which fda_cyp.parse_cell refuses explicitly
-- ("Accepting it would mint a class under the wrong system") and which two
-- independent CHECKs cannot express at all, since each sees only its own
-- column. One composite foreign key states the pair, once.
--
-- SEEDED FROM fda_cyp._PATHWAYS_BY_SYSTEM, and pinned to it by an EQUALITY
-- assertion in tests/test_fda_cyp_schema.py rather than a subset one: this is
-- the vocabulary written down twice, so the test is what keeps the two copies
-- from drifting. Widening it is now deliberately a two-file change.
CREATE TABLE IF NOT EXISTS drugref.fda_cyp_pathway (
    system   text NOT NULL,
    pathway  text NOT NULL,
    PRIMARY KEY (system, pathway),
    CONSTRAINT fda_cyp_pathway_system CHECK (system IN ('CYP', 'transporter'))
);

COMMENT ON TABLE drugref.fda_cyp_pathway IS
    'The closed (system, pathway) vocabulary FDA-CYP may mint classes under, held '
    'in SQL so it is not enforced only by the parser. Mirrors '
    'fda_cyp._PATHWAYS_BY_SYSTEM, pinned by equality in tests/test_fda_cyp_schema.py. '
    'Widening it is a deliberate two-file change: this table and that dict.';

INSERT INTO drugref.fda_cyp_pathway (system, pathway) VALUES
    ('CYP', '1A2'), ('CYP', '2B6'), ('CYP', '2C8'), ('CYP', '2C9'),
    ('CYP', '2C19'), ('CYP', '2D6'), ('CYP', '3A'),
    ('transporter', 'BCRP'), ('transporter', 'MATE1'),
    ('transporter', 'MATE2-K'), ('transporter', 'OAT1'),
    ('transporter', 'OAT3'), ('transporter', 'OATP1B'),
    ('transporter', 'OATP1B1'), ('transporter', 'OATP1B3'),
    ('transporter', 'OCT2'), ('transporter', 'P-gp')
ON CONFLICT DO NOTHING;

-- NOT VALID is deliberately NOT used: the projection is delete-and-rebuild and
-- small (419 rows), so validating existing rows costs nothing, and a constraint
-- that silently tolerates the rows already on disk is the gate-that-never-fires
-- shape this slice's review kept finding.
ALTER TABLE drugref.fda_cyp_assertion
    DROP CONSTRAINT IF EXISTS fda_cyp_assertion_pathway_fkey;
ALTER TABLE drugref.fda_cyp_assertion
    ADD CONSTRAINT fda_cyp_assertion_pathway_fkey
    FOREIGN KEY (system, pathway) REFERENCES drugref.fda_cyp_pathway (system, pathway);

-- ============================================================================
-- 2. gap_fda_cyp_unadjudicated — the view's grain, realigned with its gap_key
-- ============================================================================
-- db/039's header states the rule this restores: "one view row is one
-- independently-answerable fact and the view's grain matches the grain the
-- gap_key built from it uses."
--
-- db/042 moved the key onto the CLEAN name — questions.py's key_sql now reads
-- `'FDACYP:' || COALESCE(substance, raw_substance) || ...` — for a good reason
-- (keying on FDA's footnote NUMBERING meant a renumbered footnote changed the
-- identity of every open question about that substance). But BOTH halves of the
-- view still GROUP BY a.raw_substance, and `substance` is a strict coarsening
-- of `raw_substance`: the markers are stripped. So two printed forms of one
-- name — 'aprepitant 3' and 'aprepitant', or 'rifampin 13' and 'rifampin' —
-- produce TWO view rows carrying ONE gap_key.
--
-- That does not error. register_from_gaps upserts with
-- `ON CONFLICT (question_uuid) DO UPDATE SET question_text = EXCLUDED.question_text`
-- over an UNORDERED view, so the second row silently overwrites the first's
-- text and which one wins is not deterministic — for a question_uuid that is
-- immortal and externally citable. It also double-counts: `len(live_keys)` is a
-- list, so FdaCypSummary.questions_registered reports two.
--
-- Measured on the 2026-05-29 release: no collision occurs today (no substance
-- appears under two printed forms within one grain), which is exactly why this
-- had to be found by reading rather than by a failing test — and why the fix
-- lands before the FDA release that introduces one.
--
-- THE FIX IS THE GRAIN, NOT THE KEY. row_ordinal must NOT enter the gap_key:
-- it shifts whenever FDA inserts a row alphabetically, and question_uuid is
-- immortal. So the view is regrouped onto the same COALESCE(substance,
-- raw_substance) the key already uses, and raw_substance is projected as
-- EVIDENCE via max() rather than being the thing that identifies.
CREATE OR REPLACE VIEW drugref.gap_fda_cyp_unadjudicated AS
SELECT a.source,
       max(a.raw_substance)         AS raw_substance,
       a.column_heading,
       a.pathway,
       max(a.disposition)           AS disposition,
       max(a.raw_cell)              AS raw_cell,
       max(a.footnote_text)         AS footnote_text,
       max(a.registry_near_name)    AS registry_near_name,
       max(r.upstream_release)      AS upstream_release,
       COALESCE(a.substance, a.raw_substance) AS substance,
       max(a.row_footnote_markers)  AS row_footnote_markers,
       max(a.cell_footnote_markers) AS cell_footnote_markers,
       -- APPENDED, as column 13. CREATE OR REPLACE VIEW may add trailing
       -- columns but may not reorder or rename the ones already there
       -- (db/042's header makes the same point for its own three). This is the
       -- column db/039 has populated for every withheld row since the
       -- beginning, which is what makes it the honest fallback for
       -- questions.py's ELSE arm when the two db/042 scope columns are still
       -- NULL on a database that has not re-ingested.
       max(a.footnote_markers)      AS footnote_markers
FROM   drugref.fda_cyp_assertion a
JOIN   drugref.ingest_run r ON r.ingest_run_id = a.ingest_run
WHERE  a.disposition = 'withheld_qualified'
GROUP  BY a.source, COALESCE(a.substance, a.raw_substance), a.column_heading, a.pathway
UNION ALL
SELECT a.source,
       max(a.raw_substance)         AS raw_substance,
       NULL::text                   AS column_heading,
       NULL::text                   AS pathway,
       max(a.disposition)           AS disposition,
       NULL::text                   AS raw_cell,
       NULL::text                   AS footnote_text,
       max(a.registry_near_name)    AS registry_near_name,
       max(r.upstream_release)      AS upstream_release,
       COALESCE(a.substance, a.raw_substance) AS substance,
       NULL::text                   AS row_footnote_markers,
       NULL::text                   AS cell_footnote_markers,
       NULL::text                   AS footnote_markers
FROM   drugref.fda_cyp_assertion a
JOIN   drugref.ingest_run r ON r.ingest_run_id = a.ingest_run
WHERE  a.disposition NOT IN ('member', 'withheld_qualified')
GROUP  BY a.source, COALESCE(a.substance, a.raw_substance), a.disposition;

COMMENT ON VIEW drugref.gap_fda_cyp_unadjudicated IS
    'FDA-CYP tuples awaiting a human: a footnote nobody has adjudicated, a name '
    'drugref did not resolve, a regimen, or a non-drug entity. TWO GRAINS, ONE view, '
    'joined with UNION ALL (db/040, catch-all corrected by db/041, substance/footnote-'
    'scope corrected by db/042, GRAIN realigned with the gap_key by db/043): '
    'withheld_qualified is grouped per CELL (source, substance, column_heading, '
    'pathway); every other non-member disposition is grouped per SUBJECT (source, '
    'substance, disposition). Both halves group on the CLEAN name, which is what '
    'questions.py builds the gap_key from -- grouping on raw_substance while keying '
    'on substance let two view rows share one immortal question_uuid. raw_substance '
    'is still projected, as evidence, never as identity.';

-- A note this file cannot fix, recorded where the next reader will look.
-- db/041's header and tests/test_fda_cyp_run.py both justify the sixth-value
-- design with "this project has widened the CHECK on this exact column once
-- already". IT NEVER HAPPENED: db/039 creates fda_cyp_assertion_disposition
-- with five values and no migration has altered it since (db/040 and db/041
-- replace views; db/042 adds columns; this file adds a table and a foreign
-- key). The genuine precedent is db/035 adding a whole gap kind mid-plan,
-- which the test sentence names correctly right beside the false one. db/041
-- is applied and therefore immutable, so the correction lives here.
