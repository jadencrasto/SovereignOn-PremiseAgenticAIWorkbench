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
        # Separate client for streaming (no response timeout limit)
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(connect=10.0, read=timeout, write=30.0, pool=10.0),
        )

    @property
    def provider_name(self) -> str:
        return "ollama"

    # ------------------------------------------------------------------
    # Public interface implementation
    # ------------------------------------------------------------------

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Non-streaming chat completion.  Waits for full response."""
        payload = self._build_payload(request, stream=False)
        has_images = any(m.images for m in request.messages) or bool(request.images)
        logger.debug(
            "Ollama chat: model=%s messages=%d vision=%s",
            request.model, len(request.messages), has_images,
        )
        try:
            resp = await self._client.post("/api/chat", json=payload)
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"Cannot reach Ollama at {self._base_url}. "
                "Make sure Ollama is running: 'ollama serve'"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Ollama API error {exc.response.status_code}: {exc.response.text}"
            ) from exc

        data = resp.json()
        content = data.get("message", {}).get("content", "")
        finish_reason = "stop" if data.get("done") else "length"

        return ChatResponse(
            content=content,
            model=request.model,
            provider=self.provider_name,
            finish_reason=finish_reason,
        )

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        """
        Streaming chat completion.

        Ollama sends one JSON object per line while streaming.
        Each line has {"message": {"content": "..."}, "done": false/true}.

        Phase 5: vision images are included in the payload when present;
        the streaming protocol is identical regardless.
        """
        payload = self._build_payload(request, stream=True)
        has_images = any(m.images for m in request.messages) or bool(request.images)
        logger.debug(
            "Ollama stream: model=%s messages=%d vision=%s",
            request.model, len(request.messages), has_images,
        )
        try:
            async with self._client.stream(
                "POST", "/api/chat", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("Ollama stream: could not parse line: %r", line)
                        continue

                    delta = data.get("message", {}).get("content", "")
                    done = data.get("done", False)
                    yield ChatChunk(delta=delta, done=done)
                    if done:
                        break

        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"Cannot reach Ollama at {self._base_url}. "
                "Make sure Ollama is running: 'ollama serve'"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Ollama API error {exc.response.status_code}: {exc.response.text}"
            ) from exc

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
          - If ChatRequest.images is non-empty, attach them to the LAST
            user message in the list (the current user turn).
          - Text-only messages produce identical output to Phase 4 (no
            'images' key present).
        """
        messages = []
        for m in request.messages:
            msg_dict: dict = {"role": m.role, "content": m.content}
            if m.images:
                # Images already attached directly to this Message object
                msg_dict["images"] = list(m.images)
            messages.append(msg_dict)

        # If ChatRequest.images is set, attach to the last user message
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
            "options": {
                "temperature": request.temperature,
            },
        }
        if request.max_tokens is not None:
            payload["options"]["num_predict"] = request.max_tokens
        return payload

    async def aclose(self) -> None:
        """Clean up the underlying HTTP client."""
        await self._client.aclose()
