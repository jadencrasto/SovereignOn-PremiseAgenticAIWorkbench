"""
backend/models/hardware.py
---------------------------
Hardware telemetry & resource-aware adaptive model placement engine.

Provides live measurement of:
- GPU VRAM (total, used, free, utilization %, temperature) via NVML or nvidia-smi
- System RAM (total, used, free, percent) via psutil
- CPU utilization & core counts
- Adaptive model allocation & eviction advisories for 4 GB RTX 3050 targets.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psutil

logger = logging.getLogger(__name__)

# Approximate VRAM footprint in MB for common local models (Q4_K_M + KV cache @ 4k ctx)
MODEL_VRAM_ESTIMATES_MB = {
    "qwen2.5:7b": 4600,
    "llava:7b": 5100,
    "qwen2.5:3b": 2100,
    "qwen2.5:1.5b": 1200,
    "qwen2.5:0.5b": 600,
    "nomic-embed-text": 300,
}


@dataclass
class GPUTelemetry:
    available: bool = False
    name: str = "N/A"
    vram_total_mb: float = 0.0
    vram_used_mb: float = 0.0
    vram_free_mb: float = 0.0
    gpu_utilization_pct: float = 0.0
    temperature_c: Optional[float] = None
    telemetry_source: str = "none"  # "nvml" | "nvidia-smi" | "mock" | "cpu-only"


@dataclass
class SystemTelemetry:
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cpu_percent: float = 0.0
    cpu_cores_physical: int = 1
    cpu_cores_logical: int = 1
    ram_total_mb: float = 0.0
    ram_used_mb: float = 0.0
    ram_free_mb: float = 0.0
    ram_percent: float = 0.0
    gpu: GPUTelemetry = field(default_factory=GPUTelemetry)


@dataclass
class ModelAllocationDecision:
    model: str
    target_device: str  # "gpu" | "cpu" | "offload_split"
    vram_required_mb: float
    vram_free_before_mb: float
    evictions_required: List[str]
    allowed: bool
    reason: str


class HardwareManager:
    """
    Monitors host hardware and calculates safe model allocation plans
    so that LLM and VLM are never simultaneously resident on 4 GB GPUs.
    """

    def __init__(self, vram_headroom_mb: float = 400.0) -> None:
        self._vram_headroom_mb = vram_headroom_mb
        self._has_pynvml = False
        self._nvml_handle = None
        self._init_nvml()

    def _init_nvml(self) -> None:
        """Attempt to load pynvml if installed."""
        try:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            device_count = pynvml.nvmlDeviceGetCount()
            if device_count > 0:
                self._nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                self._has_pynvml = True
                logger.info("HardwareManager: NVML initialized for GPU 0")
        except Exception as exc:
            self._has_pynvml = False
            logger.debug("NVML not available (%s); falling back to nvidia-smi/psutil", exc)

    def get_gpu_telemetry(self) -> GPUTelemetry:
        """Poll GPU telemetry via NVML, nvidia-smi, or fallback."""
        if self._has_pynvml and self._nvml_handle:
            try:
                import pynvml  # type: ignore
                name = pynvml.nvmlDeviceGetName(self._nvml_handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8")
                mem = pynvml.nvmlDeviceGetMemoryInfo(self._nvml_handle)
                util = pynvml.nvmlDeviceGetUtilizationRates(self._nvml_handle)
                temp = pynvml.nvmlDeviceGetTemperature(self._nvml_handle, pynvml.NVML_TEMPERATURE_GPU)
                return GPUTelemetry(
                    available=True,
                    name=str(name),
                    vram_total_mb=round(mem.total / (1024 * 1024), 1),
                    vram_used_mb=round(mem.used / (1024 * 1024), 1),
                    vram_free_mb=round(mem.free / (1024 * 1024), 1),
                    gpu_utilization_pct=float(util.gpu),
                    temperature_c=float(temp),
                    telemetry_source="nvml",
                )
            except Exception as exc:
                logger.debug("NVML polling failed: %s; falling back to nvidia-smi", exc)

        # Fallback: nvidia-smi CLI
        if shutil.which("nvidia-smi"):
            try:
                cmd = [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ]
                out = subprocess.check_output(cmd, encoding="utf-8", timeout=1.5).strip()
                if out:
                    parts = [p.strip() for p in out.split(",")]
                    if len(parts) >= 6:
                        return GPUTelemetry(
                            available=True,
                            name=parts[0],
                            vram_total_mb=float(parts[1]),
                            vram_used_mb=float(parts[2]),
                            vram_free_mb=float(parts[3]),
                            gpu_utilization_pct=float(parts[4]),
                            temperature_c=float(parts[5]),
                            telemetry_source="nvidia-smi",
                        )
            except Exception as exc:
                logger.debug("nvidia-smi query failed: %s", exc)

        # Non-GPU or Simulated Environment:
        # If simulated env var is set (for testing), return target hardware proxy
        if os.environ.get("SIMULATE_RTX3050", "").lower() in ("1", "true"):
            return GPUTelemetry(
                available=True,
                name="NVIDIA GeForce RTX 3050 Laptop GPU (Simulated)",
                vram_total_mb=4096.0,
                vram_used_mb=1280.0,
                vram_free_mb=2816.0,
                gpu_utilization_pct=15.0,
                temperature_c=58.0,
                telemetry_source="mock",
            )

        return GPUTelemetry(
            available=False,
            name="CPU Host (No Dedicated GPU Detected)",
            vram_total_mb=0.0,
            vram_used_mb=0.0,
            vram_free_mb=0.0,
            gpu_utilization_pct=0.0,
            temperature_c=None,
            telemetry_source="cpu-only",
        )

    def get_system_telemetry(self) -> SystemTelemetry:
        """Capture snapshot of CPU, RAM, and GPU telemetry."""
        mem = psutil.virtual_memory()
        gpu = self.get_gpu_telemetry()
        return SystemTelemetry(
            cpu_percent=round(psutil.cpu_percent(interval=None), 1),
            cpu_cores_physical=psutil.cpu_count(logical=False) or 1,
            cpu_cores_logical=psutil.cpu_count(logical=True) or 1,
            ram_total_mb=round(mem.total / (1024 * 1024), 1),
            ram_used_mb=round(mem.used / (1024 * 1024), 1),
            ram_free_mb=round(mem.available / (1024 * 1024), 1),
            ram_percent=round(mem.percent, 1),
            gpu=gpu,
        )

    def evaluate_model_allocation(
        self,
        target_model: str,
        active_loaded_models: List[str],
        gpu_override: Optional[GPUTelemetry] = None,
    ) -> ModelAllocationDecision:
        """
        Evaluate if target_model can be safely scheduled, whether other models
        must be evicted, or whether CPU fallback is required.
        """
        base_name = target_model.split("/")[-1].lower()
        req_mb = MODEL_VRAM_ESTIMATES_MB.get(base_name, 4500)
        gpu = gpu_override or self.get_gpu_telemetry()


        # If no GPU is available, route to CPU
        if not gpu.available:
            return ModelAllocationDecision(
                model=target_model,
                target_device="cpu",
                vram_required_mb=req_mb,
                vram_free_before_mb=0.0,
                evictions_required=[],
                allowed=True,
                reason="No GPU detected. Dispatched to CPU execution.",
            )

        # If model is already active, no eviction needed
        clean_active = [m.split("/")[-1].lower() for m in active_loaded_models]
        if base_name in clean_active:
            return ModelAllocationDecision(
                model=target_model,
                target_device="gpu",
                vram_required_mb=req_mb,
                vram_free_before_mb=gpu.vram_free_mb,
                evictions_required=[],
                allowed=True,
                reason=f"Model '{base_name}' is already resident in GPU VRAM.",
            )

        # Check for models that must be evicted to prevent VRAM overcommit
        evictions: List[str] = []
        for loaded in active_loaded_models:
            loaded_clean = loaded.split("/")[-1].lower()
            if loaded_clean != base_name:
                evictions.append(loaded)

        # On 4 GB GPUs, if target model requires > 4000 MB, we must evict all other models
        effective_free = gpu.vram_free_mb
        for ev in evictions:
            ev_clean = ev.split("/")[-1].lower()
            effective_free += MODEL_VRAM_ESTIMATES_MB.get(ev_clean, 3000)

        if effective_free < (req_mb + self._vram_headroom_mb) and gpu.vram_total_mb < 6000:
            # On a 4 GB card, a 7B model requires almost the entire card
            reason = (
                f"4GB VRAM constraint on {gpu.name}: Evicting {evictions} to free "
                f"~{effective_free:.0f}MB for '{base_name}' ({req_mb}MB)."
            )
        elif evictions:
            reason = f"Evicting inactive models {evictions} before loading '{base_name}'."
        else:
            reason = f"Sufficient VRAM available ({gpu.vram_free_mb:.0f}MB free). Direct GPU loading."

        return ModelAllocationDecision(
            model=target_model,
            target_device="gpu",
            vram_required_mb=req_mb,
            vram_free_before_mb=gpu.vram_free_mb,
            evictions_required=evictions,
            allowed=True,
            reason=reason,
        )
