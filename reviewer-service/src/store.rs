use chrono::{DateTime, Duration, Utc};
use reviewer_domain::{CreateAccountRequest, ReviewerAccount, ReviewerRole, SessionGrant};
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use uuid::Uuid;

use crate::{
    auth::{new_session_token, token_digest},
    error::AppError,
};

#[derive(FromRow)]
struct AccountRow {
    reviewer_uuid: Uuid,
    username: String,
    full_name: String,
    qualifications: String,
    bio_markdown: String,
    role: String,
    active: bool,
    created_at: DateTime<Utc>,
    key_count: i64,
}

impl TryFrom<AccountRow> for ReviewerAccount {
    type Error = AppError;

    fn try_from(row: AccountRow) -> Result<Self, Self::Error> {
        let role = match row.role.as_str() {
            "reviewer" => ReviewerRole::Reviewer,
            "administrator" => ReviewerRole::Administrator,
            value => return Err(AppError::internal(format!("unknown reviewer role {value}"))),
        };
        Ok(Self {
            reviewer_uuid: row.reviewer_uuid,
            username: row.username,
            full_name: row.full_name,
            qualifications: row.qualifications,
            bio_markdown: row.bio_markdown,
            role,
            active: row.active,
            created_at: row.created_at.to_rfc3339(),
            key_count: row.key_count,
        })
    }
}

#[derive(FromRow)]
pub struct LoginRow {
    pub reviewer_uuid: Uuid,
    pub password_hash: String,
    pub active: bool,
}

pub struct Authenticated {
    pub session_uuid: Uuid,
    pub reviewer: ReviewerAccount,
}

const ACCOUNT_COLUMNS: &str = r#"
    a.reviewer_uuid, a.username, p.full_name, p.qualifications, p.bio_markdown,
    p.role, p.active, a.created_at,
    (SELECT count(*) FROM drugref.reviewer_key_enrolment e
     WHERE e.reviewer_uuid = a.reviewer_uuid AND e.superseded_by IS NULL
       AND e.enrolled) AS key_count
"#;

pub async fn ensure_schema(pool: &PgPool) -> Result<(), AppError> {
    let present: Option<String> =
        sqlx::query_scalar("SELECT to_regclass('drugref.reviewer_account')::text")
            .fetch_one(pool)
            .await?;
    if present.is_none() {
        return Err(AppError::internal(
            "db/044 is not applied; run `drugref migrate` before starting reviewer-service",
        ));
    }
    Ok(())
}

pub async fn bootstrap_required(pool: &PgPool) -> Result<bool, AppError> {
    let exists: bool = sqlx::query_scalar(
        "SELECT EXISTS (SELECT 1 FROM drugref.reviewer_profile WHERE role = \
         'administrator' AND superseded_by IS NULL)",
    )
    .fetch_one(pool)
    .await?;
    Ok(!exists)
}

pub async fn bootstrap_admin(
    pool: &PgPool,
    input: &CreateAccountRequest,
    password_hash: &str,
) -> Result<SessionGrant, AppError> {
    let mut transaction = pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext('drugref reviewer bootstrap'))")
        .execute(&mut *transaction)
        .await?;
    let exists: bool = sqlx::query_scalar(
        "SELECT EXISTS (SELECT 1 FROM drugref.reviewer_profile WHERE role = \
         'administrator' AND superseded_by IS NULL)",
    )
    .fetch_one(&mut *transaction)
    .await?;
    if exists {
        return Err(AppError::conflict(
            "administrator registration has already been completed",
        ));
    }

    let reviewer_uuid = insert_account(&mut transaction, input, password_hash, None).await?;
    let reviewer = account_by_uuid_tx(&mut transaction, reviewer_uuid).await?;
    let grant = insert_session(&mut transaction, reviewer).await?;
    transaction.commit().await?;
    Ok(grant)
}

