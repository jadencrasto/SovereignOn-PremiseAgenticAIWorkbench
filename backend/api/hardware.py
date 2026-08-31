"""
backend/api/hardware.py
------------------------
Hardware telemetry, resource monitoring, and model placement status API.

Endpoints:
  GET /api/hardware/status — live VRAM, RAM, CPU, loaded models, allocation decisions
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/hardware", tags=["hardware"])


class HardwareStatusResponse(BaseModel):
    status: str
    timestamp: str
    cpu_percent: float
    cpu_cores_physical: int
    cpu_cores_logical: int
    ram_total_mb: float
    ram_used_mb: float
    ram_free_mb: float
    ram_percent: float
    gpu_available: bool
    gpu_name: str
    gpu_vram_total_mb: float
    gpu_vram_used_mb: float
    gpu_vram_free_mb: float
    gpu_utilization_pct: float
    gpu_temperature_c: Optional[float] = None
    telemetry_source: str
    active_loaded_models: list
    last_allocation_decision: Optional[Dict[str, Any]] = None


@router.get("/status", response_model=HardwareStatusResponse, summary="Get live hardware & model manager status")
async def get_hardware_status(request: Request):
    """
    Returns live hardware telemetry and the adaptive model manager's
    current VRAM state and recent eviction decisions.
    """
    model_router = getattr(request.app.state, "model_router", None)
    if not model_router:
        return HardwareStatusResponse(
            status="uninitialized",
            timestamp="",
            cpu_percent=0.0,
            cpu_cores_physical=1,
            cpu_cores_logical=1,
            ram_total_mb=0.0,
            ram_used_mb=0.0,
            ram_free_mb=0.0,
            ram_percent=0.0,
            gpu_available=False,
            gpu_name="N/A",
            gpu_vram_total_mb=0.0,
            gpu_vram_used_mb=0.0,
            gpu_vram_free_mb=0.0,
            gpu_utilization_pct=0.0,
            gpu_temperature_c=None,
            telemetry_source="none",
            active_loaded_models=[],
            last_allocation_decision=None,
        )

    telemetry = model_router.get_hardware_telemetry()
    last_dec = model_router.get_last_allocation_decision()
    last_dec_dict = {
        "model": last_dec.model,
        "target_device": last_dec.target_device,
        "vram_required_mb": last_dec.vram_required_mb,
        "vram_free_before_mb": last_dec.vram_free_before_mb,
        "evictions_required": last_dec.evictions_required,
        "allowed": last_dec.allowed,
        "reason": last_dec.reason,
    } if last_dec else None

    return HardwareStatusResponse(
        status="active",
        timestamp=telemetry.timestamp,
        cpu_percent=telemetry.cpu_percent,
        cpu_cores_physical=telemetry.cpu_cores_physical,
        cpu_cores_logical=telemetry.cpu_cores_logical,
        ram_total_mb=telemetry.ram_total_mb,
        ram_used_mb=telemetry.ram_used_mb,
        ram_free_mb=telemetry.ram_free_mb,
        ram_percent=telemetry.ram_percent,
        gpu_available=telemetry.gpu.available,
        gpu_name=telemetry.gpu.name,
        gpu_vram_total_mb=telemetry.gpu.vram_total_mb,
        gpu_vram_used_mb=telemetry.gpu.vram_used_mb,
        gpu_vram_free_mb=telemetry.gpu.vram_free_mb,
        gpu_utilization_pct=telemetry.gpu.gpu_utilization_pct,
        gpu_temperature_c=telemetry.gpu.temperature_c,
        telemetry_source=telemetry.gpu.telemetry_source,
        active_loaded_models=getattr(model_router, "_active_loaded_models", []),
        last_allocation_decision=last_dec_dict,
    )
