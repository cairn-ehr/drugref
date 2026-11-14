-- db/075 — FDA-CYP: a name several moieties share is `ambiguous_substance`,
--          not `unresolved_substance` (issue 132).
--
-- Resolution is exact and case-insensitive, and a name matching TWO OR MORE
-- moieties is never resolved by picking one. Until now zero matches and several
-- matches both landed on unresolved_substance, whose question text says no
-- moiety's name matches -- false for the ambiguous case (issue 122's lesson: a
-- message may not state a cause it has not confirmed). Ambiguity is OBSERVED,
-- not inferred, so it meets db/039's rule for what a disposition may record.
--
-- The question_uuid does not move: questions.py's gap_key omits the
-- disposition, and both dispositions are decided from the substance name
-- alone, so one name can never carry both at once. Only the TEXT changes.
--
-- candidate_moiety_uuids holds every claimant, as evidence for a curator. NULL
-- on every other disposition: no other row has more than one candidate.

ALTER TABLE drugref.fda_cyp_assertion
    DROP CONSTRAINT fda_cyp_assertion_disposition,
    ADD CONSTRAINT fda_cyp_assertion_disposition
        CHECK (disposition IN ('member', 'withheld_qualified', 'unresolved_substance',
                               'ambiguous_substance', 'combination_regimen',
                               'non_drug_entity')),
    ADD COLUMN candidate_moiety_uuids uuid[];

COMMENT ON COLUMN drugref.fda_cyp_assertion.candidate_moiety_uuids IS
    'Every moiety whose display name or UNII official name equals this substance '
    'case-insensitively, for an ambiguous_substance row only (db/075, issue 132). '
    'Evidence for a curator, never a resolution.';

-- db/074's view, unchanged, plus candidate_moiety_uuids appended (CREATE OR
-- REPLACE VIEW may only append). The subject half groups per (substance,
-- disposition), and every row of one ambiguous name carries the same claimant
-- list (one index lookup per name), so max() picks that list.
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
       a.potency,
       NULL::uuid[]                 AS candidate_moiety_uuids
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
       NULL::text                   AS potency,
       max(a.candidate_moiety_uuids) AS candidate_moiety_uuids
FROM   drugref.fda_cyp_assertion a
JOIN   drugref.ingest_run r ON r.ingest_run_id = a.ingest_run
WHERE  a.disposition NOT IN ('member', 'withheld_qualified')
GROUP  BY a.source, COALESCE(a.substance, a.raw_substance), a.disposition;
