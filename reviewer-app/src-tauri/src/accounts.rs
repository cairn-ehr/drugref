//! Typed HTTP adapter between Tauri commands and the reviewer service.

use std::sync::Mutex;

use reqwest::{Client, Method, StatusCode};
use reviewer_domain::{
    AccountAdministrationResult, ApiErrorBody, BootstrapStatus, CreateAccountRequest,
    CreateAnnotationRequest, CreateEvidenceReferenceRequest, CreateReviewDecisionRequest,
    EvidenceReference, LoginRequest, ReviewAnnotation, ReviewDecisionRecord, ReviewQueuePage,
    ReviewQueueQuery, ReviewRecord, ReviewRecordQuery, ReviewerAccount,
    RotateReviewerPasswordRequest, SessionGrant, UpdateReviewerProfileRequest,
};
use serde::{de::DeserializeOwned, Serialize};

#[cfg(debug_assertions)]
const DEFAULT_DEBUG_SERVICE_URL: &str = "http://127.0.0.1:8787";
const BOOTSTRAP_STATUS_PATH: &str = "/v1/bootstrap/status";
const BOOTSTRAP_ADMIN_PATH: &str = "/v1/bootstrap/admin";
const SESSIONS_PATH: &str = "/v1/sessions";
const CURRENT_SESSION_PATH: &str = "/v1/sessions/current";
const USERS_PATH: &str = "/v1/users";
const REVIEW_QUEUE_PATH: &str = "/v1/review-queue";
const REVIEW_RECORD_PATH: &str = "/v1/review-record";
const REVIEW_DECISION_PATH: &str = "/v1/review-decision";
const REVIEW_ANNOTATIONS_PATH: &str = "/v1/review-annotations";
const REVIEW_EVIDENCE_REFERENCES_PATH: &str = "/v1/review-evidence-references";

/// Native HTTP client and process-memory session store managed by Tauri.
pub struct AccountClient {
    pub(crate) http: Client,
    service_url: Result<String, String>,
    session_token: Mutex<Option<String>>,
    current_reviewer: Mutex<Option<ReviewerAccount>>,
}

impl AccountClient {
    /// Construct a client using the validated configured service URL.
    pub fn new() -> Self {
        Self {
            http: Client::new(),
            service_url: configured_service_url(),
            session_token: Mutex::new(None),
            current_reviewer: Mutex::new(None),
        }
    }

    /// Join a service-relative API path to the validated base URL.
    pub(crate) fn endpoint(&self, path: &str) -> Result<String, String> {
        self.service_url
            .as_ref()
            .map(|base| format!("{base}{path}"))
            .map_err(Clone::clone)
    }

    /// Return a copy of the authenticated bearer token held in native memory.
    pub(crate) fn token(&self) -> Result<String, String> {
        self.session_token
            .lock()
            .map_err(|_| "native session store is unavailable".to_string())?
            .clone()
            .ok_or_else(|| "sign in before using account administration".to_string())
    }

    /// Replace the native process-memory bearer token after authentication.
    fn store_session(&self, token: String, reviewer: ReviewerAccount) -> Result<(), String> {
        *self
            .session_token
            .lock()
            .map_err(|_| "native session store is unavailable".to_string())? = Some(token);
        *self
            .current_reviewer
            .lock()
            .map_err(|_| "native reviewer store is unavailable".to_string())? = Some(reviewer);
        Ok(())
    }

    /// Refresh the process-memory account projection after a self-profile correction.
    fn store_reviewer(&self, reviewer: ReviewerAccount) -> Result<(), String> {
        *self
            .current_reviewer
            .lock()
            .map_err(|_| "native reviewer store is unavailable".to_string())? = Some(reviewer);
        Ok(())
    }

    /// Return the authenticated reviewer identity retained beside the bearer token.
    pub(crate) fn reviewer(&self) -> Result<ReviewerAccount, String> {
        self.current_reviewer
            .lock()
            .map_err(|_| "native reviewer store is unavailable".to_string())?
            .clone()
            .ok_or_else(|| "sign in before using the signing vault".to_string())
    }

    /// Remove the bearer token from native process memory after logout.
    fn clear_token(&self) -> Result<(), String> {
        *self
            .session_token
            .lock()
            .map_err(|_| "native session store is unavailable".to_string())? = None;
        *self
            .current_reviewer
            .lock()
            .map_err(|_| "native reviewer store is unavailable".to_string())? = None;
        Ok(())
    }
}

/// Validate the configured service URL for the current build profile.
fn configured_service_url() -> Result<String, String> {
    let configured = std::env::var("DRUGREF_REVIEW_SERVICE_URL").ok();
    #[cfg(debug_assertions)]
    let url = configured.unwrap_or_else(|| DEFAULT_DEBUG_SERVICE_URL.into());
    #[cfg(not(debug_assertions))]
    let url = configured.ok_or_else(|| {
        "DRUGREF_REVIEW_SERVICE_URL must be configured for this release build".to_string()
    })?;

    let url = url.trim_end_matches('/').to_string();
    if !cfg!(debug_assertions) && !url.starts_with("https://") {
        return Err("release builds require an https:// review service URL".into());
    }
    if cfg!(debug_assertions)
        && !(url.starts_with("https://")
            || url.starts_with("http://127.0.0.1:")
            || url.starts_with("http://localhost:"))
    {
        return Err("debug HTTP review services must use a loopback address".into());
    }
    Ok(url)
}

