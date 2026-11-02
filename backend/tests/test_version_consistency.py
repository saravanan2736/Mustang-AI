"""
The product version is recorded in several files (installer, updater, shell, backend, UI);
scripts/set_version.py updates them together. This guards against drift.
"""

import json
import re
import tomllib
from pathlib import Path

from app.config import settings

ROOT = Path(__file__).resolve().parents[2]


def test_all_version_fields_agree():
    versions = {
        "src-tauri/tauri.conf.json": json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))["version"],
        "src-tauri/Cargo.toml": tomllib.loads((ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8"))["package"]["version"],
        "package.json": json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"],
        "frontend/package.json": json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))["version"],
        "backend/pyproject.toml": tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
        "backend settings.VERSION": settings.VERSION,
    }
    assert len(set(versions.values())) == 1, f"version mismatch: {versions}"
    assert re.fullmatch(r"\d+\.\d+\.\d+", settings.VERSION)
