//! Desktop settings commands (Settings → Desktop tab), folder helpers and the policy for
//! links that leave the app.
//!
//! Nothing here gives the webview filesystem or shell access: folders are chosen in a
//! native dialog run by Rust and validated before use, "open folder" only opens the app's
//! own folders, and external links open in the default browser only for an allow-list.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use serde::Serialize;
use tauri::{AppHandle, Manager, State, Url};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
use tauri_plugin_opener::OpenerExt;

use crate::backend::BackendManager;
use crate::paths::DesktopSettings;

/// Hosts the app may open in the default browser (exact host or any subdomain).
const EXTERNAL_HOSTS: &[&str] = &["huggingface.co"];

#[derive(Serialize)]
pub struct DesktopSettingsView {
    models_dir: Option<String>,
    models_dir_is_default: bool,
    close_to_tray: bool,
    data_root: Option<String>,
    log_dir: Option<String>,
}

#[tauri::command]
pub fn desktop_settings(manager: State<'_, Arc<BackendManager>>) -> DesktopSettingsView {
    let dirs = manager.data_dirs();
    let settings = dirs.as_ref().map(DesktopSettings::load).unwrap_or_default();
    DesktopSettingsView {
        models_dir: manager.models_dir().map(|p| p.display().to_string()),
        models_dir_is_default: settings.models_dir.is_none(),
        close_to_tray: settings.close_to_tray(),
        data_root: dirs.as_ref().map(|d| d.root.display().to_string()),
        log_dir: dirs.as_ref().map(|d| d.logs.display().to_string()),
    }
}

#[tauri::command]
pub fn set_close_to_tray(manager: State<'_, Arc<BackendManager>>, enabled: bool) -> Result<(), String> {
    let dirs = manager.data_dirs().ok_or("The app is still starting")?;
    let mut settings = DesktopSettings::load(&dirs);
    settings.close_to_tray = Some(enabled);
    settings.save(&dirs)?;
    dirs.log(&format!("close to tray: {enabled}"));
    Ok(())
}

/// Native folder picker → validation → persist → backend restart. `None` = cancelled.
#[tauri::command(async)]
pub fn choose_models_folder(app: AppHandle, manager: State<'_, Arc<BackendManager>>) -> Result<Option<String>, String> {
    // Test hook: the folder the native dialog would return (the dialog itself is the OS's).
    let path = match std::env::var_os("MUSTANG_DESKTOP_TEST_PICK_FOLDER") {
        Some(p) => PathBuf::from(p),
        None => {
            let Some(picked) = app.dialog().file().set_title("Choose the folder for your GGUF models").blocking_pick_folder() else {
                return Ok(None);
            };
            picked.into_path().map_err(|e| e.to_string())?
        }
    };
    let validated = validate_models_folder(&path, &forbidden_roots(&app, &manager))?;
    let manager = manager.inner().clone();
    manager.set_models_dir(Some(validated)).map(|p| Some(p.display().to_string()))
}

#[tauri::command]
pub fn reset_models_folder(manager: State<'_, Arc<BackendManager>>) -> Result<String, String> {
    let manager = manager.inner().clone();
    manager.set_models_dir(None).map(|p| p.display().to_string())
}

/// Open one of the app's own folders in Explorer. `which` is "models" or "logs".
#[tauri::command]
pub fn open_folder(manager: State<'_, Arc<BackendManager>>, which: String) -> Result<(), String> {
    let path = match which.as_str() {
        "models" => manager.models_dir(),
        "logs" => manager.data_dirs().map(|d| d.logs),
        _ => return Err(format!("unknown folder '{which}'")),
    }
    .ok_or("The app is still starting")?;
    let explorer = std::env::var_os("SystemRoot").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(r"C:\Windows")).join("explorer.exe");
    std::process::Command::new(explorer).arg(&path).spawn().map(|_| ()).map_err(|e| e.to_string())
}

