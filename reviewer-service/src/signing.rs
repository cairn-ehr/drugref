//! Authenticated signing-key enrolment and detached signature persistence.

use chrono::{DateTime, Duration, SecondsFormat, Utc};
use ed25519_dalek::{Signature, VerifyingKey};
use reviewer_domain::{
    CanonicalField, EnrolSigningKeyRequest, PendingReviewSignature, ReviewDecisionRecord,
    ReviewKind, ReviewRecordQuery, ReviewSignatureChallenge, ReviewSignatureQuery,
    SigningKeyReplacement, SigningKeyStatus, SigningKeySummary, SubmitReviewSignatureRequest,
    CURATED_CONDITION_V1_FIELDS, CURATED_INTERACTION_V1_FIELDS,
};
use sha2::{Digest, Sha256};
use sqlx::{FromRow, PgConnection, PgPool};
use uuid::Uuid;

use crate::{decision_targets, decisions, error::AppError};

const ED25519_ALGORITHM: &str = "Ed25519";
const ACTIVE_KEY_STATUS: &str = "active";
const ROTATED_KEY_STATUS: &str = "rotated";
const UNIQUE_VIOLATION_SQLSTATE: &str = "23505";
const MAXIMUM_CHALLENGE_AGE_MINUTES: i64 = 5;
const MAXIMUM_FUTURE_CLOCK_SKEW_SECONDS: i64 = 30;

/// Current key and enrolment projection returned to the authenticated reviewer.
#[derive(FromRow)]
struct SigningKeyRow {
    key_fingerprint: String,
    algorithm: String,
    holder: String,
    status: String,
    enrolled_at: DateTime<Utc>,
    signature_count: i64,
}

/// Current owned key and enrolment locked for one replacement transaction.
#[derive(FromRow)]
struct ReplaceableSigningKeyRow {
    reviewer_key_enrolment_id: i64,
    enrolled_signing_key_id: i64,
    current_signing_key_id: i64,
    public_key: Vec<u8>,
    algorithm: String,
    holder: String,
    signature_count: i64,
}

impl From<SigningKeyRow> for SigningKeySummary {
    /// Convert database time into the wire contract's RFC 3339 spelling.
    fn from(row: SigningKeyRow) -> Self {
        Self {
            key_fingerprint: row.key_fingerprint,
            algorithm: row.algorithm,
            holder: row.holder,
            status: row.status,
            enrolled_at: row.enrolled_at.to_rfc3339(),
            signature_count: row.signature_count,
        }
    }
}

/// Typed interaction row values required by the frozen version-one payload.
#[derive(FromRow)]
struct InteractionPayloadRow {
    subject_moiety_uuid: Uuid,
    object_class_uuid: Uuid,
    relationship: String,
    applies: bool,
    severity: Option<String>,
    mechanism: Option<String>,
    management: Option<String>,
    evidence_grade: Option<String>,
    question_uuid: Option<Uuid>,
    source: String,
    reviewed_by: String,
    reviewed_against: String,
    reviewed_at: DateTime<Utc>,
}

/// Typed condition row values required by the frozen version-one payload.
#[derive(FromRow)]
struct ConditionPayloadRow {
    subject_moiety_uuid: Uuid,
    object_condition_uuid: Uuid,
    ruling: String,
    severity: Option<String>,
    mechanism: Option<String>,
    management: Option<String>,
    evidence_grade: Option<String>,
    question_uuid: Option<Uuid>,
    source: String,
    reviewed_by: String,
    reviewed_against: String,
    reviewed_at: DateTime<Utc>,
}

/// One unsigned live curated revision projected for the resume queue.
#[derive(FromRow)]
struct PendingSignatureRow {
    kind: String,
    target_key: String,
    revision_id: i64,
    subject_name: String,
    object_name: String,
    decision: String,
    reviewed_by: String,
    reviewed_at: DateTime<Utc>,
}

impl TryFrom<PendingSignatureRow> for PendingReviewSignature {
    type Error = AppError;

    /// Parse the database union's closed review-kind vocabulary.
    fn try_from(row: PendingSignatureRow) -> Result<Self, Self::Error> {
        let kind = match row.kind.as_str() {
            "interaction_rule" => ReviewKind::InteractionRule,
            "condition_contradiction" => ReviewKind::ConditionContradiction,
            value => return Err(AppError::internal(format!("unknown pending kind {value}"))),
        };
        Ok(Self {
            kind,
            target_key: row.target_key,
            revision_id: row.revision_id,
            subject_name: row.subject_name,
            object_name: row.object_name,
            decision: row.decision,
            reviewed_by: row.reviewed_by,
            reviewed_at: row.reviewed_at.to_rfc3339(),
        })
    }
}

