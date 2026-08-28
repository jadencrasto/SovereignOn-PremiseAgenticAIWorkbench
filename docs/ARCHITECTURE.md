# Sovereign On-Premise Agentic AI Workbench — Architecture

> **Project:** SIH26117  
> **Version:** 0.1.0-internal  
> **Last Updated:** 2026-08-28  

---

## 1. System Overview

The workbench is a **self-hosted, provider-agnostic platform** that lets users interact with agentic AI assistants that can:

- Reason over **local documents** (RAG)
- Execute **local tools** (file I/O, code execution, search)
- Route requests to **local models** (Ollama) or **remote providers** (OpenAI-compatible, OpenRouter)
- Maintain full **data sovereignty** — no data leaves the host unless the operator explicitly configures an external provider

### High-Level Diagram

```
┌─────────────────────────────────────────────────────┐
│                    Frontend (React/Vite)             │
│  Chat UI · Document Upload · Tool Status · Settings  │
└────────────────────────┬────────────────────────────┘
                         │  REST / WebSocket
┌────────────────────────▼────────────────────────────┐
│                  Backend (FastAPI)                    │
│                                                      │
│  ┌──────────┐  ┌────────────┐  ┌──────────────────┐ │
│  │ Agent    │  │ Model      │  │ RAG / Document   │ │
│  │ Engine   │──│ Router     │  │ Pipeline         │ │
│  └────┬─────┘  └─────┬──────┘  └───────┬──────────┘ │
│       │              │                 │             │
│  ┌────▼─────┐  ┌─────▼──────┐  ┌──────▼───────┐    │
│  │ Tool     │  │ Provider   │  │ Vector Store │    │
│  │ Registry │  │ Adapters   │  │ (ChromaDB)   │    │
│  └──────────┘  └────────────┘  └──────────────┘    │
└─────────────────────────────────────────────────────┘
```

---

## 2. Component Architecture

### 2.1 Frontend (`frontend/`)

| Concern | Choice | Rationale |
|---|---|---|
| Framework | React 18 + Vite | Fast dev server, simple tooling |
| State | React Context + `useReducer` | Enough for MVP; no Redux overhead |
| Styling | Vanilla CSS (CSS Modules) | No build-time CSS framework needed |
| HTTP | `fetch` / native `EventSource` | Zero-dep; SSE for streaming |
| Markdown | `react-markdown` | Render assistant messages |

Key pages / panels:
1. **Chat** — multi-turn conversation with streaming responses
2. **Documents** — upload & list ingested files
3. **Settings** — provider config, model selection, temperature
4. **Tool Status** — live view of tool executions (future)

### 2.2 Backend (`backend/`)

| Concern | Choice | Rationale |
|---|---|---|
| Framework | FastAPI | Async-native, auto-docs, Python ecosystem |
| Server | Uvicorn | ASGI, production-capable |
| Config | `pydantic-settings` + `.env` | Typed config, 12-factor |
| Logging | `structlog` (or stdlib) | Structured JSON logs |
| DB (metadata) | SQLite via `aiosqlite` | Zero-ops, file-based |
| Vector DB | ChromaDB (local) | Embeddable, no server needed |
| Task queue | In-process `asyncio` | MVP simplicity; swap for Celery/ARQ later |

#### Backend Package Layout

```
backend/
├── main.py                 # FastAPI app factory
├── config.py               # Settings from env
├── api/
│   ├── chat.py             # POST /chat, SSE streaming
│   ├── documents.py        # Upload, list, delete docs
│   ├── models.py           # List/configure providers
│   └── tools.py            # List/invoke tools
├── agent/
│   ├── engine.py           # Core agent loop
│   ├── planner.py          # Optional planning layer
│   └── memory.py           # Conversation history
├── models/
│   ├── router.py           # Model routing logic
│   ├── base.py             # Abstract provider interface
│   ├── ollama_provider.py  # Ollama adapter
│   ├── openai_provider.py  # OpenAI-compatible adapter
│   └── openrouter_provider.py  # OpenRouter adapter
├── rag/
│   ├── ingest.py           # Document parsing & chunking
│   ├── embeddings.py       # Embedding generation
│   ├── retriever.py        # Semantic search
│   └── store.py            # ChromaDB wrapper
├── tools/
│   ├── registry.py         # Tool registration & dispatch
│   ├── file_ops.py         # File read/write/list
│   ├── code_exec.py        # Sandboxed Python execution
│   └── search.py           # Web/local search
├── schemas/
│   ├── chat.py             # Request/response models
│   ├── document.py
│   └── tool.py
└── utils/
    ├── logging.py
    └── security.py
```

### 2.3 Agents (`agents/`)

Agent definitions live here as declarative configs + system prompts. They are loaded by the backend's `agent.engine`.

```
agents/
├── default/
│   ├── agent.yaml          # capabilities, tools, model preferences
│   └── system_prompt.md    # system prompt template
└── researcher/             # future domain-specific agent
    ├── agent.yaml
    └── system_prompt.md
```

### 2.4 RAG Pipeline (`backend/rag/`)

Document ingestion flow:

```
Upload → Parse (PDF/TXT/MD/DOCX) → Chunk → Embed → Store (ChromaDB)
```

Retrieval flow:

```
User query → Embed query → Vector search → Top-K chunks → Inject into prompt
```

### 2.5 Tools (`backend/tools/`)

Tools follow a **uniform interface**:

```python
class BaseTool:
    name: str
    description: str
    parameters: dict          # JSON Schema

    async def execute(self, params: dict) -> ToolResult:
        ...
```

The agent engine calls tools through `ToolRegistry.dispatch(name, params)`.

### 2.6 Models / Provider Abstraction (`backend/models/`)

All providers implement a common interface:

```python
class BaseModelProvider(ABC):
    async def chat_completion(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        stream: bool = False,
        **kwargs
    ) -> AsyncIterator[ChatChunk] | ChatResponse:
        ...
```

The **Model Router** selects a provider based on:
1. User-selected model
2. `config/models.yaml` routing rules
3. Fallback chain (e.g., Ollama → OpenAI → OpenRouter)

---

## 3. Data Flow

### 3.1 Chat Request (happy path)

```
Frontend                   Backend
   │                          │
   │── POST /api/chat ───────▶│
   │   {messages, model?,     │
   │    use_rag?, tools?}     │
   │                          │
   │                     ┌────▼─────┐
   │                     │ Agent    │
   │                     │ Engine   │
   │                     └────┬─────┘
   │                          │
   │               ┌──────────┼──────────┐
   │               ▼          ▼          ▼
   │          RAG lookup  Model call  Tool call
   │               │          │          │
   │               └──────────┼──────────┘
   │                          │
   │◀── SSE stream ───────────│
   │   {delta, tool_calls,    │
   │    citations, done}      │
```

### 3.2 Document Ingestion

```
Frontend                   Backend
   │                          │
   │── POST /api/documents ──▶│
   │   (multipart file)       │
   │                     ┌────▼──────┐
   │                     │ Ingest    │
   │                     │ Pipeline  │
   │                     └────┬──────┘
   │                          │
   │                     Parse → Chunk → Embed → ChromaDB
   │                          │
   │◀── 201 {doc_id, chunks} ─│
```

---

## 4. Agent Lifecycle

```
                    ┌─────────────────┐
                    │   User Message   │
                    └────────┬────────┘
                             ▼
                    ┌─────────────────┐
          ┌────────│  Agent Engine    │────────┐
          │        └────────┬────────┘         │
          ▼                 │                  ▼
   ┌─────────────┐         │          ┌──────────────┐
   │ RAG Context │         │          │ Conv History │
   └──────┬──────┘         │          └──────┬───────┘
          │                │                 │
          └────────┬───────┘─────────────────┘
                   ▼
          ┌─────────────────┐
          │ Build Prompt    │  (system + RAG context + history + user msg)
          └────────┬────────┘
                   ▼
          ┌─────────────────┐
          │ Model Router    │──▶ Provider.chat_completion()
          └────────┬────────┘
                   ▼
          ┌─────────────────┐
          │ Response Parse  │
          └────────┬────────┘
                   │
            ┌──────┴──────┐
            ▼             ▼
      [Text chunk]   [Tool call]
            │             │
            │        ┌────▼─────┐
            │        │ Execute  │
            │        │ Tool     │
            │        └────┬─────┘
            │             │
            │        Feed result back ──▶ (loop back to Build Prompt)
            │
            ▼
      Stream to frontend
```

The loop continues until the model returns a final text response with no further tool calls, or a configurable iteration limit is hit.

---

## 5. Model Routing Concept

```yaml
# config/models.yaml (example)
providers:
  ollama:
    type: ollama
    base_url: http://localhost:11434
    models:
      - llama3
      - mistral
  openai:
    type: openai_compatible
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
    models:
      - gpt-4o
      - gpt-4o-mini
  openrouter:
    type: openai_compatible
    base_url: https://openrouter.ai/api/v1
    api_key: ${OPENROUTER_API_KEY}
    models:
      - anthropic/claude-sonnet-4

default_model: ollama/llama3
fallback_chain:
  - ollama/llama3
  - openai/gpt-4o-mini
```

The router:
1. Parses `provider/model` identifiers
2. Looks up the provider adapter
3. Handles fallback on failure
4. Normalises all responses to a common `ChatResponse` / `ChatChunk` schema

---

## 6. RAG Flow

| Stage | Implementation | Notes |
|---|---|---|
| Parse | `pypdf`, `python-docx`, `unstructured` | PDF, DOCX, TXT, MD |
| Chunk | Recursive text splitter (500 tokens, 50 overlap) | Configurable via settings |
| Embed | Ollama embeddings **or** `sentence-transformers` | Provider-agnostic |
| Store | ChromaDB (local persistent mode) | `data/chromadb/` |
| Retrieve | Cosine similarity, top-k (default 5) | Re-rank is post-MVP |

---

## 7. Tool Execution Flow

