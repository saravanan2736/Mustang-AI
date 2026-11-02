"""
Mustang AI — set the product version everywhere it is recorded.

    backend\\.venv\\Scripts\\python.exe scripts\\set_version.py 0.2.0

Updates src-tauri/tauri.conf.json (the installer/updater version), src-tauri/Cargo.toml,
package.json, frontend/package.json, backend/pyproject.toml and backend/app/config.py.
backend/tests/test_version_consistency.py fails if they ever disagree.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    new, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"{path}: version field not found")
    path.write_bytes(new.encode("utf-8"))


def set_json_version(path: Path, version: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = version
    path.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def main() -> int:
    if len(sys.argv) != 2 or not SEMVER.match(sys.argv[1]):
        raise SystemExit("usage: set_version.py MAJOR.MINOR.PATCH")
    version = sys.argv[1]
    set_json_version(ROOT / "src-tauri" / "tauri.conf.json", version)
    set_json_version(ROOT / "package.json", version)
    set_json_version(ROOT / "frontend" / "package.json", version)
    replace_once(ROOT / "src-tauri" / "Cargo.toml", r'^version = "[^"]+"', f'version = "{version}"')
    replace_once(ROOT / "backend" / "pyproject.toml", r'^version = "[^"]+"', f'version = "{version}"')
    replace_once(ROOT / "backend" / "app" / "config.py", r'^(\s*VERSION: str = )"[^"]+"', rf'\g<1>"{version}"')
    print(f"Version set to {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
