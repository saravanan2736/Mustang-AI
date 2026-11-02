//! Phase B connectivity probe (feature `probe` only).
//!
//! Runs the TEST-ONLY mock backend (`desktop-prototypes/connectivity-probe/mock_backend.py`)
//! as a supervised child inside a Job Object, on a free loopback port, with a per-launch
//! test token. The probe UI drives restarts/stops through these commands and reports
//! results back for the Phase B connectivity report.

use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddrV4, TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use mustang_supervisor::{GracefulSignal, ShutdownOutcome, SpawnSpec, Supervised};
use serde::Serialize;
use tauri::{AppHandle, Manager, State, Window, WindowEvent};

use crate::runtime_info::BackendStatus;

const MOCK_SCRIPT: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../desktop-prototypes/connectivity-probe/mock_backend.py");
const DEFAULT_PYTHON: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../backend/.venv/Scripts/python.exe");
const RESULTS_DIR: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../desktop-prototypes/connectivity-probe/results");
const DEFAULT_ORIGINS: &str = "http://tauri.localhost";
const INITIAL_STARTUP_DELAY_MS: u64 = 2500;

pub struct ProbeState(Mutex<Inner>);

struct Inner {
    backend: Option<Supervised>,
    port: u16,
    token: String,
    allowed_origins: String,
    launches: u32,
}

#[derive(Serialize)]
pub struct ProbeInfo {
    base_url: String,
    port: u16,
    /// TEST-ONLY credential generated for this launch; not a production token.
    test_token: String,
    allowed_origins: String,
    closed_port: u16,
    backend_pid: Option<u32>,
    backend_running: bool,
    launches: u32,
}

fn free_port() -> u16 {
    TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0))
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .expect("no free loopback port")
}

fn test_token() -> String {
    let mut buf = [0u8; 32];
    getrandom::fill(&mut buf).expect("OS RNG unavailable");
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

fn python() -> PathBuf {
    std::env::var_os("MUSTANG_PROBE_PYTHON").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(DEFAULT_PYTHON))
}

fn spawn_mock(inner: &mut Inner, startup_delay_ms: u64) -> Result<(), String> {
    let mut spec = SpawnSpec::new(python())
        .arg("-u")
        .arg(MOCK_SCRIPT)
        .arg("--port")
        .arg(inner.port.to_string())
        .arg("--allowed-origins")
        .arg(&inner.allowed_origins)
        .arg("--startup-delay-ms")
        .arg(startup_delay_ms.to_string())
        // Passed by environment, not argv: argv is visible to every local process.
        .env("MUSTANG_PROBE_TOKEN", &inner.token);
    spec.redact.push(inner.token.clone());
    let sup = Supervised::spawn(spec, None).map_err(|e| format!("spawn mock backend: {e}"))?;
    inner.launches += 1;
    eprintln!("[probe] mock backend launch #{} pid={} port={}", inner.launches, sup.pid(), inner.port);
    inner.backend = Some(sup);
    Ok(())
}

fn stop_mock(inner: &mut Inner, graceful: bool) -> Option<ShutdownOutcome> {
    let mut sup = inner.backend.take()?;
    if graceful {
        Some(sup.shutdown(GracefulSignal::CloseStdin, Duration::from_secs(5)))
    } else {
        sup.force_kill();
        sup.wait_exit(Duration::from_secs(5));
        Some(ShutdownOutcome::Forced)
    }
}

fn info(inner: &Inner) -> ProbeInfo {
    let running = inner.backend.as_ref().map(|b| b.is_running()).unwrap_or(false);
    ProbeInfo {
        base_url: format!("http://127.0.0.1:{}", inner.port),
        port: inner.port,
        test_token: inner.token.clone(),
        allowed_origins: inner.allowed_origins.clone(),
        closed_port: free_port(),
        backend_pid: inner.backend.as_ref().map(|b| b.pid()),
        backend_running: running,
        launches: inner.launches,
    }
}

fn http_status(port: u16, host: &str, path: &str) -> Result<(u16, String), String> {
    let addr = SocketAddrV4::new(Ipv4Addr::LOCALHOST, port);
    let mut s = TcpStream::connect_timeout(&addr.into(), Duration::from_secs(2)).map_err(|e| e.to_string())?;
    s.set_read_timeout(Some(Duration::from_secs(3))).ok();
    write!(s, "GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n").map_err(|e| e.to_string())?;
    let mut resp = String::new();
    let _ = s.read_to_string(&mut resp);
    let status = resp.split_whitespace().nth(1).and_then(|c| c.parse().ok()).ok_or("no status line")?;
    let body = resp.split("\r\n\r\n").nth(1).unwrap_or("").to_string();
    Ok((status, body))
}

