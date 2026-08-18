//! Transactional append-only clinical decision revisions.

use chrono::{DateTime, Utc};
use reviewer_domain::{
    CreateReviewDecisionRequest, EvidenceGrade, ReviewDecision, ReviewDecisionRecord,
    ReviewDecisionRevision, ReviewRecordQuery, Severity,
};
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use uuid::Uuid;

use crate::{
    decision_targets::{
        lock_target, parse_target, predecessor_release, read_target, resolve_question,
        reviewed_against, ReviewTarget,
    },
    AppError,
};

/// Shared projection of either curated overlay table's immutable history.
#[derive(FromRow)]
struct RevisionRow {
    revision_id: i64,
    decision: String,
    severity: Option<String>,
    mechanism: Option<String>,
    management: Option<String>,
    evidence_grade: Option<String>,
    question_uuid: Option<Uuid>,
    reviewed_by: String,
    reviewed_against: String,
    reviewed_at: DateTime<Utc>,
    superseded_by: Option<i64>,
    signature_status: String,
}

impl TryFrom<RevisionRow> for ReviewDecisionRevision {
    type Error = AppError;

    /// Parse database vocabularies and timestamps into the stable wire contract.
    fn try_from(row: RevisionRow) -> Result<Self, Self::Error> {
        Ok(Self {
            revision_id: row.revision_id,
            decision: parse_decision(&row.decision)?,
            severity: row.severity.as_deref().map(parse_severity).transpose()?,
            mechanism: row.mechanism,
            management: row.management,
            evidence_grade: row
                .evidence_grade
                .as_deref()
                .map(parse_evidence_grade)
                .transpose()?,
            question_uuid: row.question_uuid,
            reviewed_by: row.reviewed_by,
            reviewed_against: row.reviewed_against,
            reviewed_at: row.reviewed_at.to_rfc3339(),
            superseded_by: row.superseded_by,
            signature_status: row.signature_status,
        })
    }
}

/// Load all curated revision history for one canonical target.
pub async fn load(
    pool: &PgPool,
    query: &ReviewRecordQuery,
) -> Result<ReviewDecisionRecord, AppError> {
    let target = read_target(pool, query).await?;
    let history = load_history(pool, &target).await?;
    build_record(query.target_key.clone(), history)
}

/// Atomically append a revision after checking the predecessor observed by the GUI.
pub async fn create(
    pool: &PgPool,
    input: &CreateReviewDecisionRequest,
    reviewed_by: &str,
) -> Result<ReviewDecisionRecord, AppError> {
    let target = parse_target(input.kind, &input.target_key)?;
    let mut transaction = pool.begin().await?;
    lock_target(&mut transaction, &input.target_key).await?;
    let question_uuid = resolve_question(&mut transaction, input.kind, &input.target_key).await?;
    let current_revision_id = current_revision(&mut transaction, &target).await?;
    if current_revision_id != input.expected_revision_id {
        return Err(AppError::conflict(
            "decision changed since this form was loaded; reload its history",
        ));
    }
    let reviewed_against = reviewed_against(&mut transaction, &target).await?;
    let reviewed_against = match reviewed_against {
        Some(value) => value,
        None => predecessor_release(&mut transaction, &target)
            .await?
            .ok_or_else(|| {
                AppError::conflict("review target no longer has candidate release provenance")
            })?,
    };
    let new_id = insert_revision(
        &mut transaction,
        &target,
        input,
        question_uuid,
        reviewed_by,
        &reviewed_against,
    )
    .await?;
    if let Some(previous_id) = current_revision_id {
        supersede(&mut transaction, &target, previous_id, new_id).await?;
    }
    let history = load_history_transaction(&mut transaction, &target).await?;
    let record = build_record(input.target_key.clone(), history)?;
    transaction.commit().await?;
    Ok(record)
}

/// Read the live predecessor under the target's transaction lock.
async fn current_revision(
    transaction: &mut Transaction<'_, Postgres>,
    target: &ReviewTarget,
) -> Result<Option<i64>, AppError> {
    match target {
        ReviewTarget::Interaction {
            subject_uuid,
            object_uuid,
            relationship,
        } => Ok(sqlx::query_scalar(
            "SELECT curated_interaction_id FROM drugref.curated_interaction \
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
            "SELECT curated_condition_id FROM drugref.curated_condition \
             WHERE subject_moiety_uuid = $1 AND object_condition_uuid = $2 \
             AND superseded_by IS NULL",
        )
        .bind(subject_uuid)
        .bind(object_uuid)
        .fetch_optional(&mut **transaction)
        .await?),
    }
}

