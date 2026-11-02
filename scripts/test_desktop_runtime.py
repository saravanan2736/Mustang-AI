"""
Mustang AI — run the backend test suite inside the assembled embedded runtime.

The tests (backend/tests) run under build/desktop-runtime/python/python.exe and import
`app` / `mustang_launcher` from the packaged build/desktop-runtime/backend copy, not from
the source tree. pytest and its plugins are installed into a separate, never-shipped
directory (build/desktop-runtime-tests).

Usage (from the repo root):
    backend\\.venv\\Scripts\\python.exe scripts\\build_desktop_runtime.py
    backend\\.venv\\Scripts\\python.exe scripts\\test_desktop_runtime.py [extra pytest args]
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "build" / "desktop-runtime"
EMBEDDED = RUNTIME / "python" / "python.exe"
TEST_SITE = ROOT / "build" / "desktop-runtime-tests" / "site-packages"
TEST_TOOLS = ["pytest", "pytest-asyncio"]

RUNNER = r"""
import sys
sys.path[:0] = [{test_site!r}]
import app, pytest
locations = list(app.__path__)
print("packaged app under test:", locations, flush=True)
assert locations and all(p.startswith({packaged!r}) for p in locations), "tests must import only the packaged backend copy"
sys.exit(pytest.main(["-o", "pythonpath=", "-p", "no:cacheprovider", "--rootdir", {backend!r}, {tests!r}] + {extra!r}))
"""


def main() -> int:
    if not EMBEDDED.exists():
        raise SystemExit("Embedded runtime missing; run scripts/build_desktop_runtime.py first")
    if not TEST_SITE.exists():
        freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"], check=True, capture_output=True, text=True).stdout
        pins = [line for line in freeze.splitlines() if line.split("==")[0].lower() in ("pytest", "pytest-asyncio", "iniconfig", "pluggy", "packaging", "pygments")]
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--only-binary=:all:", "--disable-pip-version-check",
                        "--target", str(TEST_SITE), *pins], check=True)
    code = RUNNER.format(
        test_site=str(TEST_SITE),
        packaged=str(RUNTIME / "backend"),
        backend=str(ROOT / "backend"),
        tests=str(ROOT / "backend" / "tests"),
        extra=sys.argv[1:],
    )
    # The desktop shell's environment allow-list (crates/mustang-supervisor ENV_ALLOWLIST):
    # PATH is System32 only (where nvidia-smi lives); ProgramFiles is required by NVML.
    # The llama.cpp runtime is not packaged until Phase D, so the GPU tests use the repo's
    # verified runtime directory.
    allow = ("SystemRoot", "SystemDrive", "windir", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
             "ProgramData", "ProgramFiles", "ProgramW6432", "CommonProgramFiles", "COMPUTERNAME",
             "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "PATHEXT")
    env = {k: os.environ[k] for k in allow if k in os.environ}
    env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    env["MUSTANG_RUNTIME_DIR"] = str(ROOT / "backend" / "runtime" / "bin")
    return subprocess.run([str(EMBEDDED), "-c", code], env=env, cwd=str(ROOT / "backend" / "tests")).returncode


if __name__ == "__main__":
    sys.exit(main())
