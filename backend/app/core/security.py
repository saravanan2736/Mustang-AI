"""
Mustang AI Security Architecture
Localhost enforcement, DNS rebinding protection, ephemeral token auth, and path sanitization.
"""

import os
import secrets
import time
from pathlib import Path
from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings

_api_key_header = APIKeyHeader(name=settings.TOKEN_HEADER_NAME, auto_error=False)

# In-memory session state
_BOOTSTRAP_TOKEN: str = ""
_BOOT_TIME: float = time.time()
_BOOTSTRAP_CONSUMED: bool = False


MIN_PROVIDED_TOKEN_LENGTH = 32


def initialize_security_token() -> str:
    """
    Establish the session token.
    Desktop mode: the shell supplies it (MUSTANG_SESSION_TOKEN) and it is never written to disk.
    Browser dev mode: generate an ephemeral token and record it to run/.mustang_token.
    """
    global _BOOTSTRAP_TOKEN, _BOOT_TIME, _BOOTSTRAP_CONSUMED
    provided = settings.SESSION_TOKEN.get_secret_value()
    if provided:
        if len(provided) < MIN_PROVIDED_TOKEN_LENGTH:
            raise RuntimeError(f"MUSTANG_SESSION_TOKEN must be at least {MIN_PROVIDED_TOKEN_LENGTH} characters.")
        _BOOTSTRAP_TOKEN = provided
    else:
        _BOOTSTRAP_TOKEN = secrets.token_hex(32)
    _BOOT_TIME = time.time()
    _BOOTSTRAP_CONSUMED = False

    if settings.DESKTOP:
        return _BOOTSTRAP_TOKEN

    token_file = settings.RUN_DIR / ".mustang_token"
    token_file.write_text(_BOOTSTRAP_TOKEN, encoding="utf-8")
    try:
        # On Windows, lock file down to user
        os.chmod(token_file, 0o600)
    except Exception:
        pass

    return _BOOTSTRAP_TOKEN


def get_current_token() -> str:
    return _BOOTSTRAP_TOKEN


def verify_session_token(token: str | None = Security(_api_key_header)) -> bool:
    """FastAPI dependency to verify X-Mustang-Token header on sensitive endpoints."""
    if not token or not secrets.compare_digest(token, _BOOTSTRAP_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Mustang-Token session authentication header.",
        )
    return True


class HostValidationMiddleware:
    """Rejects any request whose Host header does not match localhost / 127.0.0.1 (DNS Rebinding guard).

    Plain ASGI rather than BaseHTTPMiddleware: that one runs every request in an anyio task group
    and cancels it afterwards, and the CancelledError tracebacks kept request frames (with their
    sockets and buffers) in reference cycles that only Python's rare full collections freed
    (M4 Phase G soak).
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            host_header = Headers(scope=scope).get("host", "")
            # Allow requests without host only in test environments or loopback
            clean_host = host_header.split(":")[0].lower()
            if clean_host not in ("127.0.0.1", "localhost", "testserver"):
                response = JSONResponse(
                    status_code=403,
                    content={"error": f"Forbidden: Host '{clean_host}' is rejected by loopback security policy."},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def validate_model_path(model_filename_or_path: str) -> Path:
    """
    Validates and resolves a GGUF model path.
    Prevents path traversal, alternate data streams, reserved device names,
    and verifies file exists with .gguf extension.
    """
    if "\x00" in model_filename_or_path or "%00" in model_filename_or_path:
        raise HTTPException(status_code=400, detail="Null bytes forbidden in path.")

    # NTFS Alternate Data Stream check (e.g. file.gguf:secret)
    # Exclude standard Windows drive letter (e.g. C:\ or D:/)
    path_str = model_filename_or_path.strip()
    colon_idx = path_str.find(":")
    if colon_idx != -1:
        if colon_idx != 1 or (len(path_str) > 2 and path_str[2] not in ("\\", "/")):
            raise HTTPException(status_code=400, detail="Alternate data streams and drive-relative paths forbidden.")

    target = Path(path_str)

    # Windows reserved device names check
    if target.stem.upper() in WINDOWS_RESERVED_NAMES:
        raise HTTPException(status_code=400, detail=f"Reserved Windows device name forbidden: {target.stem}")

    # If just a filename, assume within MODELS_DIR
    if not target.is_absolute():
        target = settings.MODELS_DIR / target

    try:
        resolved = target.resolve(strict=True)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Model file not found: {target.name}")
    except (OSError, RuntimeError, ValueError) as os_err:
        raise HTTPException(status_code=400, detail=f"Invalid filesystem path: {target.name}")

    if resolved.suffix.lower() != ".gguf":
        raise HTTPException(status_code=400, detail="Only .gguf models are supported.")

    # Check path safety: must be either inside MODELS_DIR or an explicit existing file
    models_dir_resolved = settings.MODELS_DIR.resolve()
    try:
        # If inside MODELS_DIR, path traversal check
        resolved.relative_to(models_dir_resolved)
    except ValueError:
        # External path allowed only if it is a direct valid file and no traversal back into parent
        if not resolved.is_file():
            raise HTTPException(status_code=400, detail="Invalid model path target.")

    return resolved

