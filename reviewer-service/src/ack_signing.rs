//! Signing departure acknowledgements from the GUI (db/088, #284).
//!
//! An acknowledgement silences a CHANGED SINCE REVIEW banner, so a forged or
//! altered one hides a departure from every later reviewer. db/088 made each ack a
//! signature target whose payload is its episode's CONTENT (ruling key and drug
//! pair) plus the curator's fields, read from `graded_pair_departure_ack_payload`.
//!
//! One GUI acknowledgement can close many episodes, one ack row each, so this module
//! signs a BATCH: `challenges` prepares one payload per ack under one signing instant;
//! the native core shows them, unlocks the vault once and signs each; `submit`
//! rebuilds every payload, verifies every signature and records them all in one
//! transaction (any failure records none). Ack rows are insert-only (db/083), so no
//! row lock is needed: the content a reviewer confirmed cannot change underneath.
//!
//! "Needs a signature" is db/088's `departure_ack_signature` view (anything but
//! `signed`), the same rule `drugref audit departures` prints.

use std::collections::HashMap;

use axum::{extract::State, http::HeaderMap, http::StatusCode, Json};
use chrono::{DateTime, Utc};
use reviewer_domain::{
    AckSignatureChallenges, AckSignatureQuery, AckSignatureResult, ReviewSignatureChallenge,
    RuleGrain, SubmitAckSignaturesRequest, UnsignedDepartureAck, DEPARTURE_ACK_TARGET_KIND,
    DEPARTURE_ACK_V1_CONTEXT, GRADED_PAIR_DEPARTURE_ACK_V1_FIELDS,
};
use sha2::{Digest, Sha256};
use sqlx::{FromRow, PgConnection, PgPool};
use uuid::Uuid;

use crate::{
    authenticate_headers,
    signing::{
        encode_hex, ensure_active_enrolment, ensure_fresh, format_timestamp, insert_signature,
        named_fields, parse_signed_at, verify_detached,
    },
    AppError, AppState,
};

/// Every acknowledgement without a trusted signature, named for display, newest first.
const UNSIGNED_SQL: &str = "\
SELECT s.ack_id, s.rule_grain,
       coalesce(sm.display_name, sc.class_name, s.subject_key::text) AS subject_name,
       oc.class_name AS object_class_name, s.relationship,
       m1.display_name AS subject_moiety_name, m2.display_name AS partner_moiety_name,
       s.rationale, s.reviewed_by, s.reviewed_against, s.acknowledged_at,
       s.signature_status
FROM   drugref.departure_ack_signature s
LEFT   JOIN drugref.substance_moiety sm
       ON s.rule_grain = 'moiety_rule' AND sm.moiety_uuid = s.subject_key
LEFT   JOIN drugref.substance_class sc
       ON s.rule_grain = 'class_rule' AND sc.class_uuid = s.subject_key
JOIN   drugref.substance_class oc ON oc.class_uuid = s.object_class_uuid
JOIN   drugref.substance_moiety m1 ON m1.moiety_uuid = s.subject_moiety
JOIN   drugref.substance_moiety m2 ON m2.moiety_uuid = s.partner_moiety
WHERE  s.signature_status <> 'signed'
ORDER  BY s.acknowledged_at DESC, s.ack_id";

/// One row of `UNSIGNED_SQL`.
#[derive(FromRow)]
struct UnsignedRow {
    ack_id: i64,
    rule_grain: String,
    subject_name: String,
    object_class_name: String,
    relationship: String,
    subject_moiety_name: String,
    partner_moiety_name: String,
    rationale: String,
    reviewed_by: String,
    reviewed_against: String,
    acknowledged_at: DateTime<Utc>,
    signature_status: String,
}

impl TryFrom<UnsignedRow> for UnsignedDepartureAck {
    type Error = AppError;

    /// Parse the grain vocabulary and render the timestamp for the wire.
    fn try_from(row: UnsignedRow) -> Result<Self, Self::Error> {
        Ok(Self {
            ack_id: row.ack_id,
            rule_grain: RuleGrain::parse(&row.rule_grain)
                .ok_or_else(|| AppError::internal(format!("unknown grain {}", row.rule_grain)))?,
            subject_name: row.subject_name,
            object_class_name: row.object_class_name,
            relationship: row.relationship,
            subject_moiety_name: row.subject_moiety_name,
            partner_moiety_name: row.partner_moiety_name,
            rationale: row.rationale,
            reviewed_by: row.reviewed_by,
            reviewed_against: row.reviewed_against,
            acknowledged_at: row.acknowledged_at.to_rfc3339(),
            signature_status: row.signature_status,
        })
    }
}

