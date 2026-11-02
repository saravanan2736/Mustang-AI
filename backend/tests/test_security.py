"""
Security Unit & Integration Tests
Validates loopback enforcement, DNS rebinding guards, token authentication, and path traversal protection.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.core.security import validate_model_path
from app.main import app
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_session_token_protection(anon_client: AsyncClient):
    """Verifies that management endpoints strictly reject unauthorized requests."""
    # Attempt load without token
    res = await anon_client.post("/api/models/load", json={"model_name": "test.gguf"})
    assert res.status_code == 401

    # Attempt unload without token
    res = await anon_client.post("/api/models/unload")
    assert res.status_code == 401

    # Attempt stop without token
    res = await anon_client.post("/api/chat/stop")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_authorized_requests(async_client: AsyncClient, auth_headers):
    """Verifies that requests with the valid X-Mustang-Token header succeed."""
    res = await async_client.post("/api/models/unload", headers=auth_headers)
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_dns_rebinding_host_header_guard():
    """Verifies that Host headers from external domains are rejected with 403."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:18000") as client:
        res = await client.get("/health", headers={"Host": "evil-attacker.com"})
        assert res.status_code == 403
        assert "rejected by loopback security policy" in res.json()["error"]


def test_path_traversal_prevention():
    """Verifies that directory traversal and non-gguf paths are strictly blocked."""
    # Traversal attempt
    with pytest.raises(HTTPException) as exc:
        validate_model_path("../../Windows/System32/cmd.exe")
    assert exc.value.status_code in (400, 404)

    # Non-GGUF file attempt
    with pytest.raises(HTTPException) as exc:
        validate_model_path("malicious.bat")
    assert exc.value.status_code in (400, 404)

    # Null byte injection
    with pytest.raises(HTTPException) as exc:
        validate_model_path("model.gguf\x00.exe")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_bootstrap_token_endpoint(async_client: AsyncClient):
    """Verifies that allowed origins can retrieve bootstrap session token."""
    res = await async_client.get(
        "/api/auth/bootstrap",
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "token" in data
    assert data["status"] == "authenticated"
    assert len(data["token"]) == 64  # 32 bytes hex

