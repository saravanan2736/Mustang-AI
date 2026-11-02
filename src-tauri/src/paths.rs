//! Where the desktop app keeps user data, and where it finds the backend runtime.
//!
//! User data lives under `%LOCALAPPDATA%\MustangAI\` — deliberately *not* the Tauri
//! bundle-identifier directory (`%LOCALAPPDATA%\ai.mustang.desktop\`, WebView2 cache), so
//! the uninstaller's "delete app data" option can never reach conversations or models.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};

const DATA_ROOT_NAME: &str = "MustangAI";
const SETTINGS_FILE: &str = "desktop.json";
/// Repository root at build time; used only for development fallbacks and legacy-data detection.
const BUILD_REPO_ROOT: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/..");

#[derive(Debug, Clone, Serialize)]
pub struct DataDirs {
    pub root: PathBuf,
    pub data: PathBuf,
    pub logs: PathBuf,
    pub default_models: PathBuf,
}

impl DataDirs {
    pub fn resolve(app: &AppHandle) -> Result<Self, String> {
        let root = match std::env::var_os("MUSTANG_DESKTOP_DATA_ROOT") {
            Some(p) => PathBuf::from(p),
            None => app
                .path()
                .local_data_dir()
                .map_err(|e| format!("cannot resolve %LOCALAPPDATA%: {e}"))?
                .join(DATA_ROOT_NAME),
        };
        let dirs = Self {
            data: root.join("data"),
            logs: root.join("logs"),
            default_models: root.join("models"),
            root,
        };
        for d in [&dirs.root, &dirs.data, &dirs.logs] {
            fs::create_dir_all(d).map_err(|e| format!("cannot create {}: {e}", d.display()))?;
        }
        Ok(dirs)
    }

    pub fn database(&self) -> PathBuf {
        self.data.join("mustang.db")
    }

    pub fn settings_path(&self) -> PathBuf {
        self.root.join(SETTINGS_FILE)
    }

