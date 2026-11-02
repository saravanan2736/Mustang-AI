"""
Mustang AI M4 Phase C — desktop integration contract.
Route-level authentication, desktop-mode token handling, bootstrap rules, the Tauri
origin, graceful shutdown, schema versioning and launcher log redaction.
"""

import asyncio
import logging
import re
import sqlite3

import aiosqlite
import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from app.config import settings
from app.core import security
from app.db import database
from app.main import app
from mustang_launcher import RedactingFilter, import_database

PUBLIC_ROUTES = {"/health", "/api/auth/bootstrap"}
TAURI_ORIGIN = "http://tauri.localhost"


def _concrete_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def _api_routes():
    # From the OpenAPI schema: independent of how FastAPI stores included routers internally.
    for path, operations in app.openapi()["paths"].items():
        if path in PUBLIC_ROUTES:
            continue
        for method in operations:
            yield method.upper(), path


@pytest.mark.asyncio
async def test_every_non_public_route_requires_token(anon_client: AsyncClient):
    routes = list(_api_routes())
    assert len(routes) > 30, "route discovery failed"
    for method, path in routes:
        res = await anon_client.request(method, _concrete_path(path), json={})
        assert res.status_code == 401, f"{method} {path} answered {res.status_code} without a token"


@pytest.mark.asyncio
async def test_public_health_needs_no_token(anon_client: AsyncClient):
    res = await anon_client.get("/health")
    assert res.status_code == 200
    assert res.json()["version"] == settings.VERSION


@pytest.mark.asyncio
async def test_authenticated_client_reaches_previously_public_routes(async_client: AsyncClient):
    for path in ("/api/conversations", "/api/profiles", "/api/settings", "/api/system/hardware", "/v1/models"):
        res = await async_client.get(path)
        assert res.status_code == 200, f"GET {path} -> {res.status_code}"


@pytest.mark.asyncio
async def test_bootstrap_refuses_requests_without_origin(anon_client: AsyncClient):
    res = await anon_client.get("/api/auth/bootstrap")
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_bootstrap_requires_exact_origin_match(anon_client: AsyncClient):
    res = await anon_client.get("/api/auth/bootstrap", headers={"Origin": "http://127.0.0.1:51730"})
    assert res.status_code == 403
    res = await anon_client.get("/api/auth/bootstrap", headers={"Referer": "http://127.0.0.1:5173/some/page"})
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_bootstrap_disabled_in_desktop_mode(anon_client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "DESKTOP", True)
    res = await anon_client.get("/api/auth/bootstrap", headers={"Origin": TAURI_ORIGIN})
    assert res.status_code == 404


def test_desktop_token_comes_from_shell_and_is_not_written(monkeypatch, tmp_path):
    shell_token = "a" * 64
    monkeypatch.setattr(settings, "DESKTOP", True)
    monkeypatch.setattr(settings, "SESSION_TOKEN", SecretStr(shell_token))
    monkeypatch.setattr(settings, "RUN_DIR", tmp_path)
    assert security.initialize_security_token() == shell_token
    assert security.get_current_token() == shell_token
    assert not (tmp_path / ".mustang_token").exists()


def test_short_shell_token_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "SESSION_TOKEN", SecretStr("short"))
    with pytest.raises(RuntimeError):
        security.initialize_security_token()


def test_non_loopback_host_rejected():
    with pytest.raises(ValueError):
        type(settings)(HOST="0.0.0.0")


