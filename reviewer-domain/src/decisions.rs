//! Shared contracts for append-only clinical decision revisions.

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{records::validated_target_key, ReviewKind, ValidationError};

const CLINICAL_PROSE_MAX_LENGTH: usize = 20_000;

/// Decision spellings accepted by the two current clinical review kinds.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReviewDecision {
    /// The interaction rule is clinically applicable.
    Applies,
    /// The interaction rule was reviewed and does not apply.
    DoesNotApply,
    /// The condition's contraindication is clinically operative.
    Contraindicated,
    /// The condition's indication is clinically operative.
    Indicated,
    /// Both condition assertions are correct in different clinical contexts.
    ContextDependent,
    /// The condition contradiction is a spurious source assertion.
    Spurious,
}

impl ReviewDecision {
    /// Return whether this decision requires clinical grading fields.
    pub fn requires_grade(self) -> bool {
        !matches!(self, Self::DoesNotApply | Self::Spurious)
    }
}

/// Severity levels accepted by Drugref's curated overlay.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Severity {
    /// Combination or use should be avoided.
    Contraindicated,
    /// High-severity interaction or condition risk.
    Major,
    /// Moderate-severity interaction or condition risk.
    Moderate,
    /// Low-severity interaction or condition risk.
    Minor,
}

impl Severity {
    /// Return the stable database spelling of this severity.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Contraindicated => "contraindicated",
            Self::Major => "major",
            Self::Moderate => "moderate",
            Self::Minor => "minor",
        }
    }
}

/// Evidence-attestation grades accepted by Drugref's curated overlay.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceGrade {
    /// Evidence is established.
    Established,
    /// Evidence is probable.
    Probable,
    /// Evidence is suspected.
    Suspected,
    /// Evidence is theoretical.
    Theoretical,
}

/// Registry-level signature status published for one curated revision.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SignatureStatus {
    /// No detached signature has been recorded for the revision.
    Unsigned,
    /// At least one signature is not objected to by the public key registry.
    Signed,
    /// Every signature is objected to because its registered key is revoked.
    SignedByRevokedKey,
    /// Every signature is objected to and at least one key was never registered.
    SignedByUnknownKey,
}

impl TryFrom<&str> for SignatureStatus {
    type Error = ValidationError;

    /// Parse the complete published database vocabulary without accepting drift.
    fn try_from(value: &str) -> Result<Self, Self::Error> {
        match value {
            "unsigned" => Ok(Self::Unsigned),
            "signed" => Ok(Self::Signed),
            "signed_by_revoked_key" => Ok(Self::SignedByRevokedKey),
            "signed_by_unknown_key" => Ok(Self::SignedByUnknownKey),
            value => Err(ValidationError(format!(
                "unknown curated signature status {value}"
            ))),
        }
    }
}

impl EvidenceGrade {
    /// Return the stable database spelling of this evidence grade.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Established => "established",
            Self::Probable => "probable",
            Self::Suspected => "suspected",
            Self::Theoretical => "theoretical",
        }
    }
}

/// Request to create one immutable clinical decision revision.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CreateReviewDecisionRequest {
    /// Clinical target kind named by the canonical target key.
    pub kind: ReviewKind,
    /// Frozen canonical open-question gap key.
    pub target_key: String,
    /// Target-specific clinical decision.
    pub decision: ReviewDecision,
    /// Required severity for asserting decisions and absent for retiring decisions.
    pub severity: Option<Severity>,
    /// Optional bounded clinical mechanism.
    pub mechanism: Option<String>,
    /// Optional bounded practical management guidance.
    pub management: Option<String>,
    /// Required evidence grade for asserting decisions and absent for retiring decisions.
    pub evidence_grade: Option<EvidenceGrade>,
    /// Live revision observed by the form, or null when no revision existed.
    pub expected_revision_id: Option<i64>,
}

impl CreateReviewDecisionRequest {
    /// Validate target vocabulary, optimistic concurrency, and clinical completeness.
    pub fn validate(mut self) -> Result<Self, ValidationError> {
        self.target_key = validated_target_key(self.kind, &self.target_key)?;
        validate_decision_kind(self.kind, self.decision)?;
        if self
            .expected_revision_id
            .is_some_and(|identifier| identifier <= 0)
        {
            return Err(ValidationError(
                "expectedRevisionId must be positive".into(),
            ));
        }
        let grade_is_valid = if self.decision.requires_grade() {
            self.severity.is_some() && self.evidence_grade.is_some()
        } else {
            self.severity.is_none() && self.evidence_grade.is_none()
        };
        if !grade_is_valid {
            return Err(ValidationError(
                "asserting decisions require severity and evidenceGrade; retiring decisions require both to be absent"
                    .into(),
            ));
        }
        self.mechanism = normalised_optional("mechanism", self.mechanism)?;
        self.management = normalised_optional("management", self.management)?;
        Ok(self)
    }
}

