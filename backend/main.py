"""
backend/main.py
---------------
FastAPI application factory and entry point — Phase 2.

Startup:
  - Runtime directories created
  - Logging configured
  - ModelRouter instantiated
  - ConversationMemory initialised
  - EmbeddingService created (Ollama nomic-embed-text)
  - VectorStore initialised (ChromaDB persistent local)
  - Retriever initialised
  - DocumentService wired together
  - AgentEngine initialised and wired to DocumentService
  - All resources stored on app.state

Shutdown:
  - HTTP clients cleanly closed
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.utils.logging import setup_logging
from backend.models.router import ModelRouter
from backend.agent.memory import ConversationMemory
from backend.agent.engine import AgentEngine
from backend.rag.embeddings import EmbeddingService
from backend.rag.store import VectorStore
from backend.rag.retriever import Retriever
from backend.rag.service import DocumentService
from backend.schemas.chat import HealthResponse
from backend.api.chat import router as chat_router
from backend.api.models import router as models_router
from backend.api.documents import router as documents_router

# ---------------------------------------------------------------------------
# Logging — configure before anything else logs
# ---------------------------------------------------------------------------
setup_logging(
    log_level=settings.get_log_level(),
    log_dir=settings.log_dir,
    app_env=settings.app_env,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== %s v%s starting (%s) ===", settings.app_name, settings.app_version, settings.app_env)

    # Ensure runtime directories exist
    settings.ensure_dirs()

    # ---- Model router ----
    model_router = ModelRouter(settings)
    logger.info("ModelRouter ready | default=%s", model_router.default_model_id)

    # ---- Conversation memory ----
    memory = ConversationMemory()

    # ---- RAG: Embedding service ----
    embedding_service = EmbeddingService(
        base_url=settings.ollama_base_url,
        model=settings.embedding_model,
    )
    embed_ok = await embedding_service.health_check()
    if embed_ok:
        logger.info("EmbeddingService ready | model=%s", settings.embedding_model)
    else:
        logger.warning(
            "Ollama not reachable for embeddings at %s. "
            "Document upload will fail until Ollama is running.",
            settings.ollama_base_url,
        )

    # ---- RAG: Vector store ----
    vector_store = VectorStore(persist_dir=settings.chroma_persist_dir)
    logger.info("VectorStore ready | chunks=%d", vector_store.count())

    # ---- RAG: Retriever ----
    rag_top_k = 5
    retriever = Retriever(
        embedding_service=embedding_service,
        vector_store=vector_store,
        top_k=rag_top_k,
    )

    # ---- RAG: Document service ----
    doc_service = DocumentService(
        settings=settings,
        embedding_service=embedding_service,
        vector_store=vector_store,
        retriever=retriever,
    )
    logger.info(
        "DocumentService ready | indexed_docs=%d",
        len(doc_service.list_documents()),
    )

    # ---- Agent engine ----
    engine = AgentEngine(
        settings=settings,
        router=model_router,
        memory=memory,
        doc_service=doc_service,
    )

    # ---- Ollama chat health check ----
    chat_provider = model_router.get_provider("ollama")
    ollama_ok = await chat_provider.health_check()
    if ollama_ok:
        models = await chat_provider.list_models()
        logger.info("Ollama reachable | models: %s", models)
    else:
        logger.warning("Ollama chat provider not reachable at %s.", settings.ollama_base_url)

    # ---- Attach to app.state ----
    app.state.model_router = model_router
    app.state.engine = engine
    app.state.doc_service = doc_service
    app.state.ollama_ok = ollama_ok
    app.state.embed_ok = embed_ok

    logger.info("Startup complete — listening on %s:%d", settings.backend_host, settings.backend_port)

    yield  # ← application runs

    # ---- SHUTDOWN ----
    logger.info("Shutting down...")
    try:
        await chat_provider.aclose()
        await embedding_service.aclose()
        logger.info("HTTP clients closed")
    except Exception as exc:
        logger.warning("Shutdown cleanup error: %s", exc)
    logger.info("=== %s shutdown complete ===", settings.app_name)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="Sovereign On-Premise Agentic AI Workbench",
        description=(
            "SIH26117 — A sovereign, on-premise agentic AI workbench for local "
            "document reasoning, tool execution, and provider-agnostic model routing."
        ),
        version=settings.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ---- CORS ----
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- Routes ----
    app.include_router(chat_router)
    app.include_router(models_router)
    app.include_router(documents_router)

    # ---- Health endpoint ----
    @app.get(
        "/api/health",
        response_model=HealthResponse,
        summary="Service health check",
        tags=["health"],
    )
    async def health():
        return HealthResponse(
            status="ok",
            service="sovereign-workbench",
            version=settings.app_version,
            environment=settings.app_env,
            model_provider="ollama",
            default_model=app.state.model_router.default_model_id,
            ollama_url=settings.ollama_base_url,
        )

    return app


app = create_app()
