-- db/074 — FDA-CYP: the withheld-cell grain keys on the column's ROLE, not on
--          FDA's printed heading (issue 134, second half).
--
-- question_uuid = uuid5(gap_kind, gap_key) is immortal. Until now the withheld
-- half of this view was grouped by `column_heading`, and questions.py put that
-- heading into the gap_key, so an FDA rewording (even one drugref accepts
-- deliberately by editing fda_cyp_cell.ROLE_COLUMN_HEADINGS) re-minted every
-- withheld question. The key now carries a frozen label per (system, role,
-- potency) -- fda_cyp_cell.ROLE_QUESTION_KEYS, byte-identical to the 2026-05-29
-- headings, so no existing question_uuid changes. This view must group on what
-- the key carries (db/043's rule), so the withheld half regroups on the role
-- tuple and `column_heading` becomes evidence via max(), like raw_substance.
--
-- Within one ingest run a heading and a role tuple are 1:1 (ROLE_COLUMNS), so on
-- every database today this regrouping yields exactly the same rows.
--
-- Same 13 columns in the same order (CREATE OR REPLACE VIEW may only append),
-- plus system, role and potency at the end for questions.py's key_sql. They are
-- NULL on the subject-grain half, which has no cell and so no role.
CREATE OR REPLACE VIEW drugref.gap_fda_cyp_unadjudicated AS
SELECT a.source,
       max(a.raw_substance)         AS raw_substance,
       max(a.column_heading)        AS column_heading,
       a.pathway,
       max(a.disposition)           AS disposition,
       max(a.raw_cell)              AS raw_cell,
       max(a.footnote_text)         AS footnote_text,
       max(a.registry_near_name)    AS registry_near_name,
       max(r.upstream_release)      AS upstream_release,
       COALESCE(a.substance, a.raw_substance) AS substance,
       max(a.row_footnote_markers)  AS row_footnote_markers,
       max(a.cell_footnote_markers) AS cell_footnote_markers,
       max(a.footnote_markers)      AS footnote_markers,
       a.system,
       a.role,
       a.potency
FROM   drugref.fda_cyp_assertion a
JOIN   drugref.ingest_run r ON r.ingest_run_id = a.ingest_run
WHERE  a.disposition = 'withheld_qualified'
GROUP  BY a.source, COALESCE(a.substance, a.raw_substance), a.system, a.role,
          a.potency, a.pathway
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
       NULL::text                   AS footnote_markers,
       NULL::text                   AS system,
       NULL::text                   AS role,
       NULL::text                   AS potency
FROM   drugref.fda_cyp_assertion a
JOIN   drugref.ingest_run r ON r.ingest_run_id = a.ingest_run
WHERE  a.disposition NOT IN ('member', 'withheld_qualified')
GROUP  BY a.source, COALESCE(a.substance, a.raw_substance), a.disposition;

COMMENT ON VIEW drugref.gap_fda_cyp_unadjudicated IS
    'FDA-CYP tuples awaiting a human: a footnote nobody has adjudicated, a name '
    'drugref did not resolve, a regimen, or a non-drug entity. TWO GRAINS, ONE view, '
    'joined with UNION ALL (db/040, catch-all corrected by db/041, substance/footnote-'
    'scope corrected by db/042, grain realigned with the gap_key by db/043 and db/074): '
    'withheld_qualified is grouped per CELL (source, substance, system, role, potency, '
    'pathway) -- the ROLE, not FDA''s printed column_heading, which is evidence only '
    '(db/074, issue 134); every other non-member disposition is grouped per SUBJECT '
    '(source, substance, disposition), with system/role/potency NULL. Both halves group '
    'on the CLEAN name, which is what questions.py builds the gap_key from. '
    'raw_substance and column_heading are projected as evidence, never as identity.';
