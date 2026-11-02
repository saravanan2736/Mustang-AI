//! Owns the Python backend process for the lifetime of the desktop app.
//!
//! Start-up: resolve data directories and runtime → one-time legacy import (asks the
//! user) → spawn `mustang_launcher.py` inside a Job Object with free loopback ports, a
//! per-launch session token and an allow-listed environment → wait for `/health`.
//! Afterwards: crash detection with bounded restarts, manual restart, and graceful
//! shutdown (`POST /api/system/shutdown`, then Job Object termination as a fallback).
//! The UI learns about every transition through the `backend://state` event and reads
//! the address and token with the `backend_info` command.

use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddrV4, TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use mustang_supervisor::{Event, EventSink, GracefulSignal, SpawnSpec, Supervised};
use serde::Serialize;
use tauri::{AppHandle, Emitter, State};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

use crate::paths::{find_legacy_install, runtime_manifest, DataDirs, DesktopSettings, LegacyDecision, LegacyInstall, Runtime};
use crate::runtime_check::{self, GpuStatus};

pub const STATE_EVENT: &str = "backend://state";
const STARTUP_TIMEOUT: Duration = Duration::from_secs(60);
const IMPORT_TIMEOUT: Duration = Duration::from_secs(300);
const SHUTDOWN_GRACE: Duration = Duration::from_secs(10);
const MAX_CRASHES: usize = 3;
const CRASH_WINDOW: Duration = Duration::from_secs(60);
const LOG_TAIL_LINES: usize = 40;

#[cfg(debug_assertions)]
const ALLOWED_ORIGINS: &str = r#"["http://tauri.localhost","http://127.0.0.1:5173"]"#;
#[cfg(not(debug_assertions))]
const ALLOWED_ORIGINS: &str = r#"["http://tauri.localhost"]"#;

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum BackendState {
    Starting { detail: String },
    Ready { base_url: String, pid: u32 },
    Restarting { attempt: u32, reason: String },
    Failed { reason: String, log_tail: Vec<String> },
    Stopping,
    Stopped,
}

#[derive(Serialize)]
pub struct BackendInfo {
    #[serde(flatten)]
    state: BackendState,
    /// Session token, only while ready. Delivered over IPC to the main window only.
    token: Option<String>,
    runtime_source: Option<&'static str>,
    data_root: Option<String>,
    log_dir: Option<String>,
    /// GPU acceleration status (CPU fallback reasons are shown to the user).
    gpu: Option<GpuStatus>,
}

#[derive(Clone)]
struct Launch {
    runtime: Runtime,
    dirs: DataDirs,
    models_dir: PathBuf,
    gpu: GpuStatus,
}

struct Inner {
    state: BackendState,
    token: String,
    process: Option<Supervised>,
    port: u16,
    generation: u64,
    crashes: Vec<Instant>,
    launch: Option<Launch>,
    /// Known as soon as resolved, so failures during preparation are logged too.
    dirs: Option<DataDirs>,
    shutting_down: bool,
}

pub struct BackendManager {
    app: AppHandle,
    inner: Mutex<Inner>,
}

impl BackendManager {
    pub fn new(app: AppHandle) -> Arc<Self> {
        Arc::new(Self {
            app,
            inner: Mutex::new(Inner {
                state: BackendState::Starting { detail: "Preparing".into() },
                token: session_token(),
                process: None,
                port: 0,
                generation: 0,
                crashes: Vec::new(),
                launch: None,
                dirs: None,
                shutting_down: false,
            }),
        })
    }

    /// Kick off preparation and the first launch on a worker thread (the UI shows progress).
    pub fn start(self: &Arc<Self>) {
        let me = self.clone();
        thread::spawn(move || match me.prepare() {
            Ok(launch) => {
                me.inner.lock().unwrap().launch = Some(launch);
                me.launch_new_generation(None);
            }
            Err(reason) => me.fail(reason),
        });
    }

    pub fn info(&self) -> BackendInfo {
        let inner = self.inner.lock().unwrap();
        let ready = matches!(inner.state, BackendState::Ready { .. });
        BackendInfo {
            state: inner.state.clone(),
            token: ready.then(|| inner.token.clone()),
            runtime_source: inner.launch.as_ref().map(|l| l.runtime.source),
            data_root: inner.dirs.as_ref().map(|d| d.root.display().to_string()),
            log_dir: inner.dirs.as_ref().map(|d| d.logs.display().to_string()),
            gpu: inner.launch.as_ref().map(|l| l.gpu.clone()),
        }
    }

