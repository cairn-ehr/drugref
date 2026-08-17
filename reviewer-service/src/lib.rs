//! Authenticated HTTP service for Drugref reviewer accounts and clinical queues.
//!
//! The service owns authentication, authorisation, validation, and PostgreSQL access;
//! the GUI receives only typed projections and never connects to the database directly.
#![deny(missing_docs)]

mod auth;
mod error;
mod limiter;
mod queue;
mod store;

use std::{net::SocketAddr, sync::Arc};

use axum::{
    extract::{ConnectInfo, Query, State},
    http::{header::AUTHORIZATION, HeaderMap, StatusCode},
    routing::{get, post},
    Json, Router,
};
use reviewer_domain::{
    BootstrapStatus, CreateAccountRequest, LoginRequest, ReviewQueuePage, ReviewQueueQuery,
    ReviewerAccount, ReviewerRole, SessionGrant,
};
use sqlx::PgPool;

use auth::{dummy_password_hash, hash_password, verify_password};
use limiter::LoginLimiter;

pub use error::AppError;

/// Shared dependencies used by every reviewer HTTP handler.
#[derive(Clone)]
pub struct AppState {
    pool: PgPool,
    login_limiter: Arc<LoginLimiter>,
}

impl AppState {
    /// Construct service state around a migrated PostgreSQL connection pool.
    pub fn new(pool: PgPool) -> Self {
        Self {
            pool,
            login_limiter: Arc::new(LoginLimiter::default()),
        }
    }
}

/// Build the complete reviewer service router for the supplied state.
pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/v1/bootstrap/status", get(bootstrap_status))
        .route("/v1/bootstrap/admin", post(bootstrap_admin))
        .route("/v1/sessions", post(login))
        .route("/v1/sessions/current", post(logout))
        .route("/v1/review-queue", get(review_queue))
        .route("/v1/users", get(list_users).post(create_user))
        .with_state(state)
}

/// Fail startup unless all account and clinical queue relations are available.
pub async fn check_schema(pool: &PgPool) -> Result<(), AppError> {
    store::ensure_schema(pool).await
}

/// Report process availability without exposing service or database details.
async fn health() -> StatusCode {
    StatusCode::NO_CONTENT
}

/// Report whether the database still requires its first administrator.
async fn bootstrap_status(
    State(state): State<AppState>,
) -> Result<Json<BootstrapStatus>, AppError> {
    Ok(Json(BootstrapStatus {
        bootstrap_required: store::bootstrap_required(&state.pool).await?,
    }))
}

/// Create the one allowed bootstrap administrator and start its session.
async fn bootstrap_admin(
    State(state): State<AppState>,
    Json(mut input): Json<CreateAccountRequest>,
) -> Result<Json<SessionGrant>, AppError> {
    input.role = ReviewerRole::Administrator;
    input
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    let password_hash = hash_password(&input.password)?;
    Ok(Json(
        store::bootstrap_admin(&state.pool, &input, &password_hash).await?,
    ))
}

/// Verify credentials under rate limiting and start an authenticated session.
async fn login(
    State(state): State<AppState>,
    ConnectInfo(address): ConnectInfo<SocketAddr>,
    Json(input): Json<LoginRequest>,
) -> Result<Json<SessionGrant>, AppError> {
    if !state.login_limiter.allow(address.ip()) {
        return Err(AppError::too_many_requests());
    }
    // Initialise this before the lookup so the first missing-user attempt takes the
    // same one-time setup path as the first valid-user attempt.
    let sentinel = dummy_password_hash();
    let row = store::login_row(&state.pool, &input.username).await?;
    let encoded = row
        .as_ref()
        .map_or(sentinel, |credential| credential.password_hash.as_str());
    let password_matches = verify_password(&input.password, encoded);
    let Some(credential) = row else {
        return Err(AppError::unauthorized());
    };
    if !password_matches || !credential.active {
        return Err(AppError::unauthorized());
    }
    Ok(Json(
        store::start_session(&state.pool, credential.reviewer_uuid).await?,
    ))
}

/// Return current reviewer profiles to an authenticated administrator.
async fn list_users(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Vec<ReviewerAccount>>, AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    require_admin(&authenticated.reviewer)?;
    Ok(Json(store::list_users(&state.pool).await?))
}

/// Create a reviewer account on behalf of an authenticated administrator.
async fn create_user(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(input): Json<CreateAccountRequest>,
) -> Result<(StatusCode, Json<ReviewerAccount>), AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    require_admin(&authenticated.reviewer)?;
    input
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    let password_hash = hash_password(&input.password)?;
    let reviewer = store::create_user(
        &state.pool,
        &input,
        &password_hash,
        authenticated.reviewer.reviewer_uuid,
    )
    .await?;
    Ok((StatusCode::CREATED, Json(reviewer)))
}

/// Return one validated, filtered queue page to an authenticated reviewer.
async fn review_queue(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<ReviewQueueQuery>,
) -> Result<Json<ReviewQueuePage>, AppError> {
    authenticate_headers(&state, &headers).await?;
    let query = query
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    Ok(Json(queue::load(&state.pool, &query).await?))
}

/// Append a revocation for the current authenticated session.
async fn logout(State(state): State<AppState>, headers: HeaderMap) -> Result<StatusCode, AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    store::revoke_session(
        &state.pool,
        authenticated.session_uuid,
        authenticated.reviewer.reviewer_uuid,
    )
    .await?;
    Ok(StatusCode::NO_CONTENT)
}

/// Authenticate the bearer token from an HTTP header against a live session.
async fn authenticate_headers(
    state: &AppState,
    headers: &HeaderMap,
) -> Result<store::Authenticated, AppError> {
    let token = headers
        .get(AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .filter(|value| !value.is_empty())
        .ok_or_else(AppError::unauthorized)?;
    store::authenticate(&state.pool, token).await
}

/// Reject an authenticated reviewer whose current role is not administrator.
fn require_admin(reviewer: &ReviewerAccount) -> Result<(), AppError> {
    if reviewer.role != ReviewerRole::Administrator {
        return Err(AppError::forbidden());
    }
    Ok(())
}
