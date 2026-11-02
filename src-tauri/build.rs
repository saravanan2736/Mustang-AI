fn main() {
    // Declaring the app's commands makes each one require an explicit grant in a
    // capability file, instead of being callable from any window by default.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "desktop_runtime_info",
            "backend_info",
            "backend_restart",
            "desktop_settings",
            "set_close_to_tray",
            "choose_models_folder",
            "reset_models_folder",
            "open_folder",
            "open_external",
            "list_backups",
            "restore_backup",
            "update_settings",
            "set_auto_check_updates",
            "check_for_update",
            "install_update",
            "probe_info",
            "probe_restart_backend",
            "probe_stop_backend",
            "probe_raw_host_check",
            "probe_backend_logs",
            "probe_report",
        ]),
    ))
    .expect("failed to run tauri-build");
}
