"""
backend/models/ollama_provider.py
----------------------------------
Ollama provider adapter.

Implements BaseModelProvider by calling the Ollama HTTP API directly
via httpx (async).  No third-party Ollama SDK required.

API endpoints used:
  POST /api/chat          — chat completion (streaming & non-streaming)
  GET  /api/tags          — list locally available models
  GET  /                  — health check (root returns Ollama version string)

Phase 5: vision support
  When a Message carries non-empty .images, they are forwarded in Ollama's
  expected format: {"role": ..., "content": ..., "images": [<base64>, ...]}.
  Text-only messages are sent without an 'images' key — identical to Phase 4.
"""

from __future__ import annotations

import json
import asyncio
import logging
from typing import AsyncIterator, List, Optional

import httpx

from backend.models.base import (
    BaseModelProvider,
    ChatChunk,
    ChatRequest,
    ChatResponse,
    Message,
)

logger = logging.getLogger(__name__)


class OllamaProvider(BaseModelProvider):
    """Adapter for the locally-running Ollama inference server."""

    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        """
        Args:
            base_url: Ollama server URL, e.g. 'http://localhost:11434'.
                      Read from settings — never hardcoded.
            timeout:  HTTP timeout in seconds for blocking calls.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # High-performance client with persistent connection pooling & keep-alive
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            trust_env=False,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=120.0),
            timeout=httpx.Timeout(connect=5.0, read=timeout, write=15.0, pool=5.0),
        )


    @property
    def provider_name(self) -> str:
        return "ollama"

    # ------------------------------------------------------------------
    # Public interface implementation
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_response(data: object) -> tuple[str, bool]:
        if not isinstance(data, dict) or not isinstance(data.get("done"), bool):
            raise RuntimeError("Invalid model response")
        message = data.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise RuntimeError("Invalid model response")
        if data.get("error") or message.get("tool_calls"):
            raise RuntimeError("Invalid model response")
        return message["content"], data["done"]

    @staticmethod
    def _http_error(exc: Exception) -> RuntimeError:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
            return RuntimeError("Selected model is unavailable")
        if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
            return RuntimeError("Model request timed out")
        return RuntimeError("Model provider unavailable")

    async def chat(self, request: ChatRequest) -> ChatResponse:
        try:
            async with asyncio.timeout(self._timeout):
                resp = await self._client.post("/api/chat", json=self._build_payload(request, stream=False))
                resp.raise_for_status()
                try:
                    content, done = self._decode_response(resp.json())
                except (ValueError, TypeError) as exc:
                    raise RuntimeError("Invalid model response") from exc
                if not done or not content.strip():
                    raise RuntimeError("Incomplete model response")
                return ChatResponse(content=content, model=request.model, provider=self.provider_name)
        except (httpx.HTTPError, TimeoutError) as exc:
            raise self._http_error(exc) from exc

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        try:
            async with asyncio.timeout(self._timeout):
                async with self._client.stream("POST", "/api/chat", json=self._build_payload(request, stream=True)) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except (ValueError, TypeError) as exc:
                            raise RuntimeError("Invalid model response") from exc
                        delta, done = self._decode_response(data)
                        yield ChatChunk(delta=delta, done=done)
                        if done:
                            return
                    raise RuntimeError("Model response interrupted")
        except (httpx.HTTPError, TimeoutError) as exc:
            raise self._http_error(exc) from exc

    async def list_models(self) -> List[str]:
        """Return model tags available in the local Ollama instance."""
        try:
            resp = await self._client.get("/api/tags")
            resp.raise_for_status()
            data = resp.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception as exc:
            logger.warning("Ollama list_models failed: %s", exc)
            return []

    async def health_check(self) -> bool:
        """Ping Ollama root endpoint.  Returns True if reachable."""
        try:
            resp = await self._client.get("/", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def unload_model(self, model_name: str) -> bool:
        """
        Actively evict/unload a model from VRAM by calling Ollama /api/generate with keep_alive: 0.
        Essential for 4 GB VRAM GPUs so LLM and VLM are never co-resident.
        """
        clean_model = model_name.split("/")[-1]
        try:
            resp = await self._client.post(
                "/api/generate",
                json={"model": clean_model, "keep_alive": 0},
                timeout=10.0,
            )
            logger.info("Ollama unload_model | model=%s status=%d", clean_model, resp.status_code)
            return resp.status_code == 200
        except Exception as exc:
            logger.warning("Ollama unload_model failed for %s: %s", clean_model, exc)
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_payload(request: ChatRequest, stream: bool) -> dict:
        """
        Convert a generic ChatRequest to the Ollama /api/chat payload.

        Phase 5 vision support:
          - If a Message has non-empty .images, include 'images' key in
            that message dict (Ollama's multimodal format).
          - If ChatRequest.images is set, attach to the LAST
            user message in the list (the current user turn).
          - Enforces bounded context (num_ctx: 4096) to save ~800 MB VRAM on 4 GB GPUs.
        """
        messages = []
        for m in request.messages:
            msg_dict: dict = {"role": m.role, "content": m.content}
            if m.images:
                msg_dict["images"] = list(m.images)
            messages.append(msg_dict)

        if request.images:
            for msg_dict in reversed(messages):
                if msg_dict["role"] == "user":
                    existing = msg_dict.get("images", [])
                    msg_dict["images"] = existing + list(request.images)
                    break

        payload: dict = {
            "model": request.model,
            "messages": messages,
            "stream": stream,
            "keep_alive": "30m",  # Keep model resident in VRAM to eliminate cold-start lag
            "options": {
                "temperature": request.temperature,
                "num_ctx": 4096,   # Bounded KV cache footprint for 4 GB VRAM
                "num_thread": 8,   # Maximize multi-core execution on AMD Ryzen 7 6800HS
            },
        }
        if request.max_tokens is not None:
            payload["options"]["num_predict"] = request.max_tokens
        return payload


    async def aclose(self) -> None:
        """Clean up the underlying HTTP client."""
        await self._client.aclose()