    pub fn state(&self) -> BackendState {
        self.inner.lock().unwrap().state.clone()
    }

    pub fn pid(&self) -> Option<u32> {
        self.inner.lock().unwrap().process.as_ref().map(|p| p.pid())
    }

    /// User-initiated restart (e.g. from the failure screen). Resets the crash budget.
    pub fn restart(self: &Arc<Self>) {
        let launch_missing = {
            let mut inner = self.inner.lock().unwrap();
            if inner.shutting_down {
                return;
            }
            inner.crashes.clear();
            inner.launch.is_none()
        };
        if launch_missing {
            // Preparation itself failed earlier (e.g. import or runtime missing): redo it.
            self.start();
            return;
        }
        // Off the calling (IPC/main) thread: a graceful stop can take SHUTDOWN_GRACE.
        let me = self.clone();
        thread::spawn(move || {
            me.set_state(BackendState::Starting { detail: "Restarting the local engine".into() });
            me.stop_process(true);
            me.launch_new_generation(None);
        });
    }

    /// Graceful shutdown on app exit. Blocks for at most SHUTDOWN_GRACE (+ a few seconds).
    pub fn shutdown(&self) {
        {
            let mut inner = self.inner.lock().unwrap();
            if inner.shutting_down {
                return;
            }
            inner.shutting_down = true;
        }
        self.set_state(BackendState::Stopping);
        self.stop_process(true);
        self.set_state(BackendState::Stopped);
    }

    // ---- preparation ------------------------------------------------------------------

    fn prepare(&self) -> Result<Launch, String> {
        let dirs = DataDirs::resolve(&self.app)?;
        self.inner.lock().unwrap().dirs = Some(dirs.clone());
        dirs.log("--- desktop shell starting ---");
        let runtime = Runtime::resolve(&self.app)?;
        dirs.log(&format!(
            "runtime source={} python={} backend={} llama={:?}",
            runtime.source,
            runtime.python.display(),
            runtime.backend_dir.display(),
            runtime.llama_dir
        ));

        // Inference runtime: present, and (when bundled) byte-identical to the build manifest.
        match (&runtime.llama_dir, runtime_manifest(&self.app)) {
            (Some(llama), Some(manifest)) => {
                self.set_state(BackendState::Starting { detail: "Verifying the inference engine".into() });
                let report = runtime_check::verify_llama(&manifest, llama, &dirs.data)?;
                dirs.log(&format!(
                    "llama runtime verified: {} files, {} hashed, {} ms",
                    report.files, report.hashed, report.millis
                ));
            }
            (Some(_), None) => dirs.log("llama runtime from a development location; manifest check skipped"),
            (None, _) => {
                return Err("The inference engine (llama.cpp) is missing from this installation.                             Reinstall Mustang AI to repair it."
                    .into())
            }
        }
        let gpu = runtime_check::detect_gpu();
        dirs.log(&format!("gpu: {gpu:?}"));

        let mut settings = DesktopSettings::load(&dirs);
        if settings.legacy_decision.is_none() && !dirs.database().exists() {
            match find_legacy_install() {
                Some(legacy) => {
                    if self.ask_import(&legacy) {
                        self.set_state(BackendState::Starting { detail: "Importing your existing conversations".into() });
                        self.import_legacy(&runtime, &dirs, &legacy)?;
                        if settings.models_dir.is_none() {
                            settings.models_dir = legacy.models_dir.clone();
                        }
                        settings.legacy_decision = Some(LegacyDecision::Imported);
                    } else {
                        settings.legacy_decision = Some(LegacyDecision::Declined);
                    }
                }
                None => settings.legacy_decision = Some(LegacyDecision::NotFound),
            }
            settings.save(&dirs)?;
            dirs.log(&format!("legacy decision: {:?}", settings.legacy_decision));
        }

        let models_dir = settings.models_dir(&dirs);
        if !models_dir.is_dir() {
            if settings.models_dir.is_some() {
                return Err(format!(
                    "The models folder {} is not available (drive disconnected?). Reconnect it and press Retry.",
                    models_dir.display()
                ));
            }
            std::fs::create_dir_all(&models_dir).map_err(|e| format!("cannot create {}: {e}", models_dir.display()))?;
        }
        Ok(Launch { runtime, dirs, models_dir, gpu })
    }

