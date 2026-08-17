-- db/045_reviewer_annotations.sql -- reviewer working notes and references.
--
-- These rows record research in progress, not a clinical ruling. Both relations are
-- immutable insert-only ledgers attributed to an authenticated reviewer. They point
-- at the existing immortal open_question UUID so a later curated revision can cite
-- the same question without copying or guessing its clinical natural key.

CREATE TABLE drugref.reviewer_annotation (
    reviewer_annotation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question_uuid uuid NOT NULL REFERENCES drugref.open_question(question_uuid),
    reviewer_uuid uuid NOT NULL REFERENCES drugref.reviewer_account(reviewer_uuid),
    annotation_markdown text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT reviewer_annotation_present CHECK (btrim(annotation_markdown) <> ''),
    CONSTRAINT reviewer_annotation_length CHECK (char_length(annotation_markdown) <= 20000)
);

CREATE INDEX reviewer_annotation_by_question
    ON drugref.reviewer_annotation (question_uuid, recorded_at, reviewer_annotation_id);

CREATE TRIGGER reviewer_annotation_insert_only
    BEFORE UPDATE OR DELETE ON drugref.reviewer_annotation
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

-- A working reference says only "consult this source". It deliberately has no
-- supports/refutes verdict, evidence grade or confidence: those judgements belong to
-- the later curated-revision transaction, not to this research ledger.
CREATE TABLE drugref.reviewer_evidence_reference (
    reviewer_evidence_reference_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question_uuid uuid NOT NULL REFERENCES drugref.open_question(question_uuid),
    reviewer_uuid uuid NOT NULL REFERENCES drugref.reviewer_account(reviewer_uuid),
    reference_scheme text NOT NULL,
    reference_value text NOT NULL,
    note_markdown text NOT NULL DEFAULT '',
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT reviewer_evidence_reference_scheme CHECK (reference_scheme IN (
        'DOI', 'PMID', 'PMCID', 'NCT', 'SPL', 'URL')),
    CONSTRAINT reviewer_evidence_reference_value_present
        CHECK (btrim(reference_value) <> ''),
    CONSTRAINT reviewer_evidence_reference_value_length
        CHECK (char_length(reference_value) <= 2000),
    CONSTRAINT reviewer_evidence_reference_note_length
        CHECK (char_length(note_markdown) <= 10000)
);

CREATE INDEX reviewer_evidence_reference_by_question
    ON drugref.reviewer_evidence_reference (
        question_uuid, recorded_at, reviewer_evidence_reference_id);

CREATE TRIGGER reviewer_evidence_reference_insert_only
    BEFORE UPDATE OR DELETE ON drugref.reviewer_evidence_reference
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

COMMENT ON TABLE drugref.reviewer_annotation IS
    'Immutable reviewer-authored Markdown working notes on an open question. A note '
    'is research history, not question state, a clinical ruling or a signature.';
COMMENT ON TABLE drugref.reviewer_evidence_reference IS
    'Immutable citation-only references gathered during review. The absence of a '
    'verdict, grade and confidence is deliberate: attaching a source does not make '
    'a clinical assertion.';
