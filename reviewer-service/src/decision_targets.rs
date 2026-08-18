//! Canonical target parsing, locking, question resolution, and release provenance.

use reviewer_domain::{ReviewKind, ReviewRecordQuery};
use sqlx::{PgConnection, PgPool, Postgres, Transaction};
use uuid::Uuid;

use crate::AppError;

const DECISION_LOCK_NAMESPACE: &str = "drugref reviewer decision";

/// Parsed natural key carried by a canonical queue target.
pub(super) enum ReviewTarget {
    /// Moiety-to-class interaction rule.
    Interaction {
        subject_uuid: Uuid,
        object_uuid: Uuid,
        relationship: String,
    },
    /// Moiety-to-condition contradiction.
    Condition {
        subject_uuid: Uuid,
        object_uuid: Uuid,
    },
}

/// Resolve and validate a target for a read-only history request.
pub(super) async fn read_target(
    pool: &PgPool,
    query: &ReviewRecordQuery,
) -> Result<ReviewTarget, AppError> {
    let mut connection = pool.acquire().await?;
    read_target_connection(&mut connection, query).await
}

/// Resolve a target on a caller-owned connection or locked transaction.
pub(super) async fn read_target_connection(
    connection: &mut PgConnection,
    query: &ReviewRecordQuery,
) -> Result<ReviewTarget, AppError> {
    let target = parse_target(query.kind, &query.target_key)?;
    let found: bool = sqlx::query_scalar(
        "SELECT EXISTS (SELECT 1 FROM drugref.open_question WHERE gap_kind = $1 AND gap_key = $2)",
    )
    .bind(gap_kind(query.kind))
    .bind(&query.target_key)
    .fetch_one(connection)
    .await?;
    if !found {
        return Err(AppError::not_found("review target is not registered"));
    }
    Ok(target)
}

/// Parse UUIDs and the relationship from the frozen registry key.
pub(super) fn parse_target(kind: ReviewKind, key: &str) -> Result<ReviewTarget, AppError> {
    let body = key
        .strip_prefix("MOIETY:")
        .ok_or_else(|| AppError::bad_request("targetKey is not canonical"))?;
    match kind {
        ReviewKind::InteractionRule => {
            let (subject, remainder) = body
                .split_once("/CLASS:")
                .ok_or_else(|| AppError::bad_request("targetKey is not canonical"))?;
            let (object, relationship) = remainder
                .split_once("/CI_AXIS:")
                .ok_or_else(|| AppError::bad_request("targetKey is not canonical"))?;
            if relationship.is_empty() {
                return Err(AppError::bad_request("targetKey is not canonical"));
            }
            Ok(ReviewTarget::Interaction {
                subject_uuid: parse_uuid(subject)?,
                object_uuid: parse_uuid(object)?,
                relationship: relationship.into(),
            })
        }
        ReviewKind::ConditionContradiction => {
            let (subject, object) = body
                .split_once("/CONDITION:")
                .ok_or_else(|| AppError::bad_request("targetKey is not canonical"))?;
            Ok(ReviewTarget::Condition {
                subject_uuid: parse_uuid(subject)?,
                object_uuid: parse_uuid(object)?,
            })
        }
    }
}

/// Parse one canonical UUID component without exposing database errors.
fn parse_uuid(value: &str) -> Result<Uuid, AppError> {
    Uuid::parse_str(value).map_err(|_| AppError::bad_request("targetKey is not canonical"))
}

/// Serialize concurrent writes to one canonical target within PostgreSQL.
pub(super) async fn lock_target(
    transaction: &mut Transaction<'_, Postgres>,
    target_key: &str,
) -> Result<(), AppError> {
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))")
        .bind(DECISION_LOCK_NAMESPACE)
        .bind(target_key)
        .execute(&mut **transaction)
        .await?;
    Ok(())
}

/// Resolve the service-owned question UUID inside the write transaction.
pub(super) async fn resolve_question(
    transaction: &mut Transaction<'_, Postgres>,
    kind: ReviewKind,
    target_key: &str,
) -> Result<Uuid, AppError> {
    sqlx::query_scalar(
        "SELECT question_uuid FROM drugref.open_question WHERE gap_kind = $1 AND gap_key = $2",
    )
    .bind(gap_kind(kind))
    .bind(target_key)
    .fetch_optional(&mut **transaction)
    .await?
    .ok_or_else(|| AppError::not_found("review target is not registered"))
}

