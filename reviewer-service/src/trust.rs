//! Administrator-owned signing-key trust projection and status corrections.

use chrono::{DateTime, Utc};
use reviewer_domain::{
    AdministrativeSigningKeyStatus, SigningKeyAdministrationResult, SigningKeyTrustStatus,
    SigningKeyTrustSummary,
};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

use crate::{administration::lock_and_require_administrator, error::AppError};

const ACTIVE_KEY_STATUS: &str = "active";

/// Current public registry row plus its latest reviewer enrolment projection.
#[derive(FromRow)]
struct SigningKeyTrustRow {
    key_fingerprint: String,
    algorithm: String,
    holder: String,
    status: String,
    status_from: DateTime<Utc>,
    registered_at: DateTime<Utc>,
    reviewer_uuid: Option<Uuid>,
    username: Option<String>,
    reviewer_full_name: Option<String>,
    enrolled: bool,
    signature_count: i64,
    current_revision_count: i64,
    affected_current_revision_count: i64,
}

impl From<SigningKeyTrustRow> for SigningKeyTrustSummary {
    /// Convert database timestamps into stable wire spellings.
    fn from(row: SigningKeyTrustRow) -> Self {
        Self {
            key_fingerprint: row.key_fingerprint,
            algorithm: row.algorithm,
            holder: row.holder,
            status: row.status,
            status_from: row.status_from.to_rfc3339(),
            registered_at: row.registered_at.to_rfc3339(),
            reviewer_uuid: row.reviewer_uuid,
            username: row.username,
            reviewer_full_name: row.reviewer_full_name,
            enrolled: row.enrolled,
            signature_count: row.signature_count,
            current_revision_count: row.current_revision_count,
            affected_current_revision_count: row.affected_current_revision_count,
        }
    }
}

/// Current immutable public bytes needed to append a status correction.
#[derive(FromRow)]
struct CurrentSigningKeyRow {
    signing_key_id: i64,
    public_key: Vec<u8>,
    algorithm: String,
    holder: String,
    status: String,
}

/// Current live reviewer enrolment withdrawn alongside an administrative correction.
#[derive(FromRow)]
struct CurrentEnrolmentRow {
    reviewer_key_enrolment_id: i64,
    signing_key_id: i64,
    reviewer_uuid: Uuid,
}

/// Return every current registry key with ownership and current review impact.
pub async fn list(pool: &PgPool) -> Result<SigningKeyTrustStatus, AppError> {
    let rows = sqlx::query_as::<_, SigningKeyTrustRow>(
        "WITH current_target AS ( \
             SELECT 'curated_interaction'::text AS target_kind, \
                    curated_interaction_id AS target_id \
             FROM drugref.curated_interaction WHERE superseded_by IS NULL \
             UNION ALL \
             SELECT 'curated_condition'::text, curated_condition_id \
             FROM drugref.curated_condition WHERE superseded_by IS NULL \
         ) \
         SELECT k.key_fingerprint, k.algorithm, k.holder, k.status, k.status_from, \
                k.registered_at, owner.reviewer_uuid, owner.username, \
                owner.reviewer_full_name, COALESCE(owner.enrolled, false) AS enrolled, \
                (SELECT count(*) FROM drugref.assertion_signature s \
                 WHERE s.key_fingerprint = k.key_fingerprint) AS signature_count, \
                (SELECT count(DISTINCT (s.target_kind, s.target_id)) \
                 FROM current_target target \
                 JOIN drugref.assertion_signature s \
                   ON s.target_kind = target.target_kind AND s.target_id = target.target_id \
                 WHERE s.key_fingerprint = k.key_fingerprint) AS current_revision_count, \
                (SELECT count(DISTINCT (s.target_kind, s.target_id)) \
                 FROM current_target target \
                 JOIN drugref.assertion_signature s \
                   ON s.target_kind = target.target_kind AND s.target_id = target.target_id \
                 JOIN drugref.curated_signature_status status \
                   ON status.target_kind = target.target_kind \
                  AND status.target_id = target.target_id \
                 WHERE s.key_fingerprint = k.key_fingerprint \
                   AND status.unobjected_count = 0) AS affected_current_revision_count \
         FROM drugref.signing_key k \
         LEFT JOIN LATERAL ( \
             SELECT e.reviewer_uuid, a.username, p.full_name AS reviewer_full_name, \
                    e.enrolled \
             FROM drugref.reviewer_key_enrolment e \
             JOIN drugref.signing_key enrolled_key \
               ON enrolled_key.signing_key_id = e.signing_key_id \
             JOIN drugref.reviewer_account a ON a.reviewer_uuid = e.reviewer_uuid \
             JOIN drugref.reviewer_profile p ON p.reviewer_uuid = e.reviewer_uuid \
               AND p.superseded_by IS NULL \
             WHERE enrolled_key.key_fingerprint = k.key_fingerprint \
               AND e.superseded_by IS NULL \
             ORDER BY e.enrolled_at DESC LIMIT 1 \
         ) owner ON true \
         WHERE k.superseded_by IS NULL \
         ORDER BY COALESCE(owner.reviewer_full_name, k.holder), k.key_fingerprint",
    )
    .fetch_all(pool)
    .await?;
    Ok(SigningKeyTrustStatus {
        keys: rows.into_iter().map(SigningKeyTrustSummary::from).collect(),
    })
}