/// Return every live curated GUI revision with no detached signature yet recorded.
pub async fn pending(pool: &PgPool) -> Result<Vec<PendingReviewSignature>, AppError> {
    sqlx::query_as::<_, PendingSignatureRow>(
        "SELECT 'interaction_rule'::text AS kind, q.gap_key AS target_key, \
                c.curated_interaction_id AS revision_id, m.display_name AS subject_name, \
                k.class_name AS object_name, \
                CASE WHEN c.applies THEN 'applies' ELSE 'does_not_apply' END AS decision, \
                c.reviewed_by, c.reviewed_at \
         FROM drugref.curated_interaction c \
         JOIN drugref.open_question q ON q.question_uuid = c.question_uuid \
         JOIN drugref.substance_moiety m ON m.moiety_uuid = c.subject_moiety_uuid \
         JOIN drugref.substance_class k ON k.class_uuid = c.object_class_uuid \
         WHERE c.superseded_by IS NULL AND NOT EXISTS ( \
             SELECT 1 FROM drugref.assertion_signature s \
             WHERE s.target_kind = 'curated_interaction' \
               AND s.target_id = c.curated_interaction_id) \
         UNION ALL \
         SELECT 'condition_contradiction'::text, q.gap_key, c.curated_condition_id, \
                m.display_name, d.name, c.ruling, c.reviewed_by, c.reviewed_at \
         FROM drugref.curated_condition c \
         JOIN drugref.open_question q ON q.question_uuid = c.question_uuid \
         JOIN drugref.substance_moiety m ON m.moiety_uuid = c.subject_moiety_uuid \
         JOIN drugref.condition d ON d.condition_uuid = c.object_condition_uuid \
         WHERE c.superseded_by IS NULL AND NOT EXISTS ( \
             SELECT 1 FROM drugref.assertion_signature s \
             WHERE s.target_kind = 'curated_condition' \
               AND s.target_id = c.curated_condition_id) \
         ORDER BY reviewed_at DESC, revision_id DESC",
    )
    .fetch_all(pool)
    .await?
    .into_iter()
    .map(PendingReviewSignature::try_from)
    .collect()
}

/// Return every current registry key enrolled to one reviewer.
pub async fn list_keys(pool: &PgPool, reviewer_uuid: Uuid) -> Result<SigningKeyStatus, AppError> {
    let rows = sqlx::query_as::<_, SigningKeyRow>(
        "SELECT k.key_fingerprint, k.algorithm, k.holder, k.status, e.enrolled_at, \
                (SELECT count(*) FROM drugref.assertion_signature s \
                 WHERE s.key_fingerprint = k.key_fingerprint) AS signature_count \
         FROM drugref.reviewer_key_enrolment e \
         JOIN drugref.signing_key enrolled ON enrolled.signing_key_id = e.signing_key_id \
         JOIN drugref.signing_key k ON k.key_fingerprint = enrolled.key_fingerprint \
           AND k.superseded_by IS NULL \
         WHERE e.reviewer_uuid = $1 AND e.superseded_by IS NULL AND e.enrolled \
         ORDER BY e.enrolled_at, k.key_fingerprint",
    )
    .bind(reviewer_uuid)
    .fetch_all(pool)
    .await?;
    Ok(SigningKeyStatus {
        keys: rows.into_iter().map(SigningKeySummary::from).collect(),
    })
}