    /// Append one line to logs/desktop.log (best effort). Never pass secrets here.
    pub fn log(&self, message: &str) {
        let ts = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
        eprintln!("[desktop] {message}");
        if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(self.logs.join("desktop.log")) {
            let _ = writeln!(f, "{ts} {message}");
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum LegacyDecision {
    Imported,
    Declined,
    NotFound,
}

/// The shell's own settings (`desktop.json`); application settings stay in SQLite.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct DesktopSettings {
    pub models_dir: Option<PathBuf>,
    pub legacy_decision: Option<LegacyDecision>,
    /// Closing the window keeps the app running in the system tray (default: true).
    pub close_to_tray: Option<bool>,
}

impl DesktopSettings {
    pub fn load(dirs: &DataDirs) -> Self {
        fs::read_to_string(dirs.settings_path())
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default()
    }

    pub fn save(&self, dirs: &DataDirs) -> Result<(), String> {
        let path = dirs.settings_path();
        let tmp = path.with_extension("json.tmp");
        let text = serde_json::to_string_pretty(self).map_err(|e| e.to_string())?;
        fs::write(&tmp, text).map_err(|e| e.to_string())?;
        fs::rename(&tmp, &path).map_err(|e| e.to_string())
    }

    pub fn close_to_tray(&self) -> bool {
        self.close_to_tray.unwrap_or(true)
    }

    pub fn models_dir(&self, dirs: &DataDirs) -> PathBuf {
        self.models_dir.clone().unwrap_or_else(|| dirs.default_models.clone())
    }
}

/// An existing pre-desktop installation (the repo layout: backend/run/mustang.db, models/).
#[derive(Debug, Clone)]
pub struct LegacyInstall {
    pub database: PathBuf,
    pub models_dir: Option<PathBuf>,
}

pub fn find_legacy_install() -> Option<LegacyInstall> {
    let candidates: Vec<PathBuf> = match std::env::var_os("MUSTANG_LEGACY_ROOT") {
        Some(p) => vec![PathBuf::from(p)],
        None => vec![PathBuf::from(BUILD_REPO_ROOT)],
    };
    candidates.into_iter().find_map(|root| {
        let root = normalize(&root);
        let database = root.join("backend").join("run").join("mustang.db");
        database.is_file().then(|| LegacyInstall {
            database,
            models_dir: Some(root.join("models")).filter(|m| m.is_dir()),
        })
    })
}

#[derive(Debug, Clone, Serialize)]
pub struct Runtime {
    pub python: PathBuf,
    pub backend_dir: PathBuf,
    pub llama_dir: Option<PathBuf>,
    /// "env", "bundled" or "dev-source".
    pub source: &'static str,
}

impl Runtime {
    pub fn launcher(&self) -> PathBuf {
        self.backend_dir.join("mustang_launcher.py")
    }

    pub fn resolve(app: &AppHandle) -> Result<Self, String> {
        let llama_dir = resolve_llama_dir(app);

        if let (Some(py), Some(be)) = (std::env::var_os("MUSTANG_DESKTOP_PYTHON"), std::env::var_os("MUSTANG_DESKTOP_BACKEND_DIR")) {
            return Self { python: py.into(), backend_dir: be.into(), llama_dir, source: "env" }.validated();
        }

        if let Some(res) = resource_dir(app) {
            let rt = Self {
                python: res.join("python").join("python.exe"),
                backend_dir: res.join("backend"),
                llama_dir: llama_dir.clone(),
                source: "bundled",
            };
            if rt.python.is_file() && rt.launcher().is_file() {
                return Ok(rt);
            }
        }

        #[cfg(debug_assertions)]
        {
            let repo = normalize(Path::new(BUILD_REPO_ROOT));
            let rt = Self {
                python: repo.join("backend").join(".venv").join("Scripts").join("python.exe"),
                backend_dir: repo.join("backend"),
                llama_dir,
                source: "dev-source",
            };
            if rt.python.is_file() && rt.launcher().is_file() {
                return Ok(rt);
            }
        }

        Err("The bundled backend runtime was not found. Build it with \
             `scripts/build_desktop_runtime.py` and package with `npm run desktop:build`."
            .into())
    }

    fn validated(self) -> Result<Self, String> {
        if !self.python.is_file() {
            return Err(format!("Python not found: {}", self.python.display()));
        }
        if !self.launcher().is_file() {
            return Err(format!("Backend launcher not found: {}", self.launcher().display()));
        }
        Ok(self)
    }
}

/// llama.cpp CUDA runtime directory: the bundled `llama/` resource (verified against the
/// runtime manifest at start-up), or an explicit `MUSTANG_DESKTOP_LLAMA_DIR` override.
fn resolve_llama_dir(app: &AppHandle) -> Option<PathBuf> {
    let has_server = |d: &Path| d.join("llama-server.exe").is_file();
    if let Some(d) = std::env::var_os("MUSTANG_DESKTOP_LLAMA_DIR").map(PathBuf::from) {
        return Some(d);
    }
    if let Some(res) = resource_dir(app) {
        let d = res.join("llama");
        if has_server(&d) {
            return Some(d);
        }
    }
    // Development builds only: the repository's verified runtime (backend/runtime/bin).
    #[cfg(debug_assertions)]
    {
        let d = normalize(&Path::new(BUILD_REPO_ROOT).join("backend").join("runtime").join("bin"));
        if has_server(&d) {
            return Some(d);
        }
    }
    None
}

/// The bundled `runtime-manifest.json` (release builds), describing the llama.cpp files.
pub fn runtime_manifest(app: &AppHandle) -> Option<PathBuf> {
    resource_dir(app).map(|r| r.join("runtime-manifest.json")).filter(|p| p.is_file())
}

/// Tauri's resource directory without the `\\?\` verbatim prefix it returns on Windows
/// (passed on to Python and llama-server, which expect ordinary drive paths).
fn resource_dir(app: &AppHandle) -> Option<PathBuf> {
    let dir = app.path().resource_dir().ok()?;
    let text = dir.to_string_lossy();
    match text.strip_prefix(r"\\?\") {
        Some(rest) if !rest.starts_with("UNC\\") => Some(PathBuf::from(rest)),
        _ => Some(dir),
    }
}

/// Lexically resolve `.`/`..` without `canonicalize()`, which on Windows returns `\\?\`
/// verbatim paths that Python, SQLite URIs and llama-server handle poorly.
pub fn normalize(path: &Path) -> PathBuf {
    use std::path::Component;
    let mut out = PathBuf::new();
    for c in path.components() {
        match c {
            Component::CurDir => {}
            Component::ParentDir => {
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_resolves_parent_components_without_verbatim_prefix() {
        let p = normalize(Path::new(r"S:\Mustang AI\src-tauri\..\backend\.\run"));
        assert_eq!(p, PathBuf::from(r"S:\Mustang AI\backend\run"));
        assert!(!p.to_string_lossy().starts_with(r"\?\"));
    }

    #[test]
    fn desktop_settings_round_trip() {
        let s = DesktopSettings {
            models_dir: Some(PathBuf::from(r"S:\Mustang AI\models")),
            legacy_decision: Some(LegacyDecision::Imported),
            close_to_tray: Some(false),
        };
        let text = serde_json::to_string(&s).unwrap();
        assert!(text.contains("\"imported\""));
        let back: DesktopSettings = serde_json::from_str(&text).unwrap();
        assert_eq!(back.models_dir, s.models_dir);
        assert_eq!(back.legacy_decision, Some(LegacyDecision::Imported));
        assert!(!back.close_to_tray());
    }

    #[test]
    fn missing_settings_fields_default() {
        let s: DesktopSettings = serde_json::from_str("{}").unwrap();
        assert!(s.models_dir.is_none() && s.legacy_decision.is_none());
        assert!(s.close_to_tray(), "close-to-tray defaults to on");
    }
}
