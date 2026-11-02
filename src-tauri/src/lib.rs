//! Mustang AI desktop shell.
//!
//! A native window around the existing React UI, plus the Rust-owned lifecycle of the
//! Python backend (see `backend.rs`). The `probe` feature replaces the real backend with
//! the Phase B connectivity experiment and is never part of the default build.

use tauri::Manager;

#[cfg(not(feature = "probe"))]
mod paths;
mod runtime_info;
#[cfg(not(feature = "probe"))]
mod backend;
#[cfg(not(feature = "probe"))]
mod runtime_check;
#[cfg(feature = "probe")]
mod probe;

pub fn run() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // A second launch focuses the existing window instead of starting another
            // instance that would compete for VRAM and the database.
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init());

    #[cfg(not(feature = "probe"))]
    let builder = builder
        .setup(|app| {
            let manager = backend::BackendManager::new(app.handle().clone());
            app.manage(manager.clone());
            manager.start();
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            runtime_info::desktop_runtime_info,
            backend::backend_info,
            backend::backend_restart,
        ]);

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

    let app = builder
        .build(tauri::generate_context!())
        .expect("error while building Mustang AI desktop shell");

    app.run(|_handle, _event| {
        #[cfg(not(feature = "probe"))]
        if let tauri::RunEvent::Exit = _event {
            // Graceful backend stop (persists partial output, pauses downloads, unloads the
            // model). If this process dies instead, the Job Object kills the tree.
            if let Some(manager) = _handle.try_state::<std::sync::Arc<backend::BackendManager>>() {
                manager.shutdown();
            }
        }
    });
}