/// Withdraw one owned enrolment and time-scope the registry key as rotated.
///
/// The append-only history is retained even when the signature count is zero. Repeating
/// the request after the database commit is harmless, which lets the native client retry
/// local file cleanup after a partial filesystem failure.
pub async fn replace_key(
    pool: &PgPool,
    key_fingerprint: &str,
    reviewer_uuid: Uuid,
    actor: &str,
) -> Result<SigningKeyReplacement, AppError> {
    let mut transaction = pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext('drugref reviewer key'), hashtext($1))")
        .bind(key_fingerprint)
        .execute(&mut *transaction)
        .await?;

    let current = sqlx::query_as::<_, ReplaceableSigningKeyRow>(
        "SELECT e.reviewer_key_enrolment_id, \
                e.signing_key_id AS enrolled_signing_key_id, \
                k.signing_key_id AS current_signing_key_id, \
                k.public_key, k.algorithm, k.holder, \
                (SELECT count(*) FROM drugref.assertion_signature s \
                 WHERE s.key_fingerprint = k.key_fingerprint) AS signature_count \
         FROM drugref.reviewer_key_enrolment e \
         JOIN drugref.signing_key enrolled ON enrolled.signing_key_id = e.signing_key_id \
         JOIN drugref.signing_key k ON k.key_fingerprint = enrolled.key_fingerprint \
           AND k.superseded_by IS NULL \
         WHERE e.reviewer_uuid = $1 AND e.superseded_by IS NULL AND e.enrolled \
           AND k.key_fingerprint = $2 AND k.status = $3 \
         FOR UPDATE OF e, k",
    )
    .bind(reviewer_uuid)
    .bind(key_fingerprint)
    .bind(ACTIVE_KEY_STATUS)
    .fetch_optional(&mut *transaction)
    .await?;

    let Some(current) = current else {
        let previous_signature_count: Option<i64> = sqlx::query_scalar(
            "SELECT (SELECT count(*) FROM drugref.assertion_signature s \
                     WHERE s.key_fingerprint = enrolled.key_fingerprint) \
             FROM drugref.reviewer_key_enrolment e \
             JOIN drugref.signing_key enrolled ON enrolled.signing_key_id = e.signing_key_id \
             WHERE e.reviewer_uuid = $1 AND e.superseded_by IS NULL AND NOT e.enrolled \
               AND enrolled.key_fingerprint = $2",
        )
        .bind(reviewer_uuid)
        .bind(key_fingerprint)
        .fetch_optional(&mut *transaction)
        .await?;
        transaction.commit().await?;
        return previous_signature_count
            .map(|preserved_signature_count| SigningKeyReplacement {
                key_fingerprint: key_fingerprint.to_string(),
                preserved_signature_count,
            })
            .ok_or_else(|| AppError::not_found("no matching active signing key is enrolled"));
    };

    let rotated_key_id: i64 = sqlx::query_scalar(
        "INSERT INTO drugref.signing_key \
         (key_fingerprint, public_key, algorithm, holder, status, status_from, registered_by) \
         VALUES ($1, $2, $3, $4, $5, now(), $6) RETURNING signing_key_id",
    )
    .bind(key_fingerprint)
    .bind(current.public_key)
    .bind(current.algorithm)
    .bind(current.holder)
    .bind(ROTATED_KEY_STATUS)
    .bind(actor)
    .fetch_one(&mut *transaction)
    .await?;
    sqlx::query("UPDATE drugref.signing_key SET superseded_by = $1 WHERE signing_key_id = $2")
        .bind(rotated_key_id)
        .bind(current.current_signing_key_id)
        .execute(&mut *transaction)
        .await?;

    let withdrawn_enrolment_id: i64 = sqlx::query_scalar(
        "INSERT INTO drugref.reviewer_key_enrolment \
         (reviewer_uuid, signing_key_id, enrolled, enrolled_by) \
         VALUES ($1, $2, false, $1) RETURNING reviewer_key_enrolment_id",
    )
    .bind(reviewer_uuid)
    .bind(current.enrolled_signing_key_id)
    .fetch_one(&mut *transaction)
    .await?;
    sqlx::query(
        "UPDATE drugref.reviewer_key_enrolment SET superseded_by = $1 \
         WHERE reviewer_key_enrolment_id = $2",
    )
    .bind(withdrawn_enrolment_id)
    .bind(current.reviewer_key_enrolment_id)
    .execute(&mut *transaction)
    .await?;
    transaction.commit().await?;

    Ok(SigningKeyReplacement {
        key_fingerprint: key_fingerprint.to_string(),
        preserved_signature_count: current.signature_count,
    })
}

