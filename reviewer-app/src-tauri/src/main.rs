// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

/// Start the platform-specific shell around the documented native library.
fn main() {
    reviewer_app_lib::run()
}
