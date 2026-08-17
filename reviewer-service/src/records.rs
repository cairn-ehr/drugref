//! Append-only reviewer working notes and citation-only evidence references.

use chrono::{DateTime, Utc};
use reviewer_domain::{
    CreateAnnotationRequest, CreateEvidenceReferenceRequest, EvidenceReference,
    EvidenceReferenceScheme, ReviewAnnotation, ReviewKind, ReviewRecord, ReviewRecordQuery,
};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

use crate::AppError;

/// Database projection for one attributed annotation ledger row.
#[derive(FromRow)]
struct AnnotationRow {
    annotation_id: i64,
    reviewer_uuid: Uuid,
    username: String,
    reviewer_name: String,
    annotation_markdown: String,
    recorded_at: DateTime<Utc>,
}

/// Database projection for one attributed evidence-reference ledger row.
#[derive(FromRow)]
struct EvidenceReferenceRow {
    evidence_reference_id: i64,
    reviewer_uuid: Uuid,
    username: String,
    reviewer_name: String,
    reference_scheme: String,
    reference_value: String,
    note_markdown: String,
    recorded_at: DateTime<Utc>,
}

impl From<AnnotationRow> for ReviewAnnotation {
    /// Convert a database timestamp into the stable HTTP representation.
    fn from(row: AnnotationRow) -> Self {
        Self {
            annotation_id: row.annotation_id,
            reviewer_uuid: row.reviewer_uuid,
            username: row.username,
            reviewer_name: row.reviewer_name,
            annotation_markdown: row.annotation_markdown,
            recorded_at: row.recorded_at.to_rfc3339(),
        }
    }
}

impl TryFrom<EvidenceReferenceRow> for EvidenceReference {
    type Error = AppError;

    /// Parse the database's closed citation scheme into the shared wire enum.
    fn try_from(row: EvidenceReferenceRow) -> Result<Self, Self::Error> {
        Ok(Self {
            evidence_reference_id: row.evidence_reference_id,
            reviewer_uuid: row.reviewer_uuid,
            username: row.username,
            reviewer_name: row.reviewer_name,
            reference_scheme: parse_reference_scheme(&row.reference_scheme)?,
            reference_value: row.reference_value,
            note_markdown: row.note_markdown,
            recorded_at: row.recorded_at.to_rfc3339(),
        })
    }
}

/// Read all immutable working history for one current review target.
pub async fn load(pool: &PgPool, query: &ReviewRecordQuery) -> Result<ReviewRecord, AppError> {
    let question_uuid = resolve_question(pool, query.kind, &query.target_key).await?;
    let annotations = annotation_rows(pool, question_uuid)
        .await?
        .into_iter()
        .map(ReviewAnnotation::from)
        .collect();
    let evidence_references = evidence_reference_rows(pool, question_uuid)
        .await?
        .into_iter()
        .map(EvidenceReference::try_from)
        .collect::<Result<_, _>>()?;
    Ok(ReviewRecord {
        target_key: query.target_key.clone(),
        annotations,
        evidence_references,
    })
}

/// Append and return one authenticated reviewer's Markdown working note.
pub async fn create_annotation(
    pool: &PgPool,
    input: &CreateAnnotationRequest,
    reviewer_uuid: Uuid,
) -> Result<ReviewAnnotation, AppError> {
    let question_uuid = resolve_question(pool, input.kind, &input.target_key).await?;
    let annotation_id: i64 = sqlx::query_scalar(
        "INSERT INTO drugref.reviewer_annotation \
         (question_uuid, reviewer_uuid, annotation_markdown) VALUES ($1, $2, $3) \
         RETURNING reviewer_annotation_id",
    )
    .bind(question_uuid)
    .bind(reviewer_uuid)
    .bind(&input.annotation_markdown)
    .fetch_one(pool)
    .await?;
    Ok(annotation_by_id(pool, annotation_id).await?.into())
}

/// Append and return one authenticated reviewer's citation-only working reference.
pub async fn create_evidence_reference(
    pool: &PgPool,
    input: &CreateEvidenceReferenceRequest,
    reviewer_uuid: Uuid,
) -> Result<EvidenceReference, AppError> {
    let question_uuid = resolve_question(pool, input.kind, &input.target_key).await?;
    let evidence_reference_id: i64 = sqlx::query_scalar(
        "INSERT INTO drugref.reviewer_evidence_reference \
         (question_uuid, reviewer_uuid, reference_scheme, reference_value, note_markdown) \
         VALUES ($1, $2, $3, $4, $5) RETURNING reviewer_evidence_reference_id",
    )
    .bind(question_uuid)
    .bind(reviewer_uuid)
    .bind(input.reference_scheme.as_str())
    .bind(&input.reference_value)
    .bind(&input.note_markdown)
    .fetch_one(pool)
    .await?;
    evidence_reference_by_id(pool, evidence_reference_id)
        .await?
        .try_into()
}

