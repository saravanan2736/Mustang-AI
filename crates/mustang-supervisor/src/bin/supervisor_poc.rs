//! Scenario runner for the supervision prototype.
//!
//!   supervisor-poc all [--python <python.exe>]   run every scenario, exit 1 on failure
//!   supervisor-poc hold [--no-job]               internal: used by the parent-kill scenarios

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use mustang_supervisor::{process_alive, Event, GracefulSignal, ShutdownOutcome, SpawnSpec, Stream, Supervised};

const FIXTURE: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/child_fixture.py");
const DEFAULT_PYTHON: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../backend/.venv/Scripts/python.exe");

fn python() -> PathBuf {
    let args: Vec<String> = std::env::args().collect();
    if let Some(i) = args.iter().position(|a| a == "--python") {
        return PathBuf::from(&args[i + 1]);
    }
    std::env::var_os("MUSTANG_POC_PYTHON").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(DEFAULT_PYTHON))
}

fn fixture(extra: &[&str]) -> SpawnSpec {
    let mut spec = SpawnSpec::new(python()).arg("-u").arg(FIXTURE);
    for a in extra {
        spec = spec.arg(*a);
    }
    spec
}

fn wait_for(sup: &Supervised, needle: &str, timeout: Duration) -> Option<String> {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if let Some(l) = sup.logs().into_iter().find(|l| l.line.contains(needle)) {
            return Some(l.line);
        }
        thread::sleep(Duration::from_millis(20));
    }
    None
}

fn pid_from(sup: &Supervised, key: &str) -> Option<u32> {
    wait_for(sup, key, Duration::from_secs(10))?.split('=').nth(1)?.trim().parse().ok()
}

struct Results(Vec<(String, bool, String)>);

impl Results {
    fn record(&mut self, name: &str, ok: bool, detail: String) {
        println!("[{}] {name}: {detail}", if ok { "PASS" } else { "FAIL" });
        self.0.push((name.to_string(), ok, detail));
    }
}

fn scenario_capture(r: &mut Results) {
    let events = Arc::new(Mutex::new(0usize));
    let ev = events.clone();
    let sink: mustang_supervisor::EventSink = Arc::new(move |e: &Event| {
        if matches!(e, Event::Output(_)) {
            *ev.lock().unwrap() += 1;
        }
    });
    let mut spec = fixture(&["--lines", "25", "--echo-secret-env", "POC_SECRET"]);
    spec = spec.env("POC_SECRET", "test-secret-0123456789");
    spec.redact.push("test-secret-0123456789".into());
    let mut sup = Supervised::spawn(spec, Some(sink)).expect("spawn");
    let ready = wait_for(&sup, "READY", Duration::from_secs(10)).is_some();
    let logs = sup.logs();
    let out = logs.iter().filter(|l| l.stream == Stream::Stdout && l.line.starts_with("stdout line")).count();
    let err = logs.iter().filter(|l| l.stream == Stream::Stderr && l.line.starts_with("stderr line")).count();
    let leaked = logs.iter().any(|l| l.line.contains("test-secret-0123456789"));
    let redacted = logs.iter().any(|l| l.line.contains("[REDACTED]"));
    let outcome = sup.shutdown(GracefulSignal::StdinLine("shutdown".into()), Duration::from_secs(5));
    r.record(
        "stdout/stderr capture",
        ready && out == 25 && err == 25,
        format!("stdout={out} stderr={err} events={}", *events.lock().unwrap()),
    );
    r.record("secret redaction in captured logs", !leaked && redacted, format!("leaked={leaked} redacted={redacted}"));
    r.record("graceful exit (no grandchild)", outcome == ShutdownOutcome::Graceful, format!("{outcome:?}"));
}

fn scenario_crash(r: &mut Results) {
    let exited = Arc::new(Mutex::new(None));
    let ex = exited.clone();
    let sink: mustang_supervisor::EventSink = Arc::new(move |e: &Event| {
        if let Event::Exited(info) = e {
            *ex.lock().unwrap() = Some(info.clone());
        }
    });
    let t0 = Instant::now();
    let sup = Supervised::spawn(fixture(&["--crash-after", "1.0"]), Some(sink)).expect("spawn");
    let info = sup.wait_exit(Duration::from_secs(10));
    let event = exited.lock().unwrap().clone();
    let ok = matches!(&info, Some(i) if i.code == Some(3) && !i.expected) && event.is_some();
    r.record(
        "unexpected termination detected",
        ok,
        format!("exit={info:?} event_fired={} after {:?}", event.is_some(), t0.elapsed()),
    );
}

