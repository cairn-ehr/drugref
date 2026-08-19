//! Reviewer account, profile, credential, and session wire contracts.

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{
    validate_text, validate_username, ValidationError, BIOGRAPHY_MAX_LENGTH, FULL_NAME_MAX_LENGTH,
    OPTIONAL_TEXT_MIN_LENGTH, PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, QUALIFICATIONS_MAX_LENGTH,
    REQUIRED_TEXT_MIN_LENGTH,
};

/// Access-control roles understood by the reviewer service.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReviewerRole {
    /// May access the review workspace but not account administration.
    Reviewer,
    /// May access the review workspace and account administration.
    Administrator,
}

impl ReviewerRole {
    /// Return the stable database and wire representation of the role.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Reviewer => "reviewer",
            Self::Administrator => "administrator",
        }
    }
}

/// Fields required to create an initial or subsequent reviewer account.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CreateAccountRequest {
    /// Stable lowercase sign-in name.
    pub username: String,
    /// Reviewer's human-readable name.
    pub full_name: String,
    /// Professional qualifications displayed in the workspace.
    pub qualifications: String,
    /// Markdown biography source stored in the append-only profile.
    pub bio_markdown: String,
    /// Access-control role assigned to the account.
    pub role: ReviewerRole,
    /// Raw password accepted only for immediate hashing.
    pub password: String,
}

impl CreateAccountRequest {
    /// Validate all account fields before hashing or opening a transaction.
    pub fn validate(&self) -> Result<(), ValidationError> {
        validate_username(&self.username)?;
        validate_text(
            "full name",
            &self.full_name,
            REQUIRED_TEXT_MIN_LENGTH,
            FULL_NAME_MAX_LENGTH,
        )?;
        validate_text(
            "qualifications",
            &self.qualifications,
            OPTIONAL_TEXT_MIN_LENGTH,
            QUALIFICATIONS_MAX_LENGTH,
        )?;
        validate_text(
            "biography",
            &self.bio_markdown,
            OPTIONAL_TEXT_MIN_LENGTH,
            BIOGRAPHY_MAX_LENGTH,
        )?;
        validate_text(
            "password",
            &self.password,
            PASSWORD_MIN_LENGTH,
            PASSWORD_MAX_LENGTH,
        )?;
        Ok(())
    }
}

/// Complete replacement for one reviewer's current append-only profile.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateReviewerProfileRequest {
    /// Reviewer's corrected human-readable name.
    pub full_name: String,
    /// Corrected professional qualifications displayed in the workspace.
    pub qualifications: String,
    /// Corrected Markdown biography source.
    pub bio_markdown: String,
    /// Corrected access-control role.
    pub role: ReviewerRole,
    /// Whether the replacement profile permits authentication.
    pub active: bool,
    /// Current revision observed when the administrator opened the form.
    pub expected_profile_revision_id: i64,
}

impl UpdateReviewerProfileRequest {
    /// Validate profile content and the optimistic-concurrency token.
    pub fn validate(&self) -> Result<(), ValidationError> {
        validate_text(
            "full name",
            &self.full_name,
            REQUIRED_TEXT_MIN_LENGTH,
            FULL_NAME_MAX_LENGTH,
        )?;
        validate_text(
            "qualifications",
            &self.qualifications,
            OPTIONAL_TEXT_MIN_LENGTH,
            QUALIFICATIONS_MAX_LENGTH,
        )?;
        validate_text(
            "biography",
            &self.bio_markdown,
            OPTIONAL_TEXT_MIN_LENGTH,
            BIOGRAPHY_MAX_LENGTH,
        )?;
        if self.expected_profile_revision_id < 1 {
            return Err(ValidationError(
                "expected profile revision id must be positive".into(),
            ));
        }
        Ok(())
    }
}

/// New password supplied by an administrator for immediate Argon2id hashing.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RotateReviewerPasswordRequest {
    /// Raw replacement password accepted only for immediate hashing.
    pub password: String,
}

impl RotateReviewerPasswordRequest {
    /// Enforce the same password bounds as initial account creation.
    pub fn validate(&self) -> Result<(), ValidationError> {
        validate_text(
            "password",
            &self.password,
            PASSWORD_MIN_LENGTH,
            PASSWORD_MAX_LENGTH,
        )
    }
}

/// Credentials submitted to create an authenticated reviewer session.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LoginRequest {
    /// Stable lowercase sign-in name.
    pub username: String,
    /// Raw password accepted only for verification.
    pub password: String,
}

/// Current reviewer account projection returned to authenticated clients.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewerAccount {
    /// Stable reviewer identity.
    pub reviewer_uuid: Uuid,
    /// Stable lowercase sign-in name.
    pub username: String,
    /// Current human-readable name.
    pub full_name: String,
    /// Current professional qualifications.
    pub qualifications: String,
    /// Current Markdown biography source.
    pub bio_markdown: String,
    /// Current access-control role.
    pub role: ReviewerRole,
    /// Whether the current profile permits sign-in.
    pub active: bool,
    /// Stable identifier of the current append-only profile revision.
    pub profile_revision_id: i64,
    /// RFC 3339 account creation timestamp.
    pub created_at: String,
    /// Number of live signing-key enrolments.
    pub key_count: i64,
    /// Number of unexpired, unrevoked authenticated sessions.
    pub live_session_count: i64,
}

/// Database-derived result of one administrator account mutation.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AccountAdministrationResult {
    /// Current account projection after the mutation commits.
    pub reviewer: ReviewerAccount,
    /// Sessions revoked by this mutation; zero for non-disabling profile corrections.
    pub revoked_session_count: u64,
}

/// First-run state returned before authentication.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BootstrapStatus {
    /// Whether the database still needs its first administrator.
    pub bootstrap_required: bool,
}

/// Authenticated session material returned only to the trusted native core.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionGrant {
    /// Raw bearer token retained in native process memory.
    pub token: String,
    /// Current reviewer projection associated with the session.
    pub reviewer: ReviewerAccount,
}
