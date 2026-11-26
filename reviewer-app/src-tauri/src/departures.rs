//! Tauri commands for departed graded pairs (#283): list them and acknowledge one
//! drug's departure, through the authenticated reviewer service. The service holds
//! every rule (db/087's SQL); this adapter only forwards typed requests.

use reqwest::Method;
use reviewer_domain::{AcknowledgeDepartureRequest, DepartureAcknowledgement, GradedPairDeparture};

use crate::accounts::{send_json, AccountClient};

const DEPARTURES_PATH: &str = "/v1/graded-pair-departures";
const ACKNOWLEDGEMENTS_PATH: &str = "/v1/graded-pair-departures/acknowledgements";

/// Load every open departure for the signed-in reviewer.
#[tauri::command]
pub async fn load_graded_pair_departures(
    client: tauri::State<'_, AccountClient>,
) -> Result<Vec<GradedPairDeparture>, String> {
    send_json::<(), _>(&client, Method::GET, DEPARTURES_PATH, None, true).await
}

/// Acknowledge one drug's departure as the signed-in reviewer.
#[tauri::command]
pub async fn acknowledge_graded_pair_departure(
    input: AcknowledgeDepartureRequest,
    client: tauri::State<'_, AccountClient>,
) -> Result<DepartureAcknowledgement, String> {
    send_json(
        &client,
        Method::POST,
        ACKNOWLEDGEMENTS_PATH,
        Some(&input),
        true,
    )
    .await
}
