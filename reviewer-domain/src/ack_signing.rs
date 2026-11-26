//! Detached signatures over departure acknowledgements (db/088, #284).
//!
//! An acknowledgement silences a CHANGED SINCE REVIEW banner, so it is signed like a
//! curated ruling. One GUI acknowledgement can close many episodes, and each episode
//! is its own ack row with its own signature, so the GUI signs a BATCH: the service
//! prepares one challenge per ack under one signing instant, the native core shows
//! every payload and signs them all after one vault unlock, and the service verifies
//! and records them in one transaction.

use serde::{Deserialize, Serialize};

use crate::{
    signing::{validate_lower_hex, ED25519_SIGNATURE_HEX_LENGTH, SHA256_HEX_LENGTH},
    CanonicalField, ReviewSignatureChallenge, RuleGrain, SignatureStatus, ValidationError,
};

/// Database signature target kind of an acknowledgement.
pub const DEPARTURE_ACK_TARGET_KIND: &str = "graded_pair_departure_ack";
/// Payload context an acknowledgement is signed under today.
pub const DEPARTURE_ACK_V1_CONTEXT: &str = "graded_pair_departure_ack/v1";

/// Frozen field order for a version-one acknowledgement signature: the columns of
/// db/088's `graded_pair_departure_ack_payload` view, as Python's
/// `signing_contexts.GRADED_PAIR_DEPARTURE_ACK_V1` names them (the committed vectors
/// pin both).
pub const GRADED_PAIR_DEPARTURE_ACK_V1_FIELDS: [&str; 13] = [
    "rule_grain",
    "subject_key",
    "object_class_uuid",
    "relationship",
    "subject_moiety",
    "partner_moiety",
    // Which episode of the (key, pair): stops a signature being copied onto a
    // later episode of the same pair (db/088).
    "episode_ordinal",
    "rationale",
    "reviewed_by",
    "reviewed_against",
    "acknowledged_at",
    "signer_key_fingerprint",
    "signed_at",
];

/// Most acknowledgements one batch may sign. A drug leaving a large class can close
/// a few hundred pairs; the GUI then signs in several batches. Bounds the work one
/// request can ask of the service.
pub const MAXIMUM_ACK_SIGNATURE_BATCH: usize = 100;

/// One acknowledgement with no signature the registry trusts, named for display.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UnsignedDepartureAck {
    /// Acknowledgement row id, the signature's target id.
    pub ack_id: i64,
    /// Grain of the ruling whose departure was acknowledged.
    pub rule_grain: RuleGrain,
    /// Ruling subject: a drug (moiety grain) or a class (class grain).
    pub subject_name: String,
    /// The ruling's object class.
    pub object_class_name: String,
    /// The ruling's relationship (axis).
    pub relationship: String,
    /// Display name of the pair's subject drug.
    pub subject_moiety_name: String,
    /// Display name of the pair's partner drug.
    pub partner_moiety_name: String,
    /// Curator's stated reason the departure is correct.
    pub rationale: String,
    /// Reviewer-name snapshot recorded with the acknowledgement.
    pub reviewed_by: String,
    /// Release the acknowledgement was reviewed against.
    pub reviewed_against: String,
    /// RFC 3339 recording timestamp.
    pub acknowledged_at: String,
    /// `unsigned`, or a registry status naming only objected signatures.
    pub signature_status: SignatureStatus,
}

/// Selector for preparing signatures over a batch of acknowledgements.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AckSignatureQuery {
    /// Distinct acknowledgement ids, in the order the challenges come back.
    pub ack_ids: Vec<i64>,
    /// Enrolled device-key fingerprint that will sign every payload.
    pub key_fingerprint: String,
}

impl AckSignatureQuery {
    /// Require one to the maximum distinct positive ids and a canonical fingerprint.
    pub fn validate(self) -> Result<Self, ValidationError> {
        if self.ack_ids.is_empty() || self.ack_ids.len() > MAXIMUM_ACK_SIGNATURE_BATCH {
            return Err(ValidationError(format!(
                "ackIds must name between 1 and {MAXIMUM_ACK_SIGNATURE_BATCH} acknowledgements"
            )));
        }
        if self.ack_ids.iter().any(|id| *id <= 0) {
            return Err(ValidationError("ackIds must be positive".into()));
        }
        let mut distinct = self.ack_ids.clone();
        distinct.sort_unstable();
        distinct.dedup();
        if distinct.len() != self.ack_ids.len() {
            return Err(ValidationError("ackIds must not repeat".into()));
        }
        validate_lower_hex("keyFingerprint", &self.key_fingerprint, SHA256_HEX_LENGTH)?;
        Ok(self)
    }
}

/// Server-prepared challenges, one per queried ack in query order, all bound to one
/// signing instant.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AckSignatureChallenges {
    /// Server-issued signing instant inside every payload.
    pub signed_at: String,
    /// One challenge per acknowledgement.
    pub challenges: Vec<ReviewSignatureChallenge>,
}

/// One acknowledgement's payload, shown before the native core uses the private key.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AckSignaturePreviewItem {
    /// Acknowledgement that will be signed.
    pub ack_id: i64,
    /// SHA-256 digest of the exact canonical payload.
    pub payload_digest: String,
    /// Every canonically rendered field in the exact order covered by the digest.
    pub fields: Vec<CanonicalField>,
}

