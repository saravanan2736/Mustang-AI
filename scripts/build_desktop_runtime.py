"""
Mustang AI — build the embedded Python backend runtime for the desktop shell.

Produces build/desktop-runtime/ (bundled by src-tauri/tauri.release.conf.json):

    python/     CPython 3.11.9 embeddable distribution (PSF-signed python.exe)
      python311._pth          isolated path config: no site, no PYTHONPATH, no user site
      Lib/site-packages/      runtime dependencies from backend/requirements.lock
    backend/    app/ + mustang_launcher.py, byte-compiled
    llama/      llama.cpp CUDA 13.4 runtime pruned to llama-server's DLL closure, plus the
                app-local Visual C++ runtime it imports and third-party license notices
    runtime-manifest.json   versions, sizes and hashes of what was assembled (the desktop
                            shell verifies llama/ against it at start-up)

Usage (from the repo root, with the backend dev venv):
    backend\\.venv\\Scripts\\python.exe scripts\\build_desktop_runtime.py            # assemble
    backend\\.venv\\Scripts\\python.exe scripts\\build_desktop_runtime.py --relock   # regenerate the lock first

The lock pins exactly the versions installed in backend/.venv (the set the test suite runs
against) with their wheel SHA-256 hashes; installation uses --require-hashes.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
LOCK_FILE = BACKEND / "requirements.lock"
CACHE = ROOT / "build" / "cache"
OUT = ROOT / "build" / "desktop-runtime"

PY_VERSION = "3.11.9"
PY_EMBED_NAME = f"python-{PY_VERSION}-embed-amd64.zip"
PY_EMBED_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/{PY_EMBED_NAME}"
# Pinned 2026-11-02; python.exe and python311.dll inside verified as Authenticode-signed
# by "Python Software Foundation".
PY_EMBED_SHA256 = "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"
PY_SIGNER = "Python Software Foundation"

PTH_CONTENTS = """python311.zip
.
Lib\\site-packages
..\\backend
# 'import site' intentionally absent: isolated from PYTHONPATH and user/system site-packages.
"""

BACKEND_COPY = ["app", "mustang_launcher.py"]

# llama.cpp: the pinned release tag, URLs and SHA-256 hashes live in one place.
sys.path.insert(0, str(BACKEND / "runtime"))
import setup_runtime  # noqa: E402

# CUDA 13.x builds carry native sm_120 kernels (RTX 50-series / Blackwell); 12.4 does not.
LLAMA_VARIANT = "cuda13"
# llama-server.exe's import closure (dumpbin /dependents, recursive) plus the ggml backends
# it loads at run time: CUDA and every CPU dispatch variant (the best one for the host CPU is
# chosen at start-up). Excluded: other tools, ggml-rpc (network backend) and cudart64_13.dll
# (ggml-cuda links the CUDA runtime statically; it imports only cublas64_13.dll).
LLAMA_FILES = [
    "llama-server.exe", "llama-server-impl.dll", "llama.dll", "llama-common.dll", "mtmd.dll",
    "ggml.dll", "ggml-base.dll", "ggml-cuda.dll", "cublas64_13.dll", "cublasLt64_13.dll", "libomp.dll",
    "ggml-cpu-alderlake.dll", "ggml-cpu-cannonlake.dll", "ggml-cpu-cascadelake.dll", "ggml-cpu-cooperlake.dll",
    "ggml-cpu-haswell.dll", "ggml-cpu-icelake.dll", "ggml-cpu-ivybridge.dll", "ggml-cpu-piledriver.dll",
    "ggml-cpu-sandybridge.dll", "ggml-cpu-sapphirerapids.dll", "ggml-cpu-skylakex.dll", "ggml-cpu-sse42.dll",
    "ggml-cpu-x64.dll", "ggml-cpu-zen4.dll", "LICENSE-LLVM-OpenMP",
]
LLAMA_LICENSE_URL = f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{setup_runtime.PINNED_TAG}/LICENSE"
# The llama.cpp binaries import the Visual C++ runtime, which Windows does not ship.
# Deployed app-locally (permitted by the Visual Studio redistribution terms).
VC_RUNTIME_DLLS = ["vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"]
# Imports that are part of Windows 10/11 itself.
OS_DLL_PREFIXES = ("api-ms-win-",)
OS_DLLS = {"kernel32.dll", "advapi32.dll", "shell32.dll", "ws2_32.dll", "crypt32.dll", "user32.dll", "ole32.dll",
           "bcrypt.dll", "psapi.dll"}

THIRD_PARTY_NOTICES = """Mustang AI — third-party components in this folder