/// One acknowledgement's signed content: a row of db/088's payload view.
#[derive(FromRow)]
struct AckPayloadRow {
    ack_id: i64,
    rule_grain: String,
    subject_key: Uuid,
    object_class_uuid: Uuid,
    relationship: String,
    subject_moiety: Uuid,
    partner_moiety: Uuid,
    rationale: String,
    reviewed_by: String,
    reviewed_against: String,
    acknowledged_at: DateTime<Utc>,
}

/// Build one ack's challenge: its fields rendered in frozen order, bound to the
/// signing key and instant, with the digest of the exact bytes.
fn ack_challenge(
    row: AckPayloadRow,
    fingerprint: &str,
    signed_at: &str,
) -> Result<ReviewSignatureChallenge, AppError> {
    let values = vec![
        Some(row.rule_grain),
        Some(row.subject_key.to_string()),
        Some(row.object_class_uuid.to_string()),
        Some(row.relationship),
        Some(row.subject_moiety.to_string()),
        Some(row.partner_moiety.to_string()),
        Some(row.rationale),
        Some(row.reviewed_by),
        Some(row.reviewed_against),
        Some(format_timestamp(row.acknowledged_at)),
        Some(fingerprint.into()),
        Some(signed_at.into()),
    ];
    let mut challenge = ReviewSignatureChallenge {
        target_kind: DEPARTURE_ACK_TARGET_KIND.into(),
        target_id: row.ack_id,
        payload_context: DEPARTURE_ACK_V1_CONTEXT.into(),
        fields: named_fields(&GRADED_PAIR_DEPARTURE_ACK_V1_FIELDS, values),
        payload_digest: String::new(),
        signed_at: signed_at.into(),
    };
    let payload = challenge
        .canonical_payload()
        .map_err(|error| AppError::internal(error.0))?;
    challenge.payload_digest = encode_hex(&Sha256::digest(payload));
    Ok(challenge)
}

/// Read every queried ack and build its challenge, in query order.
async fn challenges_at(
    connection: &mut PgConnection,
    query: &AckSignatureQuery,
    signed_at: String,
) -> Result<AckSignatureChallenges, AppError> {
    let mut rows: HashMap<i64, AckPayloadRow> = sqlx::query_as::<_, AckPayloadRow>(
        "SELECT ack_id, rule_grain, subject_key, object_class_uuid, relationship, \
                subject_moiety, partner_moiety, rationale, reviewed_by, reviewed_against, \
                acknowledged_at \
         FROM drugref.graded_pair_departure_ack_payload WHERE ack_id = ANY($1)",
    )
    .bind(&query.ack_ids)
    .fetch_all(connection)
    .await?
    .into_iter()
    .map(|row| (row.ack_id, row))
    .collect();
    let challenges = query
        .ack_ids
        .iter()
        .map(|id| {
            let row = rows.remove(id).ok_or_else(|| {
                AppError::not_found(format!("acknowledgement {id} does not exist"))
            })?;
            ack_challenge(row, &query.key_fingerprint, &signed_at)
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(AckSignatureChallenges {
        signed_at,
        challenges,
    })
}

/// Every acknowledgement with no signature the registry trusts.
pub async fn unsigned(pool: &PgPool) -> Result<Vec<UnsignedDepartureAck>, AppError> {
    sqlx::query_as::<_, UnsignedRow>(UNSIGNED_SQL)
        .fetch_all(pool)
        .await?
        .into_iter()
        .map(UnsignedDepartureAck::try_from)
        .collect()
}

/// Prepare one challenge per queried ack for the reviewer's active enrolled key.
pub async fn challenges(
    pool: &PgPool,
    query: &AckSignatureQuery,
    reviewer_uuid: Uuid,
) -> Result<AckSignatureChallenges, AppError> {
    let mut connection = pool.acquire().await?;
    ensure_active_enrolment(&mut connection, reviewer_uuid, &query.key_fingerprint).await?;
    challenges_at(&mut connection, query, format_timestamp(Utc::now())).await
}

/// Verify and record every signature of a confirmed batch, or none of them.
pub async fn submit(
    pool: &PgPool,
    input: &SubmitAckSignaturesRequest,
    reviewer_uuid: Uuid,
) -> Result<AckSignatureResult, AppError> {
    let signed_at = parse_signed_at(&input.signed_at)?;
    ensure_fresh(signed_at, "the acknowledgements")?;
    let mut transaction = pool.begin().await?;
    let public_key = ensure_active_enrolment(
        &mut transaction,
        reviewer_uuid,
        &input.query.key_fingerprint,
    )
    .await?;
    let prepared = challenges_at(&mut transaction, &input.query, input.signed_at.clone()).await?;
    // Validation pinned `signatures` to the query's ids in order, so they zip.
    for (challenge, signed) in prepared.challenges.iter().zip(&input.signatures) {
        if challenge.payload_digest != signed.payload_digest {
            return Err(AppError::conflict(
                "the signing payload changed after confirmation; prepare it again",
            ));
        }
        let payload = challenge
            .canonical_payload()
            .map_err(|error| AppError::internal(error.0))?;
        let signature = verify_detached(&public_key, &payload, &signed.signature_hex)?;
        insert_signature(
            &mut transaction,
            challenge,
            &payload,
            &input.query.key_fingerprint,
            signature,
            signed_at,
        )
        .await?;
    }
    transaction.commit().await?;
    Ok(AckSignatureResult {
        recorded: input.signatures.len(),
    })
}

/// GET /v1/departure-acks/unsigned: any authenticated reviewer.
pub async fn unsigned_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Vec<UnsignedDepartureAck>>, AppError> {
    authenticate_headers(&state, &headers).await?;
    Ok(Json(unsigned(&state.pool).await?))
}

/// POST /v1/departure-ack-signatures/challenges: prepare a batch for confirmation.
pub async fn challenges_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(query): Json<AckSignatureQuery>,
) -> Result<Json<AckSignatureChallenges>, AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    let query = query
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    Ok(Json(
        challenges(&state.pool, &query, authenticated.reviewer.reviewer_uuid).await?,
    ))
}

