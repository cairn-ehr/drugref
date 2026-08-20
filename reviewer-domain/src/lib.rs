//! Shared, serializable contracts and validation for the reviewer app and service.
//!
//! This crate owns API vocabulary and behavioural limits so Rust callers cannot
//! silently disagree about account or queue semantics.
#![deny(missing_docs)]

mod accounts;
mod decisions;
mod records;
mod signing;
mod trust;

pub use accounts::{
    AccountAdministrationResult, BootstrapStatus, CreateAccountRequest, LoginRequest,
    ReviewerAccount, ReviewerRole, RotateReviewerPasswordRequest, SessionGrant,
    UpdateReviewerProfileRequest,
};
pub use decisions::{
    CreateReviewDecisionRequest, EvidenceGrade, ReviewDecision, ReviewDecisionRecord,
    ReviewDecisionRevision, Severity, SignatureStatus,
};
pub use records::{
    CreateAnnotationRequest, CreateEvidenceReferenceRequest, EvidenceReference,
    EvidenceReferenceScheme, ReviewAnnotation, ReviewRecord, ReviewRecordQuery,
};
pub use signing::{
    canonical_payload, validate_signing_passphrase, CanonicalField, DeviceSigningStatus,
    EnrolSigningKeyRequest, PendingReviewSignature, PendingSignatureReason,
    ReplaceSigningKeyRequest, ReviewSignatureChallenge, ReviewSignaturePreview,
    ReviewSignatureQuery, SigningKeyReplacement, SigningKeyStatus, SigningKeySummary,
    SubmitReviewSignatureRequest, CURATED_CONDITION_V1_FIELDS, CURATED_INTERACTION_V1_FIELDS,
};
pub use trust::{
    validate_signing_key_fingerprint, AdministerSigningKeyRequest, AdministrativeSigningKeyStatus,
    SigningKeyAdministrationResult, SigningKeyTrustStatus, SigningKeyTrustSummary,
};

use serde::{Deserialize, Serialize};
use uuid::Uuid;

const OPTIONAL_TEXT_MIN_LENGTH: usize = 0;
const REQUIRED_TEXT_MIN_LENGTH: usize = 1;
const USERNAME_MIN_LENGTH: usize = 3;
const USERNAME_MAX_LENGTH: usize = 64;
const FULL_NAME_MAX_LENGTH: usize = 200;
const QUALIFICATIONS_MAX_LENGTH: usize = 500;
const BIOGRAPHY_MAX_LENGTH: usize = 10_000;
const PASSWORD_MIN_LENGTH: usize = 12;
const PASSWORD_MAX_LENGTH: usize = 256;
const FIRST_PAGE: u32 = 1;
const MAXIMUM_PAGE: u32 = 1_000_000;
const DEFAULT_PAGE_SIZE: u16 = 25;
const MAXIMUM_PAGE_SIZE: u16 = 100;
const FILTER_MAX_LENGTH: usize = 100;
const SEARCH_MAX_LENGTH: usize = 200;

/// Stable kinds of unresolved clinical records exposed by the review service.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReviewKind {
    /// One source interaction rule awaiting a source-neutral clinical ruling.
    InteractionRule,
    /// One drug-condition pair reached by contradictory source projections.
    ConditionContradiction,
}

impl ReviewKind {
    /// Return the stable database and wire representation of the review kind.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::InteractionRule => "interaction_rule",
            Self::ConditionContradiction => "condition_contradiction",
        }
    }
}

/// Optional paging and filter parameters accepted by the review queue endpoint.
#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewQueueQuery {
    /// One-based page number.
    pub page: Option<u32>,
    /// Maximum number of records returned on one page.
    pub page_size: Option<u16>,
    /// Exact review-kind filter.
    pub kind: Option<ReviewKind>,
    /// Exact source filter.
    pub source: Option<String>,
    /// Exact relationship filter.
    pub relationship: Option<String>,
    /// Literal case-insensitive subject, object, or relationship search.
    pub search: Option<String>,
}

/// Validated and normalised paging and filter parameters used by storage code.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidatedReviewQueueQuery {
    /// Validated one-based page number.
    pub page: u32,
    /// Validated maximum number of records returned on one page.
    pub page_size: u16,
    /// Exact review-kind filter.
    pub kind: Option<ReviewKind>,
    /// Trimmed exact source filter.
    pub source: Option<String>,
    /// Trimmed exact relationship filter.
    pub relationship: Option<String>,
    /// Trimmed literal case-insensitive search.
    pub search: Option<String>,
}

