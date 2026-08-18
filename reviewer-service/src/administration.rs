//! Append-only reviewer profile, password, and session administration.

use reviewer_domain::{AccountAdministrationResult, ReviewerRole, UpdateReviewerProfileRequest};
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use uuid::Uuid;

use crate::{
    error::AppError,
    store::{account_by_uuid_tx, ADMINISTRATION_LOCK},
};

/// Current profile fields locked while an append-only correction is recorded.
#[derive(FromRow)]
struct CurrentProfileRow {
    reviewer_profile_id: i64,
    full_name: String,
    qualifications: String,
    bio_markdown: String,
    role: String,
    active: bool,
}

/// Append a complete profile correction with stale-write and last-admin guards.
pub async fn update_user_profile(
    pool: &PgPool,
    reviewer_uuid: Uuid,
    input: &UpdateReviewerProfileRequest,
    actor: Uuid,
) -> Result<AccountAdministrationResult, AppError> {
    let mut transaction = pool.begin().await?;
    lock_and_require_administrator(&mut transaction, actor).await?;
    let current = current_profile_for_update(&mut transaction, reviewer_uuid).await?;
    if current.reviewer_profile_id != input.expected_profile_revision_id {
        return Err(AppError::conflict(
            "reviewer profile changed; reload before recording this correction",
        ));
    }
    if current.full_name == input.full_name
        && current.qualifications == input.qualifications
        && current.bio_markdown == input.bio_markdown
        && current.role == input.role.as_str()
        && current.active == input.active
    {
        return Err(AppError::conflict("reviewer profile has no changes"));
    }
    if current.role == ReviewerRole::Administrator.as_str()
        && current.active
        && (input.role != ReviewerRole::Administrator || !input.active)
    {
        let other_active_administrators: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM drugref.reviewer_profile \
             WHERE role = 'administrator' AND active AND superseded_by IS NULL \
               AND reviewer_uuid <> $1",
        )
        .bind(reviewer_uuid)
        .fetch_one(&mut *transaction)
        .await?;
        if other_active_administrators == 0 {
            return Err(AppError::conflict(
                "the last active administrator cannot be disabled or demoted",
            ));
        }
    }

    let replacement_id: i64 = sqlx::query_scalar(
        "INSERT INTO drugref.reviewer_profile \
         (reviewer_uuid, full_name, qualifications, bio_markdown, role, active, recorded_by) \
         VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING reviewer_profile_id",
    )
    .bind(reviewer_uuid)
    .bind(&input.full_name)
    .bind(&input.qualifications)
    .bind(&input.bio_markdown)
    .bind(input.role.as_str())
    .bind(input.active)
    .bind(actor)
    .fetch_one(&mut *transaction)
    .await?;
    sqlx::query(
        "UPDATE drugref.reviewer_profile SET superseded_by = $1 \
         WHERE reviewer_profile_id = $2",
    )
    .bind(replacement_id)
    .bind(current.reviewer_profile_id)
    .execute(&mut *transaction)
    .await?;

    let revoked_session_count = if current.active && !input.active {
        revoke_live_sessions(&mut transaction, reviewer_uuid, actor, "administrative").await?
    } else {
        0
    };
    let reviewer = account_by_uuid_tx(&mut transaction, reviewer_uuid).await?;
    transaction.commit().await?;
    Ok(AccountAdministrationResult {
        reviewer,
        revoked_session_count,
    })
}

