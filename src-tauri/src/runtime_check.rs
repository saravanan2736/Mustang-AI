//! Start-up checks of the inference runtime:
//!
//! * integrity — every llama.cpp file must match `runtime-manifest.json` (size always; SHA-256
//!   when the file is new or changed since the last successful check, cached per data root);
//! * GPU — detect the NVIDIA GPU and driver with `nvidia-smi`. Without a GPU, or with a driver
//!   too old for the bundled CUDA 13 runtime, llama.cpp falls back to the CPU; the UI says so.

use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const VERIFIED_CACHE: &str = "runtime-verified.json";
/// CUDA 13.x requires an R580-series or newer NVIDIA driver.
const MIN_DRIVER: (u32, u32) = (580, 0);
const NVIDIA_SMI_TIMEOUT: Duration = Duration::from_secs(8);
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Deserialize)]
struct Manifest {
    llama: LlamaManifest,
}

#[derive(Deserialize)]
struct LlamaManifest {
    tag: String,
    files: Vec<ManifestFile>,
}

#[derive(Deserialize)]
struct ManifestFile {
    name: String,
    size: u64,
    sha256: String,
}

#[derive(Serialize, Deserialize, Default)]
struct VerifiedCache {
    tag: String,
    /// file name -> "size:mtime_ns" of the last copy whose hash matched the manifest
    files: HashMap<String, String>,
}

#[derive(Debug)]
pub struct IntegrityReport {
    pub files: usize,
    pub hashed: usize,
    pub millis: u128,
}

fn stamp(path: &Path) -> Option<String> {
    let meta = fs::metadata(path).ok()?;
    let mtime = meta.modified().ok()?.duration_since(UNIX_EPOCH).ok()?.as_nanos();
    Some(format!("{}:{mtime}", meta.len()))
}

