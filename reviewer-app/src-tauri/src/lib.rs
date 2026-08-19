//! Native trust boundary for the Drugref Reviewer desktop application.
//!
//! The Tauri core retains bearer tokens in process memory and exposes only typed,
//! narrowly scoped commands to the WebView.
#![deny(missing_docs)]

mod accounts;
mod signing;

use tauri::Manager;

/// Configure native state, expose approved commands, and run the Tauri event loop.
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(accounts::AccountClient::new())
        .setup(|app| {
            let directory = app
                .path()
                .app_local_data_dir()
                .map_err(|error| format!("cannot resolve local signing-vault path: {error}"))?;
            app.manage(signing::SigningClient::new(directory));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            accounts::startup_state,
            accounts::bootstrap_admin,
            accounts::login,
            accounts::load_review_queue,
            accounts::load_review_record,
            accounts::load_review_decision,
            accounts::create_review_decision,
            accounts::create_review_annotation,
            accounts::create_evidence_reference,
            accounts::list_users,
            accounts::create_user,
            accounts::update_user_profile,
            accounts::rotate_user_password,
            accounts::revoke_user_sessions,
            signing::signing_status,
            signing::load_pending_signatures,
            signing::enrol_local_signing_key,
            signing::replace_local_signing_key,
            signing::prepare_review_signature,
            signing::complete_review_signature,
            accounts::logout,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
