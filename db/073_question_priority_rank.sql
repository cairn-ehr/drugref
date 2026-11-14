-- db/073_question_priority_rank.sql
--
-- Issue 217 (SPL class-mentions design §6.4): the worklist of unconfirmed label
-- class statements is to be "ordered strongest level first". db/008's
-- question_worklist orders WITHIN a gap kind by question_uuid -- a hash, so a
-- curator met the 4,628 run-17 questions of that kind in an order unrelated to
-- strength, a `monitor` as likely first as a `contraindicated`.
--
-- ⇒ THE RANK IS DERIVED, SO IT LIVES ON THE DERIVED TABLE. open_question is
-- re-derived by questions.register_from_gaps every ingest, exactly like
-- question_text; a kind's optional `rank_sql` (gap_sources_*.py) fills it. Storing
-- it there keeps the frozen gap_key format in ONE place: the alternative -- the
-- worklist parsing gap_key back into a moiety and a class to look the level up --
-- would spell that format a second time, in SQL, and re-run the heavy
-- spl_class_statement aggregate on every worklist read.
--
-- ⇒ IT ORDERS WITHIN ONE KIND, NEVER ACROSS KINDS. 1 = sooner. Its meaning is the
-- kind's own (for unconfirmed_label_class_statement it is
-- spl_advice_level.strength_rank: 1 = contraindicated, 4 = monitor), so the
-- worklist sorts by it only after gap_kind. NULL = the kind declares no priority,
-- sorted last within its kind.

ALTER TABLE drugref.open_question
    ADD COLUMN IF NOT EXISTS priority_rank integer
        CONSTRAINT open_question_priority_rank_positive
        CHECK (priority_rank IS NULL OR priority_rank > 0);

COMMENT ON COLUMN drugref.open_question.priority_rank IS
    'Derived, refreshed every ingest like question_text: the order a curator should '
    'meet questions WITHIN ONE gap_kind (1 = first). Meaning is per kind -- for '
    'unconfirmed_label_class_statement it is spl_advice_level.strength_rank (1 = '
    'contraindicated). NULL when the kind declares no priority (db/073, #217). Never '
    'compare ranks across kinds.';

-- Same columns as db/008 in the same order (CREATE OR REPLACE VIEW may only append),
-- plus priority_rank at the end; the ORDER BY gains it after gap_kind.
CREATE OR REPLACE VIEW drugref.question_worklist AS
SELECT q.question_uuid,
       q.gap_kind,
       q.gap_key,
       q.question_text,
       COALESCE(s.state, 'open')                    AS state,
       -- The rank of the cheapest tier NOT yet checked. NULL (every tier checked)
       -- sorts last: there is nothing cheap left to try.
       (SELECT min(t.rank) FROM drugref.source_tier t
        WHERE NOT EXISTS (SELECT 1 FROM drugref.question_source_check c
                          WHERE c.question_uuid = q.question_uuid
                          AND   c.source = t.source)) AS cheapest_unchecked_rank,
       q.priority_rank
FROM   drugref.open_question q
LEFT   JOIN drugref.question_state s
       ON s.question_uuid = q.question_uuid AND s.superseded_by IS NULL
WHERE  COALESCE(s.state, 'open') <> 'withdrawn'
AND    q.is_current
ORDER  BY cheapest_unchecked_rank NULLS LAST, q.gap_kind,
          q.priority_rank NULLS LAST, q.question_uuid;

COMMENT ON VIEW drugref.question_worklist IS
    'Open questions in the order effort should be spent: cheapest unchecked source '
    'tier first, so the free structured sources are exhausted before literature '
    'mining or hand curation; then by gap_kind, then priority_rank within a kind '
    '(db/073: e.g. contraindicated label statements before monitor ones). Withdrawn '
    'questions are excluded, as are questions whose gap has closed (is_current '
    'false); ANSWERED ones are NOT, because they keep accepting evidence. A question '
    'with no state row is open. The ORDER BY is a convenience and is not guaranteed '
    'through a wrapping query -- restate it.';
