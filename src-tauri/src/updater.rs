//! Opt-in application updates (tauri-plugin-updater).
//!
//! Only builds produced with an update endpoint and public key (scripts/build_release.py
//! with MUSTANG_UPDATE_ENDPOINT / MUSTANG_UPDATE_PUBKEY) contain the updater; every other
//! build reports "not configured". Nothing is checked unless the user presses "Check for
//! updates" or turns on automatic checks (off by default).
//!
//! Install sequence: download + signature verification (plugin, minisign public key
//! embedded at build time) → graceful backend stop → pre-update database backup → run the
//! signed installer in passive mode, which replaces the app and relaunches it. User data
//! (outside the install folder) is untouched.

use std::sync::Arc;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_updater::UpdaterExt;

use crate::backend::BackendManager;
use crate::backups;
use crate::paths::DesktopSettings;

pub const AVAILABLE_EVENT: &str = "update://available";
pub const PROGRESS_EVENT: &str = "update://progress";

/// Whether this build was configured with an updater (decided from the bundled config).
pub struct UpdaterConfigured(pub bool);

#[derive(Serialize)]
pub struct UpdateSettings {
    configured: bool,
    auto_check: bool,
    current_version: String,
}

#[derive(Serialize, Clone)]
pub struct UpdateInfo {
    available: bool,
    current_version: String,
    version: Option<String>,
    notes: Option<String>,
    date: Option<String>,
}

#[derive(Serialize, Clone)]
struct Progress {
    downloaded: u64,
    total: Option<u64>,
}

fn configured(app: &AppHandle) -> bool {
    app.try_state::<UpdaterConfigured>().map(|c| c.0).unwrap_or(false)
}

fn log(app: &AppHandle, msg: &str) {
    if let Some(d) = app.try_state::<Arc<BackendManager>>().and_then(|m| m.data_dirs()) {
        d.log(msg);
    }
}

#[tauri::command]
pub fn update_settings(app: AppHandle, manager: State<'_, Arc<BackendManager>>) -> UpdateSettings {
    let auto_check = manager.data_dirs().map(|d| DesktopSettings::load(&d).auto_check_updates()).unwrap_or(false);
    UpdateSettings { configured: configured(&app), auto_check, current_version: app.package_info().version.to_string() }
}

#[tauri::command]
pub fn set_auto_check_updates(manager: State<'_, Arc<BackendManager>>, enabled: bool) -> Result<(), String> {
    let dirs = manager.data_dirs().ok_or("The app is still starting")?;
    let mut settings = DesktopSettings::load(&dirs);
    settings.auto_check_updates = Some(enabled);
    settings.save(&dirs)
}

async fn check(app: &AppHandle) -> Result<UpdateInfo, String> {
    let current_version = app.package_info().version.to_string();
    if !configured(app) {
        return Err("Updates are not configured for this build.".into());
    }
    let update = app.updater().map_err(|e| e.to_string())?.check().await.map_err(|e| format!("Update check failed: {e}"))?;
    log(app, &format!("update check: {}", update.as_ref().map(|u| u.version.as_str()).unwrap_or("up to date")));
    Ok(match update {
        Some(u) => UpdateInfo {
            available: true,
            current_version,
            version: Some(u.version.clone()),
            notes: u.body.clone(),
            date: u.date.map(|d| d.to_string()),
        },
        None => UpdateInfo { available: false, current_version, version: None, notes: None, date: None },
    })
}

#[tauri::command]
pub async fn check_for_update(app: AppHandle) -> Result<UpdateInfo, String> {
    check(&app).await
}

#[tauri::command]
pub async fn install_update(app: AppHandle) -> Result<(), String> {
    if !configured(&app) {
        return Err("Updates are not configured for this build.".into());
    }
    let manager = app.state::<Arc<BackendManager>>().inner().clone();
    let dirs = manager.data_dirs().ok_or("The app is still starting")?;
    let update = app
        .updater()
        .map_err(|e| e.to_string())?
        .check()
        .await
        .map_err(|e| format!("Update check failed: {e}"))?
        .ok_or("Mustang AI is already up to date.")?;
    let (from, to) = (update.current_version.clone(), update.version.clone());
    log(&app, &format!("downloading update {from} -> {to}"));

    // Download and verify the signature before touching anything.
    let progress_app = app.clone();
    let mut downloaded = 0u64;
    let bytes = update
        .download(
            move |chunk, total| {
                downloaded += chunk as u64;
                let _ = progress_app.emit(PROGRESS_EVENT, Progress { downloaded, total });
            },
            || {},
        )
        .await
        .map_err(|e| format!("Update download or signature verification failed: {e}"))?;
    log(&app, &format!("update {to} downloaded and verified ({} bytes)", bytes.len()));

    // Stop the backend (saves partial replies, pauses downloads), back up the database.
    let worker = manager.clone();
    let target = to.clone();
    let backup = tauri::async_runtime::spawn_blocking(move || {
        worker.stop_for_maintenance();
        backups::backup_before_update(&dirs, &from, &target)
    })
    .await
    .map_err(|e| e.to_string())?;
    if let Err(e) = backup {
        manager.restart();
        return Err(format!("Could not back up your data before updating, so the update was not installed: {e}"));
    }

    log(&app, &format!("installing update {to}"));
    // On Windows this launches the installer and exits the app.
    if let Err(e) = update.install(bytes) {
        manager.restart();
        return Err(format!("Update installation failed: {e}"));
    }
    Ok(())
}

/// Automatic check after start-up, only if the user turned it on.
pub fn spawn_auto_check(app: AppHandle) {
    if !configured(&app) {
        return;
    }
    tauri::async_runtime::spawn(async move {
        let enabled = app
            .try_state::<Arc<BackendManager>>()
            .and_then(|m| m.data_dirs())
            .map(|d| DesktopSettings::load(&d).auto_check_updates())
            .unwrap_or(false);
        if !enabled {
            return;
        }
        if let Ok(info) = check(&app).await {
            if info.available {
                let _ = app.emit(AVAILABLE_EVENT, info);
            }
        }
    });
}