pub async fn create_user(
    pool: &PgPool,
    input: &CreateAccountRequest,
    password_hash: &str,
    actor: Uuid,
) -> Result<ReviewerAccount, AppError> {
    let mut transaction = pool.begin().await?;
    let reviewer_uuid = insert_account(&mut transaction, input, password_hash, Some(actor)).await?;
    let reviewer = account_by_uuid_tx(&mut transaction, reviewer_uuid).await?;
    transaction.commit().await?;
    Ok(reviewer)
}

async fn insert_account(
    transaction: &mut Transaction<'_, Postgres>,
    input: &CreateAccountRequest,
    password_hash: &str,
    actor: Option<Uuid>,
) -> Result<Uuid, AppError> {
    let reviewer_uuid = Uuid::new_v4();
    let recorded_by = actor.unwrap_or(reviewer_uuid);
    let result = sqlx::query(
        "INSERT INTO drugref.reviewer_account (reviewer_uuid, username, created_by) \
         VALUES ($1, $2, $3)",
    )
    .bind(reviewer_uuid)
    .bind(&input.username)
    .bind(actor)
    .execute(&mut **transaction)
    .await;
    if let Err(error) = result {
        if error
            .as_database_error()
            .and_then(|detail| detail.code())
            .as_deref()
            == Some("23505")
        {
            return Err(AppError::conflict("username already exists"));
        }
        return Err(error.into());
    }
    sqlx::query(
        "INSERT INTO drugref.reviewer_profile \
         (reviewer_uuid, full_name, qualifications, bio_markdown, role, active, recorded_by) \
         VALUES ($1, $2, $3, $4, $5, true, $6)",
    )
    .bind(reviewer_uuid)
    .bind(&input.full_name)
    .bind(&input.qualifications)
    .bind(&input.bio_markdown)
    .bind(input.role.as_str())
    .bind(recorded_by)
    .execute(&mut **transaction)
    .await?;
    sqlx::query(
        "INSERT INTO drugref.reviewer_password_credential \
         (reviewer_uuid, password_hash, recorded_by) VALUES ($1, $2, $3)",
    )
    .bind(reviewer_uuid)
    .bind(password_hash)
    .bind(recorded_by)
    .execute(&mut **transaction)
    .await?;
    Ok(reviewer_uuid)
}

pub async fn login_row(pool: &PgPool, username: &str) -> Result<Option<LoginRow>, AppError> {
    Ok(sqlx::query_as::<_, LoginRow>(
        "SELECT a.reviewer_uuid, c.password_hash, p.active \
         FROM drugref.reviewer_account a \
         JOIN drugref.reviewer_profile p ON p.reviewer_uuid = a.reviewer_uuid \
           AND p.superseded_by IS NULL \
         JOIN drugref.reviewer_password_credential c ON c.reviewer_uuid = a.reviewer_uuid \
           AND c.superseded_by IS NULL \
         WHERE a.username = $1",
    )
    .bind(username)
    .fetch_optional(pool)
    .await?)
}

pub async fn start_session(pool: &PgPool, reviewer_uuid: Uuid) -> Result<SessionGrant, AppError> {
    let reviewer = account_by_uuid(pool, reviewer_uuid).await?;
    let mut transaction = pool.begin().await?;
    let grant = insert_session(&mut transaction, reviewer).await?;
    transaction.commit().await?;
    Ok(grant)
}

async fn insert_session(
    transaction: &mut Transaction<'_, Postgres>,
    reviewer: ReviewerAccount,
) -> Result<SessionGrant, AppError> {
    let token = new_session_token();
    let digest = token_digest(&token);
    let session_uuid = Uuid::new_v4();
    let expires_at = Utc::now() + Duration::hours(12);
    sqlx::query(
        "INSERT INTO drugref.auth_session \
         (session_uuid, reviewer_uuid, token_digest, expires_at) VALUES ($1, $2, $3, $4)",
    )
    .bind(session_uuid)
    .bind(reviewer.reviewer_uuid)
    .bind(digest.as_slice())
    .bind(expires_at)
    .execute(&mut **transaction)
    .await?;
    Ok(SessionGrant { token, reviewer })
}