pub fn setup(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let mut inner = Inner {
        backend: None,
        port: free_port(),
        token: test_token(),
        allowed_origins: std::env::var("MUSTANG_PROBE_ALLOWED_ORIGINS").unwrap_or_else(|_| DEFAULT_ORIGINS.into()),
        launches: 0,
    };
    // Deliberately slow first start so the UI must cope with "backend not up yet".
    spawn_mock(&mut inner, INITIAL_STARTUP_DELAY_MS)?;
    app.manage(ProbeState(Mutex::new(inner)));
    Ok(())
}

pub fn on_window_event(window: &Window, event: &WindowEvent) {
    if let WindowEvent::Destroyed = event {
        if let Some(state) = window.app_handle().try_state::<ProbeState>() {
            let mut inner = state.0.lock().unwrap();
            let outcome = stop_mock(&mut inner, true);
            eprintln!("[probe] window destroyed; mock backend shutdown: {outcome:?}");
        }
    }
}

pub fn backend_status(app: &AppHandle) -> BackendStatus {
    let Some(state) = app.try_state::<ProbeState>() else {
        return BackendStatus { managed: false, base_url: None, ready: false, pid: None };
    };
    let inner = state.0.lock().unwrap();
    let ready = matches!(http_status(inner.port, "127.0.0.1", "/health"), Ok((200, _)));
    BackendStatus {
        managed: true,
        base_url: Some(format!("http://127.0.0.1:{}", inner.port)),
        ready,
        pid: inner.backend.as_ref().map(|b| b.pid()),
    }
}

#[tauri::command(async)]
pub fn probe_info(state: State<'_, ProbeState>) -> ProbeInfo {
    info(&state.0.lock().unwrap())
}

#[tauri::command(async)]
pub fn probe_restart_backend(
    state: State<'_, ProbeState>,
    same_port: bool,
    startup_delay_ms: u64,
    allowed_origins: Option<String>,
) -> Result<ProbeInfo, String> {
    let mut inner = state.0.lock().unwrap();
    stop_mock(&mut inner, true);
    if !same_port {
        inner.port = free_port();
    }
    if let Some(o) = allowed_origins {
        inner.allowed_origins = o;
    }
    spawn_mock(&mut inner, startup_delay_ms)?;
    Ok(info(&inner))
}

#[tauri::command(async)]
pub fn probe_stop_backend(state: State<'_, ProbeState>, graceful: bool) -> String {
    let mut inner = state.0.lock().unwrap();
    format!("{:?}", stop_mock(&mut inner, graceful))
}

/// Browsers cannot forge the Host header, so the DNS-rebinding guard is exercised
/// from Rust with a raw HTTP request.
#[tauri::command(async)]
pub fn probe_raw_host_check(state: State<'_, ProbeState>, host: String) -> Result<u16, String> {
    let port = state.0.lock().unwrap().port;
    http_status(port, &host, "/health").map(|(s, _)| s)
}

#[tauri::command(async)]
pub fn probe_backend_logs(state: State<'_, ProbeState>) -> Vec<String> {
    let inner = state.0.lock().unwrap();
    inner
        .backend
        .as_ref()
        .map(|b| b.logs().into_iter().map(|l| format!("{:?}: {}", l.stream, l.line)).collect())
        .unwrap_or_default()
}

#[tauri::command(async)]
pub fn probe_report(app: AppHandle, results: serde_json::Value) -> Result<String, String> {
    let profile = if cfg!(debug_assertions) { "debug" } else { "release" };
    let ts = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    let dir = PathBuf::from(RESULTS_DIR);
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let path = dir.join(format!("probe-results-{profile}-{ts}.json"));
    let text = serde_json::to_string_pretty(&results).map_err(|e| e.to_string())?;
    std::fs::write(&path, text).map_err(|e| e.to_string())?;
    eprintln!("[probe] results written to {}", path.display());
    if std::env::var("MUSTANG_PROBE_AUTOEXIT").as_deref() == Ok("1") {
        let handle = app.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(500));
            handle.exit(0);
        });
    }
    Ok(path.display().to_string())
}