llama.cpp (llama-server, llama*.dll, ggml*.dll, mtmd.dll) — release {tag}, CUDA {cuda}
  MIT License — see LICENSE-llama.cpp.txt — https://github.com/ggml-org/llama.cpp

LLVM OpenMP runtime (libomp.dll)
  Apache License 2.0 with LLVM Exceptions — see LICENSE-LLVM-OpenMP

NVIDIA cuBLAS (cublas64_13.dll, cublasLt64_13.dll)
  Redistributed unmodified under the NVIDIA CUDA Toolkit End User License Agreement
  (redistributable components) — https://docs.nvidia.com/cuda/eula/

Microsoft Visual C++ runtime (vcruntime140.dll, vcruntime140_1.dll, msvcp140.dll) — {vc}
  Redistributed unmodified under the Microsoft Visual Studio license terms (distributable code)
"""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("  $", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, check=True, **kw)


def relock() -> None:
    """Resolve pyproject runtime dependencies, constrained to the venv's versions, with hashes."""
    deps = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"], check=True, capture_output=True, text=True).stdout
    with tempfile.TemporaryDirectory() as tmp:
        req_in, constraints, report = Path(tmp, "req.in"), Path(tmp, "constraints.txt"), Path(tmp, "report.json")
        req_in.write_text("\n".join(deps) + "\n", encoding="utf-8")
        constraints.write_text(freeze, encoding="utf-8")
        run([
            sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed", "--only-binary=:all:",
            "--quiet", "--report", str(report), "-r", str(req_in), "-c", str(constraints),
        ])
        data = json.loads(report.read_text(encoding="utf-8"))
    lines = [
        "# Generated by scripts/build_desktop_runtime.py --relock. Do not edit by hand.",
        "# Runtime closure of backend/pyproject.toml, pinned to backend/.venv versions, wheel hashes for win_amd64 / cp311.",
    ]
    for item in sorted(data["install"], key=lambda i: i["metadata"]["name"].lower()):
        name, version = item["metadata"]["name"], item["metadata"]["version"]
        algo, digest = item["download_info"]["archive_info"]["hash"].split("=", 1)
        if algo != "sha256":
            raise SystemExit(f"{name}: unexpected hash algorithm {algo}")
        lines.append(f"{name}=={version} --hash=sha256:{digest}")
    LOCK_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {LOCK_FILE} ({len(lines) - 2} packages)")


def fetch_embed() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / PY_EMBED_NAME
    if not target.exists() or sha256_of(target) != PY_EMBED_SHA256:
        print(f"Downloading {PY_EMBED_URL}")
        partial = target.with_suffix(".partial")
        with urllib.request.urlopen(PY_EMBED_URL) as resp, open(partial, "wb") as out:
            shutil.copyfileobj(resp, out)
        actual = sha256_of(partial)
        if actual != PY_EMBED_SHA256:
            partial.unlink()
            raise SystemExit(f"SHA-256 mismatch for {PY_EMBED_NAME}: {actual}")
        partial.replace(target)
    return target


def verify_signature(*files: Path, signer: str = PY_SIGNER) -> None:
    script = "; ".join(
        f"$s = Get-AuthenticodeSignature -LiteralPath '{f}'; "
        f"if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch '{signer}') "
        f"{{ Write-Error ('bad signature: {f.name} ' + $s.Status); exit 1 }}"
        for f in files
    )
    run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])


def vs_install_path() -> Path | None:
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.exists():
        return None
    out = subprocess.run([str(vswhere), "-latest", "-products", "*", "-property", "installationPath"],
                         capture_output=True, text=True).stdout.strip()
    return Path(out) if out else None