fn sha256_file(path: &Path) -> std::io::Result<String> {
    let mut file = fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(hasher.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

/// Verify `llama_dir` against the manifest. `cache_dir` holds the verification cache.
pub fn verify_llama(manifest_path: &Path, llama_dir: &Path, cache_dir: &Path) -> Result<IntegrityReport, String> {
    let started = Instant::now();
    let manifest: Manifest = serde_json::from_str(
        &fs::read_to_string(manifest_path).map_err(|e| format!("cannot read {}: {e}", manifest_path.display()))?,
    )
    .map_err(|e| format!("invalid runtime manifest: {e}"))?;

    let cache_path = cache_dir.join(VERIFIED_CACHE);
    let mut cache: VerifiedCache = fs::read_to_string(&cache_path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .filter(|c: &VerifiedCache| c.tag == manifest.llama.tag)
        .unwrap_or_else(|| VerifiedCache { tag: manifest.llama.tag.clone(), ..Default::default() });

    let damaged = |name: &str, why: &str| {
        format!("The local inference engine files are damaged or were modified ({name}: {why}). Reinstall Mustang AI to repair them.")
    };
    let mut hashed = 0;
    for f in &manifest.llama.files {
        let path = llama_dir.join(&f.name);
        let meta = fs::metadata(&path).map_err(|_| damaged(&f.name, "missing"))?;
        if meta.len() != f.size {
            return Err(damaged(&f.name, "size differs"));
        }
        let current = stamp(&path).unwrap_or_default();
        if cache.files.get(&f.name) == Some(&current) {
            continue;
        }
        hashed += 1;
        let digest = sha256_file(&path).map_err(|e| damaged(&f.name, &e.to_string()))?;
        if !digest.eq_ignore_ascii_case(&f.sha256) {
            cache.files.remove(&f.name);
            let _ = fs::write(&cache_path, serde_json::to_string(&cache).unwrap_or_default());
            return Err(damaged(&f.name, "checksum mismatch"));
        }
        cache.files.insert(f.name.clone(), current);
    }
    if hashed > 0 {
        let _ = fs::write(&cache_path, serde_json::to_string_pretty(&cache).unwrap_or_default());
    }
    Ok(IntegrityReport { files: manifest.llama.files.len(), hashed, millis: started.elapsed().as_millis() })
}

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum GpuMode {
    /// NVIDIA GPU with a CUDA 13-capable driver.
    Cuda,
    /// No NVIDIA GPU or driver found: llama.cpp runs on the CPU.
    CpuNoGpu,
    /// NVIDIA GPU present but the driver is too old for CUDA 13: llama.cpp runs on the CPU.
    CpuOldDriver,
}

#[derive(Clone, Debug, Serialize)]
pub struct GpuStatus {
    pub mode: GpuMode,
    pub gpu_name: Option<String>,
    pub driver: Option<String>,
    pub message: Option<String>,
}

fn nvidia_smi_path() -> PathBuf {
    let root = std::env::var_os("SystemRoot").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(r"C:\Windows"));
    root.join("System32").join("nvidia-smi.exe")
}

/// Run `nvidia-smi` (driver-installed, System32) without a console window, with a timeout.
fn query_nvidia_smi() -> Option<String> {
    use std::os::windows::process::CommandExt;
    let exe = nvidia_smi_path();
    if !exe.is_file() {
        return None;
    }
    let mut child = Command::new(exe)
        .args(["--query-gpu=name,driver_version", "--format=csv,noheader"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .ok()?;
    let deadline = Instant::now() + NVIDIA_SMI_TIMEOUT;
    loop {
        match child.try_wait() {
            Ok(Some(status)) if status.success() => break,
            Ok(Some(_)) => return None,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(50)),
            _ => {
                let _ = child.kill();
                return None;
            }
        }
    }
    let mut out = String::new();
    child.stdout.take()?.read_to_string(&mut out).ok()?;
    Some(out)
}

fn parse_driver(version: &str) -> Option<(u32, u32)> {
    let mut parts = version.trim().split('.');
    Some((parts.next()?.parse().ok()?, parts.next()?.parse().ok()?))
}

fn min_driver() -> (u32, u32) {
    // Test hook: MUSTANG_DESKTOP_MIN_DRIVER=999.0 exercises the old-driver path on any machine.
    std::env::var("MUSTANG_DESKTOP_MIN_DRIVER").ok().and_then(|v| parse_driver(&v)).unwrap_or(MIN_DRIVER)
}

/// Classify `nvidia-smi --query-gpu=name,driver_version --format=csv,noheader` output.
pub fn classify(output: Option<&str>, minimum: (u32, u32)) -> GpuStatus {
    let first = output.and_then(|o| o.lines().map(str::trim).find(|l| !l.is_empty()));
    let Some((name, driver)) = first.and_then(|l| l.rsplit_once(',')).map(|(n, d)| (n.trim().to_string(), d.trim().to_string()))
    else {
        return GpuStatus {
            mode: GpuMode::CpuNoGpu,
            gpu_name: None,
            driver: None,
            message: Some("No NVIDIA GPU was detected. Models run on the CPU, which is much slower.".into()),
        };
    };
    match parse_driver(&driver) {
        Some(v) if v >= minimum => GpuStatus { mode: GpuMode::Cuda, gpu_name: Some(name), driver: Some(driver), message: None },
        _ => GpuStatus {
            mode: GpuMode::CpuOldDriver,
            message: Some(format!(
                "The NVIDIA driver {driver} is too old for GPU acceleration (CUDA 13 needs {}.{:02} or newer). \
                 Models run on the CPU until the driver is updated.",
                minimum.0, minimum.1
            )),
            gpu_name: Some(name),
            driver: Some(driver),
        },
    }
}

pub fn detect_gpu() -> GpuStatus {
    classify(query_nvidia_smi().as_deref(), min_driver())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_manifest(dir: &Path, files: &[(&str, &[u8])]) -> PathBuf {
        let entries: Vec<_> = files
            .iter()
            .map(|(n, data)| {
                let digest: String = Sha256::digest(data).iter().map(|b| format!("{b:02x}")).collect();
                serde_json::json!({"name": n, "size": data.len(), "sha256": digest})
            })
            .collect();
        let path = dir.join("runtime-manifest.json");
        fs::write(&path, serde_json::json!({"llama": {"tag": "b1", "files": entries}}).to_string()).unwrap();
        path
    }

    fn temp_dir(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("mustang-rtcheck-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(d.join("llama")).unwrap();
        d
    }

    #[test]
    fn intact_runtime_passes_and_second_check_uses_cache() {
        let d = temp_dir("ok");
        fs::write(d.join("llama/a.dll"), b"alpha").unwrap();
        fs::write(d.join("llama/b.exe"), b"bravo").unwrap();
        let m = write_manifest(&d, &[("a.dll", b"alpha"), ("b.exe", b"bravo")]);
        let first = verify_llama(&m, &d.join("llama"), &d).unwrap();
        assert_eq!((first.files, first.hashed), (2, 2));
        let second = verify_llama(&m, &d.join("llama"), &d).unwrap();
        assert_eq!(second.hashed, 0, "unchanged files must not be re-hashed");
    }

    #[test]
    fn tampered_missing_or_resized_files_fail() {
        let d = temp_dir("bad");
        fs::write(d.join("llama/a.dll"), b"alpha").unwrap();
        let m = write_manifest(&d, &[("a.dll", b"alpha"), ("b.exe", b"bravo")]);
        assert!(verify_llama(&m, &d.join("llama"), &d).unwrap_err().contains("b.exe: missing"));

        fs::write(d.join("llama/b.exe"), b"bravo!").unwrap();
        assert!(verify_llama(&m, &d.join("llama"), &d).unwrap_err().contains("size differs"));

        // Same size, different content, after a successful (cached) check: still caught,
        // because modification changes the file's timestamp.
        fs::write(d.join("llama/b.exe"), b"bravo").unwrap();
        verify_llama(&m, &d.join("llama"), &d).unwrap();
        std::thread::sleep(Duration::from_millis(20));
        fs::write(d.join("llama/b.exe"), b"BRAVO").unwrap();
        assert!(verify_llama(&m, &d.join("llama"), &d).unwrap_err().contains("checksum mismatch"));
    }

    #[test]
    fn gpu_classification() {
        let ok = classify(Some("NVIDIA GeForce RTX 5050 Laptop GPU, 617.42\n"), (580, 0));
        assert_eq!(ok.mode, GpuMode::Cuda);
        assert_eq!(ok.gpu_name.as_deref(), Some("NVIDIA GeForce RTX 5050 Laptop GPU"));
        assert_eq!(ok.driver.as_deref(), Some("617.42"));

        let old = classify(Some("NVIDIA GeForce GTX 1080, 546.33"), (580, 0));
        assert_eq!(old.mode, GpuMode::CpuOldDriver);
        assert!(old.message.unwrap().contains("546.33"));

        assert_eq!(classify(None, (580, 0)).mode, GpuMode::CpuNoGpu);
        assert_eq!(classify(Some("\n"), (580, 0)).mode, GpuMode::CpuNoGpu);
        assert_eq!(classify(Some("RTX, garbage"), (580, 0)).mode, GpuMode::CpuOldDriver);
    }
}
