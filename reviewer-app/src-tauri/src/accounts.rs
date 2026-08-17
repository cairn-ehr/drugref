use std::sync::Mutex;

use reqwest::{Client, Method, StatusCode};
use reviewer_domain::{
    ApiErrorBody, BootstrapStatus, CreateAccountRequest, LoginRequest, ReviewerAccount,
    SessionGrant,
};
use serde::{de::DeserializeOwned, Serialize};

pub struct AccountClient {
    http: Client,
    service_url: Result<String, String>,
    session_token: Mutex<Option<String>>,
}

impl AccountClient {
    pub fn new() -> Self {
        Self {
            http: Client::new(),
            service_url: configured_service_url(),
            session_token: Mutex::new(None),
        }
    }

    fn endpoint(&self, path: &str) -> Result<String, String> {
        self.service_url
            .as_ref()
            .map(|base| format!("{base}{path}"))
            .map_err(Clone::clone)
    }

    fn token(&self) -> Result<String, String> {
        self.session_token
            .lock()
            .map_err(|_| "native session store is unavailable".to_string())?
            .clone()
            .ok_or_else(|| "sign in before using account administration".to_string())
    }

    fn store_token(&self, token: String) -> Result<(), String> {
        *self
            .session_token
            .lock()
            .map_err(|_| "native session store is unavailable".to_string())? = Some(token);
        Ok(())
    }

    fn clear_token(&self) -> Result<(), String> {
        *self
            .session_token
            .lock()
            .map_err(|_| "native session store is unavailable".to_string())? = None;
        Ok(())
    }

    pub fn has_session(&self) -> bool {
        self.session_token
            .lock()
            .map(|token| token.is_some())
            .unwrap_or(false)
    }
}

fn configured_service_url() -> Result<String, String> {
    let configured = std::env::var("DRUGREF_REVIEW_SERVICE_URL").ok();
    #[cfg(debug_assertions)]
    let url = configured.unwrap_or_else(|| "http://127.0.0.1:8787".into());
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

async fn response_json<T: DeserializeOwned>(response: reqwest::Response) -> Result<T, String> {
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

async fn send_json<B: Serialize, T: DeserializeOwned>(
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

#[tauri::command]
pub async fn startup_state(
    client: tauri::State<'_, AccountClient>,
) -> Result<BootstrapStatus, String> {
    send_json::<(), _>(&client, Method::GET, "/v1/bootstrap/status", None, false).await
}

#[tauri::command]
pub async fn bootstrap_admin(
    input: CreateAccountRequest,
    client: tauri::State<'_, AccountClient>,
) -> Result<ReviewerAccount, String> {
    let grant: SessionGrant = send_json(
        &client,
        Method::POST,
        "/v1/bootstrap/admin",
        Some(&input),
        false,
    )
    .await?;
    client.store_token(grant.token)?;
    Ok(grant.reviewer)
}

#[tauri::command]
pub async fn login(
    input: LoginRequest,
    client: tauri::State<'_, AccountClient>,
) -> Result<ReviewerAccount, String> {
    let grant: SessionGrant =
        send_json(&client, Method::POST, "/v1/sessions", Some(&input), false).await?;
    client.store_token(grant.token)?;
    Ok(grant.reviewer)
}

#[tauri::command]
pub async fn list_users(
    client: tauri::State<'_, AccountClient>,
) -> Result<Vec<ReviewerAccount>, String> {
    send_json::<(), _>(&client, Method::GET, "/v1/users", None, true).await
}

#[tauri::command]
pub async fn create_user(
    input: CreateAccountRequest,
    client: tauri::State<'_, AccountClient>,
) -> Result<ReviewerAccount, String> {
    send_json(&client, Method::POST, "/v1/users", Some(&input), true).await
}

#[tauri::command]
pub async fn logout(client: tauri::State<'_, AccountClient>) -> Result<(), String> {
    let token = client.token()?;
    let response = client
        .http
        .post(client.endpoint("/v1/sessions/current")?)
        .bearer_auth(token)
        .send()
        .await
        .map_err(|error| format!("cannot reach the review service: {error}"))?;
    if response.status() != StatusCode::NO_CONTENT {
        let _: serde_json::Value = response_json(response).await?;
    }
    client.clear_token()
}

#[cfg(test)]
mod tests {
    use super::configured_service_url;

    #[test]
    fn debug_default_is_loopback_only() {
        if cfg!(debug_assertions) && std::env::var("DRUGREF_REVIEW_SERVICE_URL").is_err() {
            assert_eq!(
                configured_service_url().expect("debug default"),
                "http://127.0.0.1:8787"
            );
        }
    }
}
