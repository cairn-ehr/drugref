//! Shared contracts and validation for append-only reviewer working records.

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{validate_text, ReviewKind, ValidationError};

const OPTIONAL_TEXT_MIN_LENGTH: usize = 0;
const REQUIRED_TEXT_MIN_LENGTH: usize = 1;
const TARGET_KEY_MAX_LENGTH: usize = 500;
const ANNOTATION_MAX_LENGTH: usize = 20_000;
const REFERENCE_VALUE_MAX_LENGTH: usize = 2_000;
const REFERENCE_NOTE_MAX_LENGTH: usize = 10_000;

/// Identifier schemes admitted for citation-only working references.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub enum EvidenceReferenceScheme {
    /// Digital Object Identifier.
    DOI,
    /// PubMed identifier.
    PMID,
    /// PubMed Central identifier.
    PMCID,
    /// ClinicalTrials.gov study identifier.
    NCT,
    /// Structured Product Label set or document identifier.
    SPL,
    /// A URL used only when no stronger identifier scheme is available.
    URL,
}

impl EvidenceReferenceScheme {
    /// Return the stable database and wire representation of the scheme.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::DOI => "DOI",
            Self::PMID => "PMID",
            Self::PMCID => "PMCID",
            Self::NCT => "NCT",
            Self::SPL => "SPL",
            Self::URL => "URL",
        }
    }
}

/// Stable question selector accepted by working-record reads.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewRecordQuery {
    /// Kind of clinical question named by the target key.
    pub kind: ReviewKind,
    /// Frozen canonical open-question gap key.
    pub target_key: String,
}

impl ReviewRecordQuery {
    /// Trim and bound the canonical target key before a database lookup.
    pub fn validate(mut self) -> Result<Self, ValidationError> {
        self.target_key = validated_target_key(self.kind, &self.target_key)?;
        Ok(self)
    }
}

/// Authenticated request to append one Markdown working note.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CreateAnnotationRequest {
    /// Kind of clinical question named by the target key.
    pub kind: ReviewKind,
    /// Frozen canonical open-question gap key.
    pub target_key: String,
    /// Immutable Markdown working note.
    pub annotation_markdown: String,
}

impl CreateAnnotationRequest {
    /// Validate and normalise a working note before storage is accessed.
    pub fn validate(mut self) -> Result<Self, ValidationError> {
        self.target_key = validated_target_key(self.kind, &self.target_key)?;
        validate_text(
            "annotation",
            &self.annotation_markdown,
            REQUIRED_TEXT_MIN_LENGTH,
            ANNOTATION_MAX_LENGTH,
        )?;
        self.annotation_markdown = self.annotation_markdown.trim().to_string();
        Ok(self)
    }
}

/// Authenticated request to append one citation-only working reference.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CreateEvidenceReferenceRequest {
    /// Kind of clinical question named by the target key.
    pub kind: ReviewKind,
    /// Frozen canonical open-question gap key.
    pub target_key: String,
    /// Structured identifier scheme for the cited source.
    pub reference_scheme: EvidenceReferenceScheme,
    /// Identifier or URL in the selected scheme.
    pub reference_value: String,
    /// Optional Markdown context explaining why the source was attached.
    pub note_markdown: String,
}

impl CreateEvidenceReferenceRequest {
    /// Validate and normalise a citation-only reference before storage is accessed.
    pub fn validate(mut self) -> Result<Self, ValidationError> {
        self.target_key = validated_target_key(self.kind, &self.target_key)?;
        validate_text(
            "reference value",
            &self.reference_value,
            REQUIRED_TEXT_MIN_LENGTH,
            REFERENCE_VALUE_MAX_LENGTH,
        )?;
        validate_text(
            "reference note",
            &self.note_markdown,
            OPTIONAL_TEXT_MIN_LENGTH,
            REFERENCE_NOTE_MAX_LENGTH,
        )?;
        self.reference_value = self.reference_value.trim().to_string();
        self.note_markdown = self.note_markdown.trim().to_string();
        Ok(self)
    }
}