/// Append a password credential and revoke every session authenticated by its predecessor.
pub async fn rotate_user_password(
    pool: &PgPool,
    reviewer_uuid: Uuid,
    password_hash: &str,
    actor: Uuid,
) -> Result<AccountAdministrationResult, AppError> {
    let mut transaction = pool.begin().await?;
    lock_and_require_administrator(&mut transaction, actor).await?;
    let current_credential_id: Option<i64> = sqlx::query_scalar(
        "SELECT credential_id FROM drugref.reviewer_password_credential \
         WHERE reviewer_uuid = $1 AND superseded_by IS NULL FOR UPDATE",
    )
    .bind(reviewer_uuid)
    .fetch_optional(&mut *transaction)
    .await?;
    let current_credential_id = current_credential_id
        .ok_or_else(|| AppError::not_found("reviewer account was not found"))?;
    let replacement_id: i64 = sqlx::query_scalar(
        "INSERT INTO drugref.reviewer_password_credential \
         (reviewer_uuid, password_hash, recorded_by) VALUES ($1, $2, $3) \
         RETURNING credential_id",
    )
    .bind(reviewer_uuid)
    .bind(password_hash)
    .bind(actor)
    .fetch_one(&mut *transaction)
    .await?;
    sqlx::query(
        "UPDATE drugref.reviewer_password_credential SET superseded_by = $1 \
         WHERE credential_id = $2",
    )
    .bind(replacement_id)
    .bind(current_credential_id)
    .execute(&mut *transaction)
    .await?;
    let revoked_session_count = revoke_live_sessions(
        &mut transaction,
        reviewer_uuid,
        actor,
        "credential_rotation",
    )
    .await?;
    let reviewer = account_by_uuid_tx(&mut transaction, reviewer_uuid).await?;
    transaction.commit().await?;
    Ok(AccountAdministrationResult {
        reviewer,
        revoked_session_count,
    })
}

/// Revoke every unexpired current session for one reviewer.
pub async fn revoke_user_sessions(
    pool: &PgPool,
    reviewer_uuid: Uuid,
    actor: Uuid,
) -> Result<AccountAdministrationResult, AppError> {
    let mut transaction = pool.begin().await?;
    lock_and_require_administrator(&mut transaction, actor).await?;
    current_profile_for_update(&mut transaction, reviewer_uuid).await?;
    let revoked_session_count =
        revoke_live_sessions(&mut transaction, reviewer_uuid, actor, "administrative").await?;
    let reviewer = account_by_uuid_tx(&mut transaction, reviewer_uuid).await?;
    transaction.commit().await?;
    Ok(AccountAdministrationResult {
        reviewer,
        revoked_session_count,
    })
}

/// Serialise authority mutations and confirm the caller is still an active administrator.
async fn lock_and_require_administrator(
    transaction: &mut Transaction<'_, Postgres>,
    actor: Uuid,
) -> Result<(), AppError> {
    sqlx::query(ADMINISTRATION_LOCK)
        .execute(&mut **transaction)
        .await?;
    let authorised: bool = sqlx::query_scalar(
        "SELECT EXISTS (SELECT 1 FROM drugref.reviewer_profile \
         WHERE reviewer_uuid = $1 AND role = 'administrator' AND active \
           AND superseded_by IS NULL)",
    )
    .bind(actor)
    .fetch_one(&mut **transaction)
    .await?;
    if !authorised {
        return Err(AppError::forbidden());
    }
    Ok(())
}

/// Lock and return one account's current profile revision.
async fn current_profile_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    reviewer_uuid: Uuid,
) -> Result<CurrentProfileRow, AppError> {
    sqlx::query_as::<_, CurrentProfileRow>(
        "SELECT reviewer_profile_id, full_name, qualifications, bio_markdown, role, active \
         FROM drugref.reviewer_profile WHERE reviewer_uuid = $1 \
           AND superseded_by IS NULL FOR UPDATE",
    )
    .bind(reviewer_uuid)
    .fetch_optional(&mut **transaction)
    .await?
    .ok_or_else(|| AppError::not_found("reviewer account was not found"))
}

/// Append reason-specific revocation facts for all currently live sessions.
async fn revoke_live_sessions(
    transaction: &mut Transaction<'_, Postgres>,
    reviewer_uuid: Uuid,
    actor: Uuid,
    reason: &str,
) -> Result<u64, AppError> {
    Ok(sqlx::query(
        "INSERT INTO drugref.auth_session_revocation (session_uuid, revoked_by, reason) \
         SELECT s.session_uuid, $2, $3 FROM drugref.auth_session s \
         LEFT JOIN drugref.auth_session_revocation r ON r.session_uuid = s.session_uuid \
         WHERE s.reviewer_uuid = $1 AND s.expires_at > now() AND r.session_uuid IS NULL \
         ON CONFLICT (session_uuid) DO NOTHING",
    )
    .bind(reviewer_uuid)
    .bind(actor)
    .bind(reason)
    .execute(&mut **transaction)
    .await?
    .rows_affected())
}

