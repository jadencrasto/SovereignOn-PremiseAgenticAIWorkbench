# Sovereign On-Premise Agentic AI Workbench — MVP Scope

> **Project:** SIH26117  
> **Target:** Internal Round Demo  
> **Timeline:** ~3 days  
> **Last Updated:** 2026-08-28  

---

## Feature Prioritization (MoSCoW)

### ✅ MUST HAVE — Required for Internal Round

These features define the minimum viable demonstration.

| # | Feature | Component | Day |
|---|---------|-----------|-----|
| M1 | **Chat interface** — send messages, see responses, streaming output | Frontend | 1 |
| M2 | **FastAPI backend** — `/api/chat` endpoint with SSE streaming | Backend | 1 |
| M3 | **Ollama integration** — chat completion via local Ollama | Backend/Models | 1 |
| M4 | **Provider abstraction** — abstract `BaseModelProvider` interface so logic is not coupled to Ollama | Backend/Models | 1 |
| M5 | **Model router** — route requests to configured provider by `provider/model` identifier | Backend/Models | 1 |
| M6 | **Conversation memory** — maintain multi-turn history within a session (in-memory) | Backend/Agent | 1 |
| M7 | **Document upload** — upload PDF, TXT, MD, DOCX files via UI | Frontend + Backend | 2 |
| M8 | **Document ingestion** — parse, chunk, and embed uploaded documents | Backend/RAG | 2 |
| M9 | **Vector storage** — store embeddings in ChromaDB (local) | Backend/RAG | 2 |
| M10 | **RAG retrieval** — augment chat context with relevant document chunks | Backend/RAG + Agent | 2 |
| M11 | **Tool: file_read** — agent can read files from `data/` directory | Backend/Tools | 3 |
| M12 | **Tool: code_execute** — agent can run Python snippets (sandboxed subprocess) | Backend/Tools | 3 |
| M13 | **Tool registry** — register, list, and dispatch tools to agent | Backend/Tools | 3 |
| M14 | **Agent tool loop** — agent can call tools, observe results, and continue reasoning | Backend/Agent | 3 |
| M15 | **Environment-based config** — all secrets/settings via `.env` | Backend | 1 |
| M16 | **CORS configuration** — secure cross-origin setup for dev | Backend | 1 |
| M17 | **Health check endpoint** — `GET /api/health` | Backend | 1 |

---

### 🟡 SHOULD HAVE — Highly Desirable for Demo Quality

These improve the demo significantly but the system works without them.

| # | Feature | Component | Day |
|---|---------|-----------|-----|
| S1 | **Document list UI** — view uploaded/ingested documents | Frontend | 2 |
| S2 | **Model selection UI** — dropdown to pick model from available list | Frontend | 3 |
| S3 | **RAG citations** — show which document chunks were used in the response | Frontend + Backend | 2–3 |
| S4 | **Tool execution status** — show tool calls and results in the chat | Frontend | 3 |
| S5 | **Structured logging** — JSON logs with request tracing | Backend | 3 |
| S6 | **Error handling** — graceful error messages in UI when backend fails | Frontend | 3 |
| S7 | **Loading states** — spinners, typing indicators | Frontend | 3 |
| S8 | **Tool: file_write** — agent can write files to `data/sandbox/` | Backend/Tools | 3 |
| S9 | **Tool: file_list** — agent can list directory contents | Backend/Tools | 3 |

---

### 🔵 NICE TO HAVE — Polish & Impression

These make the demo more impressive but are stretch goals.

| # | Feature | Component |
|---|---------|-----------|
| N1 | **Dark mode** — premium dark UI theme | Frontend |
| N2 | **Markdown rendering** — rich rendering of assistant responses | Frontend |
| N3 | **Code syntax highlighting** — in assistant code blocks | Frontend |
| N4 | **OpenAI-compatible provider** — connect to OpenAI or compatible API | Backend/Models |
| N5 | **Document delete** — remove ingested documents | Frontend + Backend |
| N6 | **System prompt customization** — editable in Settings panel | Frontend + Backend |
| N7 | **Multiple conversations** — basic conversation switching | Frontend |
| N8 | **Ollama model auto-detection** — list locally available models | Backend/Models |

---

### ⬜ DEFERRED — Post Internal Round

These are architecturally planned but will not be built for the internal round.

| # | Feature | Rationale |
|---|---------|-----------|
| D1 | **OpenRouter integration** | Requires API key management, external dependency |
| D2 | **Multi-agent orchestration** | Complex; single agent is sufficient for demo |
| D3 | **Docker-based code sandbox** | Subprocess sandbox is adequate for demo |
| D4 | **Authentication / JWT** | Localhost-only for internal round |
| D5 | **Persistent conversation storage** | In-memory is fine for demo |
| D6 | **Spreadsheet tool** | Domain-specific; defer to post-MVP |
| D7 | **Image generation/analysis tool** | Requires additional model setup |
| D8 | **Web scraping tool** | External network access; defer |
| D9 | **Multi-user support** | Single-user demo |
| D10 | **Docker Compose deployment** | Dev server is sufficient |
| D11 | **Advanced re-ranking** | Basic top-k retrieval is adequate |
| D12 | **Agent planning layer** | Simple ReAct loop is sufficient for MVP |
| D13 | **Rate limiting** | Localhost only |
| D14 | **Conversation export** | Not needed for demo |

---

## Success Criteria for Internal Round

The demo is successful if we can show:

1. ✅ A user opens the web UI and types a question
2. ✅ The system streams a response from a **local Ollama model**
3. ✅ The user uploads a PDF document
4. ✅ The user asks a question **about the document** and gets a relevant, grounded answer
5. ✅ The agent **calls a tool** (e.g., reads a file or executes code) and incorporates the result
6. ✅ All of the above runs **entirely on-premise** with no external API calls (when using Ollama)
7. ✅ The architecture is **clean and extensible** — easy to add new providers, tools, and agents

---

## Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Ollama model too slow on host hardware | Demo feels sluggish | Use smaller model (e.g., `llama3:8b`, `mistral:7b`); pre-pull models |
| ChromaDB embedding issues | RAG doesn't work | Fall back to Ollama embeddings; test early on Day 2 |
| Tool calling format varies by model | Agent can't call tools | Standardize on Ollama's tool-calling format; fall back to prompt-based tool use |
| Time pressure | Features cut | Strict MoSCoW adherence; cut SHOULD-HAVE before any MUST-HAVE |
| Large document parsing failures | Upload feature broken | Limit to TXT/MD initially, add PDF support once stable |

---

## Dependency Summary

### Python (backend)
```
fastapi
uvicorn[standard]
pydantic-settings
httpx                    # async HTTP client for Ollama/OpenAI
chromadb                 # vector store
pypdf                    # PDF parsing
python-docx              # DOCX parsing
python-multipart         # file uploads in FastAPI
structlog                # structured logging
pyyaml                   # agent/model config files
```

### Node.js (frontend)
```
react
react-dom
react-markdown           # markdown rendering
vite
```

### System
```
Ollama (with at least one model pulled, e.g., llama3)
```
