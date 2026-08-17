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
    use super::{validate_username, CreateAccountRequest, ReviewerRole};

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
}
