//! Authenticated HTTP service for Drugref reviewer accounts and clinical queues.
//!
//! The service owns authentication, authorisation, validation, and PostgreSQL access;
//! the GUI receives only typed projections and never connects to the database directly.
#![deny(missing_docs)]

mod administration;
mod auth;
mod decision_targets;
mod decisions;
mod error;
mod limiter;
mod queue;
mod records;
mod signing;
mod store;

use std::{net::SocketAddr, sync::Arc};

use axum::{
    extract::{ConnectInfo, Path, Query, State},
    http::{header::AUTHORIZATION, HeaderMap, StatusCode},
    routing::{get, post, put},
    Json, Router,
};
use reviewer_domain::{
    AccountAdministrationResult, BootstrapStatus, CreateAccountRequest, CreateAnnotationRequest,
    CreateEvidenceReferenceRequest, CreateReviewDecisionRequest, EnrolSigningKeyRequest,
    EvidenceReference, LoginRequest, PendingReviewSignature, ReplaceSigningKeyRequest,
    ReviewAnnotation, ReviewDecisionRecord, ReviewQueuePage, ReviewQueueQuery, ReviewRecord,
    ReviewRecordQuery, ReviewSignatureChallenge, ReviewSignatureQuery, ReviewerAccount,
    ReviewerRole, RotateReviewerPasswordRequest, SessionGrant, SigningKeyReplacement,
    SigningKeyStatus, SigningKeySummary, SubmitReviewSignatureRequest,
    UpdateReviewerProfileRequest,
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
        .route("/v1/review-record", get(review_record))
        .route(
            "/v1/review-decision",
            get(review_decision).post(create_review_decision),
        )
        .route("/v1/review-annotations", post(create_annotation))
        .route(
            "/v1/review-evidence-references",
            post(create_evidence_reference),
        )
        .route(
            "/v1/signing-keys/current",
            get(signing_keys)
                .post(enrol_signing_key)
                .delete(replace_signing_key),
        )
        .route(
            "/v1/review-signature",
            get(review_signature_challenge).post(submit_review_signature),
        )
        .route("/v1/pending-signatures", get(pending_signatures))
        .route("/v1/users", get(list_users).post(create_user))
        .route(
            "/v1/users/{reviewer_uuid}/profile",
            put(update_user_profile),
        )
        .route(
            "/v1/users/{reviewer_uuid}/password",
            put(rotate_user_password),
        )
        .route(
            "/v1/users/{reviewer_uuid}/sessions/revoke",
            post(revoke_user_sessions),
        )
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
        store::start_session(
            &state.pool,
            credential.reviewer_uuid,
            credential.credential_id,
        )
        .await?,
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

/// Append one administrator-attributed replacement for a reviewer's current profile.
async fn update_user_profile(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(reviewer_uuid): Path<uuid::Uuid>,
    Json(input): Json<UpdateReviewerProfileRequest>,
) -> Result<Json<AccountAdministrationResult>, AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    require_admin(&authenticated.reviewer)?;
    input
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    Ok(Json(
        administration::update_user_profile(
            &state.pool,
            reviewer_uuid,
            &input,
            authenticated.reviewer.reviewer_uuid,
        )
        .await?,
    ))
}

/// Append a replacement password credential and revoke sessions using its predecessor.
async fn rotate_user_password(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(reviewer_uuid): Path<uuid::Uuid>,
    Json(input): Json<RotateReviewerPasswordRequest>,
) -> Result<Json<AccountAdministrationResult>, AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    require_admin(&authenticated.reviewer)?;
    input
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    let password_hash = hash_password(&input.password)?;
    Ok(Json(
        administration::rotate_user_password(
            &state.pool,
            reviewer_uuid,
            &password_hash,
            authenticated.reviewer.reviewer_uuid,
        )
        .await?,
    ))
}

/// Append administrative revocations for every live session owned by one reviewer.
async fn revoke_user_sessions(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(reviewer_uuid): Path<uuid::Uuid>,
) -> Result<Json<AccountAdministrationResult>, AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    require_admin(&authenticated.reviewer)?;
    Ok(Json(
        administration::revoke_user_sessions(
            &state.pool,
            reviewer_uuid,
            authenticated.reviewer.reviewer_uuid,
        )
        .await?,
    ))
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