/// Register a native-generated public key and enrol it to its authenticated owner.
pub async fn enrol_key(
    pool: &PgPool,
    input: &EnrolSigningKeyRequest,
    reviewer_uuid: Uuid,
    holder: &str,
) -> Result<SigningKeySummary, AppError> {
    let public_key = decode_hex(&input.public_key_hex)?;
    let fingerprint = encode_hex(&Sha256::digest(&public_key));
    let mut transaction = pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext('drugref reviewer key'), hashtext($1))")
        .bind(&fingerprint)
        .execute(&mut *transaction)
        .await?;

    let existing: Option<(i64, Vec<u8>, String)> = sqlx::query_as(
        "SELECT signing_key_id, public_key, status FROM drugref.signing_key \
         WHERE key_fingerprint = $1 AND superseded_by IS NULL",
    )
    .bind(&fingerprint)
    .fetch_optional(&mut *transaction)
    .await?;
    let signing_key_id = if let Some((identifier, registered_public_key, status)) = existing {
        if registered_public_key != public_key {
            return Err(AppError::conflict(
                "signing-key fingerprint is already registered with different bytes",
            ));
        }
        if status != ACTIVE_KEY_STATUS {
            return Err(AppError::conflict(
                "the local signing key is no longer active in the registry",
            ));
        }
        identifier
    } else {
        sqlx::query_scalar(
            "INSERT INTO drugref.signing_key \
             (key_fingerprint, public_key, algorithm, holder, status, status_from, registered_by) \
             VALUES ($1, $2, $3, $4, $5, now(), $6) RETURNING signing_key_id",
        )
        .bind(&fingerprint)
        .bind(&public_key)
        .bind(ED25519_ALGORITHM)
        .bind(holder)
        .bind(ACTIVE_KEY_STATUS)
        .bind(holder)
        .fetch_one(&mut *transaction)
        .await?
    };

    let owner: Option<Uuid> = sqlx::query_scalar(
        "SELECT reviewer_uuid FROM drugref.reviewer_key_enrolment \
         WHERE signing_key_id = $1 AND superseded_by IS NULL AND enrolled",
    )
    .bind(signing_key_id)
    .fetch_optional(&mut *transaction)
    .await?;
    match owner {
        Some(owner) if owner != reviewer_uuid => {
            return Err(AppError::conflict(
                "signing key is already enrolled to another reviewer",
            ))
        }
        Some(_) => {}
        None => {
            sqlx::query(
                "INSERT INTO drugref.reviewer_key_enrolment \
                 (reviewer_uuid, signing_key_id, enrolled, enrolled_by) \
                 VALUES ($1, $2, true, $1)",
            )
            .bind(reviewer_uuid)
            .bind(signing_key_id)
            .execute(&mut *transaction)
            .await?;
        }
    }
    transaction.commit().await?;

    list_keys(pool, reviewer_uuid)
        .await?
        .keys
        .into_iter()
        .find(|key| key.key_fingerprint == fingerprint)
        .ok_or_else(|| AppError::internal("new signing-key enrolment was not readable"))
}

/// Prepare exact immutable row content and a server-issued attestation instant.
pub async fn challenge(
    pool: &PgPool,
    query: &ReviewSignatureQuery,
    reviewer_uuid: Uuid,
) -> Result<ReviewSignatureChallenge, AppError> {
    let mut connection = pool.acquire().await?;
    ensure_active_enrolment(&mut connection, reviewer_uuid, &query.key_fingerprint).await?;
    challenge_at(&mut connection, query, format_timestamp(Utc::now())).await
}

/// Verify and persist one detached signature, then return database-derived history.
pub async fn submit(
    pool: &PgPool,
    input: &SubmitReviewSignatureRequest,
    reviewer_uuid: Uuid,
) -> Result<ReviewDecisionRecord, AppError> {
    let parsed = DateTime::parse_from_rfc3339(&input.signed_at)
        .map_err(|_| AppError::bad_request("signedAt must be an RFC 3339 instant"))?
        .with_timezone(&Utc);
    if format_timestamp(parsed) != input.signed_at {
        return Err(AppError::bad_request(
            "signedAt must use canonical UTC microsecond precision",
        ));
    }
    let mut transaction = pool.begin().await?;
    decision_targets::lock_target(&mut transaction, &input.query.target_key).await?;
    let now = Utc::now();
    if parsed < now - Duration::minutes(MAXIMUM_CHALLENGE_AGE_MINUTES)
        || parsed > now + Duration::seconds(MAXIMUM_FUTURE_CLOCK_SKEW_SECONDS)
    {
        return Err(AppError::conflict(
            "the signing challenge expired; prepare the current revision again",
        ));
    }
    let public_key = ensure_active_enrolment(
        &mut transaction,
        reviewer_uuid,
        &input.query.key_fingerprint,
    )
    .await?;
    let challenge = challenge_at(&mut transaction, &input.query, input.signed_at.clone()).await?;
    if challenge.payload_digest != input.payload_digest {
        return Err(AppError::conflict(
            "the signing payload changed after confirmation; prepare it again",
        ));
    }
    let payload = challenge
        .canonical_payload()
        .map_err(|error| AppError::internal(error.0))?;
    let signature_bytes = decode_hex(&input.signature_hex)?;
    let public_key: [u8; 32] = public_key
        .try_into()
        .map_err(|_| AppError::internal("registered Ed25519 public key has invalid length"))?;
    let verifying_key = VerifyingKey::from_bytes(&public_key)
        .map_err(|_| AppError::internal("registered Ed25519 public key is invalid"))?;
    let signature = Signature::from_slice(&signature_bytes)
        .map_err(|_| AppError::bad_request("signatureHex has invalid Ed25519 length"))?;
    verifying_key
        .verify_strict(&payload, &signature)
        .map_err(|_| AppError::bad_request("detached signature does not verify"))?;

    let result = sqlx::query(
        "INSERT INTO drugref.assertion_signature \
         (target_kind, target_id, payload_context, payload_digest, key_fingerprint, \
          algorithm, signature, signed_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
    )
    .bind(&challenge.target_kind)
    .bind(challenge.target_id)
    .bind(&challenge.payload_context)
    .bind(Sha256::digest(&payload).as_slice())
    .bind(&input.query.key_fingerprint)
    .bind(ED25519_ALGORITHM)
    .bind(signature_bytes)
    .bind(parsed)
    .execute(&mut *transaction)
    .await;
    if let Err(error) = result {
        if error
            .as_database_error()
            .and_then(|detail| detail.code())
            .as_deref()
            == Some(UNIQUE_VIOLATION_SQLSTATE)
        {
            return Err(AppError::conflict(
                "this detached signature is already recorded",
            ));
        }
        return Err(error.into());
    }
    transaction.commit().await?;

    decisions::load(
        pool,
        &ReviewRecordQuery {
            kind: input.query.kind,
            target_key: input.query.target_key.clone(),
        },
    )
    .await
}