@pytest.mark.asyncio
async def test_tauri_origin_passes_cors_preflight(anon_client: AsyncClient):
    res = await anon_client.options(
        "/api/models/load",
        headers={
            "Origin": TAURI_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-mustang-token",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == TAURI_ORIGIN


@pytest.mark.asyncio
async def test_unauthorized_response_is_readable_cross_origin(anon_client: AsyncClient):
    res = await anon_client.get("/api/conversations", headers={"Origin": TAURI_ORIGIN})
    assert res.status_code == 401
    assert res.headers.get("access-control-allow-origin") == TAURI_ORIGIN


@pytest.mark.asyncio
async def test_shutdown_requires_token(anon_client: AsyncClient):
    res = await anon_client.post("/api/system/shutdown")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_shutdown_without_launcher_is_conflict(async_client: AsyncClient, monkeypatch):
    monkeypatch.delattr(app.state, "request_shutdown", raising=False)
    res = await async_client.post("/api/system/shutdown")
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_shutdown_triggers_launcher_hook(async_client: AsyncClient, monkeypatch):
    fired = asyncio.Event()
    monkeypatch.setattr(app.state, "request_shutdown", fired.set, raising=False)
    res = await async_client.post("/api/system/shutdown")
    assert res.status_code == 202
    await asyncio.wait_for(fired.wait(), timeout=2)


def _make_legacy_db(path):
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL, model TEXT NOT NULL, "
        "system_prompt TEXT DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL);"
        "INSERT INTO conversations VALUES ('c1', 'legacy chat', 'm.gguf', '', 1.0, 1.0);"
    )
    con.commit()
    con.close()


@pytest.mark.asyncio
async def test_legacy_database_is_backed_up_then_versioned(monkeypatch, tmp_path):
    db_path = tmp_path / "mustang.db"
    _make_legacy_db(db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)

    await database.init_db()

    backups = list((tmp_path / "backups").glob("mustang-schema0-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as con:
        assert con.execute("SELECT title FROM conversations").fetchone()[0] == "legacy chat"
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("PRAGMA user_version") as cur:
            assert (await cur.fetchone())[0] == database.SCHEMA_VERSION
        async with db.execute("SELECT profile_id FROM conversations WHERE id='c1'") as cur:
            assert (await cur.fetchone())[0] == "default"

    # Already current: no further backups on later starts.
    await database.init_db()
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1


@pytest.mark.asyncio
async def test_newer_schema_is_refused_untouched(monkeypatch, tmp_path):
    db_path = tmp_path / "mustang.db"
    _make_legacy_db(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute(f"PRAGMA user_version = {database.SCHEMA_VERSION + 1}")
    before = db_path.read_bytes()
    monkeypatch.setattr(database, "DB_PATH", db_path)

    with pytest.raises(database.NewerSchemaError):
        await database.init_db()
    assert db_path.read_bytes() == before


@pytest.mark.asyncio
async def test_fresh_database_gets_version_without_backup(monkeypatch, tmp_path):
    db_path = tmp_path / "mustang.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    await database.init_db()
    assert not (tmp_path / "backups").exists()
    with sqlite3.connect(db_path) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION


def test_legacy_import_copies_without_touching_source(tmp_path, capsys):
    source_dir = tmp_path / "legacy run dir"  # space in path, like "S:\Mustang AI"
    source_dir.mkdir()
    source = source_dir / "mustang.db"
    _make_legacy_db(source)
    with sqlite3.connect(source) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("INSERT INTO conversations VALUES ('c2', 'in wal', 'm.gguf', '', 2.0, 2.0)")
    before = source.read_bytes()
    destination = tmp_path / "desktop" / "data" / "mustang.db"

    assert import_database(source, destination) == 0
    assert '"ok": true' in capsys.readouterr().out
    assert source.read_bytes() == before
    with sqlite3.connect(destination) as con:
        assert con.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 2
    assert not destination.with_suffix(".importing").exists()


def test_legacy_import_refuses_existing_destination(tmp_path):
    source, destination = tmp_path / "a.db", tmp_path / "b.db"
    _make_legacy_db(source)
    _make_legacy_db(destination)
    before = destination.read_bytes()
    assert import_database(source, destination) == 2
    assert destination.read_bytes() == before


@pytest.mark.asyncio
async def test_slow_memory_estimate_does_not_block_other_requests(async_client: AsyncClient, monkeypatch):
    """GGUF estimation takes seconds on real models; it must not stall chat or health."""
    import time
    from app.api import models as models_api

    def slow_estimate(**kwargs):
        time.sleep(1.5)
        raise RuntimeError("stub")

    monkeypatch.setattr(models_api, "estimate_model_memory", slow_estimate)
    monkeypatch.setattr(models_api.security, "validate_model_path", lambda name: name)

    async def estimate():
        try:
            await async_client.get("/api/models/x.gguf/estimate")
        except RuntimeError:
            pass

    # Timed from dispatch: a blocking handler freezes the loop, so a timer started after
    # an asyncio.sleep would only start once the block is over.
    started = time.perf_counter()
    task = asyncio.create_task(estimate())
    await asyncio.sleep(0.2)
    res = await async_client.get("/health")
    elapsed = time.perf_counter() - started
    await task
    assert res.status_code == 200
    assert elapsed < 1.0, f"/health answered {elapsed:.2f}s after dispatch, behind a 1.5 s estimate"


def test_launcher_log_redaction():
    secret = "f" * 64
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "token=%s end", (secret,), None)
    assert RedactingFilter([secret]).filter(record)
    assert secret not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()