#[cfg(test)]
mod tests {
    use super::{revoke_user_sessions, rotate_user_password, update_user_profile};
    use crate::{
        auth::{hash_password, verify_password},
        store::{bootstrap_admin, create_user, login_row, start_session},
    };
    use reviewer_domain::{CreateAccountRequest, ReviewerRole, UpdateReviewerProfileRequest};
    use sqlx::postgres::PgPoolOptions;
    use uuid::Uuid;

    /// Exercise append-only administration, safety guards, and reason-specific revocations.
    #[tokio::test]
    #[ignore = "requires a clean migrated Drugref PostgreSQL test database"]
    async fn live_account_administration_round_trip() {
        let database_url = std::env::var("DRUGREF_REVIEW_TEST_DATABASE_URL")
            .expect("DRUGREF_REVIEW_TEST_DATABASE_URL must name a clean migrated database");
        let pool = PgPoolOptions::new()
            .max_connections(2)
            .connect(&database_url)
            .await
            .expect("review account test database");
        let existing_administrators: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM drugref.reviewer_profile \
             WHERE role = 'administrator' AND active AND superseded_by IS NULL",
        )
        .fetch_one(&pool)
        .await
        .expect("active administrator count");
        assert_eq!(
            existing_administrators, 0,
            "account administration integration requires a clean account database"
        );

        let administrator_seed = Uuid::new_v4();
        let administrator = CreateAccountRequest {
            username: format!("a{}", administrator_seed.simple()),
            full_name: "Test Administrator".into(),
            qualifications: "PharmD".into(),
            bio_markdown: String::new(),
            role: ReviewerRole::Administrator,
            password: "initial admin password".into(),
        };
        let administrator_hash =
            hash_password(&administrator.password).expect("administrator hash");
        let administrator_grant = bootstrap_admin(&pool, &administrator, &administrator_hash)
            .await
            .expect("bootstrap administrator");
        let administrator_uuid = administrator_grant.reviewer.reviewer_uuid;

        let reviewer_seed = Uuid::new_v4();
        let reviewer_input = CreateAccountRequest {
            username: format!("r{}", reviewer_seed.simple()),
            full_name: "Test Reviewer".into(),
            qualifications: "MBBS".into(),
            bio_markdown: "Initial biography".into(),
            role: ReviewerRole::Reviewer,
            password: "initial reviewer password".into(),
        };
        let reviewer_hash = hash_password(&reviewer_input.password).expect("reviewer hash");
        let reviewer = create_user(&pool, &reviewer_input, &reviewer_hash, administrator_uuid)
            .await
            .expect("reviewer account");
        let reviewer_uuid = reviewer.reviewer_uuid;
        let initial_credential = login_row(&pool, &reviewer_input.username)
            .await
            .expect("reviewer credential lookup")
            .expect("reviewer credential");
        start_session(&pool, reviewer_uuid, initial_credential.credential_id)
            .await
            .expect("initial reviewer session");

        let corrected = update_user_profile(
            &pool,
            reviewer_uuid,
            &UpdateReviewerProfileRequest {
                full_name: "Dr Test Reviewer".into(),
                qualifications: "MBBS, FRACP".into(),
                bio_markdown: "Corrected biography".into(),
                role: ReviewerRole::Reviewer,
                active: true,
                expected_profile_revision_id: reviewer.profile_revision_id,
            },
            administrator_uuid,
        )
        .await
        .expect("profile correction");
        assert_eq!(corrected.reviewer.full_name, "Dr Test Reviewer");
        assert_eq!(corrected.revoked_session_count, 0);
        assert!(update_user_profile(
            &pool,
            reviewer_uuid,
            &UpdateReviewerProfileRequest {
                full_name: "Stale correction".into(),
                qualifications: String::new(),
                bio_markdown: String::new(),
                role: ReviewerRole::Reviewer,
                active: true,
                expected_profile_revision_id: reviewer.profile_revision_id,
            },
            administrator_uuid,
        )
        .await
        .is_err());