/// Rebuild a challenge for one exact current row and signing instant.
async fn challenge_at(
    connection: &mut PgConnection,
    query: &ReviewSignatureQuery,
    signed_at: String,
) -> Result<ReviewSignatureChallenge, AppError> {
    let record_query = ReviewRecordQuery {
        kind: query.kind,
        target_key: query.target_key.clone(),
    };
    let target = decision_targets::read_target_connection(connection, &record_query).await?;
    let (target_kind, payload_context, fields) = match (query.kind, target) {
        (
            ReviewKind::InteractionRule,
            decision_targets::ReviewTarget::Interaction {
                subject_uuid,
                object_uuid,
                relationship,
            },
        ) => (
            "curated_interaction",
            "curated_interaction/v1",
            interaction_fields(
                connection,
                query.revision_id,
                subject_uuid,
                object_uuid,
                &relationship,
                &query.key_fingerprint,
                &signed_at,
            )
            .await?,
        ),
        (
            ReviewKind::ConditionContradiction,
            decision_targets::ReviewTarget::Condition {
                subject_uuid,
                object_uuid,
            },
        ) => (
            "curated_condition",
            "curated_condition/v1",
            condition_fields(
                connection,
                query.revision_id,
                subject_uuid,
                object_uuid,
                &query.key_fingerprint,
                &signed_at,
            )
            .await?,
        ),
        _ => return Err(AppError::bad_request("review kind does not match target")),
    };
    let mut challenge = ReviewSignatureChallenge {
        target_kind: target_kind.into(),
        target_id: query.revision_id,
        payload_context: payload_context.into(),
        fields,
        payload_digest: String::new(),
        signed_at,
    };
    let payload = challenge
        .canonical_payload()
        .map_err(|error| AppError::internal(error.0))?;
    challenge.payload_digest = encode_hex(&Sha256::digest(payload));
    Ok(challenge)
}

/// Read and render one current interaction revision in frozen field order.
async fn interaction_fields(
    connection: &mut PgConnection,
    revision_id: i64,
    subject_uuid: Uuid,
    object_uuid: Uuid,
    relationship: &str,
    fingerprint: &str,
    signed_at: &str,
) -> Result<Vec<CanonicalField>, AppError> {
    let row = sqlx::query_as::<_, InteractionPayloadRow>(
        "SELECT subject_moiety_uuid, object_class_uuid, relationship, applies, severity, \
         mechanism, management, evidence_grade, question_uuid, source, reviewed_by, \
         reviewed_against, reviewed_at FROM drugref.curated_interaction \
         WHERE curated_interaction_id = $1 AND subject_moiety_uuid = $2 \
           AND object_class_uuid = $3 AND relationship = $4 AND superseded_by IS NULL",
    )
    .bind(revision_id)
    .bind(subject_uuid)
    .bind(object_uuid)
    .bind(relationship)
    .fetch_optional(connection)
    .await?
    .ok_or_else(|| AppError::conflict("only the current interaction revision may be signed"))?;
    let values = vec![
        Some(row.subject_moiety_uuid.to_string()),
        Some(row.object_class_uuid.to_string()),
        Some(row.relationship),
        Some(if row.applies { "true" } else { "false" }.into()),
        row.severity,
        row.mechanism,
        row.management,
        row.evidence_grade,
        row.question_uuid.map(|value| value.to_string()),
        Some(row.source),
        Some(row.reviewed_by),
        Some(row.reviewed_against),
        Some(format_timestamp(row.reviewed_at)),
        Some(fingerprint.into()),
        Some(signed_at.into()),
    ];
    Ok(named_fields(&CURATED_INTERACTION_V1_FIELDS, values))
}