/// Everything a batch will sign, for human confirmation.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AckSignaturePreview {
    /// Domain-separated payload context of every item.
    pub payload_context: String,
    /// Enrolled key fingerprint bound inside every payload.
    pub key_fingerprint: String,
    /// Server-issued signing instant bound inside every payload.
    pub signed_at: String,
    /// One entry per acknowledgement, in query order.
    pub items: Vec<AckSignaturePreviewItem>,
}

/// One detached signature over one prepared acknowledgement payload.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AckSignature {
    /// Acknowledgement signed.
    pub ack_id: i64,
    /// Digest confirmed by the reviewer, checked against re-derived bytes.
    pub payload_digest: String,
    /// Raw 64-byte Ed25519 signature encoded as lowercase hexadecimal.
    pub signature_hex: String,
}

/// A batch of detached signatures submitted after native confirmation.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SubmitAckSignaturesRequest {
    /// The batch originally challenged.
    pub query: AckSignatureQuery,
    /// Server-issued signing instant included in every payload.
    pub signed_at: String,
    /// One signature per queried ack, in query order.
    pub signatures: Vec<AckSignature>,
}

impl SubmitAckSignaturesRequest {
    /// Validate every client-supplied shape before database lookup or cryptography.
    pub fn validate(mut self) -> Result<Self, ValidationError> {
        self.query = self.query.validate()?;
        if self.signed_at.trim().is_empty() {
            return Err(ValidationError("signedAt is required".into()));
        }
        let signed: Vec<i64> = self.signatures.iter().map(|s| s.ack_id).collect();
        if signed != self.query.ack_ids {
            return Err(ValidationError(
                "signatures must cover exactly the prepared acknowledgements, in order".into(),
            ));
        }
        for signature in &self.signatures {
            validate_lower_hex(
                "payloadDigest",
                &signature.payload_digest,
                SHA256_HEX_LENGTH,
            )?;
            validate_lower_hex(
                "signatureHex",
                &signature.signature_hex,
                ED25519_SIGNATURE_HEX_LENGTH,
            )?;
        }
        Ok(self)
    }
}

/// How many signatures a submitted batch recorded.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AckSignatureResult {
    /// Detached signatures written, one per acknowledgement.
    pub recorded: usize,
}

#[cfg(test)]
mod tests {
    use super::{
        AckSignature, AckSignatureQuery, SubmitAckSignaturesRequest,
        GRADED_PAIR_DEPARTURE_ACK_V1_FIELDS, MAXIMUM_ACK_SIGNATURE_BATCH,
    };
    use crate::{CanonicalField, ReviewSignatureChallenge};

    fn query(ack_ids: Vec<i64>) -> AckSignatureQuery {
        AckSignatureQuery {
            ack_ids,
            key_fingerprint: "a".repeat(64),
        }
    }

    fn signature(ack_id: i64) -> AckSignature {
        AckSignature {
            ack_id,
            payload_digest: "b".repeat(64),
            signature_hex: "c".repeat(128),
        }
    }

    /// A batch is one to the maximum distinct positive ack ids.
    #[test]
    fn ack_query_accepts_only_a_bounded_set_of_distinct_positive_ids() {
        assert!(query(vec![3, 1, 2]).validate().is_ok());
        assert!(query(vec![]).validate().is_err());
        assert!(query(vec![1, 0]).validate().is_err());
        assert!(query(vec![1, 1]).validate().is_err());
        let full = (1..=MAXIMUM_ACK_SIGNATURE_BATCH as i64).collect::<Vec<_>>();
        assert!(query(full.clone()).validate().is_ok());
        let over = (1..=MAXIMUM_ACK_SIGNATURE_BATCH as i64 + 1).collect::<Vec<_>>();
        assert!(query(over).validate().is_err());
        let mut bad_key = query(vec![1]);
        bad_key.key_fingerprint = "A".repeat(64);
        assert!(bad_key.validate().is_err());
    }

    /// Each prepared ack gets exactly one signature, in the prepared order.
    #[test]
    fn submission_must_sign_exactly_the_prepared_acks() {
        let request = |signatures| SubmitAckSignaturesRequest {
            query: query(vec![1, 2]),
            signed_at: "2026-11-26T00:00:00.000000Z".into(),
            signatures,
        };
        assert!(request(vec![signature(1), signature(2)]).validate().is_ok());
        assert!(request(vec![signature(1)]).validate().is_err());
        assert!(request(vec![signature(2), signature(1)])
            .validate()
            .is_err());
        let mut short = signature(2);
        short.signature_hex = "c".repeat(127);
        assert!(request(vec![signature(1), short]).validate().is_err());
    }

    /// The shared challenge type encodes an ack only under its frozen field list.
    #[test]
    fn an_ack_challenge_encodes_under_its_frozen_field_list() {
        let challenge = |names: &[&str]| ReviewSignatureChallenge {
            target_kind: "graded_pair_departure_ack".into(),
            target_id: 7,
            payload_context: "graded_pair_departure_ack/v1".into(),
            fields: names
                .iter()
                .map(|name| CanonicalField {
                    name: (*name).into(),
                    value: Some("x".into()),
                })
                .collect(),
            payload_digest: String::new(),
            signed_at: "2026-11-26T00:00:00.000000Z".into(),
        };
        assert!(challenge(&GRADED_PAIR_DEPARTURE_ACK_V1_FIELDS)
            .canonical_payload()
            .is_ok());
        let mut swapped = GRADED_PAIR_DEPARTURE_ACK_V1_FIELDS;
        swapped.swap(0, 1);
        assert!(challenge(&swapped).canonical_payload().is_err());
    }
}
