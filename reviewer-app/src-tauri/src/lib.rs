mod accounts;

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
