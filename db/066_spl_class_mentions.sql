-- db/066_spl_class_mentions.sql
-- =============================================================================
-- SPL class mentions: where a label's interactions section names a drug CLASS
-- ("strong CYP3A inhibitors", "statins"), what the sentence around it ADVISES
-- (machine-read), and the worklist that puts those readings in front of a curator.
--
-- THE STANDING RULE: *ingest preserves evidence; curation creates clinical
-- judgement*. Everything below is MACHINE-READ and NOT CONFIRMED. Nothing here is
-- an assertion, and nothing here feeds class_asserted_property, exact_ddi_pair,
-- ddi_candidate_pair, curated_ddi_pair or a signature target. Only a curator's
-- `holds` in curated_property_ruling promotes a reading; the worklist view at the
-- bottom is how a reading reaches that curator.
--
-- Facts and offsets only, no prose (db/051 section 2's rule): the quote shown with
-- a class mention is the EXISTING spl_wording_quote window that already covers the
-- span, joined on read, never a new copy.
--
-- Applied migrations are immutable, so the one db/051 comment this slice makes
-- false is corrected here by COMMENT ON (section 5), not by editing db/051.
-- =============================================================================

-- ============================================================================
-- 1. drugref.spl_advice_level -- the reading vocabulary, with its strength order
-- ============================================================================
-- A lookup table rather than a CHECK because the views need the ORDER (a CHECK
-- cannot be joined to). It is a SECOND HOME of drugref.ingest.spl_advice.LEVELS
-- and STRENGTH, pinned by a test that compares them.
CREATE TABLE IF NOT EXISTS drugref.spl_advice_level (
    level         text PRIMARY KEY,
    -- 1 = strongest. NULL for the two levels that are not a strength: an all-clear
    -- and "the reader could not tell". NULL, not 0 or 99, so a MIN() over ranks can
    -- never pick one of them by accident.
    strength_rank integer,
    CONSTRAINT spl_advice_level_rank_positive CHECK (strength_rank IS NULL OR strength_rank > 0),
    -- spl_class_statement looks a level up BY rank with a scalar subquery; two levels
    -- sharing a rank would make that subquery error or pick one arbitrarily. (NULLs
    -- stay allowed and never collide in a UNIQUE.)
    CONSTRAINT spl_advice_level_rank_unique UNIQUE (strength_rank)
);

INSERT INTO drugref.spl_advice_level (level, strength_rank) VALUES
    ('contraindicated', 1), ('avoid', 2), ('adjust_dose', 3), ('monitor', 4),
    ('no_meaningful_interaction', NULL), ('not_read', NULL)
ON CONFLICT (level) DO NOTHING;

COMMENT ON TABLE drugref.spl_advice_level IS
    'The machine-read advice vocabulary and its strength order (1 = strongest). '
    'MACHINE-READ, NOT CONFIRMED: a level is what a phrase-matching reader made of one '
    'scope (a sentence, heading block or table row), never an assertion. '
    'no_meaningful_interaction and not_read have no rank '
    'because neither is a strength. Second home of spl_advice.LEVELS / STRENGTH.';

-- ============================================================================
-- 2. drugref.spl_class_occurrence -- one row per class span (per class it resolved to)
-- ============================================================================
-- The class twin of spl_entity_occurrence (db/051 section 6), plus the reading.
-- A span folding onto several classes gets one row each, hence class_uuid in the key.
CREATE TABLE IF NOT EXISTS drugref.spl_class_occurrence (
    ingest_run      bigint  NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    source          text    NOT NULL
        CONSTRAINT spl_class_occurrence_source CHECK (source = 'SPL'),
    text_key        text    NOT NULL,
    char_start      integer NOT NULL,
    char_end        integer NOT NULL,
    class_uuid      uuid    NOT NULL REFERENCES drugref.substance_class(class_uuid),
    -- TRUE when the span folded onto more than one class. Never "pick the first".
    match_ambiguous boolean NOT NULL,
    -- How the matched key was derived from the stored class name; the second home of
    -- spl_match.MATCH_FORMS, pinned by a test. A reviewer weighs a 'number' (plural
    -- <-> singular) match differently from an exact one.
    match_form      text    NOT NULL
        CONSTRAINT spl_class_occurrence_match_form
        CHECK (match_form IN ('stored', 'number', 'label_order')),
    advice_level    text    NOT NULL REFERENCES drugref.spl_advice_level(level),
    -- More than one strength level fired in the scope, so advice_level is the
    -- strongest of several. Only meaningful for a strength level (CHECK below).
    advice_mixed    boolean NOT NULL,
    -- Which phrase rule fired, e.g. 'avoid.not_recommended'. NULL exactly when the
    -- reader read nothing.
    advice_rule     text,
    -- The span of text the reader looked at: the sentence, or the heading block when
    -- the mention stands in a list under a heading (a PLR table row is stored as a
    -- heading_block too: it is the block under its row heading). Second home of
    -- spl_advice.SCOPES.
    advice_scope    text    NOT NULL
        CONSTRAINT spl_class_occurrence_scope
        CHECK (advice_scope IN ('sentence', 'heading_block')),
    scope_start     integer NOT NULL,
    scope_end       integer NOT NULL,
    PRIMARY KEY (ingest_run, source, text_key, char_start, class_uuid),
    FOREIGN KEY (ingest_run, source, text_key)
        REFERENCES drugref.spl_wording (ingest_run, source, text_key),
    CONSTRAINT spl_class_occurrence_span CHECK (char_start >= 0 AND char_end > char_start),
    -- The reading must have looked at the mention it is about.
    CONSTRAINT spl_class_occurrence_scope_contains_span
        CHECK (scope_start <= char_start AND scope_end >= char_end),
    -- "Read nothing" and "a rule fired" are one fact; two columns nobody
    -- cross-checks would let them disagree (db/051's _complete shape).
    CONSTRAINT spl_class_occurrence_rule_iff_read
        CHECK ((advice_level = 'not_read') = (advice_rule IS NULL)),
    -- "Mixed" means several STRENGTH levels fired; an all-clear or a non-reading
    -- cannot be a mix.
    CONSTRAINT spl_class_occurrence_mixed_needs_strength
        CHECK (NOT advice_mixed
               OR advice_level IN ('contraindicated', 'avoid', 'adjust_dose', 'monitor'))
);

CREATE INDEX IF NOT EXISTS spl_class_occurrence_by_class
    ON drugref.spl_class_occurrence (class_uuid);
CREATE INDEX IF NOT EXISTS spl_class_occurrence_by_wording
    ON drugref.spl_class_occurrence (ingest_run, source, text_key);

COMMENT ON TABLE drugref.spl_class_occurrence IS
    'Every drug CLASS a section-34073-7 wording names, with the exact span and a '
    'MACHINE-READ advice level for the scope around it (sentence, heading block '
    'or table row). Facts and offsets only, no '
    'prose. NOT CONFIRMED and never an assertion: the level is a phrase reader''s '
    'guess, mixed/ambiguous flags say where it is weakest, and nothing here may feed '
    'class_asserted_property or any alert path. Only a curator''s holds ruling '
    'promotes a reading. Rebuildable per source like spl_entity_occurrence.';
COMMENT ON COLUMN drugref.spl_class_occurrence.advice_mixed IS
    'More than one strength level fired in the scope; advice_level is the strongest. '
    'Refused for no_meaningful_interaction and not_read.';
COMMENT ON COLUMN drugref.spl_class_occurrence.advice_rule IS
    'The phrase rule that fired (level.rule_id); NULL exactly when advice_level is '
    'not_read.';

-- ============================================================================
-- 3. The read path -- evidence grain, then (subject, class) grain
-- ============================================================================
-- GRAIN IN THE NAME, as db/051 section 7 insists: ..._evidence is one row per citing
-- label occurrence, so count(*) there is NOT a statement count; ..._statement is one
-- row per (subject, class) and its counts say which unit they count.

-- 3a. One row per (subject moiety, class, citing label occurrence). Joined exactly as
-- spl_ddi_evidence joins; resolved subjects only, filtered on the NULL uuid and never
-- on the route vocabulary (db/050 section 4).
CREATE OR REPLACE VIEW drugref.spl_class_statement_evidence AS
SELECT s.moiety_uuid       AS subject_moiety,
       o.class_uuid,
       l.set_id,
       l.version,
       l.effective_time,
       l.text_key,
       o.char_start,
       o.char_end,
       o.match_ambiguous,
       o.match_form,
       o.advice_level,
       o.advice_mixed,
       o.advice_rule,
       o.advice_scope,
       q.quote_text,           -- NULL where the 25% budget bought no window over the span
       EXISTS (SELECT 1 FROM drugref.class_membership cm
                WHERE cm.moiety_uuid = s.moiety_uuid
                  AND cm.class_uuid  = o.class_uuid) AS subject_in_class,
       l.ingest_run
FROM       drugref.spl_label_subject s
JOIN       drugref.spl_label l
        ON l.ingest_run = s.ingest_run AND l.source = s.source
       AND l.set_id     = s.set_id     AND l.version = s.version
JOIN       drugref.spl_class_occurrence o
        ON o.ingest_run = l.ingest_run AND o.source = l.source
       AND o.text_key   = l.text_key
-- LEFT: a mention with no window keeps its reading; an inner join would silently
-- publish only the quotable part.
LEFT JOIN  drugref.spl_wording_quote q
        ON q.ingest_run  = o.ingest_run AND q.source = o.source
       AND q.text_key    = o.text_key
       AND q.char_start <= o.char_start AND q.char_end >= o.char_end
WHERE      s.moiety_uuid IS NOT NULL;

COMMENT ON VIEW drugref.spl_class_statement_evidence IS
    'EVIDENCE GRAIN -- one row per (subject moiety, class, citing label occurrence); '
    'count(*) is NOT a statement count, read spl_class_statement. MACHINE-READ, NOT '
    'CONFIRMED, never an assertion. quote_text is the existing spl_wording_quote '
    'window covering the span (NULL where none). subject_in_class is a direct '
    'class_membership row for subject x class -- a label warning about its own class '
    'is a different statement from one warning about another.';

-- 3b. One row per (subject moiety, class).
CREATE OR REPLACE VIEW drugref.spl_class_statement AS
WITH per_level AS (
    -- Labels per level, so level_counts counts LABELS (the unit a reader cites).
    SELECT subject_moiety, class_uuid, advice_level,
           count(DISTINCT set_id) AS labels
      FROM drugref.spl_class_statement_evidence
     GROUP BY subject_moiety, class_uuid, advice_level
), levels AS (
    SELECT p.subject_moiety, p.class_uuid,
           jsonb_object_agg(p.advice_level, p.labels) AS level_counts,
           min(a.strength_rank)                       AS best_rank,
           bool_or(p.advice_level = 'no_meaningful_interaction') AS has_nmi,
           bool_or(p.advice_level = 'not_read')                  AS has_unread
      FROM per_level p
      JOIN drugref.spl_advice_level a ON a.level = p.advice_level
     GROUP BY p.subject_moiety, p.class_uuid
), totals AS (
    SELECT subject_moiety, class_uuid,
           count(DISTINCT set_id)  AS label_count,
           count(DISTINCT text_key) AS wording_count,
           bool_or(advice_mixed)    AS any_mixed,
           bool_or(match_ambiguous) AS any_ambiguous,
           bool_or(subject_in_class) AS subject_in_class
      FROM drugref.spl_class_statement_evidence
     GROUP BY subject_moiety, class_uuid
)
SELECT t.subject_moiety,
       t.class_uuid,
       t.label_count,
       t.wording_count,
       -- Strongest level by rank; failing any strength, an all-clear only when EVERY
       -- row is one, else not_read. A not_read row may be a WITHHELD avoid/monitor
       -- (spl_advice.WITHHELD), so one label's all-clear must never outvote it.
       CASE WHEN v.best_rank IS NOT NULL
            THEN (SELECT level FROM drugref.spl_advice_level WHERE strength_rank = v.best_rank)
            WHEN v.has_nmi AND NOT v.has_unread THEN 'no_meaningful_interaction'
            ELSE 'not_read' END AS strongest_level,
       v.level_counts,
       t.any_mixed,
       t.any_ambiguous,
       t.subject_in_class
FROM   totals t
JOIN   levels v ON v.subject_moiety = t.subject_moiety AND v.class_uuid = t.class_uuid;

COMMENT ON VIEW drugref.spl_class_statement IS
    '(SUBJECT, CLASS) GRAIN -- one row per subject moiety x class a label names. '
    'label_count / wording_count count DISTINCT labels / wordings (2.5 labels to a '
    'wording, so never quote one as the other). strongest_level is the lowest '
    'strength_rank present, else no_meaningful_interaction only if EVERY row has it, '
    'else not_read (a not_read row may hide a withheld strength level); '
    'level_counts is level -> label count. MACHINE-READ, NOT CONFIRMED, '
    'never an assertion.';

-- ============================================================================
-- 4. The worklist -- machine readings no curator has ruled on
-- ============================================================================
-- Only statements with a strength (an all-clear or not_read is not a claim worth a
-- curator's time) and no LIVE ruling of ANY verdict for (moiety, interacts_with,
-- class): a does_not_hold is an answer too. Same NOT EXISTS shape as db/061.
CREATE OR REPLACE VIEW drugref.gap_unconfirmed_label_class_statement AS
SELECT st.subject_moiety AS moiety_uuid,
       st.class_uuid,
       st.strongest_level,
       st.label_count,
       -- Labels at the strongest level itself (label_count spans EVERY level), so
       -- the curator's question can say "k of n labels".
       (st.level_counts ->> st.strongest_level)::int AS strongest_level_labels
FROM   drugref.spl_class_statement st
JOIN   drugref.spl_advice_level a ON a.level = st.strongest_level
WHERE  a.strength_rank IS NOT NULL
  AND  NOT EXISTS (SELECT 1 FROM drugref.curated_property_ruling r
                   WHERE r.superseded_by IS NULL
                     AND r.subject_kind = 'moiety' AND r.subject_uuid = st.subject_moiety
                     AND r.relationship = 'interacts_with'
                     AND r.object_kind = 'class' AND r.object_uuid = st.class_uuid);

COMMENT ON VIEW drugref.gap_unconfirmed_label_class_statement IS
    'Gap kind unconfirmed_label_class_statement: one row per (moiety, class) whose '
    'labels carry a machine-read STRENGTH level (strongest_level_labels of label_count '
    'labels say it; label_count spans every level) and which has no live '
    'curated_property_ruling of any verdict. MACHINE-READ, NOT CONFIRMED, never an '
    'assertion, and "not confirmed" is NOT "safe". Frozen gap key (registered by the '
    'question registry): SPLCLASS:<moiety_uuid>:<class_uuid>.';

-- ============================================================================
-- 5. Widen open_question_gap_kind by one value; correct db/051's stale comment
-- ============================================================================
-- db/061's DO-block pattern. The list is COPIED VERBATIM from db/061 (the latest
-- migration touching this constraint; 19 kinds) and extended by one -- never retyped
-- from a plan document.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%unconfirmed_label_class_statement%') THEN
        ALTER TABLE drugref.open_question DROP CONSTRAINT IF EXISTS open_question_gap_kind;
        ALTER TABLE drugref.open_question ADD CONSTRAINT open_question_gap_kind
            CHECK (gap_kind IN (
                'unpopulated_contraindication', 'unclassified_moiety',
                'unmatched_ingredient', 'unreviewed_expansion_root',
                'unresolved_ci_object', 'dead_by_expansion_policy',
                'condition_without_indication', 'uncurated_additive_effect',
                'uncurated_threshold', 'ineffective_contribution',
                'ungraded_contribution', 'unruled_composition_activity',
                'uncurated_condition_contradiction', 'uncurated_interaction_rule',
                'unresolved_onc_endpoint', 'uncurated_class_interaction_rule',
                'fda_cyp_unadjudicated', 'unresolved_ddi_endpoint',
                'unconfirmed_class_property', 'unconfirmed_label_class_statement'));
    END IF;
END $$;

COMMENT ON TABLE drugref.spl_entity_occurrence IS
    'Every known MOIETY a section-34073-7 wording NAMES, with the exact span. '
    'DERIVED FACTS AND OFFSETS ONLY -- no prose, so clear under either publisher''s '
    'reading of rule 6, and stored for every match whether or not the quote budget '
    'could afford a window over it. The match rule is the SHIPPED resolver''s: exact, '
    'case-insensitive, contiguous, whole-token, longest-match-wins over '
    'substance_moiety.display_name. It asserts NO RELATION: that two drugs are named '
    'in one interactions section is what this table says, and what the label MEANS by '
    'it is curation''s job. CORRECTED BY db/066: class occurrences ARE now stored, in '
    'spl_class_occurrence (with a machine-read advice level); db/051''s "deliberately '
    'absent" no longer holds for the class grain.';