```
Agent decides to call tool
        │
        ▼
┌──────────────────┐
│ ToolRegistry     │──▶ lookup by name
└───────┬──────────┘
        │
        ▼
┌──────────────────┐
│ Validate params  │  (JSON Schema)
└───────┬──────────┘
        │
        ▼
┌──────────────────┐
│ Execute          │  (async, with timeout)
└───────┬──────────┘
        │
        ▼
┌──────────────────┐
│ Return result    │──▶ back to Agent Engine
└──────────────────┘
```

### Security for Code Execution

- **MVP:** `subprocess` with timeout, restricted to a `data/sandbox/` directory, no network access
- **Post-MVP:** Docker-based sandboxing or `RestrictedPython`

---

## 8. Security Boundaries

| Boundary | MVP Approach | Post-MVP |
|---|---|---|
| API Authentication | None (localhost only) | API key / JWT |
| Code execution | Subprocess + timeout + chroot-like path restriction | Docker sandbox |
| File operations | Scoped to `data/` directory | ACL per user |
| External API keys | `.env` file, never logged or returned to frontend | Secrets manager |
| CORS | Allow `localhost:5173` only | Configurable origins |
| Input validation | Pydantic models on all endpoints | Rate limiting |
| Model provider keys | Server-side only, never sent to browser | Vault integration |

---

## 9. Proposed Directory Structure

```
SovereignOn-PremiseAgenticAIWorkbench/
│
├── frontend/                    # React + Vite
│   ├── public/
│   ├── src/
│   │   ├── components/          # UI components
│   │   ├── pages/               # Route-level views
│   │   ├── hooks/               # Custom React hooks
│   │   ├── services/            # API client functions
│   │   ├── context/             # React Context providers
│   │   ├── utils/               # Helpers
│   │   ├── App.jsx
│   │   ├── main.jsx
│   │   └── index.css
│   ├── index.html
│   ├── vite.config.js
│   └── package.json
│
├── backend/                     # FastAPI
│   ├── main.py
│   ├── config.py
│   ├── api/                     # Route handlers
│   ├── agent/                   # Agent engine + memory
│   ├── models/                  # Provider adapters
│   ├── rag/                     # Ingest, embed, retrieve
│   ├── tools/                   # Tool implementations
│   ├── schemas/                 # Pydantic models
│   └── utils/                   # Logging, security helpers
│
├── agents/                      # Agent definitions (YAML + prompts)
│   └── default/
│       ├── agent.yaml
│       └── system_prompt.md
│
├── config/                      # Shared configuration files
│   └── models.yaml              # Provider & model routing config
│
├── data/                        # Runtime data (gitignored contents)
│   ├── uploads/                 # Uploaded documents
│   ├── chromadb/                # Vector store
│   ├── sandbox/                 # Code execution sandbox
│   └── logs/                    # Application logs
│
├── tests/                       # Test suite
│   ├── backend/
│   └── frontend/
│
├── docs/                        # Documentation
│   ├── ARCHITECTURE.md
│   └── MVP_SCOPE.md
│
├── scripts/                     # Dev & ops scripts
│   ├── setup.ps1                # Windows setup script
│   ├── dev.ps1                  # Start dev servers
│   └── seed_data.py             # Seed sample documents
│
├── .env.example                 # Environment variable template
├── .gitignore
├── README.md
└── requirements.txt             # Python dependencies
```

---

## 10. Development Phases

### Phase 1 — Foundation (Day 1)
- [ ] Monorepo scaffold, `.gitignore`, `.env.example`
- [ ] Backend: FastAPI app, config, health endpoint
- [ ] Frontend: Vite + React scaffold, chat UI shell
- [ ] Model router + Ollama adapter
- [ ] Basic chat endpoint (no RAG, no tools)
- [ ] SSE streaming from backend to frontend

### Phase 2 — RAG + Documents (Day 2)
- [ ] Document upload endpoint
- [ ] Parse + chunk + embed pipeline
- [ ] ChromaDB integration
- [ ] RAG-augmented chat (inject context)
- [ ] Document list/delete UI

### Phase 3 — Tools + Polish (Day 3)
- [ ] Tool registry + file_ops tool
- [ ] Code execution tool (sandboxed)
- [ ] Agent loop with tool-calling support
- [ ] Settings panel (model selection, provider config)
- [ ] Error handling, loading states, basic logging
- [ ] Demo preparation

### Post-MVP (future)
- OpenAI + OpenRouter providers
- Multi-agent orchestration
- Spreadsheet / image tools
- Docker-based code sandbox
- Auth & multi-user
- Persistent conversation storage
- Advanced re-ranking

---

## 11. Internal-Round MVP Scope

See **docs/MVP_SCOPE.md** for the detailed feature breakdown.

**In summary:** The MVP demonstrates a chat interface talking to a local Ollama model, with the ability to upload documents and have the agent reason over them using RAG, plus basic file and code tools — all running fully on-premise.

---

## 12. Post-MVP Scope

| Area | Features |
|---|---|
| Providers | OpenAI, OpenRouter, Azure OpenAI, custom endpoints |
| Agents | Multi-agent, planning chains, agent handoff |
| Tools | Spreadsheet analysis, image generation, web scraping |
| Security | JWT auth, role-based access, Docker sandbox |
| Ops | Docker Compose deployment, health monitoring |
| UX | Conversation persistence, export, theming |