/// Insert the target-specific overlay row and return its stable identifier.
async fn insert_revision(
    transaction: &mut Transaction<'_, Postgres>,
    target: &ReviewTarget,
    input: &CreateReviewDecisionRequest,
    question_uuid: Uuid,
    reviewed_by: &str,
    reviewed_against: &str,
) -> Result<i64, AppError> {
    match target {
        ReviewTarget::Interaction {
            subject_uuid,
            object_uuid,
            relationship,
        } => {
            let applies = match input.decision {
                ReviewDecision::Applies => true,
                ReviewDecision::DoesNotApply => false,
                _ => return Err(AppError::bad_request("invalid interaction decision")),
            };
            Ok(sqlx::query_scalar(
                "INSERT INTO drugref.curated_interaction \
                 (subject_moiety_uuid, object_class_uuid, relationship, applies, severity, \
                  mechanism, management, evidence_grade, question_uuid, source, reviewed_by, \
                  reviewed_against) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, \
                  'DRUGREF', $10, $11) RETURNING curated_interaction_id",
            )
            .bind(subject_uuid)
            .bind(object_uuid)
            .bind(relationship)
            .bind(applies)
            .bind(input.severity.map(Severity::as_str))
            .bind(input.mechanism.as_deref())
            .bind(input.management.as_deref())
            .bind(input.evidence_grade.map(EvidenceGrade::as_str))
            .bind(question_uuid)
            .bind(reviewed_by)
            .bind(reviewed_against)
            .fetch_one(&mut **transaction)
            .await?)
        }
        ReviewTarget::Condition {
            subject_uuid,
            object_uuid,
        } => Ok(sqlx::query_scalar(
            "INSERT INTO drugref.curated_condition \
             (subject_moiety_uuid, object_condition_uuid, ruling, severity, mechanism, \
              management, evidence_grade, question_uuid, source, reviewed_by, \
              reviewed_against) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'DRUGREF', $9, $10) \
             RETURNING curated_condition_id",
        )
        .bind(subject_uuid)
        .bind(object_uuid)
        .bind(condition_ruling(input.decision)?)
        .bind(input.severity.map(Severity::as_str))
        .bind(input.mechanism.as_deref())
        .bind(input.management.as_deref())
        .bind(input.evidence_grade.map(EvidenceGrade::as_str))
        .bind(question_uuid)
        .bind(reviewed_by)
        .bind(reviewed_against)
        .fetch_one(&mut **transaction)
        .await?),
    }
}

/// Return the database ruling spelling for a validated condition decision.
fn condition_ruling(decision: ReviewDecision) -> Result<&'static str, AppError> {
    match decision {
        ReviewDecision::Contraindicated => Ok("contraindicated"),
        ReviewDecision::Indicated => Ok("indicated"),
        ReviewDecision::ContextDependent => Ok("context_dependent"),
        ReviewDecision::Spurious => Ok("spurious"),
        _ => Err(AppError::bad_request("invalid condition decision")),
    }
}

/// Point exactly the observed predecessor at its later immutable revision.
async fn supersede(
    transaction: &mut Transaction<'_, Postgres>,
    target: &ReviewTarget,
    previous_id: i64,
    new_id: i64,
) -> Result<(), AppError> {
    let result = match target {
        ReviewTarget::Interaction { .. } => {
            sqlx::query(
                "UPDATE drugref.curated_interaction SET superseded_by = $1 \
             WHERE curated_interaction_id = $2 AND superseded_by IS NULL",
            )
            .bind(new_id)
            .bind(previous_id)
            .execute(&mut **transaction)
            .await?
        }
        ReviewTarget::Condition { .. } => {
            sqlx::query(
                "UPDATE drugref.curated_condition SET superseded_by = $1 \
             WHERE curated_condition_id = $2 AND superseded_by IS NULL",
            )
            .bind(new_id)
            .bind(previous_id)
            .execute(&mut **transaction)
            .await?
        }
    };
    if result.rows_affected() != 1 {
        return Err(AppError::internal(
            "decision predecessor could not be superseded",
        ));
    }
    Ok(())
}