/// Read and render one current condition revision in frozen field order.
async fn condition_fields(
    connection: &mut PgConnection,
    revision_id: i64,
    subject_uuid: Uuid,
    object_uuid: Uuid,
    fingerprint: &str,
    signed_at: &str,
) -> Result<Vec<CanonicalField>, AppError> {
    let row = sqlx::query_as::<_, ConditionPayloadRow>(
        "SELECT subject_moiety_uuid, object_condition_uuid, ruling, severity, mechanism, \
         management, evidence_grade, question_uuid, source, reviewed_by, reviewed_against, \
         reviewed_at FROM drugref.curated_condition \
         WHERE curated_condition_id = $1 AND subject_moiety_uuid = $2 \
           AND object_condition_uuid = $3 AND superseded_by IS NULL",
    )
    .bind(revision_id)
    .bind(subject_uuid)
    .bind(object_uuid)
    .fetch_optional(connection)
    .await?
    .ok_or_else(|| AppError::conflict("only the current condition revision may be signed"))?;
    let values = vec![
        Some(row.subject_moiety_uuid.to_string()),
        Some(row.object_condition_uuid.to_string()),
        Some(row.ruling),
        row.severity,
        row.mechanism,
        row.management,
        row.evidence_grade,
        row.question_uuid.map(|value| value.to_string()),
        Some(row.source),
        Some(row.reviewed_by),
        Some(row.reviewed_against),
        Some(format_timestamp(row.reviewed_at)),
        Some(fingerprint.into()),
        Some(signed_at.into()),
    ];
    Ok(named_fields(&CURATED_CONDITION_V1_FIELDS, values))
}

/// Pair one frozen list with its equally-sized rendered value vector.
fn named_fields<const N: usize>(
    names: &[&str; N],
    values: Vec<Option<String>>,
) -> Vec<CanonicalField> {
    names
        .iter()
        .zip(values)
        .map(|(name, value)| CanonicalField {
            name: (*name).into(),
            value,
        })
        .collect()
}

/// Resolve an active enrolled key and return its public bytes for verification.
async fn ensure_active_enrolment(
    connection: &mut PgConnection,
    reviewer_uuid: Uuid,
    fingerprint: &str,
) -> Result<Vec<u8>, AppError> {
    sqlx::query_scalar(
        "SELECT k.public_key FROM drugref.reviewer_key_enrolment e \
         JOIN drugref.signing_key enrolled ON enrolled.signing_key_id = e.signing_key_id \
         JOIN drugref.signing_key k ON k.key_fingerprint = enrolled.key_fingerprint \
           AND k.superseded_by IS NULL AND k.status = $3 \
         WHERE e.reviewer_uuid = $1 AND k.key_fingerprint = $2 \
           AND e.superseded_by IS NULL AND e.enrolled",
    )
    .bind(reviewer_uuid)
    .bind(fingerprint)
    .bind(ACTIVE_KEY_STATUS)
    .fetch_optional(connection)
    .await?
    .ok_or_else(|| AppError::forbidden_message("an active enrolled signing key is required"))
}

/// Render a PostgreSQL-compatible timestamp with exactly six UTC fractional digits.
fn format_timestamp(value: DateTime<Utc>) -> String {
    value.to_rfc3339_opts(SecondsFormat::Micros, true)
}

/// Decode validated lowercase hexadecimal without accepting alternate spellings.
fn decode_hex(value: &str) -> Result<Vec<u8>, AppError> {
    if !value.len().is_multiple_of(2) {
        return Err(AppError::bad_request("hexadecimal value has odd length"));
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let high = hex_nibble(pair[0])?;
            let low = hex_nibble(pair[1])?;
            Ok((high << 4) | low)
        })
        .collect()
}

