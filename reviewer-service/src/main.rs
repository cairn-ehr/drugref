//! Process entry point for the Drugref reviewer HTTP service.

use std::{env, net::SocketAddr};

use reviewer_service::{check_schema, router, AppState};
use sqlx::postgres::PgPoolOptions;
use tracing_subscriber::EnvFilter;

const DEFAULT_LOG_FILTER: &str = "info";
const DEFAULT_BIND_ADDRESS: &str = "127.0.0.1:8787";
const DATABASE_POOL_MAX_CONNECTIONS: u32 = 10;

/// Configure observability, validate the database, and serve until graceful shutdown.
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| DEFAULT_LOG_FILTER.into()),
        )
        .init();

    let database_url = env::var("DATABASE_URL")
        .map_err(|_| "DATABASE_URL must point at the migrated Drugref PostgreSQL database")?;
    let bind_address: SocketAddr = env::var("DRUGREF_REVIEW_BIND")
        .unwrap_or_else(|_| DEFAULT_BIND_ADDRESS.into())
        .parse()?;
    let pool = PgPoolOptions::new()
        .max_connections(DATABASE_POOL_MAX_CONNECTIONS)
        .connect(&database_url)
        .await?;
    check_schema(&pool).await.map_err(|error| {
        tracing::error!(?error, "reviewer schema check failed");
        "reviewer schema check failed"
    })?;

    let listener = tokio::net::TcpListener::bind(bind_address).await?;
    tracing::info!(%bind_address, "Drugref reviewer service listening");
    axum::serve(
        listener,
        router(AppState::new(pool)).into_make_service_with_connect_info::<SocketAddr>(),
    )
    .with_graceful_shutdown(async {
        let _ = tokio::signal::ctrl_c().await;
    })
    .await?;
    Ok(())
}
