//! Mustang AI desktop shell (M4 Phase B).
//!
//! The default build is only a native window around the existing React UI plus one
//! read-only IPC command. The `probe` feature adds the Phase B connectivity
//! experiment (a test-only mock backend under a Job Object); it is never part of
//! the default build.

use tauri::Manager;

mod runtime_info;
#[cfg(feature = "probe")]
mod probe;

pub fn run() {
    let builder = tauri::Builder::default().plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
        // A second launch focuses the existing window instead of starting another
        // instance that would compete for VRAM and the database.
        if let Some(w) = app.get_webview_window("main") {
            let _ = w.unminimize();
            let _ = w.show();
            let _ = w.set_focus();
        }
    }));

    #[cfg(not(feature = "probe"))]
    let builder = builder.invoke_handler(tauri::generate_handler![runtime_info::desktop_runtime_info]);

    #[cfg(feature = "probe")]
    let builder = builder
        .setup(probe::setup)
        .on_window_event(probe::on_window_event)
        .invoke_handler(tauri::generate_handler![
            runtime_info::desktop_runtime_info,
            probe::probe_info,
            probe::probe_restart_backend,
            probe::probe_stop_backend,
            probe::probe_raw_host_check,
            probe::probe_backend_logs,
            probe::probe_report,
        ]);

    builder
        .run(tauri::generate_context!())
        .expect("error while running Mustang AI desktop shell");
}
