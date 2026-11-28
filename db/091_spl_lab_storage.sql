-- db/091 -- drug effects on laboratory values, part 3: where label readings live (#294).
--
-- EMPTY UNTIL STEP 3. The first openFDA label ingest (#294 step 3) fills these tables;
-- this round ships them so the read and coverage views (db/092) have their final shape
-- and ReportTriage can code against those views now.
--
-- ITS OWN SOURCE, 'SPL_LAB', NOT 'SPL'. Per-source projections are delete-and-rebuild
-- (CLAUDE.md), so if lab readings were written under the DDI ingest's source, rebuilding
-- either ingest would delete the other's rows. Hence a source of their own, its own
-- writer, and every table's CHECK (source = 'SPL_LAB'). The pure subject resolver
-- (spl_subject.resolve_subject) and the DailyMed recovery are reused by the ingest; the
-- tables are not shared.
--
-- NO PROSE, as in db/051: a wording is identified by its digest. Quote windows come with
-- step 3 under the 25% budget, and never for the drug_interactions section, which the
-- DDI ingest already quotes up to that budget (design spec 6.4).
--
-- The shapes follow db/051 (label, subject, wording) and db/066 (occurrence with a
-- machine reading). Design spec 2026-11-27-drugref-lab-effects-design.md section 6.4.

-- ============================================================================
-- 1. The new source and writer. Each list COPIED VERBATIM from the latest migration
--    that set it (source: db/067; writer: db/071), extended by one.
--    provenance.WRITERS gains 'spl_lab_run' in the SAME commit; if they drift apart
--    a per-source rebuild silently deletes nothing.
-- ============================================================================
ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_source;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_source
    CHECK (source IN ('UNII', 'CHEBI', 'MED-RT', 'MeSH', 'PBS', 'DRUGREF', 'GSRS',
                      'ONCHIGH', 'FDA-CYP', 'DRUGCENTRAL', 'SPL', 'DRUGREF-ALLERGY',
                      'SPL_LAB'));

ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_writer;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_writer
    CHECK (writer IN ('unii_run', 'chebi', 'medrt_run', 'mesh_run', 'mesh_rel_run',
                      'pbs_run', 'curation', 'unattributed', 'gsrs_run',
                      'onchigh_run', 'fda_cyp_run', 'drugcentral_run', 'spl_run',
                      'allergy_group_run', 'moiety_name_run', 'mesh_synonym_run',
                      'spl_lab_run'));

-- ============================================================================
-- 2. spl_lab_label -- one row per label the lab ingest read
-- ============================================================================
CREATE TABLE IF NOT EXISTS drugref.spl_lab_label (
    ingest_run     bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    source         text   NOT NULL CONSTRAINT spl_lab_label_source
                                   CHECK (source = 'SPL_LAB'),
    -- The citation, and the join key to openFDA and DailyMed (as in db/051).
    set_id         text   NOT NULL,
    -- A revised label is a new document making its own statement.
    version        text   NOT NULL,
    effective_time date,
    PRIMARY KEY (ingest_run, source, set_id, version)
);

COMMENT ON TABLE drugref.spl_lab_label IS
    'One row per label the lab-effect ingest read (#294), keyed on (set_id, version). '
    'Source SPL_LAB, separate from the DDI ingest''s SPL so neither rebuild deletes the '
    'other. No prose. Empty until #294 step 3.';

-- ============================================================================
-- 3. spl_lab_label_subject -- which drug a label is ABOUT
-- ============================================================================
-- db/051's nullable-uuid design: an unresolved subject is kept (moiety_uuid NULL), so
-- the recovery population is countable. A combination label has several subjects.
CREATE TABLE IF NOT EXISTS drugref.spl_lab_label_subject (
    spl_lab_label_subject_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingest_run  bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    source      text   NOT NULL CONSTRAINT spl_lab_label_subject_source
                                CHECK (source = 'SPL_LAB'),
    set_id      text   NOT NULL,
    version     text   NOT NULL,
    moiety_uuid uuid   REFERENCES drugref.substance_moiety(moiety_uuid),
    -- How the subject resolved, or why not. Free text here: step 3 fixes the
    -- vocabulary, as db/051 did for the DDI ingest.
    resolution  text   NOT NULL,
    FOREIGN KEY (ingest_run, source, set_id, version)
        REFERENCES drugref.spl_lab_label (ingest_run, source, set_id, version)
);

CREATE INDEX IF NOT EXISTS spl_lab_label_subject_by_moiety
    ON drugref.spl_lab_label_subject (moiety_uuid);
CREATE INDEX IF NOT EXISTS spl_lab_label_subject_by_label
    ON drugref.spl_lab_label_subject (ingest_run, source, set_id, version);

COMMENT ON TABLE drugref.spl_lab_label_subject IS
    'Which drug(s) a lab-ingest label is about; moiety_uuid NULL = did not resolve '
    '(kept, so the gap is countable). Subject resolution follows the DDI ingest: the '
    'moiety where a moiety UNII resolves, the salt only where none does, no salt -> base '
    'folding until #67. Empty until #294 step 3.';

-- ============================================================================
-- 4. spl_lab_wording -- one row per DISTINCT section wording (digest, no text)
-- ============================================================================
CREATE TABLE IF NOT EXISTS drugref.spl_lab_wording (
    ingest_run bigint  NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    source     text    NOT NULL CONSTRAINT spl_lab_wording_source
                               CHECK (source = 'SPL_LAB'),
    -- SHA-256 of the whitespace-normalised section text (spl.section_key).
    text_key   text    NOT NULL,
    char_count integer NOT NULL CHECK (char_count > 0),
    PRIMARY KEY (ingest_run, source, text_key)
);

COMMENT ON TABLE drugref.spl_lab_wording IS
    'One row per distinct label-section wording the lab ingest read, IDENTIFIED BY DIGEST '
    'AND NOT STORED (no prose column, as db/051). Many labels share a wording, so rates '
    'are per wording unless a view says otherwise. Empty until #294 step 3.';

-- ============================================================================
-- 5. spl_lab_label_section -- which wording each label carries in each section
-- ============================================================================
CREATE TABLE IF NOT EXISTS drugref.spl_lab_label_section (
    ingest_run bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    source     text   NOT NULL CONSTRAINT spl_lab_label_section_source
                             CHECK (source = 'SPL_LAB'),
    set_id     text   NOT NULL,
    version    text   NOT NULL,
    section    text   NOT NULL REFERENCES drugref.lab_effect_section(section),
    text_key   text   NOT NULL,
    PRIMARY KEY (ingest_run, source, set_id, version, section),
    FOREIGN KEY (ingest_run, source, set_id, version)
        REFERENCES drugref.spl_lab_label (ingest_run, source, set_id, version),
    FOREIGN KEY (ingest_run, source, text_key)
        REFERENCES drugref.spl_lab_wording (ingest_run, source, text_key)
);

CREATE INDEX IF NOT EXISTS spl_lab_label_section_by_wording
    ON drugref.spl_lab_label_section (ingest_run, source, text_key);

COMMENT ON TABLE drugref.spl_lab_label_section IS
    'Label x section -> the wording it carries there. The section is the statement''s '
    'grade (lab_effect_section.section_rank). Empty until #294 step 3.';

-- ============================================================================
-- 6. spl_lab_effect_occurrence -- one machine reading per term span
-- ============================================================================
-- The twin of spl_class_occurrence (db/066): the span, the term the reader matched,
-- and its reading of direction, ROLE and co-medication for the scope around it.
CREATE TABLE IF NOT EXISTS drugref.spl_lab_effect_occurrence (
    ingest_run     bigint  NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    source         text    NOT NULL CONSTRAINT spl_lab_effect_occurrence_source
                                    CHECK (source = 'SPL_LAB'),
    text_key       text    NOT NULL,
    char_start     integer NOT NULL,
    char_end       integer NOT NULL,
    term_id        text    NOT NULL REFERENCES drugref.lab_effect_term(term_id),
    -- From the term, or overridden by the scope ("decreased serum potassium").
    direction_read text    NOT NULL
        CHECK (direction_read IN ('high', 'low', 'unspecified')),
    -- The four roles; not_read = no role rule fired (NEVER a default 'causes': owner
    -- ruling 2026-11-27, design spec 2.5); not_an_effect = the reader recognised a
    -- precondition, not something the drug does ("do not use in patients with
    -- hyperkalemia") -- kept as evidence, never in the read view.
    role_read      text    NOT NULL
        CHECK (role_read IN ('causes', 'more_dangerous', 'monitoring', 'interferes',
                             'not_read', 'not_an_effect')),
    -- The rule that fired, e.g. 'more_dangerous.predisposes_to_toxicity'.
    role_rule      text,
    -- A drug or class the same scope names ("INR increased in patients on warfarin").
    co_medication_moiety_uuid uuid REFERENCES drugref.substance_moiety(moiety_uuid),
    co_medication_class_uuid  uuid REFERENCES drugref.substance_class(class_uuid),
    scope          text    NOT NULL CHECK (scope IN ('sentence', 'heading_block')),
    scope_start    integer NOT NULL,
    scope_end      integer NOT NULL,
    PRIMARY KEY (ingest_run, source, text_key, char_start, term_id),
    FOREIGN KEY (ingest_run, source, text_key)
        REFERENCES drugref.spl_lab_wording (ingest_run, source, text_key),
    CONSTRAINT spl_lab_effect_occurrence_span
        CHECK (char_start >= 0 AND char_end > char_start),
    CONSTRAINT spl_lab_effect_occurrence_scope_contains_span
        CHECK (scope_start <= char_start AND scope_end >= char_end),
    -- "Read nothing" and "no rule fired" are one fact (db/066's shape).
    CONSTRAINT spl_lab_effect_occurrence_rule_iff_read
        CHECK ((role_read = 'not_read') = (role_rule IS NULL)),
    CONSTRAINT spl_lab_effect_occurrence_one_co_medication
        CHECK (num_nonnulls(co_medication_moiety_uuid, co_medication_class_uuid) <= 1)
);

CREATE INDEX IF NOT EXISTS spl_lab_effect_occurrence_by_wording
    ON drugref.spl_lab_effect_occurrence (ingest_run, source, text_key);

COMMENT ON TABLE drugref.spl_lab_effect_occurrence IS
    'Every lab-effect term a label-section wording names, with its span and a '
    'MACHINE-READ direction, role and co-medication for the scope around it. Facts and '
    'offsets only, no prose. NOT CONFIRMED and never an assertion: only a curator''s '
    'curated_lab_effect ruling confirms. role_read not_read = no rule fired (never a '
    'default causes); not_an_effect = a precondition, evidence only. Empty until #294 '
    'step 3.';