/// Resolve a current canonical target through the registry's one UUID authority.
async fn resolve_question(
    pool: &PgPool,
    kind: ReviewKind,
    target_key: &str,
) -> Result<Uuid, AppError> {
    let question_uuid = sqlx::query_scalar(
        "SELECT question_uuid FROM drugref.open_question \
         WHERE gap_kind = $1 AND gap_key = $2 AND is_current",
    )
    .bind(gap_kind(kind))
    .bind(target_key)
    .fetch_optional(pool)
    .await?;
    question_uuid.ok_or_else(|| AppError::not_found("review target is no longer current"))
}

/// Return the open-question vocabulary associated with a GUI review kind.
fn gap_kind(kind: ReviewKind) -> &'static str {
    match kind {
        ReviewKind::InteractionRule => "uncurated_interaction_rule",
        ReviewKind::ConditionContradiction => "uncurated_condition_contradiction",
    }
}

/// Return all annotation rows for one question in insertion order.
async fn annotation_rows(
    pool: &PgPool,
    question_uuid: Uuid,
) -> Result<Vec<AnnotationRow>, AppError> {
    Ok(sqlx::query_as::<_, AnnotationRow>(
        "SELECT n.reviewer_annotation_id AS annotation_id, n.reviewer_uuid, \
         a.username, p.full_name AS reviewer_name, n.annotation_markdown, n.recorded_at \
         FROM drugref.reviewer_annotation n \
         JOIN drugref.reviewer_account a ON a.reviewer_uuid = n.reviewer_uuid \
         JOIN drugref.reviewer_profile p ON p.reviewer_uuid = n.reviewer_uuid \
           AND p.superseded_by IS NULL \
         WHERE n.question_uuid = $1 \
         ORDER BY n.recorded_at, n.reviewer_annotation_id",
    )
    .bind(question_uuid)
    .fetch_all(pool)
    .await?)
}

/// Return one attributed annotation row by its stable ledger identifier.
async fn annotation_by_id(pool: &PgPool, annotation_id: i64) -> Result<AnnotationRow, AppError> {
    Ok(sqlx::query_as::<_, AnnotationRow>(
        "SELECT n.reviewer_annotation_id AS annotation_id, n.reviewer_uuid, \
         a.username, p.full_name AS reviewer_name, n.annotation_markdown, n.recorded_at \
         FROM drugref.reviewer_annotation n \
         JOIN drugref.reviewer_account a ON a.reviewer_uuid = n.reviewer_uuid \
         JOIN drugref.reviewer_profile p ON p.reviewer_uuid = n.reviewer_uuid \
           AND p.superseded_by IS NULL \
         WHERE n.reviewer_annotation_id = $1",
    )
    .bind(annotation_id)
    .fetch_one(pool)
    .await?)
}

/// Return all working-reference rows for one question in insertion order.
async fn evidence_reference_rows(
    pool: &PgPool,
    question_uuid: Uuid,
) -> Result<Vec<EvidenceReferenceRow>, AppError> {
    Ok(sqlx::query_as::<_, EvidenceReferenceRow>(
        "SELECT e.reviewer_evidence_reference_id AS evidence_reference_id, \
         e.reviewer_uuid, a.username, p.full_name AS reviewer_name, \
         e.reference_scheme, e.reference_value, e.note_markdown, e.recorded_at \
         FROM drugref.reviewer_evidence_reference e \
         JOIN drugref.reviewer_account a ON a.reviewer_uuid = e.reviewer_uuid \
         JOIN drugref.reviewer_profile p ON p.reviewer_uuid = e.reviewer_uuid \
           AND p.superseded_by IS NULL \
         WHERE e.question_uuid = $1 \
         ORDER BY e.recorded_at, e.reviewer_evidence_reference_id",
    )
    .bind(question_uuid)
    .fetch_all(pool)
    .await?)
}

/// Return one attributed working-reference row by its stable ledger identifier.
async fn evidence_reference_by_id(
    pool: &PgPool,
    evidence_reference_id: i64,
) -> Result<EvidenceReferenceRow, AppError> {
    Ok(sqlx::query_as::<_, EvidenceReferenceRow>(
        "SELECT e.reviewer_evidence_reference_id AS evidence_reference_id, \
         e.reviewer_uuid, a.username, p.full_name AS reviewer_name, \
         e.reference_scheme, e.reference_value, e.note_markdown, e.recorded_at \
         FROM drugref.reviewer_evidence_reference e \
         JOIN drugref.reviewer_account a ON a.reviewer_uuid = e.reviewer_uuid \
         JOIN drugref.reviewer_profile p ON p.reviewer_uuid = e.reviewer_uuid \
           AND p.superseded_by IS NULL \
         WHERE e.reviewer_evidence_reference_id = $1",
    )
    .bind(evidence_reference_id)
    .fetch_one(pool)
    .await?)
}

/// Parse every evidence-reference spelling admitted by db/045.
fn parse_reference_scheme(value: &str) -> Result<EvidenceReferenceScheme, AppError> {
    match value {
        "DOI" => Ok(EvidenceReferenceScheme::DOI),
        "PMID" => Ok(EvidenceReferenceScheme::PMID),
        "PMCID" => Ok(EvidenceReferenceScheme::PMCID),
        "NCT" => Ok(EvidenceReferenceScheme::NCT),
        "SPL" => Ok(EvidenceReferenceScheme::SPL),
        "URL" => Ok(EvidenceReferenceScheme::URL),
        value => Err(AppError::internal(format!(
            "unknown evidence reference scheme {value}"
        ))),
    }
}