/// Open an allow-listed https link in the user's default browser.
#[tauri::command]
pub fn open_external(app: AppHandle, url: String) -> Result<(), String> {
    if !is_allowed_external(&url) {
        if let Some(m) = app.try_state::<Arc<BackendManager>>() {
            if let Some(d) = m.data_dirs() {
                d.log(&format!("blocked external link: {url}"));
            }
        }
        return Err("This link cannot be opened from Mustang AI.".into());
    }
    open_allowed(&app, &url)
}

pub fn open_allowed(app: &AppHandle, url: &str) -> Result<(), String> {
    if let Some(d) = app.try_state::<Arc<BackendManager>>().and_then(|m| m.data_dirs()) {
        d.log(&format!("opening external link: {url}"));
    }
    // Test hook: record instead of launching the browser.
    if std::env::var("MUSTANG_DESKTOP_DRY_RUN_OPEN").as_deref() == Ok("1") {
        return Ok(());
    }
    app.opener().open_url(url, None::<&str>).map_err(|e| e.to_string())
}

/// https only, no credentials or custom port, host = an allow-listed domain or subdomain.
pub fn is_allowed_external(url: &str) -> bool {
    let Ok(u) = Url::parse(url) else { return false };
    if u.scheme() != "https" || !u.username().is_empty() || u.password().is_some() || u.port().is_some() {
        return false;
    }
    let Some(host) = u.host_str() else { return false };
    let host = host.to_ascii_lowercase();
    EXTERNAL_HOSTS.iter().any(|h| host == *h || host.ends_with(&format!(".{h}")))
}

/// URLs the main webview may navigate to: the app itself and Tauri's IPC.
pub fn is_app_url(url: &Url) -> bool {
    match url.scheme() {
        "tauri" | "ipc" | "asset" | "about" => true,
        "http" | "https" => match url.host_str() {
            Some("tauri.localhost") | Some("ipc.localhost") | Some("asset.localhost") => true,
            #[cfg(debug_assertions)]
            Some("127.0.0.1") | Some("localhost") => matches!(url.port(), Some(5173) | Some(1430)),
            _ => false,
        },
        _ => false,
    }
}

fn forbidden_roots(app: &AppHandle, manager: &BackendManager) -> Vec<PathBuf> {
    let mut roots: Vec<PathBuf> = ["SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData"]
        .iter()
        .filter_map(std::env::var_os)
        .map(PathBuf::from)
        .collect();
    if let Ok(res) = app.path().resource_dir() {
        roots.push(res);
    }
    if let Some(d) = manager.data_dirs() {
        roots.push(d.data.clone());
        roots.push(d.logs.clone());
    }
    roots
}

