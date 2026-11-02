//! Mustang AI — Phase B process-supervision prototype.
//!
//! Supervises one child process (in production: the Python backend, which in turn
//! spawns `llama-server.exe`) inside a Windows Job Object configured with
//! `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. When the job handle closes — including when
//! the supervising process is killed from Task Manager — Windows terminates every
//! process in the job, grandchildren included.
//!
//! The child is created suspended, assigned to the job, then resumed, so it cannot
//! spawn a grandchild before it is inside the job.

use std::collections::VecDeque;
use std::ffi::OsString;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

#[cfg(windows)]
mod win;
#[cfg(windows)]
pub use win::{process_alive, JobObject};

/// Environment variables passed through to supervised children. Everything else is
/// dropped so secrets or hostile values in the parent environment do not leak in.
/// `SystemRoot` is required by Python (sockets, `os.urandom`).
pub const ENV_ALLOWLIST: &[&str] = &[
    "SystemRoot", "SystemDrive", "windir", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA",
    "APPDATA", "ProgramData", "COMPUTERNAME", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "OS", "PATHEXT",
];

const LOG_CAPACITY: usize = 500;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Stream {
    Stdout,
    Stderr,
}

#[derive(Debug, Clone)]
pub struct LogLine {
    pub stream: Stream,
    pub line: String,
}

#[derive(Debug, Clone)]
pub struct ExitInfo {
    pub code: Option<i32>,
    /// True when the exit followed a shutdown request from the supervisor.
    pub expected: bool,
}

#[derive(Debug, Clone)]
pub enum Event {
    Output(LogLine),
    Exited(ExitInfo),
}

pub type EventSink = Arc<dyn Fn(&Event) + Send + Sync>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ShutdownOutcome {
    AlreadyExited,
    Graceful,
    Forced,
}

/// How the supervisor asks the child to exit before falling back to force.
pub enum GracefulSignal {
    CloseStdin,
    StdinLine(String),
    /// Production path: e.g. an authenticated `POST /api/system/shutdown`.
    Custom(Box<dyn FnOnce() + Send>),
}

pub struct SpawnSpec {
    pub program: PathBuf,
    pub args: Vec<OsString>,
    pub env: Vec<(String, String)>,
    pub cwd: Option<PathBuf>,
    /// Values replaced with `[REDACTED]` in captured output.
    pub redact: Vec<String>,
    pub use_job: bool,
}

impl SpawnSpec {
    pub fn new(program: impl Into<PathBuf>) -> Self {
        Self { program: program.into(), args: Vec::new(), env: Vec::new(), cwd: None, redact: Vec::new(), use_job: true }
    }

    pub fn arg(mut self, a: impl Into<OsString>) -> Self {
        self.args.push(a.into());
        self
    }

    pub fn env(mut self, k: impl Into<String>, v: impl Into<String>) -> Self {
        self.env.push((k.into(), v.into()));
        self
    }
}

pub struct Supervised {
    child: Arc<Mutex<Child>>,
    #[cfg(windows)]
    job: Option<JobObject>,
    pid: u32,
    stdin: Option<ChildStdin>,
    logs: Arc<Mutex<VecDeque<LogLine>>>,
    exit: Arc<Mutex<Option<ExitInfo>>>,
    shutdown_requested: Arc<AtomicBool>,
}

