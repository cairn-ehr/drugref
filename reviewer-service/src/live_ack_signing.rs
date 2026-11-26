//! Live (`#[ignore]`) test of the GUI ack-signing batch (#284), moved out of
//! `ack_signing.rs` to keep that module under the ~500-line cap.

use axum::http::StatusCode;
use ed25519_dalek::{Signer, SigningKey};
use reviewer_domain::{
    AckSignature, AckSignatureQuery, AcknowledgeDepartureRequest, EnrolSigningKeyRequest,
    RuleGrain, SignatureStatus, SubmitAckSignaturesRequest,
};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::ack_signing::{challenges, submit, unsigned};
use crate::live_graded_seed::{live_pool, seed_gui_moiety_ruling};
use crate::signing::{encode_hex, enrol_key};

/// The GUI path end to end (#284): an acknowledged departure is listed unsigned,
/// a batch of two is prepared and signed with an enrolled key, and the acks then
/// read `signed`. A batch with one stale digest or one forged signature records
/// none of its signatures, and a retried batch is refused.
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
    assert_eq!(ours.signature_status, SignatureStatus::Unsigned);
    assert_eq!(ours.subject_name, "Subject");
    let ack_id = ours.ack_id;

    let reviewer_uuid = Uuid::new_v4();
    sqlx::query("INSERT INTO drugref.reviewer_account (reviewer_uuid, username) VALUES ($1, $2)")
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

    // The stayer leaves too: a second ack, so the batch can be half bad. A
    // keeper joins first: a memberless class records reviewed_against from a
    // FINISHED run (db/087), which a scratch database lacks.
    let keeper = Uuid::new_v4();
    sqlx::query(
        "WITH run AS (SELECT ingest_run_id FROM drugref.ingest_run \
                      WHERE upstream_release = $3), \
         moiety AS (INSERT INTO drugref.substance_moiety \
                    (moiety_uuid, display_name, first_seen_ingest) \
                    SELECT $1, 'Keeper', ingest_run_id FROM run RETURNING moiety_uuid) \
         INSERT INTO drugref.class_membership \
         (moiety_uuid, class_uuid, relationship, ingest_run) \
         SELECT moiety_uuid, $2, 'has_MoA', ingest_run_id FROM moiety, run",
    )
    .bind(keeper)
    .bind(seeded.object_class)
    .bind(&seeded.release)
    .execute(&pool)
    .await
    .expect("keeper joins");
    sqlx::query("DELETE FROM drugref.class_membership WHERE moiety_uuid = $1")
        .bind(seeded.stayer)
        .execute(&pool)
        .await
        .expect("stayer leaves");
    crate::departures::acknowledge(
        &pool,
        &AcknowledgeDepartureRequest {
            rule_grain: RuleGrain::MoietyRule,
            ruling_id,
            moiety_uuid: seeded.stayer,
            rationale: "Stayer reclassified upstream".into(),
        },
        "Ack Signer",
    )
    .await
    .expect("second acknowledgement");
    let second = unsigned(&pool)
        .await
        .expect("unsigned acks")
        .into_iter()
        .find(|ack| ack.partner_moiety_name == "Stayer" && ack.reviewed_by == "Ack Signer")
        .expect("the second ack is unsigned")
        .ack_id;

    let ids = vec![ack_id, second];
    let query = AckSignatureQuery {
        ack_ids: ids.clone(),
        key_fingerprint: enrolled.key_fingerprint.clone(),
    };
    let prepared = challenges(&pool, &query, reviewer_uuid)
        .await
        .expect("challenges");
    let payloads: Vec<Vec<u8>> = prepared
        .challenges
        .iter()
        .map(|c| c.canonical_payload().expect("payload"))
        .collect();
    let good = |slot: usize| AckSignature {
        ack_id: ids[slot],
        payload_digest: prepared.challenges[slot].payload_digest.clone(),
        signature_hex: encode_hex(&key.sign(&payloads[slot]).to_bytes()),
    };
    let request = |second: AckSignature| SubmitAckSignaturesRequest {
        query: query.clone(),
        signed_at: prepared.signed_at.clone(),
        signatures: vec![good(0), second],
    };
    let recorded = || async {
        sqlx::query_scalar::<_, i64>(
            "SELECT count(*) FROM drugref.assertion_signature \
             WHERE target_kind = 'graded_pair_departure_ack' AND target_id = ANY($1)",
        )
        .bind(&ids)
        .fetch_one(&pool)
        .await
        .expect("signature count")
    };

    // One bad slot after a good one records NOTHING: the batch is all or none.
    let stale = submit(
        &pool,
        &request(AckSignature {
            payload_digest: "0".repeat(64),
            ..good(1)
        }),
        reviewer_uuid,
    )
    .await
    .expect_err("a stale digest is refused");
    assert_eq!(stale.status(), StatusCode::CONFLICT);
    assert_eq!(recorded().await, 0);
    let forged = submit(
        &pool,
        &request(AckSignature {
            signature_hex: encode_hex(&key.sign(&payloads[0]).to_bytes()),
            ..good(1)
        }),
        reviewer_uuid,
    )
    .await
    .expect_err("a signature over other bytes is refused");
    assert_eq!(forged.status(), StatusCode::BAD_REQUEST);
    assert_eq!(recorded().await, 0);

    let done = submit(&pool, &request(good(1)), reviewer_uuid)
        .await
        .expect("signed");
    assert_eq!(done.recorded, 2);
    let again = submit(&pool, &request(good(1)), reviewer_uuid)
        .await
        .expect_err("a retried batch is refused");
    assert_eq!(again.status(), StatusCode::CONFLICT);
    assert_eq!(recorded().await, 2);
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
        .any(|ack| ids.contains(&ack.ack_id)));
}
