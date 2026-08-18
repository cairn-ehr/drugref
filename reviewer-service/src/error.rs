//! Safe HTTP error translation for reviewer service failures.

use axum::{
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use reviewer_domain::ApiErrorBody;

/// HTTP status and client-safe message returned by reviewer service handlers.
#[derive(Debug)]
pub struct AppError {
    status: StatusCode,
    message: String,
}

impl AppError {
    /// Construct a client-visible validation failure.
    pub fn bad_request(message: impl Into<String>) -> Self {
        Self::new(StatusCode::BAD_REQUEST, message)
    }

    /// Construct the deliberately generic authentication failure.
    pub fn unauthorized() -> Self {
        Self::new(StatusCode::UNAUTHORIZED, "invalid username or password")
    }

    /// Construct an authorisation failure for administrator-only operations.
    pub fn forbidden() -> Self {
        Self::new(StatusCode::FORBIDDEN, "administrator access required")
    }

    /// Construct an authorisation failure with a workflow-specific safe message.
    pub fn forbidden_message(message: impl Into<String>) -> Self {
        Self::new(StatusCode::FORBIDDEN, message)
    }

    /// Construct a client-visible uniqueness or state conflict.
    pub fn conflict(message: impl Into<String>) -> Self {
        Self::new(StatusCode::CONFLICT, message)
    }

    /// Construct a client-visible missing-resource response.
    pub fn not_found(message: impl Into<String>) -> Self {
        Self::new(StatusCode::NOT_FOUND, message)
    }

    /// Construct the fixed response used when login rate limits are exhausted.
    pub fn too_many_requests() -> Self {
        Self::new(
            StatusCode::TOO_MANY_REQUESTS,
            "too many login attempts; try again shortly",
        )
    }

    /// Log internal detail and return a non-sensitive service failure.
    pub fn internal(message: impl Into<String>) -> Self {
        let detail = message.into();
        tracing::error!(error = %detail, "review service internal error");
        Self::new(StatusCode::INTERNAL_SERVER_ERROR, "review service error")
    }

    /// Construct an error from an explicit status and safe message.
    fn new(status: StatusCode, message: impl Into<String>) -> Self {
        Self {
            status,
            message: message.into(),
        }
    }
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        (
            self.status,
            Json(ApiErrorBody {
                error: self.message,
            }),
        )
            .into_response()
    }
}

impl From<sqlx::Error> for AppError {
    fn from(error: sqlx::Error) -> Self {
        Self::internal(error.to_string())
    }
}
