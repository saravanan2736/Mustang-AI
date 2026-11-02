"""
Mustang AI Runtime Setup & Verification
Downloads and validates official pinned llama.cpp Windows binaries with SHA-256 verification.
"""

import hashlib
import os
import sys
import zipfile
import urllib.request
from pathlib import Path

# Pinned official llama.cpp release configuration
PINNED_TAG = "b12000"
BASE_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{PINNED_TAG}"

PINNED_PACKAGES = {
    "cuda13": {
        "bin": {
            "filename": f"llama-{PINNED_TAG}-bin-win-cuda-13.4-x64.zip",
            "sha256": "b46f897c9f45e8d83a08dd88724307dde1f672c96652608062be3e08374ac3d5",
        },
        "cudart": {
            "filename": "cudart-llama-bin-win-cuda-13.4-x64.zip",
            "sha256": "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668",
        },
    },
    "cuda12": {
        "bin": {
            "filename": f"llama-{PINNED_TAG}-bin-win-cuda-12.4-x64.zip",
            "sha256": "a10a292ef8a85c5563f7f8a03e2d286133c890025010ecf0492ea55dd78c31a2",
        },
        "cudart": {
            "filename": "cudart-llama-bin-win-cuda-12.4-x64.zip",
            "sha256": "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6",
        },
    },
}

REQUIRED_BINARIES = [
    "llama-server.exe",
    "llama.dll",
    "ggml.dll",
]

REQUIRED_CUDA_DLLS = [
    "cublas64_*.dll",
    "cudart64_*.dll",
]


def compute_sha256(filepath: Path) -> str:
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(1024 * 1024):
            sha.update(chunk)
    return sha.hexdigest().lower()


def download_file(url: str, dest_path: Path, expected_sha256: str) -> bool:
    print(f"Downloading: {url} -> {dest_path}")
    headers = {"User-Agent": "MustangAI-Setup/1.0"}
    req = urllib.request.Request(url, headers=headers)
    
    try:
        with urllib.request.urlopen(req) as resp, open(dest_path, "wb") as out_f:
            total_size = int(resp.headers.get("content-length", 0))
            downloaded = 0
            while chunk := resp.read(1024 * 128):
                out_f.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    pct = (downloaded / total_size) * 100
                    sys.stdout.write(f"\rProgress: {downloaded / (1024*1024):.1f}MB / {total_size / (1024*1024):.1f}MB ({pct:.1f}%)")
                    sys.stdout.flush()
        print()
    except Exception as e:
        print(f"\n[ERROR] Failed to download {url}: {e}")
        if dest_path.exists():
            dest_path.unlink()
        return False

    # Checksum verification
    actual_sha = compute_sha256(dest_path)
    if actual_sha != expected_sha256.lower():
        print(f"[SECURITY ERROR] SHA-256 mismatch for {dest_path.name}!")
        print(f"  Expected: {expected_sha256.lower()}")
        print(f"  Received: {actual_sha}")
        dest_path.unlink()
        raise ValueError(f"Checksum verification failed for {dest_path.name}")
    
    print(f"[VERIFIED] SHA-256 matches: {dest_path.name}")
    return True


def setup_llama_runtime(variant: str = "cuda13", target_dir: Path | None = None) -> Path:
    if target_dir is None:
        target_dir = Path(__file__).resolve().parent / "bin"
    target_dir.mkdir(parents=True, exist_ok=True)

    server_exe = target_dir / "llama-server.exe"
    if server_exe.exists():
        print(f"[CHECK] Existing runtime found at {server_exe}")
        return server_exe

    if variant not in PINNED_PACKAGES:
        raise ValueError(f"Unknown variant '{variant}'. Supported: {list(PINNED_PACKAGES.keys())}")

    pkg_info = PINNED_PACKAGES[variant]
    temp_dir = target_dir.parent / "downloads"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # 1. Download & verify main bin zip
    bin_meta = pkg_info["bin"]
    bin_zip = temp_dir / bin_meta["filename"]
    bin_url = f"{BASE_URL}/{bin_meta['filename']}"
    if not bin_zip.exists():
        download_file(bin_url, bin_zip, bin_meta["sha256"])
    else:
        if compute_sha256(bin_zip) != bin_meta["sha256"]:
            bin_zip.unlink()
            download_file(bin_url, bin_zip, bin_meta["sha256"])

    # 2. Download & verify cudart zip
    cudart_meta = pkg_info["cudart"]
    cudart_zip = temp_dir / cudart_meta["filename"]
    cudart_url = f"{BASE_URL}/{cudart_meta['filename']}"
    if not cudart_zip.exists():
        download_file(cudart_url, cudart_zip, cudart_meta["sha256"])
    else:
        if compute_sha256(cudart_zip) != cudart_meta["sha256"]:
            cudart_zip.unlink()
            download_file(cudart_url, cudart_zip, cudart_meta["sha256"])

    # 3. Extract verified archives into target_dir
    print(f"Extracting {bin_zip.name} to {target_dir}...")
    with zipfile.ZipFile(bin_zip, "r") as z:
        z.extractall(target_dir)

    print(f"Extracting {cudart_zip.name} to {target_dir}...")
    with zipfile.ZipFile(cudart_zip, "r") as z:
        z.extractall(target_dir)

    # 4. Validate all required binaries exist
    for req in REQUIRED_BINARIES:
        p = target_dir / req
        if not p.exists():
            raise FileNotFoundError(f"Required binary '{req}' not found after extraction in {target_dir}")

    print(f"[SUCCESS] llama-server runtime successfully installed and verified in: {target_dir}")
    return server_exe


if __name__ == "__main__":
    variant_arg = sys.argv[1] if len(sys.argv) > 1 else "cuda13"
    try:
        setup_llama_runtime(variant_arg)
    except Exception as err:
        print(f"[FATAL] Runtime setup failed: {err}", file=sys.stderr)
        sys.exit(1)