/// POST /v1/departure-ack-signatures: verify and record a confirmed batch.
pub async fn submit_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(input): Json<SubmitAckSignaturesRequest>,
) -> Result<(StatusCode, Json<AckSignatureResult>), AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    let input = input
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    let done = submit(&state.pool, &input, authenticated.reviewer.reviewer_uuid).await?;
    Ok((StatusCode::CREATED, Json(done)))
}

#[cfg(test)]
mod tests {
    use axum::http::StatusCode;
    use chrono::DateTime;
    use ed25519_dalek::{Signer, SigningKey};
    use reviewer_domain::{
        AckSignature, AckSignatureQuery, AcknowledgeDepartureRequest, EnrolSigningKeyRequest,
        RuleGrain, SubmitAckSignaturesRequest,
    };
    use serde_json::Value;
    use sha2::{Digest, Sha256};
    use uuid::Uuid;

    use super::{ack_challenge, challenges, submit, unsigned, AckPayloadRow};
    use crate::live_graded_seed::{live_pool, seed_gui_moiety_ruling};
    use crate::signing::{encode_hex, enrol_key};

    /// The service renders an ack (uuids, the microsecond UTC timestamp) exactly as
    /// the Python reference encoder does: the committed vector's bytes, rebuilt here
    /// from a payload-view row.
    #[test]
    fn an_ack_challenge_reproduces_the_published_vector() {
        let fixture: Value =
            serde_json::from_str(include_str!("../../tests/fixtures/signing_vectors.json"))
                .expect("signing vectors");
        let case = fixture["cases"]
            .as_array()
            .expect("cases")
            .iter()
            .find(|case| case["context"] == "graded_pair_departure_ack/v1")
            .expect("an ack vector");
        let field = |name: &str| -> String {
            case["fields"]
                .as_array()
                .expect("fields")
                .iter()
                .find(|pair| pair[0] == name)
                .and_then(|pair| pair[1].as_str())
                .expect("field")
                .to_string()
        };
        let uuid = |name: &str| Uuid::parse_str(&field(name)).expect("uuid");
        let row = AckPayloadRow {
            ack_id: 7,
            rule_grain: field("rule_grain"),
            subject_key: uuid("subject_key"),
            object_class_uuid: uuid("object_class_uuid"),
            relationship: field("relationship"),
            subject_moiety: uuid("subject_moiety"),
            partner_moiety: uuid("partner_moiety"),
            rationale: field("rationale"),
            reviewed_by: field("reviewed_by"),
            reviewed_against: field("reviewed_against"),
            acknowledged_at: DateTime::parse_from_rfc3339(&field("acknowledged_at"))
                .expect("instant")
                .to_utc(),
        };
        let challenge = ack_challenge(row, &field("signer_key_fingerprint"), &field("signed_at"))
            .expect("challenge");
        assert_eq!(
            challenge.canonical_payload().expect("payload"),
            case["payload"].as_str().expect("payload").as_bytes()
        );
        assert_eq!(
            challenge.payload_digest,
            case["digest"].as_str().expect("digest")
        );
    }

