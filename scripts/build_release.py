"""
Mustang AI — build a release: embedded runtime + NSIS installer (+ optional updater
artifacts and Authenticode signing) into build/release/<version>/.

    backend\\.venv\\Scripts\\python.exe scripts\\build_release.py [--skip-runtime] [--notes "..."]

Optional environment (each feature is off unless configured):

  Updates (opt-in in the app; builds without these report "updates not configured"):
    MUSTANG_UPDATE_ENDPOINT     URL of latest.json, e.g. https://example.org/mustang/latest.json
    MUSTANG_UPDATE_PUBKEY       minisign public key (contents, or path to the .pub file)
    MUSTANG_UPDATE_BASE_URL     URL prefix where the installer will be hosted (for latest.json)
    TAURI_SIGNING_PRIVATE_KEY   private key (contents or path), plus TAURI_SIGNING_PRIVATE_KEY_PASSWORD
    MUSTANG_UPDATE_ALLOW_HTTP=1 TEST ONLY: allow a plain-http endpoint (local update tests)

  Code signing (Authenticode, via scripts/sign.ps1 and the Windows SDK signtool):
    MUSTANG_SIGN_CERT_THUMBPRINT  SHA-1 thumbprint of a code-signing certificate in the user's store
    MUSTANG_SIGN_TIMESTAMP_URL    RFC 3161 timestamp server (default http://timestamp.digicert.com)
"""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAURI = ROOT / "src-tauri"
OUT = ROOT / "build" / "release"


def run(cmd: list[str], **kw) -> None:
    print("  $", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def read_key(value: str) -> str:
    p = Path(value)
    return p.read_text(encoding="utf-8").strip() if len(value) < 400 and p.is_file() else value.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-runtime", action="store_true", help="reuse build/desktop-runtime")
    ap.add_argument("--notes", default="", help="release notes shown in the update dialog")
    args = ap.parse_args()

    conf = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    version, product = conf["version"], conf["productName"]
    print(f"Release {product} {version}")
    run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_version_consistency.py"], cwd=ROOT / "backend")

    if not args.skip_runtime:
        run([sys.executable, str(ROOT / "scripts" / "build_desktop_runtime.py")])

    overlay = json.loads((TAURI / "tauri.release.conf.json").read_text(encoding="utf-8"))
    overlay.pop("$schema", None)
    env = dict(os.environ)

    endpoint = os.environ.get("MUSTANG_UPDATE_ENDPOINT")
    if endpoint:
        pubkey = os.environ.get("MUSTANG_UPDATE_PUBKEY")
        if not pubkey or not os.environ.get("TAURI_SIGNING_PRIVATE_KEY"):
            raise SystemExit("MUSTANG_UPDATE_ENDPOINT needs MUSTANG_UPDATE_PUBKEY and TAURI_SIGNING_PRIVATE_KEY")
        env["TAURI_SIGNING_PRIVATE_KEY"] = read_key(os.environ["TAURI_SIGNING_PRIVATE_KEY"])
        env.setdefault("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", "")
        updater = {"pubkey": read_key(pubkey), "endpoints": [endpoint], "windows": {"installMode": "passive"}}
        if os.environ.get("MUSTANG_UPDATE_ALLOW_HTTP") == "1":
            print("  WARNING: plain-http update endpoint allowed (test build)")
            updater["dangerousInsecureTransportProtocol"] = True
        overlay.setdefault("bundle", {})["createUpdaterArtifacts"] = True
        overlay["plugins"] = {"updater": updater}
        print(f"  updater: {endpoint}")
    else:
        print("  updater: not configured")

    thumbprint = os.environ.get("MUSTANG_SIGN_CERT_THUMBPRINT")
    if thumbprint:
        overlay.setdefault("bundle", {}).setdefault("windows", {})["signCommand"] = {
            "cmd": "powershell",
            "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "sign.ps1"), "%1"],
        }
        print("  code signing: enabled")
    else:
        print("  code signing: not configured (unsigned build; Windows SmartScreen will warn)")

    overlay_path = ROOT / "build" / "release.conf.json"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_path.write_text(json.dumps(overlay, indent=2), encoding="utf-8")

    npx = shutil.which("npx") or shutil.which("npx.cmd")
    run([npx, "tauri", "build", "--bundles", "nsis", "--config", str(overlay_path)], cwd=ROOT, env=env)

    nsis = TAURI / "target" / "release" / "bundle" / "nsis"
    installer = nsis / f"{product}_{version}_x64-setup.exe"
    if not installer.is_file():
        raise SystemExit(f"installer not found: {installer}")
    dest = OUT / version
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copy2(installer, dest / installer.name)
    artifacts = [dest / installer.name]

    sig = installer.with_name(installer.name + ".sig")
    if endpoint:
        shutil.copy2(sig, dest / sig.name)
        artifacts.append(dest / sig.name)
        base = os.environ.get("MUSTANG_UPDATE_BASE_URL", endpoint.rsplit("/", 1)[0]).rstrip("/")
        latest = {
            "version": version,
            "notes": args.notes or f"{product} {version}",
            "pub_date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "platforms": {
                "windows-x86_64": {
                    "signature": sig.read_text(encoding="utf-8").strip(),
                    "url": f"{base}/{installer.name.replace(' ', '%20')}",
                }
            },
        }
        (dest / "latest.json").write_text(json.dumps(latest, indent=2), encoding="utf-8")
        artifacts.append(dest / "latest.json")

    lines = [f"{sha256_of(p)}  {p.name}" for p in artifacts]
    (dest / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"Release artifacts: {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