/// Append a retired or compromised correction and withdraw any live enrolment.
pub async fn administer(
    pool: &PgPool,
    key_fingerprint: &str,
    status: AdministrativeSigningKeyStatus,
    actor_uuid: Uuid,
    actor_name: &str,
) -> Result<SigningKeyAdministrationResult, AppError> {
    let mut transaction = pool.begin().await?;
    lock_and_require_administrator(&mut transaction, actor_uuid).await?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext('drugref reviewer key'), hashtext($1))")
        .bind(key_fingerprint)
        .execute(&mut *transaction)
        .await?;

    let current = sqlx::query_as::<_, CurrentSigningKeyRow>(
        "SELECT signing_key_id, public_key, algorithm, holder, status \
         FROM drugref.signing_key WHERE key_fingerprint = $1 \
           AND superseded_by IS NULL FOR UPDATE",
    )
    .bind(key_fingerprint)
    .fetch_optional(&mut *transaction)
    .await?
    .ok_or_else(|| AppError::not_found("signing key was not found"))?;
    validate_transition(&mut transaction, key_fingerprint, &current.status, status).await?;

    if current.status != status.as_str() {
        let replacement_id: i64 = sqlx::query_scalar(
            "INSERT INTO drugref.signing_key \
             (key_fingerprint, public_key, algorithm, holder, status, status_from, registered_by) \
             VALUES ($1, $2, $3, $4, $5, now(), $6) RETURNING signing_key_id",
        )
        .bind(key_fingerprint)
        .bind(current.public_key)
        .bind(current.algorithm)
        .bind(current.holder)
        .bind(status.as_str())
        .bind(actor_name)
        .fetch_one(&mut *transaction)
        .await?;
        sqlx::query("UPDATE drugref.signing_key SET superseded_by = $1 WHERE signing_key_id = $2")
            .bind(replacement_id)
            .bind(current.signing_key_id)
            .execute(&mut *transaction)
            .await?;
    }

    let enrolment = current_enrolment(&mut transaction, key_fingerprint).await?;
    let withdrawn_enrolment = enrolment.is_some();
    if let Some(enrolment) = enrolment {
        let withdrawn_id: i64 = sqlx::query_scalar(
            "INSERT INTO drugref.reviewer_key_enrolment \
             (reviewer_uuid, signing_key_id, enrolled, enrolled_by) \
             VALUES ($1, $2, false, $3) RETURNING reviewer_key_enrolment_id",
        )
        .bind(enrolment.reviewer_uuid)
        .bind(enrolment.signing_key_id)
        .bind(actor_uuid)
        .fetch_one(&mut *transaction)
        .await?;
        sqlx::query(
            "UPDATE drugref.reviewer_key_enrolment SET superseded_by = $1 \
             WHERE reviewer_key_enrolment_id = $2",
        )
        .bind(withdrawn_id)
        .bind(enrolment.reviewer_key_enrolment_id)
        .execute(&mut *transaction)
        .await?;
    }
    transaction.commit().await?;

    let key = list(pool)
        .await?
        .keys
        .into_iter()
        .find(|key| key.key_fingerprint == key_fingerprint)
        .ok_or_else(|| AppError::internal("corrected signing key was not readable"))?;
    Ok(SigningKeyAdministrationResult {
        revisions_awaiting_counter_signature: key.affected_current_revision_count,
        key,
        withdrawn_enrolment,
    })
}

/// Refuse trust downgrades while permitting retrospective compromise escalation.
async fn validate_transition(
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    key_fingerprint: &str,
    current_status: &str,
    requested: AdministrativeSigningKeyStatus,
) -> Result<(), AppError> {
    let permanently_compromised: bool = sqlx::query_scalar(
        "SELECT EXISTS (SELECT 1 FROM drugref.signing_key k \
         JOIN drugref.signing_key_status_kind status ON status.status = k.status \
         WHERE k.key_fingerprint = $1 AND status.invalidates_all_signatures)",
    )
    .bind(key_fingerprint)
    .fetch_one(&mut **transaction)
    .await?;
    match requested {
        AdministrativeSigningKeyStatus::Retired if permanently_compromised => Err(
            AppError::conflict("a compromised key cannot be downgraded to retired"),
        ),
        AdministrativeSigningKeyStatus::Retired
            if current_status != ACTIVE_KEY_STATUS && current_status != requested.as_str() =>
        {
            Err(AppError::conflict("only an active key can be retired"))
        }
        _ => Ok(()),
    }
}

/// Lock the current live enrolment for a fingerprint when one still exists.
async fn current_enrolment(
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    key_fingerprint: &str,
) -> Result<Option<CurrentEnrolmentRow>, AppError> {
    Ok(sqlx::query_as::<_, CurrentEnrolmentRow>(
        "SELECT e.reviewer_key_enrolment_id, e.signing_key_id, e.reviewer_uuid \
         FROM drugref.reviewer_key_enrolment e \
         JOIN drugref.signing_key enrolled ON enrolled.signing_key_id = e.signing_key_id \
         WHERE enrolled.key_fingerprint = $1 AND e.superseded_by IS NULL AND e.enrolled \
         FOR UPDATE OF e",
    )
    .bind(key_fingerprint)
    .fetch_optional(&mut **transaction)
    .await?)
}
