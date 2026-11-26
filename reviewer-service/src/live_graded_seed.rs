//! Test-only seed shared by the live graded-pair tests (#281, #283): one GUI
//! moiety-grain ruling, recorded through the real decision write path, that grades
//! the pairs (subject, partner) and (subject, stayer).

use reviewer_domain::{
    CreateReviewDecisionRequest, EvidenceGrade, ReviewDecision, ReviewKind, Severity,
};
use sqlx::PgPool;
use uuid::Uuid;

use crate::decisions::create;

/// What `seed_gui_moiety_ruling` wrote.
pub(crate) struct SeededRuling {
    /// The ruling's subject drug.
    pub subject: Uuid,
    /// A member of the object class (a test makes this one leave).
    pub partner: Uuid,
    /// A second member of the object class that stays.
    pub stayer: Uuid,
    /// The object class.
    pub object_class: Uuid,
    /// The unfinished MED-RT run holding every seeded row.
    pub release: String,
}

/// Connect to `DRUGREF_REVIEW_TEST_DATABASE_URL` (a migrated database).
pub(crate) async fn live_pool() -> PgPool {
    let database_url = std::env::var("DRUGREF_REVIEW_TEST_DATABASE_URL")
        .expect("DRUGREF_REVIEW_TEST_DATABASE_URL must name a migrated database");
    sqlx::postgres::PgPoolOptions::new()
        .max_connections(2)
        .connect(&database_url)
        .await
        .expect("live test database")
}

/// Seed one candidate S -> class C (members P and T) and rule it `applies` /
/// `major` through `decisions::create`. Its COMMIT fires db/083's refresh, so both
/// pairs are observed when this returns. All names are unique per call.
pub(crate) async fn seed_gui_moiety_ruling(pool: &PgPool) -> SeededRuling {
    let (subject, partner, stayer, object_class, question) = (
        Uuid::new_v4(),
        Uuid::new_v4(),
        Uuid::new_v4(),
        Uuid::new_v4(),
        Uuid::new_v4(),
    );
    let release = format!("graded-pair-test-{question}");
    let target_key = format!("MOIETY:{subject}/CLASS:{object_class}/CI_AXIS:CI_MoA");
    let run_id: i64 = sqlx::query_scalar(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, writer) \
         VALUES ('MED-RT', $1, 'deadbeef', 'medrt_run') RETURNING ingest_run_id",
    )
    .bind(&release)
    .fetch_one(pool)
    .await
    .expect("test ingest run");
    for (uuid, name) in [
        (subject, "Subject"),
        (partner, "Partner"),
        (stayer, "Stayer"),
    ] {
        sqlx::query(
            "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) \
             VALUES ($1, $2, $3)",
        )
        .bind(uuid)
        .bind(name)
        .bind(run_id)
        .execute(pool)
        .await
        .expect("test moiety");
    }
    sqlx::query(
        "INSERT INTO drugref.substance_class \
         (class_uuid, source, source_code, class_name, concept_type, first_seen_ingest) \
         VALUES ($1, 'MED-RT', $2, 'Test class', 'MoA', $3)",
    )
    .bind(object_class)
    .bind(format!("N{}", object_class.simple()))
    .bind(run_id)
    .execute(pool)
    .await
    .expect("test class");
    // Membership of the object class is what makes each pair gradable.
    for member in [partner, stayer] {
        sqlx::query(
            "INSERT INTO drugref.class_membership (moiety_uuid, class_uuid, relationship, ingest_run) \
             VALUES ($1, $2, 'has_MoA', $3)",
        )
        .bind(member)
        .bind(object_class)
        .bind(run_id)
        .execute(pool)
        .await
        .expect("test membership");
    }
    sqlx::query(
        "INSERT INTO drugref.class_contraindication \
         (subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run) \
         VALUES ($1, $2, 'CI_MoA', 'MED-RT', $3)",
    )
    .bind(subject)
    .bind(object_class)
    .bind(run_id)
    .execute(pool)
    .await
    .expect("test candidate");
    sqlx::query(
        "INSERT INTO drugref.open_question (question_uuid, gap_kind, gap_key, question_text, \
         first_derived_ingest, last_derived_ingest) \
         VALUES ($1, 'uncurated_interaction_rule', $2, 'Test decision?', $3, $3)",
    )
    .bind(question)
    .bind(&target_key)
    .bind(run_id)
    .execute(pool)
    .await
    .expect("test question");
    create(
        pool,
        &CreateReviewDecisionRequest {
            kind: ReviewKind::InteractionRule,
            target_key,
            decision: ReviewDecision::Applies,
            severity: Some(Severity::Major),
            mechanism: Some("Test mechanism".into()),
            management: Some("Avoid combination".into()),
            evidence_grade: Some(EvidenceGrade::Probable),
            expected_revision_id: None,
        },
        "Test Reviewer",
    )
    .await
    .expect("GUI ruling");
    SeededRuling {
        subject,
        partner,
        stayer,
        object_class,
        release,
    }
}