impl ReviewQueueQuery {
    /// Apply defaults, bounds, and whitespace normalisation to a queue query.
    pub fn validate(self) -> Result<ValidatedReviewQueueQuery, ValidationError> {
        let page = self.page.unwrap_or(FIRST_PAGE);
        if !(FIRST_PAGE..=MAXIMUM_PAGE).contains(&page) {
            return Err(ValidationError(format!(
                "page must be between {FIRST_PAGE} and {MAXIMUM_PAGE}"
            )));
        }
        let page_size = self.page_size.unwrap_or(DEFAULT_PAGE_SIZE);
        if !(FIRST_PAGE as u16..=MAXIMUM_PAGE_SIZE).contains(&page_size) {
            return Err(ValidationError(format!(
                "pageSize must be between {FIRST_PAGE} and {MAXIMUM_PAGE_SIZE}"
            )));
        }

        Ok(ValidatedReviewQueueQuery {
            page,
            page_size,
            kind: self.kind,
            source: trimmed_optional("source", self.source, FILTER_MAX_LENGTH)?,
            relationship: trimmed_optional("relationship", self.relationship, FILTER_MAX_LENGTH)?,
            search: trimmed_optional("search", self.search, SEARCH_MAX_LENGTH)?,
        })
    }
}

impl ValidatedReviewQueueQuery {
    /// Return the SQL row offset implied by this one-based page.
    pub fn offset(&self) -> i64 {
        i64::from(self.page - FIRST_PAGE) * i64::from(self.page_size)
    }
}

/// Trim and validate one optional, non-empty query parameter.
fn trimmed_optional(
    label: &str,
    value: Option<String>,
    maximum: usize,
) -> Result<Option<String>, ValidationError> {
    let Some(value) = value else {
        return Ok(None);
    };
    let trimmed = value.trim();
    if trimmed.is_empty() || trimmed.chars().count() > maximum {
        return Err(ValidationError(format!(
            "{label} must contain between {REQUIRED_TEXT_MIN_LENGTH} and {maximum} characters"
        )));
    }
    Ok(Some(trimmed.to_string()))
}

/// Counts that describe the complete current review queue snapshot.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewQueueSummary {
    /// Number of uncurated interaction rules.
    pub interaction_rules: i64,
    /// Number of drug-condition pairs with contradictory projections.
    pub condition_contradictions: i64,
    /// Number of concrete DDI pairs expanded from curated rules.
    pub reviewed_pairs: i64,
}

/// Filter values derived from the complete current queue.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewQueueFilters {
    /// Review kinds currently present.
    pub kinds: Vec<ReviewKind>,
    /// Candidate sources currently present.
    pub sources: Vec<String>,
    /// Clinical relationships currently present.
    pub relationships: Vec<String>,
}

/// One stable, unresolved clinical target displayed in the reviewer queue.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewQueueItem {
    /// UI identity derived from the target's stable natural key.
    pub id: String,
    /// Source-neutral natural key used by the future write path.
    pub target_key: String,
    /// Kind of clinical question represented by this target.
    pub kind: ReviewKind,
    /// Stable Drugref UUID for the subject moiety.
    pub subject_uuid: Uuid,
    /// Stable Drugref UUID for the object class or condition.
    pub object_uuid: Uuid,
    /// Human-readable subject name.
    pub subject_name: String,
    /// Human-readable class or condition name.
    pub object_name: String,
    /// Source relationships that produced the target.
    pub relationships: Vec<String>,
    /// Sources asserting the candidate target.
    pub candidate_sources: Vec<String>,
    /// Upstream releases supporting the candidate target.
    pub upstream_releases: Vec<String>,
    /// Number of concrete pairs affected by this target.
    pub impact_count: i64,
    /// Human-readable clinical question for the reviewer.
    pub question: String,
    /// Explanation of why the target entered the queue.
    pub provenance: String,
}

/// Page metadata returned with every queue snapshot.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Pagination {
    /// One-based current page number.
    pub page: u32,
    /// Maximum records requested for the page.
    pub page_size: u16,
    /// Number of records matching the current filters.
    pub total_items: i64,
    /// Number of pages matching the current filters.
    pub total_pages: u32,
}

/// Complete review queue response for one filtered page.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewQueuePage {
    /// RFC 3339 timestamp identifying the database snapshot time.
    pub generated_at: String,
    /// Counts for the unfiltered queue.
    pub summary: ReviewQueueSummary,
    /// Available filters derived from the unfiltered queue.
    pub filters: ReviewQueueFilters,
    /// Paging metadata for the filtered result.
    pub pagination: Pagination,
    /// Stable review targets on the requested page.
    pub items: Vec<ReviewQueueItem>,
}

/// Error envelope returned by the reviewer HTTP API.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ApiErrorBody {
    /// Safe human-readable error message.
    pub error: String,
}

/// Human-readable validation failure produced before storage is accessed.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidationError(pub String);

impl std::fmt::Display for ValidationError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ValidationError {}

/// Validate a username against the database's stable lowercase grammar.
pub fn validate_username(username: &str) -> Result<(), ValidationError> {
    let bytes = username.as_bytes();
    if !(USERNAME_MIN_LENGTH..=USERNAME_MAX_LENGTH).contains(&bytes.len())
        || !bytes[0].is_ascii_lowercase()
        || !bytes.iter().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'.' | b'_' | b'-')
        })
    {
        return Err(ValidationError(format!(
            "username must be {USERNAME_MIN_LENGTH}-{USERNAME_MAX_LENGTH} lowercase letters, digits, dots, underscores or hyphens, and start with a letter"
        )));
    }
    Ok(())
}

