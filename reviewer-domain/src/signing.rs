//! Shared contracts and canonical encoding for detached reviewer signatures.
//!
//! The service owns row lookup and verification while the native desktop core owns
//! the private key. Both sides use this frozen encoder, which is pinned against the
//! repository's published Python signing format by unit vectors.

use serde::{Deserialize, Serialize};

use crate::{records::validated_target_key, ReviewKind, ValidationError};

const ED25519_PUBLIC_KEY_HEX_LENGTH: usize = 64;
const ED25519_SIGNATURE_HEX_LENGTH: usize = 128;
const SHA256_HEX_LENGTH: usize = 64;
const MAXIMUM_SIGNING_PASSPHRASE_LENGTH: usize = 1024;
const MINIMUM_SIGNING_PASSPHRASE_LENGTH: usize = 12;
const PAYLOAD_PROLOGUE: &str = "drugref-sig-v1";

/// Frozen field order for a version-one curated interaction signature.
pub const CURATED_INTERACTION_V1_FIELDS: [&str; 15] = [
    "subject_moiety_uuid",
    "object_class_uuid",
    "relationship",
    "applies",
    "severity",
    "mechanism",
    "management",
    "evidence_grade",
    "question_uuid",
    "source",
    "reviewed_by",
    "reviewed_against",
    "reviewed_at",
    "signer_key_fingerprint",
    "signed_at",
];

/// Frozen field order for a version-one curated condition signature.
pub const CURATED_CONDITION_V1_FIELDS: [&str; 14] = [
    "subject_moiety_uuid",
    "object_condition_uuid",
    "ruling",
    "severity",
    "mechanism",
    "management",
    "evidence_grade",
    "question_uuid",
    "source",
    "reviewed_by",
    "reviewed_against",
    "reviewed_at",
    "signer_key_fingerprint",
    "signed_at",
];

/// Raw public key sent from the native device for authenticated enrolment.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EnrolSigningKeyRequest {
    /// Raw 32-byte Ed25519 public key encoded as lowercase hexadecimal.
    pub public_key_hex: String,
}

/// Authenticated request to retire one enrolled device key before replacement.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReplaceSigningKeyRequest {
    /// SHA-256 fingerprint recorded beside the native vault.
    pub key_fingerprint: String,
}

impl ReplaceSigningKeyRequest {
    /// Require the one canonical fingerprint spelling before ownership lookup.
    pub fn validate(self) -> Result<Self, ValidationError> {
        validate_lower_hex("keyFingerprint", &self.key_fingerprint, SHA256_HEX_LENGTH)?;
        Ok(self)
    }
}

/// Result of retiring an enrolled key while preserving its audit history.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SigningKeyReplacement {
    /// Fingerprint of the key withdrawn from this reviewer and device.
    pub key_fingerprint: String,
    /// Existing detached signatures retained under time-scoped rotation semantics.
    pub preserved_signature_count: i64,
    /// Current registry status retained after local cleanup.
    pub registry_status: String,
}

impl EnrolSigningKeyRequest {
    /// Reject malformed or non-canonical public-key encodings before persistence.
    pub fn validate(self) -> Result<Self, ValidationError> {
        validate_lower_hex(
            "publicKeyHex",
            &self.public_key_hex,
            ED25519_PUBLIC_KEY_HEX_LENGTH,
        )?;
        Ok(self)
    }
}

/// One current signing key enrolled to the authenticated reviewer.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SigningKeySummary {
    /// SHA-256 fingerprint of the raw public key.
    pub key_fingerprint: String,
    /// Registry algorithm name.
    pub algorithm: String,
    /// Human-readable holder recorded with the registry row.
    pub holder: String,
    /// Current database-owned key status.
    pub status: String,
    /// RFC 3339 enrolment timestamp.
    pub enrolled_at: String,
    /// Detached signatures already recorded with this fingerprint.
    pub signature_count: i64,
}

/// Service-side signing keys enrolled to the current authenticated reviewer.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SigningKeyStatus {
    /// Current enrolled registry rows ordered by enrolment time.
    pub keys: Vec<SigningKeySummary>,
}

/// Signing status merged by the native core with device-vault availability.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DeviceSigningStatus {
    /// Whether this reviewer has an encrypted Stronghold snapshot on this device.
    pub local_vault_exists: bool,
    /// Public fingerprint recorded beside the vault after local generation.
    pub local_key_fingerprint: Option<String>,
    /// Current service enrolments for the authenticated reviewer.
    pub keys: Vec<SigningKeySummary>,
}

/// Reason a current curated revision needs a detached signature.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PendingSignatureReason {
    /// No detached signature has been recorded for the current revision.
    Unsigned,
    /// Recorded signatures exist but every one is objected to by the registry.
    NeedsCounterSignature,
}