/// One immutable interaction or condition revision returned to the GUI.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewDecisionRevision {
    /// Stable curated overlay row identifier within this target kind.
    pub revision_id: i64,
    /// Target-specific clinical decision stored by this revision.
    pub decision: ReviewDecision,
    /// Stored severity, absent for retiring decisions.
    pub severity: Option<Severity>,
    /// Optional clinical mechanism.
    pub mechanism: Option<String>,
    /// Optional practical management guidance.
    pub management: Option<String>,
    /// Stored evidence grade, absent for retiring decisions.
    pub evidence_grade: Option<EvidenceGrade>,
    /// Immortal question UUID answered by this revision, absent on legacy CLI rows.
    pub question_uuid: Option<Uuid>,
    /// Authenticated reviewer-name snapshot stored by the curated overlay.
    pub reviewed_by: String,
    /// Candidate releases against which the reviewer formed the judgement.
    pub reviewed_against: String,
    /// RFC 3339 time at which the revision was recorded.
    pub reviewed_at: String,
    /// Later revision that superseded this row, or null while it is live.
    pub superseded_by: Option<i64>,
    /// Database-derived registry-level detached signature status.
    pub signature_status: SignatureStatus,
}

/// Complete append-only decision history for one canonical target.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewDecisionRecord {
    /// Frozen canonical open-question gap key.
    pub target_key: String,
    /// Current live revision identifier, or null before the first decision.
    pub current_revision_id: Option<i64>,
    /// Immutable revisions in insertion order.
    pub history: Vec<ReviewDecisionRevision>,
}

/// Reject a decision spelling that belongs to the other target kind.
fn validate_decision_kind(
    kind: ReviewKind,
    decision: ReviewDecision,
) -> Result<(), ValidationError> {
    let valid = match kind {
        ReviewKind::InteractionRule => {
            matches!(
                decision,
                ReviewDecision::Applies | ReviewDecision::DoesNotApply
            )
        }
        ReviewKind::ConditionContradiction => matches!(
            decision,
            ReviewDecision::Contraindicated
                | ReviewDecision::Indicated
                | ReviewDecision::ContextDependent
                | ReviewDecision::Spurious
        ),
    };
    valid.then_some(()).ok_or_else(|| {
        ValidationError("decision does not belong to the selected review kind".into())
    })
}

/// Trim optional prose, treating blank input as absent and enforcing its service bound.
fn normalised_optional(
    label: &str,
    value: Option<String>,
) -> Result<Option<String>, ValidationError> {
    let Some(value) = value else {
        return Ok(None);
    };
    let value = value.trim();
    if value.chars().count() > CLINICAL_PROSE_MAX_LENGTH {
        return Err(ValidationError(format!(
            "{label} must contain at most {CLINICAL_PROSE_MAX_LENGTH} characters"
        )));
    }
    Ok((!value.is_empty()).then(|| value.to_string()))
}

#[cfg(test)]
mod tests {
    use super::{
        CreateReviewDecisionRequest, EvidenceGrade, ReviewDecision, Severity, SignatureStatus,
    };
    use crate::ReviewKind;

    /// Require complete grading only for decisions that assert clinical content.
    #[test]
    fn decision_validation_matches_overlay_completeness() {
        let valid = CreateReviewDecisionRequest {
            kind: ReviewKind::InteractionRule,
            target_key: "MOIETY:a/CLASS:b/CI_AXIS:CI_with".into(),
            decision: ReviewDecision::Applies,
            severity: Some(Severity::Major),
            mechanism: Some("  CYP inhibition  ".into()),
            management: Some("   ".into()),
            evidence_grade: Some(EvidenceGrade::Established),
            expected_revision_id: None,
        }
        .validate()
        .expect("complete interaction decision");
        assert_eq!(valid.mechanism.as_deref(), Some("CYP inhibition"));
        assert_eq!(valid.management, None);

        let incomplete = CreateReviewDecisionRequest {
            evidence_grade: None,
            ..valid
        };
        assert!(incomplete.clone().validate().is_err());

        let partly_graded_retirement = CreateReviewDecisionRequest {
            decision: ReviewDecision::DoesNotApply,
            severity: Some(Severity::Minor),
            evidence_grade: None,
            ..incomplete
        };
        assert!(partly_graded_retirement.validate().is_err());
    }

    /// Pin all four published signature-status spellings and reject silent widening.
    #[test]
    fn signature_status_vocabulary_is_exact() {
        assert_eq!(
            SignatureStatus::try_from("signed_by_unknown_key"),
            Ok(SignatureStatus::SignedByUnknownKey)
        );
        assert!(SignatureStatus::try_from("verified").is_err());
    }

    /// Keep interaction and condition decision vocabularies disjoint at the boundary.
    #[test]
    fn decision_validation_rejects_the_other_target_vocabulary() {
        let invalid = CreateReviewDecisionRequest {
            kind: ReviewKind::ConditionContradiction,
            target_key: "MOIETY:a/CONDITION:b".into(),
            decision: ReviewDecision::DoesNotApply,
            severity: None,
            mechanism: None,
            management: None,
            evidence_grade: None,
            expected_revision_id: None,
        };
        assert!(invalid.validate().is_err());
    }
}