    fn ask_import(&self, legacy: &LegacyInstall) -> bool {
        match std::env::var("MUSTANG_DESKTOP_LEGACY_IMPORT").as_deref() {
            Ok("accept") => return true,
            Ok("decline") => return false,
            _ => {}
        }
        let models = legacy
            .models_dir
            .as_ref()
            .map(|m| format!("\n\nModels in {} will be used where they are (not moved).", m.display()))
            .unwrap_or_default();
        let text = format!(
            "Mustang AI found existing data from an earlier version:\n{}\n\n\
             Import it? Your conversations, personas and settings are copied into the desktop app. \
             The original files are not changed.{models}",
            legacy.database.display()
        );
        self.app
            .dialog()
            .message(text)
            .title("Import existing Mustang AI data")
            .kind(MessageDialogKind::Info)
            .buttons(MessageDialogButtons::OkCancelCustom("Import".into(), "Start fresh".into()))
            .blocking_show()
    }

    fn import_legacy(&self, runtime: &Runtime, dirs: &DataDirs, legacy: &LegacyInstall) -> Result<(), String> {
        dirs.log(&format!("importing {} -> {}", legacy.database.display(), dirs.database().display()));
        let spec = SpawnSpec::new(&runtime.python)
            .arg(runtime.launcher())
            .arg("--import-db")
            .arg(&legacy.database)
            .arg(dirs.database())
            .env("PYTHONUTF8", "1")
            .env("PYTHONNOUSERSITE", "1");
        let proc = Supervised::spawn(SpawnSpec { cwd: Some(dirs.data.clone()), ..spec }, None).map_err(|e| e.to_string())?;
        let exit = proc.wait_exit(IMPORT_TIMEOUT);
        let output: Vec<String> = proc.logs().into_iter().map(|l| l.line).collect();
        dirs.log(&format!("import result: {:?} {}", exit, output.join(" | ")));
        match exit {
            Some(e) if e.code == Some(0) && output.iter().any(|l| l.contains("\"ok\": true")) => Ok(()),
            _ => Err(format!("Importing existing data failed: {}", output.join("\n"))),
        }
    }

    // ---- process lifecycle ----------------------------------------------------------

    fn launch_new_generation(self: &Arc<Self>, restart_attempt: Option<u32>) {
        let (generation, launch, token) = {
            let mut inner = self.inner.lock().unwrap();
            if inner.shutting_down {
                return;
            }
            inner.generation += 1;
            (inner.generation, inner.launch.clone(), inner.token.clone())
        };
        let Some(launch) = launch else { return };
        if restart_attempt.is_none() {
            self.set_state(BackendState::Starting { detail: "Starting the local engine".into() });
        }

        let port = free_port();
        let engine_port = free_port();
        let spec = self.spawn_spec(&launch, port, engine_port, &token);
        let sink = self.exit_sink(generation);
        let process = match Supervised::spawn(spec, Some(sink)) {
            Ok(p) => p,
            Err(e) => return self.fail(format!("Could not start the backend process: {e}")),
        };
        let pid = process.pid();
        launch.dirs.log(&format!("backend generation {generation} pid={pid} port={port} engine_port={engine_port}"));
        {
            let mut inner = self.inner.lock().unwrap();
            if inner.generation != generation || inner.shutting_down {
                return; // superseded while spawning; the dropped process is killed with its job
            }
            inner.port = port;
            inner.process = Some(process);
        }

        let deadline = Instant::now() + STARTUP_TIMEOUT;
        while Instant::now() < deadline {
            {
                let inner = self.inner.lock().unwrap();
                if inner.generation != generation || inner.shutting_down {
                    return;
                }
                if let Some(exit) = inner.process.as_ref().and_then(|p| p.exit_info()) {
                    drop(inner);
                    return self.fail(format!("The backend exited during start-up (exit code {}).", exit_code_text(exit.code)));
                }
            }
            if matches!(http(port, "GET", "/health", None), Ok(200)) {
                launch.dirs.log(&format!("backend generation {generation} ready"));
                self.set_state(BackendState::Ready { base_url: format!("http://127.0.0.1:{port}"), pid });
                return;
            }
            thread::sleep(Duration::from_millis(200));
        }
        self.stop_process(false);
        self.fail(format!("The backend did not become ready within {} s.", STARTUP_TIMEOUT.as_secs()));
    }