/// One live curated revision awaiting an unobjected detached signature.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PendingReviewSignature {
    /// Review kind needed to resolve the canonical target.
    pub kind: ReviewKind,
    /// Frozen open-question target key retained after queue resolution.
    pub target_key: String,
    /// Current immutable curated row identifier.
    pub revision_id: i64,
    /// Human-readable subject name.
    pub subject_name: String,
    /// Human-readable class or condition name.
    pub object_name: String,
    /// Stored target-specific clinical decision.
    pub decision: String,
    /// Authenticated reviewer-name snapshot on the curated row.
    pub reviewed_by: String,
    /// RFC 3339 recording timestamp.
    pub reviewed_at: String,
    /// Whether this is first sign-off or counter-signing after registry objection.
    pub pending_reason: PendingSignatureReason,
    /// Existing signature rows currently objected to by the registry.
    pub objected_signature_count: i64,
}

/// Stable selector for preparing one current curated-row signature.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewSignatureQuery {
    /// Review kind that owns the curated row.
    pub kind: ReviewKind,
    /// Frozen canonical open-question target key.
    pub target_key: String,
    /// Current immutable curated revision identifier.
    pub revision_id: i64,
    /// Enrolled device-key fingerprint that will sign the payload.
    pub key_fingerprint: String,
}

impl ReviewSignatureQuery {
    /// Validate target identity, positive revision id, and fingerprint shape.
    pub fn validate(mut self) -> Result<Self, ValidationError> {
        self.target_key = validated_target_key(self.kind, &self.target_key)?;
        if self.revision_id <= 0 {
            return Err(ValidationError("revisionId must be positive".into()));
        }
        validate_lower_hex("keyFingerprint", &self.key_fingerprint, SHA256_HEX_LENGTH)?;
        Ok(self)
    }
}

/// One named, already-rendered value in a canonical signing payload.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CanonicalField {
    /// Frozen field name.
    pub name: String,
    /// Canonically rendered value, or null for SQL NULL.
    pub value: Option<String>,
}

/// Server-prepared row content that the native core independently encodes.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewSignatureChallenge {
    /// Database signature target kind.
    pub target_kind: String,
    /// Immutable curated row identifier.
    pub target_id: i64,
    /// Domain-separated canonical payload context.
    pub payload_context: String,
    /// Frozen fields in canonical order.
    pub fields: Vec<CanonicalField>,
    /// SHA-256 digest of the canonical bytes as lowercase hex.
    pub payload_digest: String,
    /// Server-issued signing instant included inside the payload.
    pub signed_at: String,
}

impl ReviewSignatureChallenge {
    /// Encode the challenge only when context, target kind, and frozen names agree.
    pub fn canonical_payload(&self) -> Result<Vec<u8>, ValidationError> {
        let expected = match (self.target_kind.as_str(), self.payload_context.as_str()) {
            ("curated_interaction", "curated_interaction/v1") => {
                CURATED_INTERACTION_V1_FIELDS.as_slice()
            }
            ("curated_condition", "curated_condition/v1") => CURATED_CONDITION_V1_FIELDS.as_slice(),
            _ => {
                return Err(ValidationError(
                    "signature challenge has an unsupported target context".into(),
                ))
            }
        };
        if self.target_id <= 0
            || self.fields.len() != expected.len()
            || !self
                .fields
                .iter()
                .zip(expected)
                .all(|(field, expected_name)| field.name == *expected_name)
        {
            return Err(ValidationError(
                "signature challenge does not match the frozen field contract".into(),
            ));
        }
        canonical_payload(&self.payload_context, &self.fields)
    }
}

/// Metadata shown before the native core is allowed to use the private key.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewSignaturePreview {
    /// Current curated revision that will be signed.
    pub revision_id: i64,
    /// Domain-separated payload context.
    pub payload_context: String,
    /// SHA-256 digest of the exact canonical payload.
    pub payload_digest: String,
    /// Enrolled key fingerprint bound inside the payload.
    pub key_fingerprint: String,
    /// Server-issued signing instant bound inside the payload.
    pub signed_at: String,
    /// Number of frozen row and attestation fields covered.
    pub field_count: usize,
    /// Every canonically rendered field in the exact order covered by the digest.
    pub fields: Vec<CanonicalField>,
}

/// Detached signature submitted after native confirmation and local signing.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SubmitReviewSignatureRequest {
    /// Stable target and device-key selector originally challenged.
    pub query: ReviewSignatureQuery,
    /// Server-issued signing instant included in the signed bytes.
    pub signed_at: String,
    /// Digest displayed during confirmation and checked against re-derived bytes.
    pub payload_digest: String,
    /// Raw 64-byte Ed25519 signature encoded as lowercase hexadecimal.
    pub signature_hex: String,
}

impl SubmitReviewSignatureRequest {
    /// Validate all client-supplied shapes before database lookup or cryptography.
    pub fn validate(mut self) -> Result<Self, ValidationError> {
        self.query = self.query.validate()?;
        validate_lower_hex("payloadDigest", &self.payload_digest, SHA256_HEX_LENGTH)?;
        validate_lower_hex(
            "signatureHex",
            &self.signature_hex,
            ED25519_SIGNATURE_HEX_LENGTH,
        )?;
        if self.signed_at.trim().is_empty() {
            return Err(ValidationError("signedAt is required".into()));
        }
        Ok(self)
    }
}