def find_vc_runtime() -> tuple[Path, str]:
    """Newest x64 Visual C++ runtime redistributable folder from the installed Build Tools."""
    vs = vs_install_path()
    if vs is None:
        raise SystemExit("Visual Studio Build Tools not found (needed for the app-local VC++ runtime)")
    candidates = sorted((vs / "VC" / "Redist" / "MSVC").glob("*/x64/Microsoft.VC*.CRT"))
    if not candidates:
        raise SystemExit("No x64 VC++ redistributable folder under the Build Tools")
    crt = candidates[-1]
    return crt, crt.parents[1].name


def dumpbin_path() -> Path | None:
    vs = vs_install_path()
    hits = sorted(vs.glob("VC/Tools/MSVC/*/bin/Hostx64/x64/dumpbin.exe")) if vs else []
    return hits[-1] if hits else None


def pe_imports(dumpbin: Path, pe: Path) -> list[str]:
    out = subprocess.run([str(dumpbin), "/nologo", "/dependents", str(pe)], capture_output=True, text=True).stdout
    names, active = [], False
    for line in out.splitlines():
        if "Image has the following dependencies" in line:
            active = True
        elif "Summary" in line:
            active = False
        elif active and line.strip().lower().endswith(".dll"):
            names.append(line.strip())
    return names


def fetch_llama_zip(kind: str) -> Path:
    meta = setup_runtime.PINNED_PACKAGES[LLAMA_VARIANT][kind]
    for folder in (BACKEND / "runtime" / "downloads", CACHE):
        cand = folder / meta["filename"]
        if cand.exists() and sha256_of(cand) == meta["sha256"]:
            return cand
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / meta["filename"]
    setup_runtime.download_file(f"{setup_runtime.BASE_URL}/{meta['filename']}", target, meta["sha256"])
    return target


def assemble_llama(dest: Path) -> dict:
    """Pruned, verified llama.cpp runtime. Returns its manifest section."""
    dest.mkdir(parents=True)
    wanted = {name.lower(): name for name in LLAMA_FILES}
    for kind in ("bin", "cudart"):
        archive = fetch_llama_zip(kind)
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():
                base = Path(member).name
                if base.lower() in wanted:
                    (dest / base).write_bytes(z.read(member))
                    wanted.pop(base.lower())
    if wanted:
        raise SystemExit(f"llama.cpp release is missing expected files: {sorted(wanted.values())}")

    crt, vc_version = find_vc_runtime()
    for name in VC_RUNTIME_DLLS:
        shutil.copy2(crt / name, dest / name)
    verify_signature(*(dest / n for n in VC_RUNTIME_DLLS), signer="Microsoft Corporation")

    print(f"  fetching llama.cpp license ({setup_runtime.PINNED_TAG})")
    with urllib.request.urlopen(LLAMA_LICENSE_URL) as resp:
        license_text = resp.read().decode("utf-8")
    if "MIT License" not in license_text:
        raise SystemExit("Unexpected llama.cpp LICENSE content")
    (dest / "LICENSE-llama.cpp.txt").write_text(license_text, encoding="utf-8")
    (dest / "THIRD-PARTY-NOTICES.txt").write_text(
        THIRD_PARTY_NOTICES.format(tag=setup_runtime.PINNED_TAG, cuda="13.4", vc=f"MSVC {vc_version}"), encoding="utf-8")

    dumpbin = dumpbin_path()
    if dumpbin:
        local = {p.name.lower() for p in dest.iterdir()}
        missing = set()
        for pe in [p for p in dest.iterdir() if p.suffix.lower() in (".exe", ".dll")]:
            for dep in pe_imports(dumpbin, pe):
                d = dep.lower()
                if d not in local and d not in OS_DLLS and not d.startswith(OS_DLL_PREFIXES):
                    missing.add(f"{pe.name} -> {dep}")
        if missing:
            raise SystemExit("Unresolved imports in llama runtime:\n  " + "\n  ".join(sorted(missing)))
        print("  import closure verified with dumpbin: every non-OS DLL import is present locally")
    else:
        print("  WARNING: dumpbin not found; import closure not verified")

    with tempfile.TemporaryDirectory() as tmp:
        env = {"SystemRoot": os.environ["SystemRoot"], "PATH": str(Path(os.environ["SystemRoot"]) / "System32")}
        version = subprocess.run([str(dest / "llama-server.exe"), "--version"], env=env, cwd=tmp,
                                 capture_output=True, text=True, timeout=60)
        text = (version.stdout + version.stderr).strip()
        if version.returncode != 0 or f"build {setup_runtime.PINNED_TAG.lstrip('b')}" not in text:
            raise SystemExit(f"llama-server --version failed ({version.returncode}): {text[-400:]}")
        print(f"  {text.splitlines()[0]}")

    files = sorted(p for p in dest.iterdir() if p.is_file())
    return {
        "tag": setup_runtime.PINNED_TAG,
        "variant": LLAMA_VARIANT,
        "vc_runtime": vc_version,
        "files": [{"name": p.name, "size": p.stat().st_size, "sha256": sha256_of(p)} for p in files],
    }