/// Load history through the pool for a read-only request.
async fn load_history(pool: &PgPool, target: &ReviewTarget) -> Result<Vec<RevisionRow>, AppError> {
    match target {
        ReviewTarget::Interaction {
            subject_uuid,
            object_uuid,
            relationship,
        } => Ok(sqlx::query_as::<_, RevisionRow>(interaction_history_sql())
            .bind(subject_uuid)
            .bind(object_uuid)
            .bind(relationship)
            .fetch_all(pool)
            .await?),
        ReviewTarget::Condition {
            subject_uuid,
            object_uuid,
        } => Ok(sqlx::query_as::<_, RevisionRow>(condition_history_sql())
            .bind(subject_uuid)
            .bind(object_uuid)
            .fetch_all(pool)
            .await?),
    }
}

/// Load history inside the write transaction before its commit.
async fn load_history_transaction(
    transaction: &mut Transaction<'_, Postgres>,
    target: &ReviewTarget,
) -> Result<Vec<RevisionRow>, AppError> {
    match target {
        ReviewTarget::Interaction {
            subject_uuid,
            object_uuid,
            relationship,
        } => Ok(sqlx::query_as::<_, RevisionRow>(interaction_history_sql())
            .bind(subject_uuid)
            .bind(object_uuid)
            .bind(relationship)
            .fetch_all(&mut **transaction)
            .await?),
        ReviewTarget::Condition {
            subject_uuid,
            object_uuid,
        } => Ok(sqlx::query_as::<_, RevisionRow>(condition_history_sql())
            .bind(subject_uuid)
            .bind(object_uuid)
            .fetch_all(&mut **transaction)
            .await?),
    }
}

/// Return the shared interaction-history projection.
fn interaction_history_sql() -> &'static str {
    "SELECT c.curated_interaction_id AS revision_id, \
            CASE WHEN c.applies THEN 'applies' ELSE 'does_not_apply' END AS decision, \
            c.severity, c.mechanism, c.management, c.evidence_grade, c.question_uuid, \
            c.reviewed_by, c.reviewed_against, c.reviewed_at, c.superseded_by, \
            coalesce(s.signature_status, 'unsigned') AS signature_status \
     FROM drugref.curated_interaction c LEFT JOIN drugref.curated_signature_status s \
       ON s.target_kind = 'curated_interaction' AND s.target_id = c.curated_interaction_id \
     WHERE c.subject_moiety_uuid = $1 AND c.object_class_uuid = $2 AND c.relationship = $3 \
     ORDER BY c.reviewed_at, c.curated_interaction_id"
}

/// Return the shared condition-history projection.
fn condition_history_sql() -> &'static str {
    "SELECT c.curated_condition_id AS revision_id, c.ruling AS decision, c.severity, \
            c.mechanism, c.management, c.evidence_grade, c.question_uuid, c.reviewed_by, \
            c.reviewed_against, c.reviewed_at, c.superseded_by, \
            coalesce(s.signature_status, 'unsigned') AS signature_status \
     FROM drugref.curated_condition c LEFT JOIN drugref.curated_signature_status s \
       ON s.target_kind = 'curated_condition' AND s.target_id = c.curated_condition_id \
     WHERE c.subject_moiety_uuid = $1 AND c.object_condition_uuid = $2 \
     ORDER BY c.reviewed_at, c.curated_condition_id"
}

/// Convert database rows into one target history and identify its live revision.
fn build_record(
    target_key: String,
    rows: Vec<RevisionRow>,
) -> Result<ReviewDecisionRecord, AppError> {
    let history = rows
        .into_iter()
        .map(ReviewDecisionRevision::try_from)
        .collect::<Result<Vec<_>, _>>()?;
    let current_revision_id = history
        .iter()
        .find(|revision| revision.superseded_by.is_none())
        .map(|revision| revision.revision_id);
    Ok(ReviewDecisionRecord {
        target_key,
        current_revision_id,
        history,
    })
}