impl Supervised {
    pub fn spawn(spec: SpawnSpec, sink: Option<EventSink>) -> io::Result<Self> {
        let mut cmd = Command::new(&spec.program);
        cmd.args(&spec.args)
            .env_clear()
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        for key in ENV_ALLOWLIST {
            if let Some(v) = std::env::var_os(key) {
                cmd.env(key, v);
            }
        }
        if let Some(sysroot) = std::env::var_os("SystemRoot") {
            // PATH limited to System32 so nothing is resolved from user-writable dirs.
            let mut p = PathBuf::from(sysroot);
            p.push("System32");
            cmd.env("PATH", p);
        }
        for (k, v) in &spec.env {
            cmd.env(k, v);
        }
        if let Some(cwd) = &spec.cwd {
            cmd.current_dir(cwd);
        }

        #[cfg(windows)]
        let (mut child, job) = if spec.use_job {
            let job = JobObject::new_kill_on_close()?;
            let child = win::spawn_in_job(&mut cmd, &job)?;
            (child, Some(job))
        } else {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(win::CREATE_NO_WINDOW);
            (cmd.spawn()?, None)
        };
        #[cfg(not(windows))]
        let mut child = cmd.spawn()?;

        let pid = child.id();
        let stdin = child.stdin.take();
        let logs = Arc::new(Mutex::new(VecDeque::with_capacity(LOG_CAPACITY)));
        let redact = Arc::new(spec.redact);

        if let Some(out) = child.stdout.take() {
            pump(out, Stream::Stdout, logs.clone(), redact.clone(), sink.clone());
        }
        if let Some(err) = child.stderr.take() {
            pump(err, Stream::Stderr, logs.clone(), redact.clone(), sink.clone());
        }

        let child = Arc::new(Mutex::new(child));
        let exit = Arc::new(Mutex::new(None));
        let shutdown_requested = Arc::new(AtomicBool::new(false));
        {
            let (child, exit, requested) = (child.clone(), exit.clone(), shutdown_requested.clone());
            thread::spawn(move || loop {
                let status = child.lock().unwrap().try_wait();
                match status {
                    Ok(Some(st)) => {
                        let info = ExitInfo { code: st.code(), expected: requested.load(Ordering::SeqCst) };
                        *exit.lock().unwrap() = Some(info.clone());
                        if let Some(s) = &sink {
                            s(&Event::Exited(info));
                        }
                        break;
                    }
                    Ok(None) => thread::sleep(Duration::from_millis(50)),
                    Err(_) => break,
                }
            });
        }

        Ok(Self {
            child,
            #[cfg(windows)]
            job,
            pid,
            stdin,
            logs,
            exit,
            shutdown_requested,
        })
    }

    pub fn pid(&self) -> u32 {
        self.pid
    }

    #[cfg(windows)]
    pub fn job(&self) -> Option<&JobObject> {
        self.job.as_ref()
    }

    pub fn logs(&self) -> Vec<LogLine> {
        self.logs.lock().unwrap().iter().cloned().collect()
    }

    pub fn exit_info(&self) -> Option<ExitInfo> {
        self.exit.lock().unwrap().clone()
    }

    pub fn is_running(&self) -> bool {
        self.exit_info().is_none()
    }

    pub fn wait_exit(&self, timeout: Duration) -> Option<ExitInfo> {
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline {
            if let Some(e) = self.exit_info() {
                return Some(e);
            }
            thread::sleep(Duration::from_millis(20));
        }
        self.exit_info()
    }

    /// Ask the child to exit; if it has not exited within `timeout`, terminate the
    /// whole job (child and every descendant).
    pub fn shutdown(&mut self, signal: GracefulSignal, timeout: Duration) -> ShutdownOutcome {
        if !self.is_running() {
            return ShutdownOutcome::AlreadyExited;
        }
        self.shutdown_requested.store(true, Ordering::SeqCst);
        match signal {
            GracefulSignal::CloseStdin => {
                self.stdin.take();
            }
            GracefulSignal::StdinLine(line) => {
                if let Some(stdin) = self.stdin.as_mut() {
                    let _ = writeln!(stdin, "{line}");
                    let _ = stdin.flush();
                }
            }
            GracefulSignal::Custom(f) => f(),
        }
        if self.wait_exit(timeout).is_some() {
            return ShutdownOutcome::Graceful;
        }
        self.force_kill();
        self.wait_exit(Duration::from_secs(5));
        ShutdownOutcome::Forced
    }

    pub fn force_kill(&mut self) {
        self.shutdown_requested.store(true, Ordering::SeqCst);
        #[cfg(windows)]
        if let Some(job) = &self.job {
            let _ = job.terminate(1);
            return;
        }
        let _ = self.child.lock().unwrap().kill();
    }
}

impl Drop for Supervised {
    fn drop(&mut self) {
        if self.is_running() {
            self.force_kill();
        }
        // The JobObject drop closes the handle, which also kills anything left.
    }
}

fn pump(
    src: impl Read + Send + 'static,
    stream: Stream,
    logs: Arc<Mutex<VecDeque<LogLine>>>,
    redact: Arc<Vec<String>>,
    sink: Option<EventSink>,
) {
    thread::spawn(move || {
        let reader = BufReader::new(src);
        for line in reader.split(b'\n') {
            let Ok(bytes) = line else { break };
            let mut text = String::from_utf8_lossy(&bytes).trim_end_matches('\r').to_string();
            for secret in redact.iter().filter(|s| !s.is_empty()) {
                text = text.replace(secret.as_str(), "[REDACTED]");
            }
            let entry = LogLine { stream, line: text };
            {
                let mut l = logs.lock().unwrap();
                if l.len() == LOG_CAPACITY {
                    l.pop_front();
                }
                l.push_back(entry.clone());
            }
            if let Some(s) = &sink {
                s(&Event::Output(entry));
            }
        }
    });
}
