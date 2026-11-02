"""
Mustang AI Authentication Router
Provides secure development-mode session bootstrapping with origin checks.
"""

import time
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request, status
from app.config import settings
from app.core import security

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _request_origin(request: Request) -> str:
    """Origin header, or scheme://host[:port] derived from Referer. Empty if neither is present."""
    origin = request.headers.get("origin", "").strip()
    if origin:
        return origin.rstrip("/")
    referer = request.headers.get("referer", "").strip()
    if referer:
        parts = urlsplit(referer)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    return ""


@router.get("/bootstrap")
async def bootstrap_token(request: Request):
    """
    Browser development bootstrap endpoint.
    Hands the session token only to an exactly allow-listed Origin. Requests without an
    Origin (e.g. other local processes) are refused. Disabled in desktop mode.
    """
    if settings.DESKTOP:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bootstrap is disabled in desktop mode; the desktop shell supplies session credentials.",
        )

    origin = _request_origin(request)
    allowed_origins = {o.rstrip("/") for o in settings.ALLOWED_ORIGINS}
    if not origin or origin not in allowed_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Origin '{origin or '<none>'}' is not authorized to bootstrap session credentials.",
        )

    elapsed = time.time() - security._BOOT_TIME
    window = settings.BOOTSTRAP_WINDOW_SECONDS
    if window > 0 and elapsed > window:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Session bootstrapping window has expired. Reload backend to generate fresh token.",
        )

    token = security.get_current_token()
    return {
        "status": "authenticated",
        "token": token,
        "token_header": settings.TOKEN_HEADER_NAME,
        "expires_in_seconds": max(0, int(window - elapsed)) if window > 0 else None,
    }