/// Decode one lowercase hexadecimal nibble.
fn hex_nibble(value: u8) -> Result<u8, AppError> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        _ => Err(AppError::bad_request(
            "hexadecimal values must use lowercase characters",
        )),
    }
}

/// Encode bytes as the registry's canonical lowercase hexadecimal.
fn encode_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

#[cfg(test)]
mod tests {
    use super::{
        challenge, decode_hex, encode_hex, enrol_key, format_timestamp, list_keys, pending,
        replace_key, submit,
    };
    use chrono::{TimeZone, Utc};
    use ed25519_dalek::{Signer, SigningKey};
    use reviewer_domain::{
        CreateReviewDecisionRequest, EnrolSigningKeyRequest, EvidenceGrade, ReviewDecision,
        ReviewKind, ReviewSignatureQuery, Severity, SubmitReviewSignatureRequest,
    };
    use sha2::{Digest, Sha256};
    use sqlx::postgres::PgPoolOptions;
    use uuid::Uuid;

    /// Keep wire timestamps aligned with Python's six-digit UTC renderer.
    #[test]
    fn signing_timestamps_are_canonical_utc() {
        let instant = Utc
            .with_ymd_and_hms(2026, 8, 18, 1, 2, 3)
            .single()
            .expect("instant");
        assert_eq!(format_timestamp(instant), "2026-08-18T01:02:03.000000Z");
    }

    /// Round-trip canonical bytes without admitting uppercase aliases.
    #[test]
    fn signing_hex_is_lowercase_and_exact() {
        assert_eq!(encode_hex(&[0x00, 0xaf, 0xff]), "00afff");
        assert_eq!(decode_hex("00afff").expect("hex"), vec![0x00, 0xaf, 0xff]);
        assert!(decode_hex("00AF").is_err());
    }