/// Parse every clinical decision spelling emitted by the two overlay tables.
fn parse_decision(value: &str) -> Result<ReviewDecision, AppError> {
    match value {
        "applies" => Ok(ReviewDecision::Applies),
        "does_not_apply" => Ok(ReviewDecision::DoesNotApply),
        "contraindicated" => Ok(ReviewDecision::Contraindicated),
        "indicated" => Ok(ReviewDecision::Indicated),
        "context_dependent" => Ok(ReviewDecision::ContextDependent),
        "spurious" => Ok(ReviewDecision::Spurious),
        value => Err(AppError::internal(format!(
            "unknown clinical decision {value}"
        ))),
    }
}

/// Parse every severity admitted by the database vocabulary table.
fn parse_severity(value: &str) -> Result<Severity, AppError> {
    match value {
        "contraindicated" => Ok(Severity::Contraindicated),
        "major" => Ok(Severity::Major),
        "moderate" => Ok(Severity::Moderate),
        "minor" => Ok(Severity::Minor),
        value => Err(AppError::internal(format!("unknown severity {value}"))),
    }
}

/// Parse every evidence grade admitted by the curated overlay.
fn parse_evidence_grade(value: &str) -> Result<EvidenceGrade, AppError> {
    match value {
        "established" => Ok(EvidenceGrade::Established),
        "probable" => Ok(EvidenceGrade::Probable),
        "suspected" => Ok(EvidenceGrade::Suspected),
        "theoretical" => Ok(EvidenceGrade::Theoretical),
        value => Err(AppError::internal(format!(
            "unknown evidence grade {value}"
        ))),
    }
}

#[cfg(test)]
mod tests {
    use super::{condition_ruling, create, load, parse_decision};
    use reviewer_domain::{
        CreateReviewDecisionRequest, EvidenceGrade, ReviewDecision, ReviewKind, ReviewRecordQuery,
        Severity,
    };
    use sqlx::postgres::PgPoolOptions;
    use uuid::Uuid;

    /// Keep request and database decision spellings exhaustive in one focused test.
    #[test]
    fn database_decision_spellings_round_trip() {
        assert_eq!(
            condition_ruling(ReviewDecision::ContextDependent).expect("condition ruling"),
            "context_dependent"
        );
        assert_eq!(
            parse_decision("does_not_apply").expect("interaction decision"),
            ReviewDecision::DoesNotApply
        );
        assert!(parse_decision("unknown").is_err());
    }

    /// Exercise initial write, correction, history, and stale-form refusal in PostgreSQL.
    #[tokio::test]
    #[ignore = "requires a migrated Drugref PostgreSQL database"]
    async fn live_decision_revision_round_trip() {
        let database_url = std::env::var("DRUGREF_REVIEW_TEST_DATABASE_URL")
            .expect("DRUGREF_REVIEW_TEST_DATABASE_URL must name a migrated database");
        let pool = PgPoolOptions::new()
            .max_connections(2)
            .connect(&database_url)
            .await
            .expect("review decision test database");
        let subject_uuid = Uuid::new_v4();
        let object_uuid = Uuid::new_v4();
        let question_uuid = Uuid::new_v4();
        let target_key = format!("MOIETY:{subject_uuid}/CLASS:{object_uuid}/CI_AXIS:CI_MoA");
        let release = format!("review-decision-test-{question_uuid}");
        let run_id: i64 = sqlx::query_scalar(
            "INSERT INTO drugref.ingest_run \
             (source, upstream_release, source_checksum, writer) \
             VALUES ('MED-RT', $1, 'deadbeef', 'medrt_run') \
             RETURNING ingest_run_id",
        )
        .bind(&release)
        .fetch_one(&pool)
        .await
        .expect("test ingest run");
        sqlx::query(
            "INSERT INTO drugref.substance_moiety \
             (moiety_uuid, display_name, first_seen_ingest) VALUES ($1, 'Test moiety', $2)",
        )
        .bind(subject_uuid)
        .bind(run_id)
        .execute(&pool)
        .await
        .expect("test moiety");
        sqlx::query(
            "INSERT INTO drugref.substance_class \
             (class_uuid, source, source_code, class_name, concept_type, first_seen_ingest) \
             VALUES ($1, 'MED-RT', $2, 'Test class', 'MoA', $3)",
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
             VALUES ($1, $2, 'CI_MoA', 'MED-RT', $3)",
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
              'Test decision?', $3, $3)",
        )
        .bind(question_uuid)
        .bind(&target_key)
        .bind(run_id)
        .execute(&pool)
        .await
        .expect("test question");

