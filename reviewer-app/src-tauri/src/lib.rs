mod workspace;

#[tauri::command]
fn load_review_workspace() -> Result<workspace::ReviewWorkspace, String> {
    workspace::load_demo_workspace()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![load_review_workspace])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
