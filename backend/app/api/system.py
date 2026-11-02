"""
Mustang AI System & Hardware Router
Provides authentic hardware telemetry, health, and desktop shutdown endpoints.
"""

import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from app.config import settings
from app.core import security
from app.core.hardware import HardwareTelemetry, get_system_telemetry

router = APIRouter(tags=["system"])

# Delay before the server is told to exit, so the 202 response reaches the caller first.
SHUTDOWN_DELAY_SECONDS = 0.2


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "app": "Mustang AI",
        "version": settings.VERSION,
    }


@router.get(
    "/api/system/hardware",
    response_model=HardwareTelemetry,
    dependencies=[Depends(security.verify_session_token)],
)
async def get_hardware_metrics():
    """Returns authentic NVIDIA GPU VRAM and DDR5 host RAM telemetry."""
    # nvidia-smi is a blocking subprocess call: run it off the event loop.
    return await asyncio.to_thread(get_system_telemetry)


@router.post(
    "/api/system/shutdown",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(security.verify_session_token)],
)
async def request_shutdown(request: Request):
    """
    Graceful shutdown requested by the desktop shell. Any in-flight generation is cancelled
    first, so its partial reply is saved and its stream ends instead of uvicorn waiting for it
    to finish (which could exceed the shell's grace period on long replies). The lifespan
    shutdown phase then stops downloads (persisted as PAUSED) and the inference engine.
    Only available when the process was started by mustang_launcher.
    """
    trigger = getattr(request.app.state, "request_shutdown", None)
    if trigger is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Shutdown is only available when the backend is started by the desktop launcher.",
        )
    engine = getattr(request.app.state, "engine", None)
    if engine is not None:
        await engine.cancel_generation()
    asyncio.get_running_loop().call_later(SHUTDOWN_DELAY_SECONDS, trigger)
    return {"status": "shutting_down"}
