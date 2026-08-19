//! Shared administrator contracts for public signing-key trust decisions.

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{signing::validate_lower_hex, ValidationError};

const SHA256_HEX_LENGTH: usize = 64;

/// Administrative signing-key dispositions exposed by the reviewer GUI.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AdministrativeSigningKeyStatus {
    /// End future use while preserving signatures made before the status boundary.
    Retired,
    /// Object to every signature because the private key may no longer be controlled.
    Compromised,
}

impl AdministrativeSigningKeyStatus {
    /// Return the existing database vocabulary spelling for this disposition.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Retired => "retired",
            Self::Compromised => "compromised",
        }
    }
}

/// Administrator request to append one signing-key status correction.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AdministerSigningKeyRequest {
    /// Narrow retirement or compromise action selected after confirmation.
    pub status: AdministrativeSigningKeyStatus,
}

/// One current public registry key with reviewer ownership and review impact.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SigningKeyTrustSummary {
    /// SHA-256 fingerprint of the raw Ed25519 public key.
    pub key_fingerprint: String,
    /// Registry algorithm name.
    pub algorithm: String,
    /// Human-readable holder recorded with the key.
    pub holder: String,
    /// Current database-owned status.
    pub status: String,
    /// RFC 3339 instant at which the current status began.
    pub status_from: String,
    /// RFC 3339 registry timestamp for the current correction.
    pub registered_at: String,
    /// Stable reviewer identity owning the enrolment, when one exists.
    pub reviewer_uuid: Option<Uuid>,
    /// Stable reviewer username, when one exists.
    pub username: Option<String>,
    /// Current reviewer display name, when one exists.
    pub reviewer_full_name: Option<String>,
    /// Whether the current reviewer enrolment still permits this key.
    pub enrolled: bool,
    /// Every detached signature recorded with this fingerprint.
    pub signature_count: i64,
    /// Current curated revisions carrying a signature from this fingerprint.
    pub current_revision_count: i64,
    /// Current revisions with no registry-unobjected signature remaining.
    pub affected_current_revision_count: i64,
}

/// Complete current public signing-key registry projection for administrators.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SigningKeyTrustStatus {
    /// Current registry keys ordered by holder and fingerprint.
    pub keys: Vec<SigningKeyTrustSummary>,
}

/// Result of one append-only administrative trust correction.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SigningKeyAdministrationResult {
    /// Fresh database projection after the correction commits.
    pub key: SigningKeyTrustSummary,
    /// Whether a live reviewer enrolment was withdrawn in the transaction.
    pub withdrawn_enrolment: bool,
    /// Current revisions now awaiting an unobjected counter-signature.
    pub revisions_awaiting_counter_signature: i64,
}

/// Require one canonical signing-key fingerprint for URL path lookup.
pub fn validate_signing_key_fingerprint(value: &str) -> Result<(), ValidationError> {
    validate_lower_hex("keyFingerprint", value, SHA256_HEX_LENGTH)
}

#[cfg(test)]
mod tests {
    use super::AdministrativeSigningKeyStatus;

    /// Keep administrative trust actions narrower than the registry's full vocabulary.
    #[test]
    fn administrative_key_status_accepts_only_retirement_or_compromise() {
        assert_eq!(
            serde_json::from_str::<AdministrativeSigningKeyStatus>("\"retired\"")
                .expect("retired action")
                .as_str(),
            "retired"
        );
        assert_eq!(
            serde_json::from_str::<AdministrativeSigningKeyStatus>("\"compromised\"")
                .expect("compromised action")
                .as_str(),
            "compromised"
        );
        assert!(serde_json::from_str::<AdministrativeSigningKeyStatus>("\"active\"").is_err());
        assert!(serde_json::from_str::<AdministrativeSigningKeyStatus>("\"rotated\"").is_err());
    }
}
