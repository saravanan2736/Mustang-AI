"""
Mustang AI Authentication Router
Provides secure development-mode session bootstrapping with origin checks.
"""

import time
from fastapi import APIRouter, HTTPException, Request, status
from app.config import settings
from app.core import security

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/bootstrap")
async def bootstrap_token(request: Request):
    """
    Secure development bootstrap endpoint.
    Only allows token retrieval from whitelisted origins within the boot window.
    """
    origin = request.headers.get("origin") or request.headers.get("referer", "")
    clean_origin = origin.rstrip("/")

    # Check origin whitelist
    allowed = False
    for allowed_origin in settings.ALLOWED_ORIGINS:
        if clean_origin.startswith(allowed_origin.rstrip("/")):
            allowed = True
            break

    if not allowed and origin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Origin '{origin}' is not authorized to bootstrap session credentials.",
        )

    # Check time window (first 60s of boot)
    elapsed = time.time() - security._BOOT_TIME
    if elapsed > settings.BOOTSTRAP_WINDOW_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Session bootstrapping window has expired. Reload backend to generate fresh token.",
        )

    token = security.get_current_token()
    return {
        "status": "authenticated",
        "token": token,
        "token_header": settings.TOKEN_HEADER_NAME,
        "expires_in_seconds": max(0, int(settings.BOOTSTRAP_WINDOW_SECONDS - elapsed)),
    }

