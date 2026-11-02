use serde::Serialize;
use tauri::AppHandle;

/// Backend connection state as the shell will eventually expose it to the UI.
/// In Phase B the shell does not manage the production backend yet, so the
/// default build reports `managed: false` and never carries a credential.
#[derive(Serialize)]
pub struct BackendStatus {
    pub managed: bool,
    pub base_url: Option<String>,
    pub ready: bool,
    pub pid: Option<u32>,
}

#[derive(Serialize)]
pub struct DesktopRuntimeInfo {
    pub app_name: String,
    pub app_version: String,
    pub tauri_version: &'static str,
    pub webview_version: Option<String>,
    pub os: &'static str,
    pub arch: &'static str,
    pub build_profile: &'static str,
    pub probe_build: bool,
    pub backend: BackendStatus,
}

#[tauri::command]
pub fn desktop_runtime_info(app: AppHandle) -> DesktopRuntimeInfo {
    let pkg = app.package_info();
    DesktopRuntimeInfo {
        app_name: pkg.name.clone(),
        app_version: pkg.version.to_string(),
        tauri_version: tauri::VERSION,
        webview_version: tauri::webview_version().ok(),
        os: std::env::consts::OS,
        arch: std::env::consts::ARCH,
        build_profile: if cfg!(debug_assertions) { "debug" } else { "release" },
        probe_build: cfg!(feature = "probe"),
        backend: backend_status(&app),
    }
}

#[cfg(not(feature = "probe"))]
fn backend_status(_app: &AppHandle) -> BackendStatus {
    BackendStatus { managed: false, base_url: None, ready: false, pid: None }
}

#[cfg(feature = "probe")]
fn backend_status(app: &AppHandle) -> BackendStatus {
    crate::probe::backend_status(app)
}