    /// The GUI path end to end (#284): an acknowledged departure is listed unsigned,
    /// a batch is prepared and signed with an enrolled key, the ack then reads
    /// `signed`, and a submission whose confirmed digest is stale is refused.
    #[tokio::test]
    #[ignore = "requires a migrated Drugref PostgreSQL database"]
    async fn live_ack_is_signed_in_a_batch() {
        let pool = live_pool().await;
        let seeded = seed_gui_moiety_ruling(&pool).await;
        sqlx::query("DELETE FROM drugref.class_membership WHERE moiety_uuid = $1")
            .bind(seeded.partner)
            .execute(&pool)
            .await
            .expect("partner leaves");
        let ruling_id: i64 = sqlx::query_scalar(
            "SELECT curated_interaction_id FROM drugref.curated_interaction \
             WHERE subject_moiety_uuid = $1 AND superseded_by IS NULL",
        )
        .bind(seeded.subject)
        .fetch_one(&pool)
        .await
        .expect("ruling");
        crate::departures::acknowledge(
            &pool,
            &AcknowledgeDepartureRequest {
                rule_grain: RuleGrain::MoietyRule,
                ruling_id,
                moiety_uuid: seeded.partner,
                rationale: "Partner reclassified upstream".into(),
            },
            "Ack Signer",
        )
        .await
        .expect("acknowledgement");
        let listed = unsigned(&pool).await.expect("unsigned acks");
        let ours = listed
            .iter()
            .find(|ack| ack.partner_moiety_name == "Partner" && ack.reviewed_by == "Ack Signer")
            .expect("our ack is unsigned");
        assert_eq!(ours.signature_status, "unsigned");
        assert_eq!(ours.subject_name, "Subject");
        let ack_id = ours.ack_id;

        let reviewer_uuid = Uuid::new_v4();
        sqlx::query(
            "INSERT INTO drugref.reviewer_account (reviewer_uuid, username) VALUES ($1, $2)",
        )
        .bind(reviewer_uuid)
        .bind(format!("t{}", reviewer_uuid.simple()))
        .execute(&pool)
        .await
        .expect("test reviewer");
        sqlx::query(
            "INSERT INTO drugref.reviewer_profile \
             (reviewer_uuid, full_name, role, active, recorded_by) \
             VALUES ($1, 'Ack Signer', 'reviewer', true, $1)",
        )
        .bind(reviewer_uuid)
        .execute(&pool)
        .await
        .expect("test reviewer profile");
        let seed: [u8; 32] = Sha256::digest(reviewer_uuid.as_bytes()).into();
        let key = SigningKey::from_bytes(&seed);
        let enrolled = enrol_key(
            &pool,
            &EnrolSigningKeyRequest {
                public_key_hex: encode_hex(key.verifying_key().as_bytes()),
            },
            reviewer_uuid,
            "Ack Signer",
        )
        .await
        .expect("enrolled key");

        let query = AckSignatureQuery {
            ack_ids: vec![ack_id],
            key_fingerprint: enrolled.key_fingerprint.clone(),
        };
        let prepared = challenges(&pool, &query, reviewer_uuid)
            .await
            .expect("challenges");
        let challenge = &prepared.challenges[0];
        let payload = challenge.canonical_payload().expect("payload");
        let request = |digest: String| SubmitAckSignaturesRequest {
            query: query.clone(),
            signed_at: prepared.signed_at.clone(),
            signatures: vec![AckSignature {
                ack_id,
                payload_digest: digest,
                signature_hex: encode_hex(&key.sign(&payload).to_bytes()),
            }],
        };
        let stale = submit(&pool, &request("0".repeat(64)), reviewer_uuid)
            .await
            .expect_err("a stale digest is refused");
        assert_eq!(stale.status(), StatusCode::CONFLICT);

        let done = submit(
            &pool,
            &request(challenge.payload_digest.clone()),
            reviewer_uuid,
        )
        .await
        .expect("signed");
        assert_eq!(done.recorded, 1);
        let status: String = sqlx::query_scalar(
            "SELECT signature_status FROM drugref.departure_ack_signature WHERE ack_id = $1",
        )
        .bind(ack_id)
        .fetch_one(&pool)
        .await
        .expect("status");
        assert_eq!(status, "signed");
        assert!(!unsigned(&pool)
            .await
            .expect("unsigned acks")
            .iter()
            .any(|ack| ack.ack_id == ack_id));
    }
}