    fn spawn_spec(&self, launch: &Launch, port: u16, engine_port: u16, token: &str) -> SpawnSpec {
        let Launch { runtime, dirs, models_dir, .. } = launch;
        let llama_dir = runtime.llama_dir.clone().unwrap_or_else(|| dirs.root.join("runtime"));
        let mut spec = SpawnSpec::new(&runtime.python)
            .arg("-u")
            .arg(runtime.launcher())
            .env("MUSTANG_DESKTOP", "1")
            .env("MUSTANG_PORT", port.to_string())
            .env("MUSTANG_ENGINE_PORT", engine_port.to_string())
            // Environment, never argv: argv is visible to every local process.
            .env("MUSTANG_SESSION_TOKEN", token)
            .env("MUSTANG_ALLOWED_ORIGINS", ALLOWED_ORIGINS)
            .env("MUSTANG_RUN_DIR", dirs.data.display().to_string())
            .env("MUSTANG_LOG_DIR", dirs.logs.display().to_string())
            .env("MUSTANG_MODELS_DIR", models_dir.display().to_string())
            .env("MUSTANG_DOWNLOADS_DIR", models_dir.join(".downloads").display().to_string())
            .env("MUSTANG_RUNTIME_DIR", llama_dir.display().to_string())
            .env("PYTHONUTF8", "1")
            .env("PYTHONNOUSERSITE", "1");
        spec.cwd = Some(dirs.data.clone());
        spec.redact.push(token.to_string());
        spec
    }

    fn exit_sink(self: &Arc<Self>, generation: u64) -> EventSink {
        let me = Arc::downgrade(self);
        Arc::new(move |event: &Event| {
            if let Event::Exited(info) = event {
                if !info.expected {
                    if let Some(me) = me.upgrade() {
                        let code = info.code;
                        thread::spawn(move || me.on_unexpected_exit(generation, code));
                    }
                }
            }
        })
    }

    fn on_unexpected_exit(self: &Arc<Self>, generation: u64, code: Option<i32>) {
        let attempt = {
            let mut inner = self.inner.lock().unwrap();
            let was_ready = matches!(inner.state, BackendState::Ready { .. });
            if inner.generation != generation || inner.shutting_down || !was_ready {
                return; // start-up failures are reported by the launch loop
            }
            // Python died but its children (llama-server: VRAM, engine port) may still run
            // in the crashed generation's job. Terminate them now, not at the next spawn.
            if let Some(p) = inner.process.as_mut() {
                p.force_kill();
            }
            let now = Instant::now();
            inner.crashes.retain(|t| now.duration_since(*t) < CRASH_WINDOW);
            inner.crashes.push(now);
            inner.crashes.len()
        };
        if let Some(l) = self.inner.lock().unwrap().launch.as_ref() {
            l.dirs.log(&format!("backend generation {generation} exited unexpectedly (exit code {}); crash {attempt} in window", exit_code_text(code)));
        }
        if attempt >= MAX_CRASHES {
            return self.fail(format!(
                "The backend stopped unexpectedly {MAX_CRASHES} times within {} s (last exit code {}).",
                CRASH_WINDOW.as_secs(),
                exit_code_text(code)
            ));
        }
        self.set_state(BackendState::Restarting {
            attempt: attempt as u32,
            reason: format!("The backend stopped unexpectedly (exit code {}).", exit_code_text(code)),
        });
        thread::sleep(Duration::from_secs(1 << (attempt - 1)));
        self.launch_new_generation(Some(attempt as u32));
    }

    fn stop_process(&self, graceful: bool) {
        let (process, port, token) = {
            let mut inner = self.inner.lock().unwrap();
            (inner.process.take(), inner.port, inner.token.clone())
        };
        let Some(mut process) = process else { return };
        let outcome = if graceful && process.is_running() {
            let signal = GracefulSignal::Custom(Box::new(move || {
                let _ = http(port, "POST", "/api/system/shutdown", Some(&token));
            }));
            process.shutdown(signal, SHUTDOWN_GRACE)
        } else {
            process.force_kill();
            process.wait_exit(Duration::from_secs(5));
            mustang_supervisor::ShutdownOutcome::Forced
        };
        if let Some(l) = self.inner.lock().unwrap().launch.as_ref() {
            l.dirs.log(&format!("backend pid {} stopped: {outcome:?}", process.pid()));
        }
    }