/// Return immutable working history for one current review target.
async fn review_record(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<ReviewRecordQuery>,
) -> Result<Json<ReviewRecord>, AppError> {
    authenticate_headers(&state, &headers).await?;
    let query = query
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    Ok(Json(records::load(&state.pool, &query).await?))
}

/// Return immutable clinical decision history for one registered review target.
async fn review_decision(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<ReviewRecordQuery>,
) -> Result<Json<ReviewDecisionRecord>, AppError> {
    authenticate_headers(&state, &headers).await?;
    let query = query
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    Ok(Json(decisions::load(&state.pool, &query).await?))
}

/// Append one authenticated clinical decision through the overlay transaction.
async fn create_review_decision(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(input): Json<CreateReviewDecisionRequest>,
) -> Result<(StatusCode, Json<ReviewDecisionRecord>), AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    let input = input
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    let record = decisions::create(&state.pool, &input, &authenticated.reviewer.full_name).await?;
    Ok((StatusCode::CREATED, Json(record)))
}

/// Append one authenticated reviewer's Markdown working note.
async fn create_annotation(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(input): Json<CreateAnnotationRequest>,
) -> Result<(StatusCode, Json<ReviewAnnotation>), AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    let input = input
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    let annotation =
        records::create_annotation(&state.pool, &input, authenticated.reviewer.reviewer_uuid)
            .await?;
    Ok((StatusCode::CREATED, Json(annotation)))
}

/// Append one authenticated reviewer's citation-only working reference.
async fn create_evidence_reference(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(input): Json<CreateEvidenceReferenceRequest>,
) -> Result<(StatusCode, Json<EvidenceReference>), AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    let input = input
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    let reference = records::create_evidence_reference(
        &state.pool,
        &input,
        authenticated.reviewer.reviewer_uuid,
    )
    .await?;
    Ok((StatusCode::CREATED, Json(reference)))
}

/// Return signing keys enrolled to the current authenticated reviewer.
async fn signing_keys(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<SigningKeyStatus>, AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    Ok(Json(
        signing::list_keys(&state.pool, authenticated.reviewer.reviewer_uuid).await?,
    ))
}

/// Return live curated revisions whose detached sign-off can be resumed.
async fn pending_signatures(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Vec<PendingReviewSignature>>, AppError> {
    authenticate_headers(&state, &headers).await?;
    Ok(Json(signing::pending(&state.pool).await?))
}

/// Register and enrol one public key generated by the authenticated native device.
async fn enrol_signing_key(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(input): Json<EnrolSigningKeyRequest>,
) -> Result<(StatusCode, Json<SigningKeySummary>), AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    let input = input
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    let key = signing::enrol_key(
        &state.pool,
        &input,
        authenticated.reviewer.reviewer_uuid,
        &authenticated.reviewer.full_name,
    )
    .await?;
    Ok((StatusCode::CREATED, Json(key)))
}

/// Retire one owned key, withdraw its enrolment, and preserve prior signatures.
async fn replace_signing_key(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(input): Json<ReplaceSigningKeyRequest>,
) -> Result<Json<SigningKeyReplacement>, AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    let input = input
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    Ok(Json(
        signing::replace_key(
            &state.pool,
            &input.key_fingerprint,
            authenticated.reviewer.reviewer_uuid,
            &authenticated.reviewer.full_name,
        )
        .await?,
    ))
}

/// Prepare one exact current curated-row payload for native confirmation.
async fn review_signature_challenge(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<ReviewSignatureQuery>,
) -> Result<Json<ReviewSignatureChallenge>, AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    let query = query
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    Ok(Json(
        signing::challenge(&state.pool, &query, authenticated.reviewer.reviewer_uuid).await?,
    ))
}

/// Independently re-derive, verify, and record a native detached signature.
async fn submit_review_signature(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(input): Json<SubmitReviewSignatureRequest>,
) -> Result<(StatusCode, Json<ReviewDecisionRecord>), AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    let input = input
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    let record = signing::submit(&state.pool, &input, authenticated.reviewer.reviewer_uuid).await?;
    Ok((StatusCode::CREATED, Json(record)))
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
