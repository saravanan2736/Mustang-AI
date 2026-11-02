"""
Mustang AI Milestone 3 Stage 6 - Security Test Suite
Exhaustive security validation covering:
1. Anti-SSRF & DNS Rebinding (Socket Pinning, Redirect Interception, Disallowed CIDRs)
2. Path Traversal & Windows Filesystem Quirks (Reserved Device Names, Alternate Data Streams, Slashes, Null Bytes)
3. Authentication, Origin Verification & Loopback Policy Enforcement
"""

import hashlib
import httpcore
import io
import socket
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import HTTPException

from app.config import settings
from app.core import security
from app.core.download_manager import (
    DISALLOWED_CIDRS,
    PinningSafeNetworkBackend,
    can_transition,
    is_ip_allowed,
    is_safe_public_destination,
)
from app.core.hf_client import validate_hf_filename, validate_hf_repo_id
from app.core.security import validate_model_path
from app.main import app


# =====================================================================
# 1. ANTI-SSRF & IP PINNING AUDIT
# =====================================================================

def test_anti_ssrf_disallowed_cidrs_coverage():
    """Verifies that all standard private, loopback, link-local and unroutable ranges are blocked."""
    prohibited_ips = [
        # Loopback IPv4 & IPv6
        "127.0.0.1",
        "127.0.0.254",
        "::1",
        # RFC 1918 Private IPv4
        "10.0.0.1",
        "10.255.255.254",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.0.1",
        "192.168.1.100",
        # Cloud Metadata & Link-Local (RFC 3927)
        "169.254.169.254",
        "169.254.1.1",
        "fe80::1",
        # Carrier-Grade NAT (RFC 6598)
        "100.64.0.1",
        "100.127.255.254",
        # Benchmark / TEST-NET
        "198.18.0.1",
        "192.0.2.1",
        "198.51.100.1",
        "203.0.113.1",
        # Multicast & Broadcast
        "224.0.0.1",
        "255.255.255.255",
        "ff02::1",
    ]

    for ip in prohibited_ips:
        assert not is_ip_allowed(ip), f"Security violation: IP '{ip}' must be prohibited by SSRF filter!"

    allowed_public_ips = [
        "8.8.8.8",
        "1.1.1.1",
        "140.82.121.4",  # GitHub
        "2600:1f18:43b6:ec00::",  # Public IPv6
    ]

    for ip in allowed_public_ips:
        assert is_ip_allowed(ip), f"False positive: Public IP '{ip}' was rejected by SSRF filter!"


def test_public_destination_url_validation():
    """Verifies that only public HTTPS Hugging Face URLs are considered safe destinations."""
    # Valid
    assert is_safe_public_destination("https://huggingface.co/Qwen/Qwen2.5-0.5B-GGUF/resolve/main/model.gguf")
    assert is_safe_public_destination("https://hf.co/meta-llama/Llama-3-8B/resolve/main/model.gguf")

    # Invalid protocols
    assert not is_safe_public_destination("http://huggingface.co/model.gguf")  # Plain HTTP
    assert not is_safe_public_destination("file:///etc/passwd")
    assert not is_safe_public_destination("ftp://huggingface.co/model.gguf")
    assert not is_safe_public_destination("gopher://127.0.0.1:80/")

    # Private/internal hosts masquerading as domains
    assert not is_safe_public_destination("https://localhost/model.gguf")
    assert not is_safe_public_destination("https://127.0.0.1/model.gguf")
    assert not is_safe_public_destination("https://169.254.169.254/latest/meta-data/")
    assert not is_safe_public_destination("https://192.168.1.1/admin")


@pytest.mark.asyncio
async def test_pinning_backend_rejects_loopback_connection():
    """Verifies that PinningSafeNetworkBackend blocks connection attempts to loopback or private addresses."""
    backend = PinningSafeNetworkBackend()
    with pytest.raises(httpcore.ConnectError) as exc:
        await backend.connect_tcp(
            host="127.0.0.1",
            port=8080,
            timeout=1.0,
        )
    assert "Blocked SSRF" in str(exc.value)


# =====================================================================
# 2. PATH TRAVERSAL & WINDOWS-SPECIFIC SECURITY AUDIT
# =====================================================================

