//! System tray, single-instance command-line actions, and keep-awake.
//!
//! Tray menu: Show / status line / Unload model / Quit. The same actions are reachable from
//! a second launch (`mustang-desktop.exe --show | --unload-model | --quit`), forwarded to
//! the running instance by the single-instance plugin.
//!
//! A monitor thread keeps the status line current and, while a model is generating or a
//! download is running, asks Windows not to sleep (SetThreadExecutionState).

use std::sync::Arc;
use std::thread;
use std::time::Duration;

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager};

use crate::backend::{http_json, BackendManager, BackendState};

const MONITOR_INTERVAL: Duration = Duration::from_secs(2);

pub fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn log(app: &AppHandle, message: &str) {
    if let Some(d) = app.try_state::<Arc<BackendManager>>().and_then(|m| m.data_dirs()) {
        d.log(message);
    }
}

/// Run a tray / command-line action: "show", "unload", "quit".
pub fn perform(app: &AppHandle, action: &str) {
    log(app, &format!("action: {action}"));
    match action {
        "show" => show_main(app),
        "unload" => {
            let Some(manager) = app.try_state::<Arc<BackendManager>>() else { return };
            if let Some((port, token)) = manager.api() {
                thread::spawn(move || {
                    let _ = http_json(port, "POST", "/api/models/unload", &token);
                });
            }
        }
        "quit" => app.exit(0), // RunEvent::Exit then stops the backend gracefully
        _ => {}
    }
}

/// Action for a second launch's arguments; a plain second launch shows the window.
pub fn action_for_args(args: &[String]) -> &'static str {
    if args.iter().any(|a| a == "--quit") {
        "quit"
    } else if args.iter().any(|a| a == "--unload-model") {
        "unload"
    } else {
        "show"
    }
}

/// Arguments of a second launch, forwarded by the single-instance plugin.
pub fn handle_args(app: &AppHandle, args: &[String]) {
    perform(app, action_for_args(args));
}

pub fn create(app: &AppHandle) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "Show Mustang AI", true, None::<&str>)?;
    let status = MenuItem::with_id(app, "status", "Engine: starting…", false, None::<&str>)?;
    let unload = MenuItem::with_id(app, "unload", "Unload model", false, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Mustang AI", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[&show, &status, &PredefinedMenuItem::separator(app)?, &unload, &PredefinedMenuItem::separator(app)?, &quit],
    )?;

    let mut builder = TrayIconBuilder::with_id("main")
        .tooltip("Mustang AI")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| perform(app, event.id.as_ref()))
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                show_main(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    let tray = builder.build(app)?;

    let app = app.clone();
    thread::spawn(move || monitor(app, tray, status, unload));
    Ok(())
}

struct Snapshot {
    text: String,
    can_unload: bool,
    busy: bool,
}

fn snapshot(manager: &BackendManager) -> Snapshot {
    let idle = |text: &str| Snapshot { text: text.into(), can_unload: false, busy: false };
    match manager.state() {
        BackendState::Ready { .. } => {}
        BackendState::Starting { .. } | BackendState::Restarting { .. } => return idle("Engine: starting…"),
        BackendState::Failed { .. } => return idle("Engine: stopped (error)"),
        BackendState::Stopping | BackendState::Stopped => return idle("Engine: stopping…"),
    }
    let Some((port, token)) = manager.api() else { return idle("Engine: starting…") };
    let engine = http_json(port, "GET", "/api/models/status", &token).map(|(_, v)| v).unwrap_or_default();
    let downloads = http_json(port, "GET", "/api/downloads", &token).map(|(_, v)| v).unwrap_or_default();

    let state = engine["state"].as_str().unwrap_or("UNLOADED");
    let model = engine["model_name"].as_str().unwrap_or("model");
    let downloading = downloads
        .as_array()
        .map(|items| items.iter().filter(|d| matches!(d["status"].as_str(), Some("DOWNLOADING") | Some("VERIFYING"))).count())
        .unwrap_or(0);
    let mut text = match state {
        "READY" => format!("Ready: {model}"),
        "GENERATING" => format!("Generating: {model}"),
        "STARTING" => "Loading model…".to_string(),
        "ERROR" => "Model error".to_string(),
        _ => "No model loaded".to_string(),
    };
    if downloading > 0 {
        text.push_str(&format!(" · {downloading} download{}", if downloading == 1 { "" } else { "s" }));
    }
    Snapshot { text, can_unload: matches!(state, "READY" | "GENERATING"), busy: state == "GENERATING" || downloading > 0 }
}

fn monitor(app: AppHandle, tray: tauri::tray::TrayIcon, status: MenuItem<tauri::Wry>, unload: MenuItem<tauri::Wry>) {
    let mut last_text = String::new();
    let mut awake = false;
    let mut announced = false;
    loop {
        thread::sleep(MONITOR_INTERVAL);
        let Some(manager) = app.try_state::<Arc<BackendManager>>() else { continue };
        if !announced && manager.data_dirs().is_some() {
            log(&app, "tray icon active");
            announced = true;
        }
        if matches!(manager.state(), BackendState::Stopped) {
            keep_awake(false);
            return;
        }
        let snap = snapshot(&manager);
        if snap.text != last_text {
            let _ = status.set_text(&snap.text);
            let _ = tray.set_tooltip(Some(format!("Mustang AI · {}", snap.text)));
            last_text = snap.text.clone();
        }
        let _ = unload.set_enabled(snap.can_unload);
        if snap.busy != awake {
            keep_awake(snap.busy);
            awake = snap.busy;
            log(&app, &format!("keep-awake {}", if awake { "on (generating or downloading)" } else { "off" }));
        }
    }
}

/// Per-thread request to keep the system (not the display) awake; must be called from the
/// long-lived monitor thread.
fn keep_awake(on: bool) {
    use windows_sys::Win32::System::Power::{SetThreadExecutionState, ES_CONTINUOUS, ES_SYSTEM_REQUIRED};
    let flags = if on { ES_CONTINUOUS | ES_SYSTEM_REQUIRED } else { ES_CONTINUOUS };
    unsafe { SetThreadExecutionState(flags) };
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn second_launch_arguments_map_to_actions() {
        let args = |a: &[&str]| a.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(action_for_args(&args(&["mustang-desktop.exe"])), "show");
        assert_eq!(action_for_args(&args(&["mustang-desktop.exe", "--unload-model"])), "unload");
        assert_eq!(action_for_args(&args(&["mustang-desktop.exe", "--quit"])), "quit");
        assert_eq!(action_for_args(&args(&["mustang-desktop.exe", "--unload-model", "--quit"])), "quit");
    }
}