/// Validate a bounded text field and reject whitespace-only required values.
fn validate_text(
    label: &str,
    value: &str,
    minimum: usize,
    maximum: usize,
) -> Result<(), ValidationError> {
    let length = value.chars().count();
    if length < minimum || length > maximum || (minimum > 0 && value.trim().is_empty()) {
        return Err(ValidationError(format!(
            "{label} must contain between {minimum} and {maximum} characters"
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        validate_username, CreateAccountRequest, ReviewKind, ReviewQueueQuery, ReviewerRole,
        RotateReviewerPasswordRequest, UpdateReviewerProfileRequest, DEFAULT_PAGE_SIZE, FIRST_PAGE,
        MAXIMUM_PAGE_SIZE,
    };

    const FILTER_TEST_PAGE: u32 = 3;
    const FILTER_TEST_PAGE_SIZE: u16 = 20;
    const FILTER_TEST_OFFSET: i64 = 40;

    /// Return one valid account request that tests can mutate in isolation.
    fn request() -> CreateAccountRequest {
        CreateAccountRequest {
            username: "maya.chen".into(),
            full_name: "Maya Chen".into(),
            qualifications: "MD".into(),
            bio_markdown: String::new(),
            role: ReviewerRole::Administrator,
            password: "a long passphrase".into(),
        }
    }

    /// Confirm the complete representative account request passes validation.
    #[test]
    fn valid_account_input_is_accepted() {
        request().validate().expect("valid account input");
    }

    /// Pin the username grammar shared with the database constraint.
    #[test]
    fn username_validation_matches_the_database_constraint() {
        for bad in ["MC", "Maya.Chen", "-maya", "maya chen", "a@b"] {
            assert!(validate_username(bad).is_err(), "{bad} should be rejected");
        }
        assert!(validate_username("maya_chen-2").is_ok());
    }

    /// Reject passwords below the named domain minimum before hashing begins.
    #[test]
    fn short_password_is_rejected_before_hashing() {
        let mut input = request();
        input.password = "too short".into();
        assert!(input.validate().is_err());
    }

    /// Apply profile bounds to corrections and reject an invalid concurrency token.
    #[test]
    fn profile_correction_requires_complete_content_and_a_revision() {
        let valid = UpdateReviewerProfileRequest {
            full_name: "Maya Chen".into(),
            qualifications: "MD".into(),
            bio_markdown: String::new(),
            role: ReviewerRole::Reviewer,
            active: true,
            expected_profile_revision_id: 1,
        };
        valid.validate().expect("valid profile correction");

        let invalid = UpdateReviewerProfileRequest {
            expected_profile_revision_id: 0,
            ..valid
        };
        assert!(invalid.validate().is_err());
    }

    /// Keep administrative password rotation on the initial password bounds.
    #[test]
    fn password_rotation_uses_shared_password_bounds() {
        assert!(RotateReviewerPasswordRequest {
            password: "a new long passphrase".into()
        }
        .validate()
        .is_ok());
        assert!(RotateReviewerPasswordRequest {
            password: "short".into()
        }
        .validate()
        .is_err());
    }

    /// Pin one-based paging defaults and their zero SQL offset.
    #[test]
    fn queue_query_defaults_are_bounded() {
        let query = ReviewQueueQuery::default();
        let validated = query.validate().expect("default queue query");

        assert_eq!(validated.page, FIRST_PAGE);
        assert_eq!(validated.page_size, DEFAULT_PAGE_SIZE);
        assert_eq!(validated.offset(), 0);
    }

    /// Reject page sizes above the named bound and whitespace-only filters.
    #[test]
    fn queue_query_rejects_unbounded_pages_and_blank_filters() {
        let too_large = ReviewQueueQuery {
            page_size: Some(MAXIMUM_PAGE_SIZE + 1),
            ..Default::default()
        };
        assert!(too_large.validate().is_err());

        let blank_source = ReviewQueueQuery {
            source: Some("  ".into()),
            ..Default::default()
        };
        assert!(blank_source.validate().is_err());
    }

    /// Trim filter boundaries without treating SQL wildcard characters specially.
    #[test]
    fn queue_query_trims_filters_without_changing_literal_search() {
        let query = ReviewQueueQuery {
            page: Some(FILTER_TEST_PAGE),
            page_size: Some(FILTER_TEST_PAGE_SIZE),
            kind: Some(ReviewKind::InteractionRule),
            source: Some("  MED-RT ".into()),
            relationship: Some(" CI_PE ".into()),
            search: Some("  50% dextrose_  ".into()),
        };
        let validated = query.validate().expect("valid queue query");

        assert_eq!(validated.offset(), FILTER_TEST_OFFSET);
        assert_eq!(validated.source.as_deref(), Some("MED-RT"));
        assert_eq!(validated.relationship.as_deref(), Some("CI_PE"));
        assert_eq!(validated.search.as_deref(), Some("50% dextrose_"));
    }
}