#[cfg(test)]
mod tests {
    use super::{
        create_annotation, create_evidence_reference, gap_kind, load, parse_reference_scheme,
    };
    use reviewer_domain::{
        CreateAnnotationRequest, CreateEvidenceReferenceRequest, EvidenceReferenceScheme,
        ReviewKind, ReviewRecordQuery,
    };
    use sqlx::postgres::PgPoolOptions;
    use uuid::Uuid;

    /// Pin the GUI-to-registry kind mapping used for target resolution.
    #[test]
    fn review_kinds_resolve_to_existing_question_vocabularies() {
        assert_eq!(
            gap_kind(ReviewKind::InteractionRule),
            "uncurated_interaction_rule"
        );
        assert_eq!(
            gap_kind(ReviewKind::ConditionContradiction),
            "uncurated_condition_contradiction"
        );
    }

    /// Pin every db/045 citation scheme at the service boundary.
    #[test]
    fn database_reference_schemes_are_exhaustive() {
        assert_eq!(
            parse_reference_scheme("PMID").expect("PMID scheme"),
            EvidenceReferenceScheme::PMID
        );
        assert!(parse_reference_scheme("OTHER").is_err());
    }

    /// Exercise target resolution, attributed inserts, and history reads in PostgreSQL.
    #[tokio::test]
    #[ignore = "requires a migrated Drugref PostgreSQL database"]
    async fn live_working_record_round_trip() {
        let database_url = std::env::var("DRUGREF_REVIEW_TEST_DATABASE_URL")
            .expect("DRUGREF_REVIEW_TEST_DATABASE_URL must name a migrated database");
        let pool = PgPoolOptions::new()
            .max_connections(1)
            .connect(&database_url)
            .await
            .expect("review working-record test database");
        let reviewer_uuid = Uuid::new_v4();
        let subject_uuid = Uuid::new_v4();
        let object_uuid = Uuid::new_v4();
        let question_uuid = Uuid::new_v4();
        let username = format!("reviewer_{}", reviewer_uuid.simple());
        let run_id: i64 = sqlx::query_scalar(
            "INSERT INTO drugref.ingest_run \
             (source, upstream_release, source_checksum, writer) \
             VALUES ('MED-RT', 'reviewer-record-test', 'deadbeef', 'medrt_run') \
             RETURNING ingest_run_id",
        )
        .fetch_one(&pool)
        .await
        .expect("test ingest run");
        sqlx::query(
            "INSERT INTO drugref.reviewer_account (reviewer_uuid, username) VALUES ($1, $2)",
        )
        .bind(reviewer_uuid)
        .bind(&username)
        .execute(&pool)
        .await
        .expect("test reviewer account");
        sqlx::query(
            "INSERT INTO drugref.reviewer_profile \
             (reviewer_uuid, full_name, role, active, recorded_by) \
             VALUES ($1, 'Test Reviewer', 'reviewer', true, $1)",
        )
        .bind(reviewer_uuid)
        .execute(&pool)
        .await
        .expect("test reviewer profile");
        let target_key = format!("MOIETY:{subject_uuid}/CLASS:{object_uuid}/CI_AXIS:CI_with");
        sqlx::query(
            "INSERT INTO drugref.open_question \
             (question_uuid, gap_kind, gap_key, question_text, first_derived_ingest, \
              last_derived_ingest) \
             VALUES ($1, 'uncurated_interaction_rule', $2, 'Test question', $3, $3)",
        )
        .bind(question_uuid)
        .bind(&target_key)
        .bind(run_id)
        .execute(&pool)
        .await
        .expect("test open question");

        create_annotation(
            &pool,
            &CreateAnnotationRequest {
                kind: ReviewKind::InteractionRule,
                target_key: target_key.clone(),
                annotation_markdown: "Working note".into(),
            },
            reviewer_uuid,
        )
        .await
        .expect("append annotation");
        create_evidence_reference(
            &pool,
            &CreateEvidenceReferenceRequest {
                kind: ReviewKind::InteractionRule,
                target_key: target_key.clone(),
                reference_scheme: EvidenceReferenceScheme::PMID,
                reference_value: "12345678".into(),
                note_markdown: "Primary study".into(),
            },
            reviewer_uuid,
        )
        .await
        .expect("append evidence reference");
        let record = load(
            &pool,
            &ReviewRecordQuery {
                kind: ReviewKind::InteractionRule,
                target_key,
            },
        )
        .await
        .expect("load working history");

        assert_eq!(record.annotations.len(), 1);
        assert_eq!(record.evidence_references.len(), 1);
        assert_eq!(record.annotations[0].reviewer_uuid, reviewer_uuid);
        assert_eq!(
            record.evidence_references[0].reference_scheme,
            EvidenceReferenceScheme::PMID
        );
    }
}
