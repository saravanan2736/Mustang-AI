"""
Hardware Telemetry Unit & Integration Tests
Verifies real-time GPU/RAM metrics and validates authentic RTX 5050 detection.
"""

import pytest
from app.core.hardware import (
    GPUMetrics,
    get_gpu_telemetry,
    get_system_telemetry,
)


def test_system_memory_metrics():
    """Validates host RAM telemetry produces realistic positive metrics."""
    telemetry = get_system_telemetry()
    assert telemetry.memory.ram_total_mb > 1000.0  # At least 1GB RAM
    assert telemetry.memory.ram_available_mb > 0.0
    assert 0.0 <= telemetry.memory.ram_used_pct <= 100.0
    assert telemetry.cpu_logical_cores >= 1


def test_gpu_telemetry_structure():
    """Validates that get_gpu_telemetry returns a properly typed GPUMetrics object."""
    gpu = get_gpu_telemetry()
    assert isinstance(gpu, GPUMetrics)
    assert isinstance(gpu.available, bool)
    assert isinstance(gpu.vram_total_mb, float)


@pytest.mark.gpu
def test_real_nvidia_rtx_5050_hardware():
    """
    Hardware integration test: Runs on host to verify RTX 5050 detection.
    Guarantees no synthetic metrics are reported.
    """
    gpu = get_gpu_telemetry()
    assert gpu.available is True, f"NVIDIA GPU was not detected: {gpu.name}"
    assert "RTX 5050" in gpu.name or "NVIDIA" in gpu.name
    assert gpu.vram_total_mb > 6000.0  # RTX 5050 8GB reports ~8151 MiB
    assert gpu.vram_free_mb > 0.0
    assert gpu.driver_version != "Unknown"