def assemble() -> None:
    if not LOCK_FILE.exists():
        raise SystemExit(f"{LOCK_FILE} missing; run with --relock")
    embed = fetch_embed()
    if OUT.exists():
        shutil.rmtree(OUT)
    py_dir, be_dir = OUT / "python", OUT / "backend"
    py_dir.mkdir(parents=True)

    print("Extracting embeddable Python")
    with zipfile.ZipFile(embed) as z:
        z.extractall(py_dir)
    verify_signature(py_dir / "python.exe", py_dir / "python311.dll")
    (py_dir / "python311._pth").write_text(PTH_CONTENTS, encoding="utf-8")

    print("Installing locked dependencies")
    run([
        sys.executable, "-m", "pip", "install", "--quiet", "--require-hashes", "--only-binary=:all:", "--no-deps",
        "--no-compile", "--disable-pip-version-check", "--target", str(py_dir / "Lib" / "site-packages"),
        "-r", str(LOCK_FILE),
    ])

    print("Copying backend")
    be_dir.mkdir()
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in BACKEND_COPY:
        src = BACKEND / name
        if src.is_dir():
            shutil.copytree(src, be_dir / name, ignore=ignore)
        else:
            shutil.copy2(src, be_dir / name)

    print("Byte-compiling with the embedded interpreter")
    embedded = py_dir / "python.exe"
    run([str(embedded), "-m", "compileall", "-q", "-j", "0", str(be_dir), str(py_dir / "Lib" / "site-packages")])

    print("Smoke test: import the app with the embedded interpreter")
    with tempfile.TemporaryDirectory() as tmp:
        # Minimal environment, like the desktop shell's allow-list.
        env = {
            "SystemRoot": os.environ["SystemRoot"],
            "MUSTANG_RUN_DIR": tmp,
            "MUSTANG_MODELS_DIR": str(Path(tmp, "models")),
            "MUSTANG_RUNTIME_DIR": str(Path(tmp, "bin")),
        }
        out = run([str(embedded), "-c", "import sys, app.main, mustang_launcher; print(sys.version.split()[0], len(app.main.app.routes))"],
                  env=env, capture_output=True, text=True, cwd=tmp).stdout.strip()
        print(f"  imported OK: python {out}")

    print("Assembling llama.cpp runtime")
    llama = assemble_llama(OUT / "llama")

    files = [p for p in OUT.rglob("*") if p.is_file()]
    manifest = {
        "python": PY_VERSION,
        "python_embed_sha256": PY_EMBED_SHA256,
        "lock_sha256": sha256_of(LOCK_FILE),
        "packages": [l.split(" ")[0] for l in LOCK_FILE.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")],
        "file_count": len(files),
        "total_bytes": sum(p.stat().st_size for p in files),
        "launcher_sha256": sha256_of(be_dir / "mustang_launcher.py"),
        "llama": llama,
    }
    (OUT / "runtime-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Done: {OUT} ({manifest['file_count']} files, {manifest['total_bytes'] / 1e6:.1f} MB)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--relock", action="store_true", help="regenerate backend/requirements.lock from the venv first")
    args = ap.parse_args()
    if args.relock:
        relock()
    assemble()
    return 0


if __name__ == "__main__":
    sys.exit(main())
