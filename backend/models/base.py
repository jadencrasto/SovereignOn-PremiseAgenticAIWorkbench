"""
backend/models/base.py
----------------------
Abstract base class for all model providers.

Every provider (Ollama, OpenAI-compatible, OpenRouter, …) must implement
this interface. The agent engine only ever talks to BaseModelProvider —
it never imports a concrete provider directly.

Design principles:
- No Ollama-specific implementation details here.
- Structured request/response types so callers are type-safe.
- AsyncIterator for streaming keeps things composable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, List, Optional


# ---------------------------------------------------------------------------
# Shared data structures
# ---------------------------------------------------------------------------

@dataclass
class Message:
    """A single conversation message."""
    role: str          # "system" | "user" | "assistant"
    content: str


@dataclass
class ChatRequest:
    """
    Provider-agnostic chat completion request.
    The agent engine builds one of these and passes it to the provider.
    """
    messages: List[Message]
    model: str
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    stream: bool = False


@dataclass
class ChatResponse:
    """A complete (non-streaming) chat completion result."""
    content: str
    model: str
    provider: str
    finish_reason: str = "stop"


@dataclass
class ChatChunk:
    """A single streaming delta from a provider."""
    delta: str          # incremental text fragment
    done: bool = False  # True on the final (empty) chunk


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------

class BaseModelProvider(ABC):
    """
    All model providers implement this interface.

    Concrete implementations live in:
      backend/models/ollama_provider.py
      backend/models/openai_provider.py   (future)
      backend/models/openrouter_provider.py (future)
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier, e.g. 'ollama'."""
        ...

    @abstractmethod
    async def chat(self, request: ChatRequest) -> ChatResponse:
        """
        Non-streaming chat completion.
        Returns the full response after the model finishes.
        """
        ...

    @abstractmethod
    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        """
        Streaming chat completion.
        Yields ChatChunk objects until done=True.
        """
        ...

    @abstractmethod
    async def list_models(self) -> List[str]:
        """Return the model identifiers available from this provider."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the provider is reachable and operational."""
        ...
