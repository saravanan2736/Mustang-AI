"""
Mustang AI System & Hardware Router
Provides authentic hardware telemetry and health endpoints.
"""

from fastapi import APIRouter
from app.core.hardware import HardwareTelemetry, get_system_telemetry

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "app": "Mustang AI",
        "version": "0.1.0",
    }


@router.get("/api/system/hardware", response_model=HardwareTelemetry)
async def get_hardware_metrics():
    """Returns authentic NVIDIA GPU VRAM and DDR5 host RAM telemetry."""
    return get_system_telemetry()