/// Validate a local signing-vault passphrase without confusing it with login.
pub fn validate_signing_passphrase(passphrase: &str) -> Result<(), ValidationError> {
    let length = passphrase.chars().count();
    if !(MINIMUM_SIGNING_PASSPHRASE_LENGTH..=MAXIMUM_SIGNING_PASSPHRASE_LENGTH).contains(&length) {
        return Err(ValidationError(format!(
            "signing passphrase must contain between {MINIMUM_SIGNING_PASSPHRASE_LENGTH} and {MAXIMUM_SIGNING_PASSPHRASE_LENGTH} characters"
        )));
    }
    Ok(())
}

/// Generate the repository's version-one length-prefixed canonical payload.
pub fn canonical_payload(
    context: &str,
    fields: &[CanonicalField],
) -> Result<Vec<u8>, ValidationError> {
    if !valid_context(context) {
        return Err(ValidationError("payload context is not canonical".into()));
    }
    let mut output = format!("{PAYLOAD_PROLOGUE}\n{context}\n{}\n", fields.len()).into_bytes();
    for field in fields {
        let name = field.name.as_bytes();
        match &field.value {
            Some(value) => {
                let value = value.as_bytes();
                output.extend_from_slice(
                    format!("{}:{}:S:{}:", name.len(), field.name, value.len()).as_bytes(),
                );
                output.extend_from_slice(value);
                output.push(b'\n');
            }
            None => {
                output.extend_from_slice(format!("{}:{}:N:0:\n", name.len(), field.name).as_bytes())
            }
        }
    }
    Ok(output)
}

/// Return whether a context follows the deliberately narrow `<kind>/v<number>` grammar.
fn valid_context(context: &str) -> bool {
    let Some((kind, version)) = context.split_once("/v") else {
        return false;
    };
    !kind.is_empty()
        && kind
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte == b'_')
        && !version.is_empty()
        && version.bytes().all(|byte| byte.is_ascii_digit())
}

/// Require lowercase fixed-width hexadecimal so identifiers have one wire spelling.
pub(crate) fn validate_lower_hex(
    label: &str,
    value: &str,
    length: usize,
) -> Result<(), ValidationError> {
    if value.len() != length
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(ValidationError(format!(
            "{label} must be {length} lowercase hexadecimal characters"
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        canonical_payload, CanonicalField, ReplaceSigningKeyRequest, ReviewSignatureChallenge,
    };
    use serde_json::Value;

    /// Pin byte lengths, null distinction, embedded separators, and the final newline.
    #[test]
    fn canonical_payload_matches_the_published_format() {
        let payload = canonical_payload(
            "curated_interaction/v1",
            &[
                CanonicalField {
                    name: "mechanism".into(),
                    value: Some("line one:\nline two".into()),
                },
                CanonicalField {
                    name: "management".into(),
                    value: None,
                },
            ],
        )
        .expect("canonical payload");
        assert_eq!(
            String::from_utf8(payload).expect("UTF-8 vector"),
            "drugref-sig-v1\ncurated_interaction/v1\n2\n9:mechanism:S:18:line one:\nline two\n10:management:N:0:\n"
        );
    }

    /// Refuse a challenge whose server-supplied field order has drifted.
    #[test]
    fn challenge_requires_the_complete_frozen_field_list() {
        let challenge = ReviewSignatureChallenge {
            target_kind: "curated_interaction".into(),
            target_id: 1,
            payload_context: "curated_interaction/v1".into(),
            fields: vec![],
            payload_digest: "0".repeat(64),
            signed_at: "2026-08-18T00:00:00.000000Z".into(),
        };
        assert!(challenge.canonical_payload().is_err());
    }

    /// Keep key replacement bound to one canonical registry fingerprint.
    #[test]
    fn replacement_requires_a_lowercase_sha256_fingerprint() {
        assert!(ReplaceSigningKeyRequest {
            key_fingerprint: "a".repeat(64),
        }
        .validate()
        .is_ok());
        assert!(ReplaceSigningKeyRequest {
            key_fingerprint: "A".repeat(64),
        }
        .validate()
        .is_err());
    }

    /// Reproduce every curated-row payload committed by the Python reference encoder.
    #[test]
    fn rust_encoder_matches_published_signing_vectors() {
        let fixture: Value =
            serde_json::from_str(include_str!("../../tests/fixtures/signing_vectors.json"))
                .expect("signing vectors");
        let cases = fixture["cases"].as_array().expect("vector cases");
        for case in cases {
            let context = case["context"].as_str().expect("context");
            if !context.starts_with("curated_") {
                continue;
            }
            let fields = case["fields"]
                .as_array()
                .expect("fields")
                .iter()
                .map(|field| {
                    let pair = field.as_array().expect("field pair");
                    CanonicalField {
                        name: pair[0].as_str().expect("field name").into(),
                        value: pair[1].as_str().map(str::to_string),
                    }
                })
                .collect::<Vec<_>>();
            assert_eq!(
                canonical_payload(context, &fields).expect("canonical payload"),
                case["payload"]
                    .as_str()
                    .expect("fixture payload")
                    .as_bytes(),
                "{}",
                case["name"].as_str().expect("case name")
            );
        }
    }
}