    fn fail(&self, reason: String) {
        let log_tail = {
            let inner = self.inner.lock().unwrap();
            if let Some(d) = inner.dirs.as_ref() {
                d.log(&format!("FAILED: {reason}"));
            }
            inner
                .process
                .as_ref()
                .map(|p| {
                    let lines = p.logs();
                    let skip = lines.len().saturating_sub(LOG_TAIL_LINES);
                    lines.into_iter().skip(skip).map(|l| l.line).collect()
                })
                .unwrap_or_default()
        };
        self.set_state(BackendState::Failed { reason, log_tail });
    }

    fn set_state(&self, state: BackendState) {
        self.inner.lock().unwrap().state = state.clone();
        let _ = self.app.emit(STATE_EVENT, state);
    }
}

fn exit_code_text(code: Option<i32>) -> String {
    code.map(|c| c.to_string()).unwrap_or_else(|| "unknown".into())
}

fn session_token() -> String {
    let mut buf = [0u8; 32];
    getrandom::fill(&mut buf).expect("OS random number generator unavailable");
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

fn free_port() -> u16 {
    TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0))
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .expect("no free loopback port")
}

/// Minimal HTTP/1.1 request to the loopback backend; returns the status code.
fn http(port: u16, method: &str, path: &str, token: Option<&str>) -> Result<u16, String> {
    let addr = SocketAddrV4::new(Ipv4Addr::LOCALHOST, port);
    let mut s = TcpStream::connect_timeout(&addr.into(), Duration::from_millis(500)).map_err(|e| e.to_string())?;
    s.set_read_timeout(Some(Duration::from_secs(3))).ok();
    let auth = token.map(|t| format!("X-Mustang-Token: {t}\r\n")).unwrap_or_default();
    let request = format!("{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n{auth}Content-Length: 0\r\nConnection: close\r\n\r\n");
    s.write_all(request.as_bytes()).map_err(|e| e.to_string())?;
    let mut buf = [0u8; 64];
    let n = s.read(&mut buf).map_err(|e| e.to_string())?;
    String::from_utf8_lossy(&buf[..n])
        .split_whitespace()
        .nth(1)
        .and_then(|c| c.parse().ok())
        .ok_or_else(|| "malformed response".into())
}

#[tauri::command]
pub fn backend_info(manager: State<'_, Arc<BackendManager>>) -> BackendInfo {
    manager.info()
}

#[tauri::command]
pub fn backend_restart(manager: State<'_, Arc<BackendManager>>) {
    manager.restart();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn session_token_is_256_bit_hex_and_unique() {
        let (a, b) = (session_token(), session_token());
        assert_eq!(a.len(), 64);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b);
    }

    #[test]
    fn exit_code_text_is_user_readable() {
        assert_eq!(exit_code_text(Some(-1)), "-1");
        assert_eq!(exit_code_text(None), "unknown");
    }

    #[test]
    fn free_port_is_bindable_on_loopback() {
        let port = free_port();
        assert!(TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, port)).is_ok());
    }

    #[test]
    fn http_helper_sends_token_and_host_and_parses_status() {
        let listener = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = thread::spawn(move || {
            let (mut s, _) = listener.accept().unwrap();
            let mut received = Vec::new();
            let mut buf = [0u8; 1024];
            while !received.windows(4).any(|w| w == b"\r\n\r\n") {
                let n = s.read(&mut buf).unwrap();
                if n == 0 {
                    break;
                }
                received.extend_from_slice(&buf[..n]);
            }
            s.write_all(b"HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\n\r\n").unwrap();
            String::from_utf8_lossy(&received).to_string()
        });
        let status = http(port, "POST", "/api/system/shutdown", Some("tok123")).unwrap();
        let request = server.join().unwrap();
        assert_eq!(status, 202);
        assert!(request.starts_with("POST /api/system/shutdown HTTP/1.1\r\n"));
        assert!(request.contains(&format!("Host: 127.0.0.1:{port}\r\n")));
        assert!(request.contains("X-Mustang-Token: tok123\r\n"));
    }

    #[test]
    fn http_helper_reports_refused_connection() {
        assert!(http(free_port(), "GET", "/health", None).is_err());
    }
}
