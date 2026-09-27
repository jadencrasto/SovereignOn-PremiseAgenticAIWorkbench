"""
backend/tools/hardware_status.py
--------------------------------
Hardware telemetry agent tool.

Provides real-time host telemetry (CPU, RAM, GPU, VRAM, temperature, and placement status)
using HardwareManager / ModelRouter.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class HardwareStatusInput(BaseModel):
    """Input schema for hardware_status tool."""
    include_model_placement: bool = Field(
        default=True,
        description="Whether to include recent model placement / VRAM allocation advisories.",
    )


def create_hardware_status(model_router: Any = None) -> callable:
    """Create the hardware_status execution function."""

    async def execute_hardware_status(args: HardwareStatusInput, **kwargs: Any) -> Dict[str, Any]:
        """Query host hardware telemetry without fabricating values."""
        if not model_router:
            from backend.models.hardware import HardwareManager
            hw_mgr = HardwareManager()
            gpu = hw_mgr.get_gpu_telemetry()
            sys_tel = hw_mgr.get_system_telemetry()
            ram_gb = round(sys_tel.ram_used_mb / 1024, 1)
            ram_tot_gb = round(sys_tel.ram_total_mb / 1024, 1)
            gpu_vram_gb = round(gpu.vram_used_mb / 1024, 1)
            gpu_vram_tot_gb = round(gpu.vram_total_mb / 1024, 1)
            gpu_part = f"GPU: {gpu.name} ({gpu.gpu_utilization_pct}% load, {gpu_vram_gb}/{gpu_vram_tot_gb} GB VRAM)" if gpu.available else "GPU: Not available"
            summary = f"CPU: {sys_tel.cpu_percent}% | RAM: {ram_gb}/{ram_tot_gb} GB ({sys_tel.ram_percent}%) | {gpu_part}"

            return {
                "status": "active",
                "timestamp": sys_tel.timestamp,
                "summary": summary,
                "cpu_percent": sys_tel.cpu_percent,
                "cpu_cores_physical": sys_tel.cpu_cores_physical,
                "cpu_cores_logical": sys_tel.cpu_cores_logical,
                "ram_total_mb": sys_tel.ram_total_mb,
                "ram_used_mb": sys_tel.ram_used_mb,
                "ram_free_mb": sys_tel.ram_free_mb,
                "ram_percent": sys_tel.ram_percent,
                "gpu_available": gpu.available,
                "gpu_name": gpu.name,
                "gpu_vram_total_mb": gpu.vram_total_mb,
                "gpu_vram_used_mb": gpu.vram_used_mb,
                "gpu_vram_free_mb": gpu.vram_free_mb,
                "gpu_utilization_pct": gpu.gpu_utilization_pct,
                "gpu_temperature_c": gpu.temperature_c,
                "telemetry_source": gpu.telemetry_source,
                "active_loaded_models": [],
            }

        telemetry = model_router.get_hardware_telemetry()
        last_dec = model_router.get_last_allocation_decision() if args.include_model_placement else None

        active_models = []
        if hasattr(model_router, "get_loaded_models"):
            try:
                active_models = model_router.get_loaded_models()
            except Exception:
                pass

        result = {
            "status": "active",
            "timestamp": telemetry.timestamp,
            "cpu_percent": telemetry.cpu_percent,
            "cpu_cores_physical": telemetry.cpu_cores_physical,
            "cpu_cores_logical": telemetry.cpu_cores_logical,
            "ram_total_mb": telemetry.ram_total_mb,
            "ram_used_mb": telemetry.ram_used_mb,
            "ram_free_mb": telemetry.ram_free_mb,
            "ram_percent": telemetry.ram_percent,
            "gpu_available": telemetry.gpu.available,
            "gpu_name": telemetry.gpu.name,
            "gpu_vram_total_mb": telemetry.gpu.vram_total_mb,
            "gpu_vram_used_mb": telemetry.gpu.vram_used_mb,
            "gpu_vram_free_mb": telemetry.gpu.vram_free_mb,
            "gpu_utilization_pct": telemetry.gpu.gpu_utilization_pct,
            "gpu_temperature_c": telemetry.gpu.temperature_c,
            "telemetry_source": telemetry.gpu.telemetry_source,
            "active_loaded_models": active_models,
        }

        ram_gb = round(result["ram_used_mb"] / 1024, 1)
        ram_tot_gb = round(result["ram_total_mb"] / 1024, 1)
        gpu_vram_gb = round(result["gpu_vram_used_mb"] / 1024, 1)
        gpu_vram_tot_gb = round(result["gpu_vram_total_mb"] / 1024, 1)
        gpu_part = f"GPU: {result['gpu_name']} ({result['gpu_utilization_pct']}% load, {gpu_vram_gb}/{gpu_vram_tot_gb} GB VRAM)" if result["gpu_available"] else "GPU: Not available"
        result["summary"] = f"CPU: {result['cpu_percent']}% | RAM: {ram_gb}/{ram_tot_gb} GB ({result['ram_percent']}%) | {gpu_part}"

        if last_dec:
            result["last_allocation_decision"] = {
                "model": last_dec.model,
                "target_device": last_dec.target_device,
                "vram_required_mb": last_dec.vram_required_mb,
                "vram_free_before_mb": last_dec.vram_free_before_mb,
                "evictions_required": last_dec.evictions_required,
                "allowed": last_dec.allowed,
                "reason": last_dec.reason,
            }

        return result

    return execute_hardware_status