/// Decode a successful JSON response or return the service's safe error message.
pub(crate) async fn response_json<T: DeserializeOwned>(
    response: reqwest::Response,
) -> Result<T, String> {
    if response.status().is_success() {
        return response
            .json::<T>()
            .await
            .map_err(|error| format!("review service returned invalid JSON: {error}"));
    }
    let status = response.status();
    let message = response
        .json::<ApiErrorBody>()
        .await
        .map(|body| body.error)
        .unwrap_or_else(|_| format!("review service request failed ({status})"));
    Err(message)
}

/// Send one typed JSON request with optional native bearer authentication.
pub(crate) async fn send_json<B: Serialize, T: DeserializeOwned>(
    client: &AccountClient,
    method: Method,
    path: &str,
    body: Option<&B>,
    authenticated: bool,
) -> Result<T, String> {
    let mut request = client.http.request(method, client.endpoint(path)?);
    if let Some(body) = body {
        request = request.json(body);
    }
    if authenticated {
        request = request.bearer_auth(client.token()?);
    }
    let response = request
        .send()
        .await
        .map_err(|error| format!("cannot reach the review service: {error}"))?;
    response_json(response).await
}

/// Read whether the service database still requires its first administrator.
#[tauri::command]
pub async fn startup_state(
    client: tauri::State<'_, AccountClient>,
) -> Result<BootstrapStatus, String> {
    send_json::<(), _>(&client, Method::GET, BOOTSTRAP_STATUS_PATH, None, false).await
}

/// Create the first administrator and retain its bearer token in native memory.
#[tauri::command]
pub async fn bootstrap_admin(
    input: CreateAccountRequest,
    client: tauri::State<'_, AccountClient>,
) -> Result<ReviewerAccount, String> {
    let grant: SessionGrant = send_json(
        &client,
        Method::POST,
        BOOTSTRAP_ADMIN_PATH,
        Some(&input),
        false,
    )
    .await?;
    let reviewer = grant.reviewer;
    client.store_session(grant.token, reviewer.clone())?;
    Ok(reviewer)
}

/// Authenticate a reviewer and retain the returned bearer token in native memory.
#[tauri::command]
pub async fn login(
    input: LoginRequest,
    client: tauri::State<'_, AccountClient>,
) -> Result<ReviewerAccount, String> {
    let grant: SessionGrant =
        send_json(&client, Method::POST, SESSIONS_PATH, Some(&input), false).await?;
    let reviewer = grant.reviewer;
    client.store_session(grant.token, reviewer.clone())?;
    Ok(reviewer)
}

/// List reviewer accounts through an authenticated native service request.
#[tauri::command]
pub async fn list_users(
    client: tauri::State<'_, AccountClient>,
) -> Result<Vec<ReviewerAccount>, String> {
    send_json::<(), _>(&client, Method::GET, USERS_PATH, None, true).await
}

/// Create a reviewer account through an authenticated native service request.
#[tauri::command]
pub async fn create_user(
    input: CreateAccountRequest,
    client: tauri::State<'_, AccountClient>,
) -> Result<ReviewerAccount, String> {
    send_json(&client, Method::POST, USERS_PATH, Some(&input), true).await
}

/// Append a complete reviewer-profile correction through the authenticated service.
#[tauri::command]
pub async fn update_user_profile(
    reviewer_uuid: uuid::Uuid,
    input: UpdateReviewerProfileRequest,
    client: tauri::State<'_, AccountClient>,
    signing: tauri::State<'_, crate::signing::SigningClient>,
) -> Result<AccountAdministrationResult, String> {
    let current_reviewer_uuid = client.reviewer()?.reviewer_uuid;
    let path = format!("{USERS_PATH}/{reviewer_uuid}/profile");
    let result: AccountAdministrationResult =
        send_json(&client, Method::PUT, &path, Some(&input), true).await?;
    if reviewer_uuid == current_reviewer_uuid {
        if result.reviewer.active {
            client.store_reviewer(result.reviewer.clone())?;
        } else {
            signing.clear_pending()?;
            client.clear_token()?;
        }
    }
    Ok(result)
}

/// Rotate one reviewer's password and clear native state when it is the current user.
#[tauri::command]
pub async fn rotate_user_password(
    reviewer_uuid: uuid::Uuid,
    input: RotateReviewerPasswordRequest,
    client: tauri::State<'_, AccountClient>,
    signing: tauri::State<'_, crate::signing::SigningClient>,
) -> Result<AccountAdministrationResult, String> {
    let current_reviewer_uuid = client.reviewer()?.reviewer_uuid;
    let path = format!("{USERS_PATH}/{reviewer_uuid}/password");
    let result: AccountAdministrationResult =
        send_json(&client, Method::PUT, &path, Some(&input), true).await?;
    if reviewer_uuid == current_reviewer_uuid {
        signing.clear_pending()?;
        client.clear_token()?;
    }
    Ok(result)
}