def test_windows_reserved_device_names_prevention():
    """
    On Windows, creating or accessing files named CON, PRN, AUX, NUL, COM1-9, LPT1-9
    causes severe kernel hangs or device mapping escapes.
    Mustang AI must strictly reject them.
    """
    reserved_names = [
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4",
        "LPT1", "LPT2", "LPT3", "LPT4",
        "con.gguf", "aux.gguf", "nul.gguf", "prn.gguf",
    ]

    for name in reserved_names:
        # Check via filename validator
        with pytest.raises(HTTPException) as exc:
            validate_hf_filename(name)
        assert exc.value.status_code == 400, f"Reserved device name '{name}' was not rejected by validate_hf_filename!"

        # Check via model path validator
        with pytest.raises(HTTPException) as exc2:
            validate_model_path(name)
        assert exc2.value.status_code in (400, 404), f"Reserved device name '{name}' was not rejected by validate_model_path!"


def test_windows_alternate_data_streams_and_drive_relative_paths():
    """
    Verifies that NTFS Alternate Data Streams (":$DATA", ":stream") and drive-relative paths
    are blocked from bypassing model path boundaries.
    """
    malicious_paths = [
        "model.gguf::$DATA",
        "model.gguf:hidden_stream",
        "C:model.gguf",
        "D:../Windows/System32/calc.exe",
        "models/../../secret.txt",
        "....//....//Windows/win.ini",
        "model.gguf\x00.exe",
        "model.gguf%00.exe",
    ]

    for path in malicious_paths:
        with pytest.raises(HTTPException):
            validate_model_path(path)


def test_hf_repo_id_and_filename_sanitization():
    """Verifies strict syntax rules for Hugging Face repo IDs and filenames."""
    bad_repo_ids = [
        "../etc/passwd",
        "org/../../evil",
        "org\\evil",
        "/absolute/root",
        "org//double_slash",
        "-leading_hyphen/repo",
        ".hidden_dot/repo",
        "spaces in repo name/repo",
        "repo\0nullbyte",
    ]

    for bad_id in bad_repo_ids:
        with pytest.raises(HTTPException) as exc:
            validate_hf_repo_id(bad_id)
        assert exc.value.status_code == 400


# =====================================================================
# 3. AUTHENTICATION & ORIGIN POLICY ENFORCEMENT
# =====================================================================

@pytest.mark.asyncio
async def test_bootstrap_origin_enforcement(async_client: AsyncClient):
    """
    Verifies that /api/auth/bootstrap rejects untrusted web origins.
    Only configured allowed origins (e.g. 127.0.0.1:5173, localhost:5173) may obtain tokens.
    """
    # Untrusted external web origin
    bad_origin_res = await async_client.get(
        "/api/auth/bootstrap",
        headers={"Origin": "https://malicious-website.com"},
    )
    assert bad_origin_res.status_code == 403

    # Phishing lookalike origin
    fake_origin_res = await async_client.get(
        "/api/auth/bootstrap",
        headers={"Origin": "http://127.0.0.1.attacker.com"},
    )
    assert fake_origin_res.status_code == 403

    # Valid origin
    valid_origin_res = await async_client.get(
        "/api/auth/bootstrap",
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    assert valid_origin_res.status_code == 200
    assert "token" in valid_origin_res.json()


@pytest.mark.asyncio
async def test_management_endpoints_require_token(anon_client: AsyncClient):
    """
    Exhaustively tests that every state-mutating endpoint requires a valid X-Mustang-Token.
    """
    endpoints = [
        ("POST", "/api/models/load", {"model_name": "test.gguf"}),
        ("POST", "/api/models/unload", {}),
        ("DELETE", "/api/models/test-id", None),
        ("POST", "/api/downloads", {"repo_id": "org/repo", "filename": "model.gguf"}),
        ("POST", "/api/downloads/test-dl/pause", None),
        ("POST", "/api/downloads/test-dl/resume", None),
        ("POST", "/api/downloads/test-dl/cancel", None),
    ]

    for method, path, payload in endpoints:
        # Request without header
        if method == "POST":
            r_no_auth = await anon_client.post(path, json=payload or {})
        elif method == "DELETE":
            r_no_auth = await anon_client.delete(path)
        assert r_no_auth.status_code == 401, f"{method} {path} succeeded without token!"

        # Request with bogus token
        bogus_headers = {settings.TOKEN_HEADER_NAME: "deadbeefcafebabe000000000000000000000000000000000000000000000000"}
        if method == "POST":
            r_bogus = await anon_client.post(path, json=payload or {}, headers=bogus_headers)
        elif method == "DELETE":
            r_bogus = await anon_client.delete(path, headers=bogus_headers)
        assert r_bogus.status_code == 401, f"{method} {path} accepted invalid token!"

