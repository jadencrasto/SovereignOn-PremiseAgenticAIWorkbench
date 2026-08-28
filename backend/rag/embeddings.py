"""
backend/rag/embeddings.py
-------------------------
Local embedding service using Ollama.

Provides a clean EmbeddingService abstraction that the RAG store and
retriever use.  The rest of the RAG pipeline never imports httpx or
Ollama-specific code directly.

Default model: nomic-embed-text (274 MB, runs locally via Ollama)

All embedding generation is local — no data is sent to external APIs.
"""

from __future__ import annotations

import logging
from typing import List

import httpx

logger = logging.getLogger(__name__)


class EmbeddingService:
    """
    Generates text embeddings using Ollama's /api/embeddings endpoint.

    This is a separate abstraction from BaseModelProvider because:
      - Embeddings use a different API path than chat completion.
      - The interface (embed one / embed many) differs from chat.
      - Future providers (e.g. sentence-transformers) can swap in here
        without touching the chat provider hierarchy.
    """

    def __init__(self, base_url: str, model: str, timeout: float = 60.0) -> None:
        """
        Args:
            base_url : Ollama server URL, e.g. 'http://localhost:11434'
            model    : embedding model name, e.g. 'nomic-embed-text'
            timeout  : HTTP timeout in seconds
        """
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(connect=10.0, read=timeout, write=30.0, pool=10.0),
        )
        logger.info("EmbeddingService ready | model=%s url=%s", model, base_url)

    @property
    def model_name(self) -> str:
        return self._model

    async def embed(self, text: str) -> List[float]:
        """
        Generate a single embedding vector.

        Args:
            text : the text to embed

        Returns:
            A list of floats (the embedding vector).

        Raises:
            RuntimeError : if Ollama is unreachable or returns an error
        """
        if not text.strip():
            raise ValueError("Cannot embed empty text.")

        try:
            resp = await self._client.post(
                "/api/embeddings",
                json={"model": self._model, "prompt": text},
            )
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"Cannot reach Ollama embedding endpoint at {self._base_url}. "
                "Ensure Ollama is running: 'ollama serve'"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Ollama embedding error {exc.response.status_code}: {exc.response.text}"
            ) from exc

        data = resp.json()
        embedding = data.get("embedding")
        if not embedding:
            raise RuntimeError(
                f"Ollama returned no embedding. "
                f"Is '{self._model}' pulled? Run: ollama pull {self._model}"
            )
        return embedding

    async def embed_many(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts.

        Ollama's /api/embeddings endpoint processes one text at a time.
        This method calls it sequentially.  A future optimisation could
        batch these with asyncio.gather if latency becomes a concern.

        Args:
            texts : list of non-empty strings

        Returns:
            List of embedding vectors, same order as input.
        """
        results: List[List[float]] = []
        for i, text in enumerate(texts):
            try:
                vec = await self.embed(text)
                results.append(vec)
            except Exception as exc:
                logger.error("embed_many: failed on text %d/%d: %s", i + 1, len(texts), exc)
                raise
        logger.debug("Embedded %d texts with model=%s", len(texts), self._model)
        return results

    async def health_check(self) -> bool:
        """Return True if the Ollama embedding endpoint is reachable."""
        try:
            resp = await self._client.get("/", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