        let first_input = CreateReviewDecisionRequest {
            kind: ReviewKind::InteractionRule,
            target_key: target_key.clone(),
            decision: ReviewDecision::Applies,
            severity: Some(Severity::Major),
            mechanism: Some("Test mechanism".into()),
            management: Some("Avoid combination".into()),
            evidence_grade: Some(EvidenceGrade::Probable),
            expected_revision_id: None,
        };
        let first = create(&pool, &first_input, "Test Reviewer")
            .await
            .expect("initial revision");
        assert_eq!(first.history.len(), 1);
        assert_eq!(first.history[0].reviewed_against, release);
        let first_id = first.current_revision_id.expect("live first revision");

        let correction = CreateReviewDecisionRequest {
            severity: Some(Severity::Moderate),
            expected_revision_id: Some(first_id),
            ..first_input.clone()
        };
        let second = create(&pool, &correction, "Test Reviewer")
            .await
            .expect("corrected revision");
        assert_eq!(second.history.len(), 2);
        assert_eq!(second.history[0].superseded_by, second.current_revision_id);
        assert!(create(&pool, &first_input, "Stale Reviewer").await.is_err());

        let loaded = load(
            &pool,
            &ReviewRecordQuery {
                kind: ReviewKind::InteractionRule,
                target_key,
            },
        )
        .await
        .expect("decision history");
        assert_eq!(loaded.history.len(), 2);

        let condition_uuid = Uuid::new_v4();
        let condition_question_uuid = Uuid::new_v4();
        let condition_target_key = format!("MOIETY:{subject_uuid}/CONDITION:{condition_uuid}");
        sqlx::query(
            "INSERT INTO drugref.condition \
             (condition_uuid, source, source_code, name, record_kind, first_seen_ingest) \
             VALUES ($1, 'MeSH', $2, 'Test condition', 'DESCRIPTOR', $3)",
        )
        .bind(condition_uuid)
        .bind(format!("D{}", condition_uuid.simple()))
        .bind(run_id)
        .execute(&pool)
        .await
        .expect("test condition");
        sqlx::query(
            "INSERT INTO drugref.moiety_condition_contraindication \
             (subject_moiety_uuid, object_condition_uuid, relationship, source, ingest_run) \
             VALUES ($1, $2, 'CI_with', 'MED-RT', $3)",
        )
        .bind(subject_uuid)
        .bind(condition_uuid)
        .bind(run_id)
        .execute(&pool)
        .await
        .expect("test condition contraindication");
        sqlx::query(
            "INSERT INTO drugref.moiety_condition_indication \
             (subject_moiety_uuid, object_condition_uuid, relationship, source, ingest_run) \
             VALUES ($1, $2, 'may_treat', 'MED-RT', $3)",
        )
        .bind(subject_uuid)
        .bind(condition_uuid)
        .bind(run_id)
        .execute(&pool)
        .await
        .expect("test condition indication");
        sqlx::query(
            "INSERT INTO drugref.open_question \
             (question_uuid, gap_kind, gap_key, question_text, first_derived_ingest, \
              last_derived_ingest) VALUES ($1, 'uncurated_condition_contradiction', $2, \
              'Test condition decision?', $3, $3)",
        )
        .bind(condition_question_uuid)
        .bind(&condition_target_key)
        .bind(run_id)
        .execute(&pool)
        .await
        .expect("test condition question");
        let condition = create(
            &pool,
            &CreateReviewDecisionRequest {
                kind: ReviewKind::ConditionContradiction,
                target_key: condition_target_key,
                decision: ReviewDecision::ContextDependent,
                severity: Some(Severity::Major),
                mechanism: Some("Clinical context changes the balance".into()),
                management: Some("Assess the current clinical state".into()),
                evidence_grade: Some(EvidenceGrade::Established),
                expected_revision_id: None,
            },
            "Test Reviewer",
        )
        .await
        .expect("condition revision");
        assert_eq!(condition.history.len(), 1);
        assert_eq!(
            condition.history[0].decision,
            ReviewDecision::ContextDependent
        );
    }
}