pub async fn authenticate(pool: &PgPool, token: &str) -> Result<Authenticated, AppError> {
    let digest = token_digest(token);
    let session_uuid: Option<Uuid> = sqlx::query_scalar(
        "SELECT s.session_uuid FROM drugref.auth_session s \
         JOIN drugref.reviewer_profile p ON p.reviewer_uuid = s.reviewer_uuid \
           AND p.superseded_by IS NULL AND p.active \
         LEFT JOIN drugref.auth_session_revocation r ON r.session_uuid = s.session_uuid \
         WHERE s.token_digest = $1 AND s.expires_at > now() AND r.session_uuid IS NULL",
    )
    .bind(digest.as_slice())
    .fetch_optional(pool)
    .await?;
    let session_uuid = session_uuid.ok_or_else(AppError::unauthorized)?;
    let reviewer_uuid: Uuid = sqlx::query_scalar(
        "SELECT reviewer_uuid FROM drugref.auth_session WHERE session_uuid = $1",
    )
    .bind(session_uuid)
    .fetch_one(pool)
    .await?;
    Ok(Authenticated {
        session_uuid,
        reviewer: account_by_uuid(pool, reviewer_uuid).await?,
    })
}

pub async fn list_users(pool: &PgPool) -> Result<Vec<ReviewerAccount>, AppError> {
    let query = format!(
        "SELECT {ACCOUNT_COLUMNS} FROM drugref.reviewer_account a \
         JOIN drugref.reviewer_profile p ON p.reviewer_uuid = a.reviewer_uuid \
         AND p.superseded_by IS NULL ORDER BY a.username"
    );
    sqlx::query_as::<_, AccountRow>(&query)
        .fetch_all(pool)
        .await?
        .into_iter()
        .map(ReviewerAccount::try_from)
        .collect()
}

pub async fn revoke_session(
    pool: &PgPool,
    session_uuid: Uuid,
    reviewer_uuid: Uuid,
) -> Result<(), AppError> {
    sqlx::query(
        "INSERT INTO drugref.auth_session_revocation \
         (session_uuid, revoked_by, reason) VALUES ($1, $2, 'logout') \
         ON CONFLICT (session_uuid) DO NOTHING",
    )
    .bind(session_uuid)
    .bind(reviewer_uuid)
    .execute(pool)
    .await?;
    Ok(())
}

async fn account_by_uuid(pool: &PgPool, reviewer_uuid: Uuid) -> Result<ReviewerAccount, AppError> {
    let query = format!(
        "SELECT {ACCOUNT_COLUMNS} FROM drugref.reviewer_account a \
         JOIN drugref.reviewer_profile p ON p.reviewer_uuid = a.reviewer_uuid \
         AND p.superseded_by IS NULL WHERE a.reviewer_uuid = $1"
    );
    sqlx::query_as::<_, AccountRow>(&query)
        .bind(reviewer_uuid)
        .fetch_one(pool)
        .await?
        .try_into()
}

async fn account_by_uuid_tx(
    transaction: &mut Transaction<'_, Postgres>,
    reviewer_uuid: Uuid,
) -> Result<ReviewerAccount, AppError> {
    let query = format!(
        "SELECT {ACCOUNT_COLUMNS} FROM drugref.reviewer_account a \
         JOIN drugref.reviewer_profile p ON p.reviewer_uuid = a.reviewer_uuid \
         AND p.superseded_by IS NULL WHERE a.reviewer_uuid = $1"
    );
    sqlx::query_as::<_, AccountRow>(&query)
        .bind(reviewer_uuid)
        .fetch_one(&mut **transaction)
        .await?
        .try_into()
}