fn scenario_graceful_tree(r: &mut Results) {
    let mut sup = Supervised::spawn(fixture(&["--grandchild"]), None).expect("spawn");
    let gc = pid_from(&sup, "GRANDCHILD_PID").expect("grandchild pid");
    wait_for(&sup, "READY", Duration::from_secs(10));
    let in_job = sup.job().map(|j| j.contains_pid(gc).unwrap_or(false)).unwrap_or(false);
    let active = sup.job().and_then(|j| j.active_processes().ok()).unwrap_or(0);
    r.record(
        "grandchild inherits Job Object",
        in_job,
        format!("grandchild pid={gc} in_job={in_job} active_processes_in_job={active}"),
    );
    let t0 = Instant::now();
    let outcome = sup.shutdown(GracefulSignal::StdinLine("shutdown".into()), Duration::from_secs(5));
    thread::sleep(Duration::from_millis(300));
    let gc_alive = process_alive(gc);
    r.record(
        "graceful shutdown of process tree",
        outcome == ShutdownOutcome::Graceful && !gc_alive,
        format!("{outcome:?} in {:?}, grandchild_alive={gc_alive}", t0.elapsed()),
    );
}

fn scenario_force(r: &mut Results) {
    let mut sup = Supervised::spawn(fixture(&["--grandchild", "--ignore-shutdown"]), None).expect("spawn");
    let gc = pid_from(&sup, "GRANDCHILD_PID").expect("grandchild pid");
    let fx = pid_from(&sup, "FIXTURE_PID").expect("fixture pid");
    wait_for(&sup, "READY", Duration::from_secs(10));
    let t0 = Instant::now();
    let outcome = sup.shutdown(GracefulSignal::StdinLine("shutdown".into()), Duration::from_secs(2));
    thread::sleep(Duration::from_millis(300));
    let (fx_alive, gc_alive) = (process_alive(fx), process_alive(gc));
    r.record(
        "forced termination after graceful timeout",
        outcome == ShutdownOutcome::Forced && !fx_alive && !gc_alive,
        format!("{outcome:?} in {:?}, fixture_alive={fx_alive} grandchild_alive={gc_alive}", t0.elapsed()),
    );
}

fn scenario_parent_kill(r: &mut Results, use_job: bool) {
    let exe = std::env::current_exe().unwrap();
    let mut cmd = Command::new(exe);
    cmd.arg("hold").arg("--python").arg(python()).stdout(Stdio::piped()).stderr(Stdio::inherit());
    if !use_job {
        cmd.arg("--no-job");
    }
    let mut hold = cmd.spawn().expect("spawn hold");
    let mut line = String::new();
    BufReader::new(hold.stdout.take().unwrap()).read_line(&mut line).unwrap();
    // HOLD launcher=<pid> fixture=<pid> grandchild=<pid>
    let pids: Vec<u32> = line.split_whitespace().skip(1).filter_map(|kv| kv.split('=').nth(1)?.parse().ok()).collect();
    let before: Vec<bool> = pids.iter().map(|p| process_alive(*p)).collect();
    hold.kill().unwrap(); // TerminateProcess: no destructors, no cleanup code runs
    let _ = hold.wait();
    thread::sleep(Duration::from_millis(1500));
    let after: Vec<bool> = pids.iter().map(|p| process_alive(*p)).collect();
    let detail = format!("pids(launcher,fixture,grandchild)={pids:?} alive_before={before:?} alive_after_parent_kill={after:?}");
    if use_job {
        r.record("parent hard-kill cleans whole tree (Job Object)", before.iter().all(|a| *a) && after.iter().all(|a| !*a), detail);
    } else {
        let orphaned = after.iter().any(|a| *a);
        r.record("control: without Job Object, children are orphaned", orphaned, detail);
        for p in pids {
            let _ = Command::new("taskkill").args(["/F", "/PID", &p.to_string()]).output();
        }
    }
}

fn hold(use_job: bool) {
    // --ignore-shutdown: like uvicorn, the child does not notice stdin EOF when its
    // parent dies, so only the Job Object can clean it up.
    let mut spec = fixture(&["--grandchild", "--ignore-shutdown"]);
    spec.use_job = use_job;
    let sup = Supervised::spawn(spec, None).expect("spawn");
    let fx = pid_from(&sup, "FIXTURE_PID").unwrap_or(0);
    let gc = pid_from(&sup, "GRANDCHILD_PID").unwrap_or(0);
    wait_for(&sup, "READY", Duration::from_secs(10));
    println!("HOLD launcher={} fixture={fx} grandchild={gc}", sup.pid());
    loop {
        thread::sleep(Duration::from_secs(60));
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("hold") => hold(!args.iter().any(|a| a == "--no-job")),
        _ => {
            println!("python: {}", python().display());
            let mut r = Results(Vec::new());
            scenario_capture(&mut r);
            scenario_crash(&mut r);
            scenario_graceful_tree(&mut r);
            scenario_force(&mut r);
            scenario_parent_kill(&mut r, true);
            scenario_parent_kill(&mut r, false);
            let failed = r.0.iter().filter(|(_, ok, _)| !ok).count();
            println!("\n{} scenarios, {} passed, {} failed", r.0.len(), r.0.len() - failed, failed);
            std::process::exit(if failed == 0 { 0 } else { 1 });
        }
    }
}
