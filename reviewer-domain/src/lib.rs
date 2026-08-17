use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReviewerRole {
    Reviewer,
    Administrator,
}

impl ReviewerRole {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Reviewer => "reviewer",
            Self::Administrator => "administrator",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CreateAccountRequest {
    pub username: String,
    pub full_name: String,
    pub qualifications: String,
    pub bio_markdown: String,
    pub role: ReviewerRole,
    pub password: String,
}

impl CreateAccountRequest {
    pub fn validate(&self) -> Result<(), ValidationError> {
        validate_username(&self.username)?;
        validate_text("full name", &self.full_name, 1, 200)?;
        validate_text("qualifications", &self.qualifications, 0, 500)?;
        validate_text("biography", &self.bio_markdown, 0, 10_000)?;
        validate_text("password", &self.password, 12, 256)?;
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LoginRequest {
    pub username: String,
    pub password: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewerAccount {
    pub reviewer_uuid: Uuid,
    pub username: String,
    pub full_name: String,
    pub qualifications: String,
    pub bio_markdown: String,
    pub role: ReviewerRole,
    pub active: bool,
    pub created_at: String,
    pub key_count: i64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BootstrapStatus {
    pub bootstrap_required: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionGrant {
    pub token: String,
    pub reviewer: ReviewerAccount,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReviewKind {
    InteractionRule,
    ConditionContradiction,
}

impl ReviewKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::InteractionRule => "interaction_rule",
            Self::ConditionContradiction => "condition_contradiction",
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewQueueQuery {
    pub page: Option<u32>,
    pub page_size: Option<u16>,
    pub kind: Option<ReviewKind>,
    pub source: Option<String>,
    pub relationship: Option<String>,
    pub search: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidatedReviewQueueQuery {
    pub page: u32,
    pub page_size: u16,
    pub kind: Option<ReviewKind>,
    pub source: Option<String>,
    pub relationship: Option<String>,
    pub search: Option<String>,
}

impl ReviewQueueQuery {
    pub fn validate(self) -> Result<ValidatedReviewQueueQuery, ValidationError> {
        let page = self.page.unwrap_or(1);
        if page == 0 || page > 1_000_000 {
            return Err(ValidationError("page must be between 1 and 1000000".into()));
        }
        let page_size = self.page_size.unwrap_or(25);
        if !(1..=100).contains(&page_size) {
            return Err(ValidationError("pageSize must be between 1 and 100".into()));
        }

        Ok(ValidatedReviewQueueQuery {
            page,
            page_size,
            kind: self.kind,
            source: trimmed_optional("source", self.source, 100)?,
            relationship: trimmed_optional("relationship", self.relationship, 100)?,
            search: trimmed_optional("search", self.search, 200)?,
        })
    }
}

impl ValidatedReviewQueueQuery {
    pub fn offset(&self) -> i64 {
        i64::from(self.page - 1) * i64::from(self.page_size)
    }
}

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
            "{label} must contain between 1 and {maximum} characters"
        )));
    }
    Ok(Some(trimmed.to_string()))
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewQueueSummary {
    pub interaction_rules: i64,
    pub condition_contradictions: i64,
    pub reviewed_pairs: i64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewQueueFilters {
    pub kinds: Vec<ReviewKind>,
    pub sources: Vec<String>,
    pub relationships: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewQueueItem {
    pub id: String,
    pub target_key: String,
    pub kind: ReviewKind,
    pub subject_uuid: Uuid,
    pub object_uuid: Uuid,
    pub subject_name: String,
    pub object_name: String,
    pub relationships: Vec<String>,
    pub candidate_sources: Vec<String>,
    pub upstream_releases: Vec<String>,
    pub impact_count: i64,
    pub question: String,
    pub provenance: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Pagination {
    pub page: u32,
    pub page_size: u16,
    pub total_items: i64,
    pub total_pages: u32,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewQueuePage {
    pub generated_at: String,
    pub summary: ReviewQueueSummary,
    pub filters: ReviewQueueFilters,
    pub pagination: Pagination,
    pub items: Vec<ReviewQueueItem>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ApiErrorBody {
    pub error: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidationError(pub String);

impl std::fmt::Display for ValidationError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ValidationError {}

pub fn validate_username(username: &str) -> Result<(), ValidationError> {
    let bytes = username.as_bytes();
    if !(3..=64).contains(&bytes.len())
        || !bytes[0].is_ascii_lowercase()
        || !bytes.iter().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'.' | b'_' | b'-')
        })
    {
        return Err(ValidationError(
            "username must be 3-64 lowercase letters, digits, dots, underscores or hyphens, and start with a letter".into(),
        ));
    }
    Ok(())
}

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
    };

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

    #[test]
    fn valid_account_input_is_accepted() {
        request().validate().expect("valid account input");
    }

    #[test]
    fn username_validation_matches_the_database_constraint() {
        for bad in ["MC", "Maya.Chen", "-maya", "maya chen", "a@b"] {
            assert!(validate_username(bad).is_err(), "{bad} should be rejected");
        }
        assert!(validate_username("maya_chen-2").is_ok());
    }

    #[test]
    fn short_password_is_rejected_before_hashing() {
        let mut input = request();
        input.password = "too short".into();
        assert!(input.validate().is_err());
    }

    #[test]
    fn queue_query_defaults_are_bounded() {
        let query = ReviewQueueQuery::default();
        let validated = query.validate().expect("default queue query");

        assert_eq!(validated.page, 1);
        assert_eq!(validated.page_size, 25);
        assert_eq!(validated.offset(), 0);
    }

    #[test]
    fn queue_query_rejects_unbounded_pages_and_blank_filters() {
        let too_large = ReviewQueueQuery {
            page_size: Some(101),
            ..Default::default()
        };
        assert!(too_large.validate().is_err());

        let blank_source = ReviewQueueQuery {
            source: Some("  ".into()),
            ..Default::default()
        };
        assert!(blank_source.validate().is_err());
    }

    #[test]
    fn queue_query_trims_filters_without_changing_literal_search() {
        let query = ReviewQueueQuery {
            page: Some(3),
            page_size: Some(20),
            kind: Some(ReviewKind::InteractionRule),
            source: Some("  MED-RT ".into()),
            relationship: Some(" CI_PE ".into()),
            search: Some("  50% dextrose_  ".into()),
        };
        let validated = query.validate().expect("valid queue query");

        assert_eq!(validated.offset(), 40);
        assert_eq!(validated.source.as_deref(), Some("MED-RT"));
        assert_eq!(validated.relationship.as_deref(), Some("CI_PE"));
        assert_eq!(validated.search.as_deref(), Some("50% dextrose_"));
    }
}
