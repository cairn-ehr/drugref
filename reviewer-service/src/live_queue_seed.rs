//! Rows the review queue's live (`#[ignore]`) test seeds for itself (#282). Kept
//! out of `queue.rs` so that file stays under the ~500-line guideline.

use sqlx::PgPool;
use uuid::Uuid;

/// Seed one MED-RT run with `interaction_rules` interaction rules and
/// `contradictions` condition contradictions, so `queue`'s live test also passes
/// on a freshly migrated database (#282). Like the other live tests' seeds, the rows
/// are uniquely keyed and left in place. That test's equalities compare the queue
/// with the authoritative gap views, so they hold whatever else the database holds.
pub async fn seed_queue(pool: &PgPool, interaction_rules: i32, contradictions: i32) {
    let seed = Uuid::new_v4().simple().to_string();
    let mut tx = pool.begin().await.expect("seed transaction");
    let run: i64 = sqlx::query_scalar(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, writer) \
         VALUES ('MED-RT', $1, 'deadbeef', 'medrt_run') RETURNING ingest_run_id",
    )
    .bind(format!("review-queue-test-{seed}"))
    .fetch_one(&mut *tx)
    .await
    .expect("test ingest run");
    // One MoA class with one member (the partner). Each subject below is
    // contraindicated with the class, so each is one rule reaching one partner.
    sqlx::query(
        "WITH class AS ( \
             INSERT INTO drugref.substance_class (class_uuid, source, source_code, \
                 class_name, concept_type, first_seen_ingest) \
             VALUES (gen_random_uuid(), 'MED-RT', 'N' || $2, 'Queue test class', 'MoA', $1) \
             RETURNING class_uuid), \
         partner AS ( \
             INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) \
             VALUES (gen_random_uuid(), 'Queue test partner', $1) RETURNING moiety_uuid), \
         member AS ( \
             INSERT INTO drugref.class_membership (moiety_uuid, class_uuid, relationship, ingest_run) \
             SELECT moiety_uuid, class_uuid, 'has_MoA', $1 FROM partner, class), \
         subject AS ( \
             INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) \
             SELECT gen_random_uuid(), 'Queue test subject ' || n, $1 \
             FROM generate_series(1, $3) n RETURNING moiety_uuid) \
         INSERT INTO drugref.class_contraindication (subject_moiety_uuid, \
             object_class_uuid, relationship, source, ingest_run) \
         SELECT moiety_uuid, class_uuid, 'CI_MoA', 'MED-RT', $1 FROM subject, class",
    )
    .bind(run)
    .bind(&seed)
    .bind(interaction_rules)
    .execute(&mut *tx)
    .await
    .expect("test interaction rules");
    // A drug both indicated and contraindicated for one condition is one
    // condition contradiction.
    sqlx::query(
        "WITH cond AS ( \
             INSERT INTO drugref.condition (condition_uuid, source, source_code, name, \
                 record_kind, first_seen_ingest) \
             VALUES (gen_random_uuid(), 'MeSH', 'D' || $2, 'Queue test condition', \
                 'DESCRIPTOR', $1) RETURNING condition_uuid), \
         subject AS ( \
             INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) \
             SELECT gen_random_uuid(), 'Queue test drug ' || n, $1 \
             FROM generate_series(1, $3) n RETURNING moiety_uuid), \
         indicated AS ( \
             INSERT INTO drugref.moiety_condition_indication (subject_moiety_uuid, \
                 object_condition_uuid, relationship, source, ingest_run) \
             SELECT moiety_uuid, condition_uuid, 'may_treat', 'MED-RT', $1 FROM subject, cond) \
         INSERT INTO drugref.moiety_condition_contraindication (subject_moiety_uuid, \
             object_condition_uuid, relationship, source, ingest_run) \
         SELECT moiety_uuid, condition_uuid, 'CI_with', 'MED-RT', $1 FROM subject, cond",
    )
    .bind(run)
    .bind(&seed)
    .bind(contradictions)
    .execute(&mut *tx)
    .await
    .expect("test condition contradictions");
    tx.commit().await.expect("commit queue seed");
}