/// Immutable reviewer-authored working note returned for one target.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewAnnotation {
    /// Stable annotation ledger identifier.
    pub annotation_id: i64,
    /// Stable reviewer identity that authored the note.
    pub reviewer_uuid: Uuid,
    /// Current reviewer username used for compact attribution.
    pub username: String,
    /// Current reviewer display name used for human-readable attribution.
    pub reviewer_name: String,
    /// Immutable Markdown source.
    pub annotation_markdown: String,
    /// RFC 3339 time at which the note was recorded.
    pub recorded_at: String,
}

/// Immutable citation-only working reference returned for one target.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EvidenceReference {
    /// Stable evidence-reference ledger identifier.
    pub evidence_reference_id: i64,
    /// Stable reviewer identity that attached the reference.
    pub reviewer_uuid: Uuid,
    /// Current compact reviewer username.
    pub username: String,
    /// Current reviewer display name.
    pub reviewer_name: String,
    /// Structured identifier scheme for the cited source.
    pub reference_scheme: EvidenceReferenceScheme,
    /// Identifier or URL in the selected scheme.
    pub reference_value: String,
    /// Optional Markdown context supplied when the reference was attached.
    pub note_markdown: String,
    /// RFC 3339 time at which the reference was recorded.
    pub recorded_at: String,
}

/// Complete immutable working history attached to one current review target.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewRecord {
    /// Frozen canonical open-question gap key.
    pub target_key: String,
    /// Reviewer notes in insertion order.
    pub annotations: Vec<ReviewAnnotation>,
    /// Citation-only references in insertion order.
    pub evidence_references: Vec<EvidenceReference>,
}

/// Validate the canonical gap-key prefix associated with one review kind.
fn validated_target_key(kind: ReviewKind, value: &str) -> Result<String, ValidationError> {
    let value = value.trim();
    let shape_is_valid = match kind {
        ReviewKind::InteractionRule => {
            value.starts_with("MOIETY:") && value.contains("/CLASS:") && value.contains("/CI_AXIS:")
        }
        ReviewKind::ConditionContradiction => {
            value.starts_with("MOIETY:") && value.contains("/CONDITION:")
        }
    };
    if value.is_empty() || value.chars().count() > TARGET_KEY_MAX_LENGTH || !shape_is_valid {
        return Err(ValidationError(
            "targetKey is not a canonical review target key".into(),
        ));
    }
    Ok(value.to_string())
}

#[cfg(test)]
mod tests {
    use super::{CreateAnnotationRequest, CreateEvidenceReferenceRequest, EvidenceReferenceScheme};
    use crate::ReviewKind;

    /// Accept canonical keys and trim immutable working-note text.
    #[test]
    fn annotation_input_requires_a_canonical_target_and_non_blank_note() {
        let input = CreateAnnotationRequest {
            kind: ReviewKind::InteractionRule,
            target_key: " MOIETY:a/CLASS:b/CI_AXIS:CI_with ".into(),
            annotation_markdown: "  Check renal-dose guidance.  ".into(),
        }
        .validate()
        .expect("valid annotation");
        assert_eq!(input.target_key, "MOIETY:a/CLASS:b/CI_AXIS:CI_with");
        assert_eq!(input.annotation_markdown, "Check renal-dose guidance.");

        let invalid = CreateAnnotationRequest {
            kind: ReviewKind::ConditionContradiction,
            target_key: "MOIETY:a/CLASS:b/CI_AXIS:CI_with".into(),
            annotation_markdown: "  ".into(),
        };
        assert!(invalid.validate().is_err());
    }

    /// Keep working references citation-only while validating their identifier.
    #[test]
    fn evidence_reference_input_allows_context_but_requires_an_identifier() {
        let input = CreateEvidenceReferenceRequest {
            kind: ReviewKind::ConditionContradiction,
            target_key: "MOIETY:a/CONDITION:b".into(),
            reference_scheme: EvidenceReferenceScheme::PMID,
            reference_value: " 12345678 ".into(),
            note_markdown: "  Primary cohort. ".into(),
        }
        .validate()
        .expect("valid working reference");
        assert_eq!(input.reference_value, "12345678");
        assert_eq!(input.note_markdown, "Primary cohort.");

        let invalid = CreateEvidenceReferenceRequest {
            reference_value: " ".into(),
            ..input
        };
        assert!(invalid.validate().is_err());
    }
}
