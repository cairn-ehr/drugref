use axum::{
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use reviewer_domain::ApiErrorBody;

#[derive(Debug)]
pub struct AppError {
    status: StatusCode,
    message: String,
}

impl AppError {
    pub fn bad_request(message: impl Into<String>) -> Self {
        Self::new(StatusCode::BAD_REQUEST, message)
    }

    pub fn unauthorized() -> Self {
        Self::new(StatusCode::UNAUTHORIZED, "invalid username or password")
    }

    pub fn forbidden() -> Self {
        Self::new(StatusCode::FORBIDDEN, "administrator access required")
    }

    pub fn conflict(message: impl Into<String>) -> Self {
        Self::new(StatusCode::CONFLICT, message)
    }

    pub fn too_many_requests() -> Self {
        Self::new(
            StatusCode::TOO_MANY_REQUESTS,
            "too many login attempts; try again shortly",
        )
    }

    pub fn internal(message: impl Into<String>) -> Self {
        let detail = message.into();
        tracing::error!(error = %detail, "review service internal error");
        Self::new(StatusCode::INTERNAL_SERVER_ERROR, "review service error")
    }

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