fn lower(p: &Path) -> String {
    p.to_string_lossy().trim_start_matches(r"\\?\").replace('/', "\\").trim_end_matches('\\').to_ascii_lowercase()
}

/// A models folder must be an existing, writable, local directory outside Windows,
/// program and app-internal folders.
pub fn validate_models_folder(path: &Path, forbidden: &[PathBuf]) -> Result<PathBuf, String> {
    let shown = path.display();
    let text = path.to_string_lossy();
    if text.starts_with(r"\\") && !text.starts_with(r"\\?\") {
        return Err(format!("{shown} is a network location. Choose a folder on a local drive."));
    }
    if !path.is_absolute() || !path.is_dir() {
        return Err(format!("{shown} is not an existing folder."));
    }
    let candidate = lower(path);
    for root in forbidden {
        let r = lower(root);
        if !r.is_empty() && (candidate == r || candidate.starts_with(&format!("{r}\\"))) {
            return Err(format!("{shown} is inside a system or application folder ({}). Choose another folder.", root.display()));
        }
    }
    let probe = path.join(".mustang-write-test");
    std::fs::write(&probe, b"").map_err(|e| format!("Mustang AI cannot write to {shown}: {e}"))?;
    let _ = std::fs::remove_file(&probe);
    Ok(path.to_path_buf())
}

/// One-time hint after the first close-to-tray, so the app does not seem to vanish.
pub fn hint_close_to_tray_once(app: &AppHandle) {
    let Some(manager) = app.try_state::<Arc<BackendManager>>() else { return };
    let Some(dirs) = manager.data_dirs() else { return };
    let marker = dirs.root.join(".tray-hint-shown");
    if marker.exists() {
        return;
    }
    let _ = std::fs::write(&marker, b"");
    app.dialog()
        .message("Mustang AI keeps running in the system tray so your model stays loaded.\n\nUse the tray icon to reopen it or choose Quit. You can change this in Settings → Desktop.")
        .title("Mustang AI is still running")
        .kind(MessageDialogKind::Info)
        .show(|_| {});
}

/// Whether closing the main window should hide it instead of quitting.
pub fn close_to_tray_enabled(app: &AppHandle) -> bool {
    let Some(manager) = app.try_state::<Arc<BackendManager>>() else { return false };
    if matches!(manager.state(), crate::backend::BackendState::Failed { .. }) {
        return false; // nothing useful keeps running; closing should quit
    }
    manager.data_dirs().map(|d| DesktopSettings::load(&d).close_to_tray()).unwrap_or(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn external_link_allow_list() {
        assert!(is_allowed_external("https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF"));
        assert!(is_allowed_external("https://cdn-lfs.huggingface.co/x"));
        for bad in [
            "http://huggingface.co/x",
            "https://huggingface.co.evil.example/x",
            "https://evilhuggingface.co/x",
            "https://user:pw@huggingface.co/x",
            "https://huggingface.co:8443/x",
            "file:///C:/Windows/System32/calc.exe",
            "javascript:alert(1)",
            "https://example.com",
            "not a url",
        ] {
            assert!(!is_allowed_external(bad), "{bad} must be rejected");
        }
    }

    #[test]
    fn app_urls() {
        assert!(is_app_url(&Url::parse("http://tauri.localhost/").unwrap()));
        assert!(is_app_url(&Url::parse("http://ipc.localhost/backend_info").unwrap()));
        assert!(!is_app_url(&Url::parse("https://example.com/").unwrap()));
        assert!(!is_app_url(&Url::parse("http://127.0.0.1:18000/").unwrap()));
        assert!(!is_app_url(&Url::parse("file:///C:/").unwrap()));
    }

    #[test]
    fn models_folder_validation() {
        let base = std::env::temp_dir().join(format!("mustang-modelsdir-{}", std::process::id()));
        let ok = base.join("My Models");
        std::fs::create_dir_all(&ok).unwrap();
        let program_files = base.join("Program Files");
        std::fs::create_dir_all(program_files.join("Vendor")).unwrap();
        let forbidden = vec![program_files.clone()];

        assert_eq!(validate_models_folder(&ok, &forbidden).unwrap(), ok);
        assert!(!ok.join(".mustang-write-test").exists(), "probe file must be removed");
        assert!(validate_models_folder(&program_files.join("Vendor"), &forbidden).unwrap_err().contains("system or application folder"));
        assert!(validate_models_folder(&program_files, &forbidden).is_err());
        assert!(validate_models_folder(&base.join("missing"), &forbidden).unwrap_err().contains("not an existing folder"));
        assert!(validate_models_folder(Path::new(r"\\server\share\models"), &forbidden).unwrap_err().contains("network"));
        assert!(validate_models_folder(Path::new("relative\\models"), &forbidden).is_err());
        // A sibling whose name merely starts with a forbidden folder's name is fine.
        let sibling = base.join("Program Files Data");
        std::fs::create_dir_all(&sibling).unwrap();
        assert!(validate_models_folder(&sibling, &forbidden).is_ok());
        let _ = std::fs::remove_dir_all(&base);
    }
}
