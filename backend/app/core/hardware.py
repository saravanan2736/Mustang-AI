"""
Mustang AI Hardware Telemetry Service
Collects authentic VRAM, RAM, and GPU metrics via nvidia-smi / NVML with zero synthetic metrics.
"""

import subprocess
import shutil
import psutil
from pydantic import BaseModel


class GPUMetrics(BaseModel):
    available: bool = False
    name: str = "None"
    driver_version: str = "Unknown"
    vram_total_mb: float = 0.0
    vram_used_mb: float = 0.0
    vram_free_mb: float = 0.0
    gpu_utilization_pct: float = 0.0
    temperature_c: float = 0.0


class SystemMemoryMetrics(BaseModel):
    ram_total_mb: float
    ram_available_mb: float
    ram_used_mb: float
    ram_used_pct: float


class HardwareTelemetry(BaseModel):
    gpu: GPUMetrics
    memory: SystemMemoryMetrics
    cpu_utilization_pct: float
    cpu_logical_cores: int


def get_gpu_telemetry() -> GPUMetrics:
    """Queries authentic NVIDIA GPU metrics using nvidia-smi with strict validation."""
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return GPUMetrics(available=False, name="nvidia-smi not found")

    try:
        # Run nvidia-smi with CSV output for deterministic parsing
        proc = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=name,memory.total,memory.used,memory.free,driver_version,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2.5,
            check=False,
        )

        if proc.returncode != 0:
            return GPUMetrics(available=False, name=f"nvidia-smi error: {proc.stderr.strip()[:100]}")

        output = proc.stdout.strip()
        if not output:
            return GPUMetrics(available=False, name="No GPU data returned")

        # Parse first GPU line
        first_gpu = output.splitlines()[0]
        parts = [p.strip() for p in first_gpu.split(",")]
        if len(parts) < 7:
            return GPUMetrics(available=False, name="Malformed nvidia-smi output")

        name = parts[0]
        total = float(parts[1])
        used = float(parts[2])
        free = float(parts[3])
        driver = parts[4]
        util = float(parts[5]) if parts[5] != "[N/A]" else 0.0
        temp = float(parts[6]) if parts[6] != "[N/A]" else 0.0

        return GPUMetrics(
            available=True,
            name=name,
            driver_version=driver,
            vram_total_mb=total,
            vram_used_mb=used,
            vram_free_mb=free,
            gpu_utilization_pct=util,
            temperature_c=temp,
        )
    except Exception as err:
        return GPUMetrics(available=False, name=f"Telemetry exception: {str(err)}")


def get_system_telemetry() -> HardwareTelemetry:
    """Combines authentic GPU and system RAM/CPU metrics."""
    gpu = get_gpu_telemetry()
    vmem = psutil.virtual_memory()

    mem_metrics = SystemMemoryMetrics(
        ram_total_mb=round(vmem.total / (1024 * 1024), 1),
        ram_available_mb=round(vmem.available / (1024 * 1024), 1),
        ram_used_mb=round(vmem.used / (1024 * 1024), 1),
        ram_used_pct=round(vmem.percent, 1),
    )

    cpu_pct = psutil.cpu_percent(interval=None)
    cores = psutil.cpu_count(logical=True) or 1

    return HardwareTelemetry(
        gpu=gpu,
        memory=mem_metrics,
        cpu_utilization_pct=round(cpu_pct, 1),
        cpu_logical_cores=cores,
    )

