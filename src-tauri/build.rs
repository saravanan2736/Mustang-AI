fn main() {
    // Declaring the app's commands makes each one require an explicit grant in a
    // capability file, instead of being callable from any window by default.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "desktop_runtime_info",
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
