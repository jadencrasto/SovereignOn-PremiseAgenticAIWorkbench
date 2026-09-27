"""
backend/tools/model_scan.py
---------------------------
Model scanning and local AI model inspection tool.

Scans the local Ollama instance and local models, reporting installed models,
sizes, parameters, quantization, and capabilities without fabricating data.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ModelScanInput(BaseModel):
    """Input schema for model_scan tool."""
    filter_capability: Optional[str] = Field(
        default=None,
        description="Optional filter by capability, e.g. 'chat', 'vision', 'embedding', 'reasoning'.",
    )


def create_model_scan(model_router: Any = None, ollama_url: str = "http://localhost:11434") -> callable:
    """Create the model_scan execution function."""

    async def execute_model_scan(args: ModelScanInput, **kwargs: Any) -> Dict[str, Any]:
        """Discover installed local models from host Ollama."""
        target_url = ollama_url.rstrip("/")
        discovered_models: List[Dict[str, Any]] = []
        service_online = False
        error_msg = None

        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(f"{target_url}/api/tags")
                if resp.status_code == 200:
                    service_online = True
                    data = resp.json()
                    for m in data.get("models", []):
                        name = m.get("name", "")
                        details = m.get("details", {})
                        size_bytes = m.get("size", 0)
                        size_gb = round(size_bytes / (1024 ** 3), 2)

                        caps = []
                        name_lower = name.lower()
                        if "embed" in name_lower or "nomic" in name_lower:
                            caps.append("embedding")
                        elif "llava" in name_lower or "vision" in name_lower or "vl" in name_lower:
                            caps.append("vision")
                            caps.append("chat")
                        else:
                            caps.append("chat")
                            caps.append("reasoning")

                        if args.filter_capability and args.filter_capability.lower() not in [c.lower() for c in caps]:
                            continue

                        discovered_models.append({
                            "name": name,
                            "id": f"ollama/{name}",
                            "size_gb": size_gb,
                            "parameter_size": details.get("parameter_size", "unknown"),
                            "quantization_level": details.get("quantization_level", "unknown"),
                            "format": details.get("format", "gguf"),
                            "family": details.get("family", "unknown"),
                            "modified_at": m.get("modified_at", ""),
                            "capabilities": caps,
                        })
        except Exception as exc:
            error_msg = f"Local Ollama unreachable at {target_url}: {exc}"
            logger.warning("model_scan tool: %s", error_msg)

        default_model = getattr(model_router, "default_model_id", "qwen2.5:7b") if model_router else "qwen2.5:7b"

        return {
            "status": "online" if service_online else "offline",
            "service_url": target_url,
            "models_count": len(discovered_models),
            "models": discovered_models,
            "default_model": default_model,
            "error": error_msg,
        }

    return execute_model_scan
