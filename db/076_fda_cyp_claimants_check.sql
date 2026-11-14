-- db/076 — FDA-CYP: the database enforces db/075's claimant rule (PR #242 review).
--
-- db/075 added candidate_moiety_uuids with the rule "every claimant, on an
-- ambiguous_substance row only" -- but held it only in Python
-- (fda_cyp_run.py). A row breaking it was invisible or papered over: the
-- question text read "Several drugref moieties share that name ...
-- (unrecorded)" instead of the write failing. A name is ambiguous exactly when
-- two or more moieties claim it, so the column is non-NULL with at least two
-- entries IF AND ONLY IF the row is ambiguous_substance. That includes a
-- non-drug or regimen name several moieties share: _classify files those under
-- the FDA-asserted disposition first, and the column is evidence for an
-- ambiguous_substance question only.
--
-- A new file rather than an edit to db/075: the ledger makes applied
-- migrations immutable, and db/075 is applied wherever the PR was tested.

ALTER TABLE drugref.fda_cyp_assertion
    ADD CONSTRAINT fda_cyp_assertion_claimants
        CHECK ((disposition = 'ambiguous_substance')
               = (candidate_moiety_uuids IS NOT NULL
                  AND cardinality(candidate_moiety_uuids) >= 2));

-- CREATE OR REPLACE VIEW in db/075 kept db/074's view comment, which predates
-- the ambiguous case and the appended column.
COMMENT ON VIEW drugref.gap_fda_cyp_unadjudicated IS
    'FDA-CYP tuples awaiting a human: a footnote nobody has adjudicated, a name '
    'drugref did not resolve, a name several moieties share (ambiguous_substance, '
    'db/075), a regimen, or a non-drug entity. TWO GRAINS, ONE view, joined with '
    'UNION ALL (db/040, catch-all corrected by db/041, substance/footnote-scope '
    'corrected by db/042, grain realigned with the gap_key by db/043 and db/074): '
    'withheld_qualified is grouped per CELL (source, substance, system, role, potency, '
    'pathway) -- the ROLE, not FDA''s printed column_heading, which is evidence only '
    '(db/074, issue 134); every other non-member disposition is grouped per SUBJECT '
    '(source, substance, disposition), with system/role/potency NULL. Both halves group '
    'on the CLEAN name, which is what gap_sources_interactions.py builds the gap_key '
    'from. raw_substance, column_heading and candidate_moiety_uuids (an ambiguous '
    'name''s claimants, NULL on every other row) are projected as evidence, never as '
    'identity.';