/// Revoke every live session and clear native state when it is the current user.
#[tauri::command]
pub async fn revoke_user_sessions(
    reviewer_uuid: uuid::Uuid,
    client: tauri::State<'_, AccountClient>,
    signing: tauri::State<'_, crate::signing::SigningClient>,
) -> Result<AccountAdministrationResult, String> {
    let current_reviewer_uuid = client.reviewer()?.reviewer_uuid;
    let path = format!("{USERS_PATH}/{reviewer_uuid}/sessions/revoke");
    let result: AccountAdministrationResult =
        send_json::<(), _>(&client, Method::POST, &path, None, true).await?;
    if reviewer_uuid == current_reviewer_uuid {
        signing.clear_pending()?;
        client.clear_token()?;
    }
    Ok(result)
}

/// Load one filtered review queue page without exposing the bearer token to the WebView.
#[tauri::command]
pub async fn load_review_queue(
    query: ReviewQueueQuery,
    client: tauri::State<'_, AccountClient>,
) -> Result<ReviewQueuePage, String> {
    let response = client
        .http
        .get(client.endpoint(REVIEW_QUEUE_PATH)?)
        .bearer_auth(client.token()?)
        .query(&query)
        .send()
        .await
        .map_err(|error| format!("cannot reach the review service: {error}"))?;
    response_json(response).await
}

/// Load immutable working history for one review target.
#[tauri::command]
pub async fn load_review_record(
    query: ReviewRecordQuery,
    client: tauri::State<'_, AccountClient>,
) -> Result<ReviewRecord, String> {
    let response = client
        .http
        .get(client.endpoint(REVIEW_RECORD_PATH)?)
        .bearer_auth(client.token()?)
        .query(&query)
        .send()
        .await
        .map_err(|error| format!("cannot reach the review service: {error}"))?;
    response_json(response).await
}

/// Load immutable clinical decision history for one review target.
#[tauri::command]
pub async fn load_review_decision(
    query: ReviewRecordQuery,
    client: tauri::State<'_, AccountClient>,
) -> Result<ReviewDecisionRecord, String> {
    let response = client
        .http
        .get(client.endpoint(REVIEW_DECISION_PATH)?)
        .bearer_auth(client.token()?)
        .query(&query)
        .send()
        .await
        .map_err(|error| format!("cannot reach the review service: {error}"))?;
    response_json(response).await
}

/// Record one clinical revision through the authenticated native boundary.
#[tauri::command]
pub async fn create_review_decision(
    input: CreateReviewDecisionRequest,
    client: tauri::State<'_, AccountClient>,
) -> Result<ReviewDecisionRecord, String> {
    send_json(
        &client,
        Method::POST,
        REVIEW_DECISION_PATH,
        Some(&input),
        true,
    )
    .await
}

/// Append one Markdown working note through the authenticated native boundary.
#[tauri::command]
pub async fn create_review_annotation(
    input: CreateAnnotationRequest,
    client: tauri::State<'_, AccountClient>,
) -> Result<ReviewAnnotation, String> {
    send_json(
        &client,
        Method::POST,
        REVIEW_ANNOTATIONS_PATH,
        Some(&input),
        true,
    )
    .await
}

/// Append one citation-only working reference through the authenticated native boundary.
#[tauri::command]
pub async fn create_evidence_reference(
    input: CreateEvidenceReferenceRequest,
    client: tauri::State<'_, AccountClient>,
) -> Result<EvidenceReference, String> {
    send_json(
        &client,
        Method::POST,
        REVIEW_EVIDENCE_REFERENCES_PATH,
        Some(&input),
        true,
    )
    .await
}

/// Revoke the current service session and clear its token from native memory.
#[tauri::command]
pub async fn logout(
    client: tauri::State<'_, AccountClient>,
    signing: tauri::State<'_, crate::signing::SigningClient>,
) -> Result<(), String> {
    let service_result = async {
        let token = client.token()?;
        let response = client
            .http
            .post(client.endpoint(CURRENT_SESSION_PATH)?)
            .bearer_auth(token)
            .send()
            .await
            .map_err(|error| format!("cannot reach the review service: {error}"))?;
        if response.status() != StatusCode::NO_CONTENT {
            let _: serde_json::Value = response_json(response).await?;
        }
        Ok::<(), String>(())
    }
    .await;
    signing.clear_pending()?;
    client.clear_token()?;
    service_result
}

#[cfg(test)]
mod tests {
    use super::{configured_service_url, DEFAULT_DEBUG_SERVICE_URL};

    /// Keep the implicit debug service confined to the local machine.
    #[test]
    fn debug_default_is_loopback_only() {
        if cfg!(debug_assertions) && std::env::var("DRUGREF_REVIEW_SERVICE_URL").is_err() {
            assert_eq!(
                configured_service_url().expect("debug default"),
                DEFAULT_DEBUG_SERVICE_URL
            );
        }
    }
}