/// Return the open-question vocabulary associated with a GUI review kind.
fn gap_kind(kind: ReviewKind) -> &'static str {
    match kind {
        ReviewKind::InteractionRule => "uncurated_interaction_rule",
        ReviewKind::ConditionContradiction => "uncurated_condition_contradiction",
    }
}

/// Derive the release snapshot from the candidate projection, never from the client.
pub(super) async fn reviewed_against(
    transaction: &mut Transaction<'_, Postgres>,
    target: &ReviewTarget,
) -> Result<Option<String>, AppError> {
    match target {
        ReviewTarget::Interaction {
            subject_uuid,
            object_uuid,
            relationship,
        } => Ok(sqlx::query_scalar(
            "SELECT string_agg(DISTINCT r.upstream_release, ' / ' ORDER BY r.upstream_release) \
             FROM drugref.class_contraindication c JOIN drugref.ingest_run r \
               ON r.ingest_run_id = c.ingest_run \
             WHERE c.subject_moiety_uuid = $1 AND c.object_class_uuid = $2 \
               AND c.relationship = $3",
        )
        .bind(subject_uuid)
        .bind(object_uuid)
        .bind(relationship)
        .fetch_one(&mut **transaction)
        .await?),
        ReviewTarget::Condition {
            subject_uuid,
            object_uuid,
        } => Ok(sqlx::query_scalar(
            "SELECT string_agg(DISTINCT r.upstream_release, ' / ' ORDER BY r.upstream_release) \
             FROM (SELECT ingest_run FROM drugref.moiety_condition_contraindication \
                   WHERE subject_moiety_uuid = $1 AND object_condition_uuid = $2 \
                   UNION SELECT ingest_run FROM drugref.moiety_condition_indication \
                   WHERE subject_moiety_uuid = $1 AND object_condition_uuid = $2) candidate \
             JOIN drugref.ingest_run r ON r.ingest_run_id = candidate.ingest_run",
        )
        .bind(subject_uuid)
        .bind(object_uuid)
        .fetch_one(&mut **transaction)
        .await?),
    }
}

/// Retain the predecessor's release label when correcting an orphaned curated row.
pub(super) async fn predecessor_release(
    transaction: &mut Transaction<'_, Postgres>,
    target: &ReviewTarget,
) -> Result<Option<String>, AppError> {
    match target {
        ReviewTarget::Interaction {
            subject_uuid,
            object_uuid,
            relationship,
        } => Ok(sqlx::query_scalar(
            "SELECT reviewed_against FROM drugref.curated_interaction \
             WHERE subject_moiety_uuid = $1 AND object_class_uuid = $2 \
               AND relationship = $3 AND superseded_by IS NULL",
        )
        .bind(subject_uuid)
        .bind(object_uuid)
        .bind(relationship)
        .fetch_optional(&mut **transaction)
        .await?),
        ReviewTarget::Condition {
            subject_uuid,
            object_uuid,
        } => Ok(sqlx::query_scalar(
            "SELECT reviewed_against FROM drugref.curated_condition \
             WHERE subject_moiety_uuid = $1 AND object_condition_uuid = $2 \
               AND superseded_by IS NULL",
        )
        .bind(subject_uuid)
        .bind(object_uuid)
        .fetch_optional(&mut **transaction)
        .await?),
    }
}

#[cfg(test)]
mod tests {
    use super::{parse_target, ReviewTarget};
    use reviewer_domain::ReviewKind;

    /// Parse both frozen canonical target-key shapes into their natural keys.
    #[test]
    fn canonical_targets_parse_without_re_minting_identity() {
        let subject = "00000000-0000-0000-0000-000000000001";
        let object = "00000000-0000-0000-0000-000000000002";
        let interaction = parse_target(
            ReviewKind::InteractionRule,
            &format!("MOIETY:{subject}/CLASS:{object}/CI_AXIS:CI_with"),
        )
        .expect("interaction target");
        assert!(matches!(interaction, ReviewTarget::Interaction { .. }));
        let condition = parse_target(
            ReviewKind::ConditionContradiction,
            &format!("MOIETY:{subject}/CONDITION:{object}"),
        )
        .expect("condition target");
        assert!(matches!(condition, ReviewTarget::Condition { .. }));
    }
}