        assert!(update_user_profile(
            &pool,
            administrator_uuid,
            &UpdateReviewerProfileRequest {
                full_name: administrator.full_name.clone(),
                qualifications: administrator.qualifications.clone(),
                bio_markdown: administrator.bio_markdown.clone(),
                role: ReviewerRole::Administrator,
                active: false,
                expected_profile_revision_id: administrator_grant.reviewer.profile_revision_id,
            },
            administrator_uuid,
        )
        .await
        .is_err());

        let disabled = update_user_profile(
            &pool,
            reviewer_uuid,
            &UpdateReviewerProfileRequest {
                full_name: corrected.reviewer.full_name.clone(),
                qualifications: corrected.reviewer.qualifications.clone(),
                bio_markdown: corrected.reviewer.bio_markdown.clone(),
                role: ReviewerRole::Reviewer,
                active: false,
                expected_profile_revision_id: corrected.reviewer.profile_revision_id,
            },
            administrator_uuid,
        )
        .await
        .expect("disable reviewer");
        assert_eq!(disabled.revoked_session_count, 1);
        assert!(!disabled.reviewer.active);
        assert!(
            !login_row(&pool, &reviewer_input.username)
                .await
                .expect("disabled login row")
                .expect("disabled credential")
                .active
        );

        let enabled = update_user_profile(
            &pool,
            reviewer_uuid,
            &UpdateReviewerProfileRequest {
                full_name: disabled.reviewer.full_name.clone(),
                qualifications: disabled.reviewer.qualifications.clone(),
                bio_markdown: disabled.reviewer.bio_markdown.clone(),
                role: ReviewerRole::Reviewer,
                active: true,
                expected_profile_revision_id: disabled.reviewer.profile_revision_id,
            },
            administrator_uuid,
        )
        .await
        .expect("re-enable reviewer");
        assert!(enabled.reviewer.active);
        start_session(&pool, reviewer_uuid, initial_credential.credential_id)
            .await
            .expect("session after re-enable");

        let new_password = "replacement reviewer password";
        let replacement_hash = hash_password(new_password).expect("replacement hash");
        let rotated =
            rotate_user_password(&pool, reviewer_uuid, &replacement_hash, administrator_uuid)
                .await
                .expect("password rotation");
        assert_eq!(rotated.revoked_session_count, 1);
        assert!(
            start_session(&pool, reviewer_uuid, initial_credential.credential_id)
                .await
                .is_err()
        );
        let replacement_credential = login_row(&pool, &reviewer_input.username)
            .await
            .expect("replacement lookup")
            .expect("replacement credential");
        assert!(!verify_password(
            &reviewer_input.password,
            &replacement_credential.password_hash
        ));
        assert!(verify_password(
            new_password,
            &replacement_credential.password_hash
        ));
        start_session(&pool, reviewer_uuid, replacement_credential.credential_id)
            .await
            .expect("replacement-credential session");
        let revoked = revoke_user_sessions(&pool, reviewer_uuid, administrator_uuid)
            .await
            .expect("administrative session revocation");
        assert_eq!(revoked.revoked_session_count, 1);
        assert_eq!(revoked.reviewer.live_session_count, 0);

        let profile_history: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM drugref.reviewer_profile WHERE reviewer_uuid = $1",
        )
        .bind(reviewer_uuid)
        .fetch_one(&pool)
        .await
        .expect("profile history");
        let credential_history: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM drugref.reviewer_password_credential WHERE reviewer_uuid = $1",
        )
        .bind(reviewer_uuid)
        .fetch_one(&pool)
        .await
        .expect("credential history");
        let revocation_reasons: Vec<String> = sqlx::query_scalar(
            "SELECT r.reason FROM drugref.auth_session_revocation r \
             JOIN drugref.auth_session s ON s.session_uuid = r.session_uuid \
             WHERE s.reviewer_uuid = $1 ORDER BY r.revoked_at, r.reason",
        )
        .bind(reviewer_uuid)
        .fetch_all(&pool)
        .await
        .expect("revocation reasons");
        assert_eq!(profile_history, 4);
        assert_eq!(credential_history, 2);
        assert_eq!(
            revocation_reasons,
            vec!["administrative", "credential_rotation", "administrative"]
        );
    }
}
