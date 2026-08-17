//! Native trust boundary for the Drugref Reviewer desktop application.
//!
//! The Tauri core retains bearer tokens in process memory and exposes only typed,
//! narrowly scoped commands to the WebView.
#![deny(missing_docs)]

mod accounts;

/// Configure native state, expose approved commands, and run the Tauri event loop.
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(accounts::AccountClient::new())
        .invoke_handler(tauri::generate_handler![
            accounts::startup_state,
            accounts::bootstrap_admin,
            accounts::login,
            accounts::load_review_queue,
            accounts::list_users,
            accounts::create_user,
            accounts::logout,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
