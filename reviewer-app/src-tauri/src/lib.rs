mod accounts;
mod workspace;

#[tauri::command]
fn load_review_workspace(
    client: tauri::State<'_, accounts::AccountClient>,
) -> Result<workspace::ReviewWorkspace, String> {
    if !client.has_session() {
        return Err("sign in before loading the reviewer workspace".into());
    }
    workspace::load_demo_workspace()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(accounts::AccountClient::new())
        .invoke_handler(tauri::generate_handler![
            load_review_workspace,
            accounts::startup_state,
            accounts::bootstrap_admin,
            accounts::login,
            accounts::list_users,
            accounts::create_user,
            accounts::logout,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