    /// Exercise enrolment, resume discovery, challenge, verification, and insertion.
    #[tokio::test]
    #[ignore = "requires a migrated Drugref PostgreSQL database"]
    async fn live_detached_signing_round_trip() {
        let database_url = std::env::var("DRUGREF_REVIEW_TEST_DATABASE_URL")
            .expect("DRUGREF_REVIEW_TEST_DATABASE_URL must name a migrated database");
        let pool = PgPoolOptions::new()
            .max_connections(2)
            .connect(&database_url)
            .await
            .expect("review signing test database");
        let reviewer_uuid = Uuid::new_v4();
        let subject_uuid = Uuid::new_v4();
        let object_uuid = Uuid::new_v4();
        let question_uuid = Uuid::new_v4();
        let username = format!("t{}", reviewer_uuid.simple());
        let target_key = format!("MOIETY:{subject_uuid}/CLASS:{object_uuid}/CI_AXIS:CI_PE");
        let release = format!("review-signing-test-{question_uuid}");

        sqlx::query(
            "INSERT INTO drugref.reviewer_account (reviewer_uuid, username) VALUES ($1, $2)",
        )
        .bind(reviewer_uuid)
        .bind(username)
        .execute(&pool)
        .await
        .expect("test reviewer");
        let run_id: i64 = sqlx::query_scalar(
            "INSERT INTO drugref.ingest_run \
             (source, upstream_release, source_checksum, writer) \
             VALUES ('MED-RT', $1, 'deadbeef', 'medrt_run') RETURNING ingest_run_id",
        )
        .bind(&release)
        .fetch_one(&pool)
        .await
        .expect("test ingest run");
        sqlx::query(
            "INSERT INTO drugref.substance_moiety \
             (moiety_uuid, display_name, first_seen_ingest) VALUES ($1, 'Signing moiety', $2)",
        )
        .bind(subject_uuid)
        .bind(run_id)
        .execute(&pool)
        .await
        .expect("test moiety");
        sqlx::query(
            "INSERT INTO drugref.substance_class \
             (class_uuid, source, source_code, class_name, concept_type, first_seen_ingest) \
             VALUES ($1, 'MED-RT', $2, 'Signing class', 'PE', $3)",
        )
        .bind(object_uuid)
        .bind(format!("N{}", object_uuid.simple()))
        .bind(run_id)
        .execute(&pool)
        .await
        .expect("test class");
        sqlx::query(
            "INSERT INTO drugref.class_contraindication \
             (subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run) \
             VALUES ($1, $2, 'CI_PE', 'MED-RT', $3)",
        )
        .bind(subject_uuid)
        .bind(object_uuid)
        .bind(run_id)
        .execute(&pool)
        .await
        .expect("test candidate");
        sqlx::query(
            "INSERT INTO drugref.open_question \
             (question_uuid, gap_kind, gap_key, question_text, first_derived_ingest, \
              last_derived_ingest) VALUES ($1, 'uncurated_interaction_rule', $2, \
              'Test signing?', $3, $3)",
        )
        .bind(question_uuid)
        .bind(&target_key)
        .bind(run_id)
        .execute(&pool)
        .await
        .expect("test question");
        let decision = crate::decisions::create(
            &pool,
            &CreateReviewDecisionRequest {
                kind: ReviewKind::InteractionRule,
                target_key: target_key.clone(),
                decision: ReviewDecision::Applies,
                severity: Some(Severity::Major),
                mechanism: Some("Test mechanism".into()),
                management: Some("Test management".into()),
                evidence_grade: Some(EvidenceGrade::Established),
                expected_revision_id: None,
            },
            "Signing Reviewer",
        )
        .await
        .expect("test decision");
        let revision_id = decision.current_revision_id.expect("current revision");
        assert!(pending(&pool)
            .await
            .expect("pending queue")
            .iter()
            .any(|item| item.revision_id == revision_id));

        let signing_seed: [u8; 32] = Sha256::digest(reviewer_uuid.as_bytes()).into();
        let signing_key = SigningKey::from_bytes(&signing_seed);
        let public_key_hex = encode_hex(signing_key.verifying_key().as_bytes());
        let enrolled = enrol_key(
            &pool,
            &EnrolSigningKeyRequest { public_key_hex },
            reviewer_uuid,
            "Signing Reviewer",
        )
        .await
        .expect("enrolled key");
        let unused_seed: [u8; 32] = Sha256::digest(question_uuid.as_bytes()).into();
        let unused_key = SigningKey::from_bytes(&unused_seed);
        let unused = enrol_key(
            &pool,
            &EnrolSigningKeyRequest {
                public_key_hex: encode_hex(unused_key.verifying_key().as_bytes()),
            },
            reviewer_uuid,
            "Signing Reviewer",
        )
        .await
        .expect("unused enrolled key");
        let other_reviewer_uuid = Uuid::new_v4();
        sqlx::query(
            "INSERT INTO drugref.reviewer_account (reviewer_uuid, username) VALUES ($1, $2)",
        )
        .bind(other_reviewer_uuid)
        .bind(format!("t{}", other_reviewer_uuid.simple()))
        .execute(&pool)
        .await
        .expect("other test reviewer");
        assert!(replace_key(
            &pool,
            &unused.key_fingerprint,
            other_reviewer_uuid,
            "Other Reviewer",
        )
        .await
        .is_err());
        let unused_replacement = replace_key(
            &pool,
            &unused.key_fingerprint,
            reviewer_uuid,
            "Signing Reviewer",
        )
        .await
        .expect("unused key replacement");
        assert_eq!(unused_replacement.preserved_signature_count, 0);
        assert_eq!(
            replace_key(
                &pool,
                &unused.key_fingerprint,
                reviewer_uuid,
                "Signing Reviewer",
            )
            .await
            .expect("idempotent unused-key replacement")
            .preserved_signature_count,
            0
        );
        let query = ReviewSignatureQuery {
            kind: ReviewKind::InteractionRule,
            target_key: target_key.clone(),
            revision_id,
            key_fingerprint: enrolled.key_fingerprint.clone(),
        };
        let challenge = challenge(&pool, &query, reviewer_uuid)
            .await
            .expect("signing challenge");
        let payload = challenge.canonical_payload().expect("canonical payload");
        let signed = submit(
            &pool,
            &SubmitReviewSignatureRequest {
                query,
                signed_at: challenge.signed_at,
                payload_digest: challenge.payload_digest,
                signature_hex: encode_hex(&signing_key.sign(&payload).to_bytes()),
            },
            reviewer_uuid,
        )
        .await
        .expect("verified detached signature");
        assert_eq!(signed.history[0].signature_status, "signed");
        assert!(!pending(&pool)
            .await
            .expect("refreshed pending queue")
            .iter()
            .any(|item| item.revision_id == revision_id));
        let replacement = replace_key(
            &pool,
            &enrolled.key_fingerprint,
            reviewer_uuid,
            "Signing Reviewer",
        )
        .await
        .expect("signed key rotation");
        assert_eq!(replacement.preserved_signature_count, 1);
        assert!(list_keys(&pool, reviewer_uuid)
            .await
            .expect("keys after rotation")
            .keys
            .is_empty());
    }
}
