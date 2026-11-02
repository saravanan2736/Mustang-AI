//! Mustang AI desktop shell.
//!
//! A native window around the existing React UI, plus the Rust-owned lifecycle of the
//! Python backend (see `backend.rs`), the system tray (`tray.rs`), Windows session-end
//! handling (`session.rs`) and desktop settings (`desktop.rs`). The `probe` feature replaces
//! the real backend with the Phase B connectivity experiment and is never part of the
//! default build.

use tauri::Manager;

#[cfg(not(feature = "probe"))]
mod backend;
#[cfg(not(feature = "probe"))]
mod backups;
#[cfg(not(feature = "probe"))]
mod desktop;
#[cfg(not(feature = "probe"))]
mod paths;
#[cfg(feature = "probe")]
mod probe;
#[cfg(not(feature = "probe"))]
mod runtime_check;
mod runtime_info;
#[cfg(not(feature = "probe"))]
mod session;
#[cfg(not(feature = "probe"))]
mod tray;
#[cfg(not(feature = "probe"))]
mod updater;

/// Grace period for the backend at Windows logoff/shutdown (Windows shows the block reason).
#[cfg(not(feature = "probe"))]
const SESSION_END_GRACE: std::time::Duration = std::time::Duration::from_secs(8);

pub fn run() {
    let context = tauri::generate_context!();
    // The updater exists only in builds configured with an endpoint and public key.
    #[cfg(not(feature = "probe"))]
    let updater_configured = context.config().plugins.0.contains_key("updater");

    // A second launch never starts another instance (it would compete for VRAM and the
    // database); its arguments are forwarded to the running one.
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|_app, _args, _cwd| {
            #[cfg(not(feature = "probe"))]
            tray::handle_args(_app, &_args);
            #[cfg(feature = "probe")]
            if let Some(w) = _app.get_webview_window("main") {
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init());

    #[cfg(not(feature = "probe"))]
    let builder = builder
        .plugin(
            // Size, position and maximized state; never "hidden", so an app quit from the
            // tray does not start invisible next time.
            tauri_plugin_window_state::Builder::default()
                .with_state_flags(tauri_plugin_window_state::StateFlags::all() & !tauri_plugin_window_state::StateFlags::VISIBLE)
                .build(),
        )
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(
            // The main webview only ever shows the app. Allow-listed https links open in the
            // default browser instead; everything else is refused.
            tauri::plugin::Builder::<tauri::Wry>::new("navigation-guard")
                .on_navigation(|webview, url| {
                    if desktop::is_app_url(url) {
                        return true;
                    }
                    if desktop::is_allowed_external(url.as_str()) {
                        let _ = desktop::open_allowed(webview.app_handle(), url.as_str());
                    } else if let Some(d) = manager_dirs(webview.app_handle()) {
                        d.log(&format!("blocked navigation: {url}"));
                    }
                    false
                })
                .build(),
        )
        .setup(move |app| {
            app.manage(updater::UpdaterConfigured(updater_configured));
            if std::env::args().any(|a| a == "--quit") {
                std::process::exit(0); // no running instance to quit
            }
            let manager = backend::BackendManager::new(app.handle().clone());
            app.manage(manager.clone());
            tray::create(app.handle())?;
            if let Some(window) = app.get_webview_window("main") {
                let handle = app.handle().clone();
                let on_end = move || {
                    use tauri_plugin_window_state::{AppHandleExt, StateFlags};
                    if let Some(d) = manager_dirs(&handle) {
                        d.log("Windows session ending: stopping the backend");
                    }
                    let _ = handle.save_window_state(StateFlags::all() & !StateFlags::VISIBLE);
                    if let Some(m) = handle.try_state::<std::sync::Arc<backend::BackendManager>>() {
                        m.shutdown_with(SESSION_END_GRACE);
                    }
                    if let Some(d) = manager_dirs(&handle) {
                        d.log("Windows session ending: backend stopped, exiting");
                    }
                    std::process::exit(0);
                };
                if let Err(e) = session::install(&window, Box::new(on_end)) {
                    eprintln!("[desktop] session-end handler not installed: {e}");
                }
            }
            manager.start();
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" && desktop::close_to_tray_enabled(window.app_handle()) {
                    api.prevent_close();
                    let _ = window.hide();
                    desktop::hint_close_to_tray_once(window.app_handle());
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            runtime_info::desktop_runtime_info,
            backend::backend_info,
            backend::backend_restart,
            desktop::desktop_settings,
            desktop::set_close_to_tray,
            desktop::choose_models_folder,
            desktop::reset_models_folder,
            desktop::open_folder,
            desktop::open_external,
            backups::list_backups,
            backups::restore_backup,
            updater::update_settings,
            updater::set_auto_check_updates,
            updater::check_for_update,
            updater::install_update,
        ]);

    #[cfg(not(feature = "probe"))]
    let builder = if updater_configured { builder.plugin(tauri_plugin_updater::Builder::new().build()) } else { builder };

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
        .build(context)
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

#[cfg(not(feature = "probe"))]
fn manager_dirs(app: &tauri::AppHandle) -> Option<paths::DataDirs> {
    app.try_state::<std::sync::Arc<backend::BackendManager>>().and_then(|m| m.data_dirs())
}
