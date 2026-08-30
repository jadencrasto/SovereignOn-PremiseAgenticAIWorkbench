# Sovereign On-Premise Agentic AI Workbench

> **SIH26117** — A sovereign, on-premise agentic AI workbench for local document reasoning,
> tool execution, multimodal vision, provider-agnostic model routing, enterprise agent planning,
> and human-in-the-loop task execution.
> **100% local — zero cloud APIs.**

## Quick Start

> ⚠️ **Pre-requisite:** Ensure [Ollama](https://ollama.com) is running with the required models:
> ```bash
> ollama pull qwen2.5:7b        # Chat / Reasoning / Planning
> ollama pull nomic-embed-text  # Embeddings (RAG)
> ollama pull llava:7b          # Vision (Phase 5)
> ```

### 1. Clone & Configure
```bash
git clone <repo-url>
cd SovereignOn-PremiseAgenticAIWorkbench
cp .env.example .env
# Edit .env with your settings
```

### 2. Backend
```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r ../requirements.txt
uvicorn main:app --reload --port 8000
```

### 3. Frontend
```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173)

---

## Feature Phases

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | FastAPI + Ollama (qwen2.5:7b) + SSE streaming + Conversation Memory | ✅ Complete |
| 2 | PDF/DOCX/TXT/MD ingestion + nomic-embed-text + ChromaDB RAG | ✅ Complete |
| 3 | Semantic retrieval + context injection + sources SSE events | ✅ Complete |
| 4 | Tool registry (5 tools) + agent tool-loop + SSE tool events | ✅ Complete |
| 5 | LLaVA vision pipeline + `/api/chat/multimodal` + image UI | ✅ Complete |
| 6 | Enterprise Agent Planning + PlanValidator + Human Approval + SQLite Task Store | ✅ Complete |
| 7 | Enterprise Hardening + Argon2id Auth + Dual-Boundary RBAC + CSRF + Audit Logging | ✅ Complete |
| 8 | SIH Industrial Evaluation Suite + Benchmark Dataset + Master Evaluation Runner | ✅ Complete |

---

## Phase 7 & 8 — Enterprise Hardening & SIH Evaluation Suite

### Security & Governance Guarantees
- **Dual-Boundary RBAC:** Strict role enforcement at FastAPI route boundaries AND tool dispatch engine (`ADMIN`, `OPERATOR`, `VIEWER`).
- **Cryptographic Approval Binding:** Human-in-the-loop approvals bound via SHA-256 to `(task_id, step_id, tool_name, arguments)`.
- **Zero Cloud Egress:** 100% on-premise execution with local Ollama LLMs and ChromaDB vector embeddings.
- **Double-Submit CSRF & Argon2id Sessions:** Enterprise authentication and credential protection.
- **Auditable Sandbox:** Comprehensive filesystem sandbox with Windows reserved device / UNC path rejection and atomic file writes.

### Architecture

```
User Multi-Step Request
        │
        ▼
[Complexity Heuristic] ──(Simple)──► Existing Phase 4 Tool Loop
        │ (Multi-Step / Mutating)
        ▼
[AgentPlanner] ──► Generate JSON Plan (PlanStep[])
        │
        ▼
[PlanValidator] ──► Deterministic Backend Safety & Schema Validation
        │
        ▼
[TaskManager] ──► SQLite WAL Task State (data/tasks/tasks.db)
        │
        ├── Safe Steps ──────────► [ToolRegistry] ──► Execute & Update Step State
        │
        └── High-Risk Steps ─────► [ApprovalManager] ──► SHA-256 Hash Binding
                                           │
                                           ▼ (Yield approval_required event)
                                  [Human Operator Review]
                                           │
                                    Approve / Reject
                                           │
                                           ▼
                                 Recompute & Verify Hash ──(Match)──► Execute Step
```

### API Endpoints

| Method | Endpoint | Notes |
|--------|----------|-------|
| `POST` | `/api/auth/login` | Session login with Argon2id credentials |
| `POST` | `/api/auth/logout` | Revokes current session token |
| `GET`  | `/api/auth/me` | Returns authenticated user profile & RBAC role |
| `POST` | `/api/chat` | JSON — supports `planning_enabled` flag (defaults to true) |
| `POST` | `/api/chat/multimodal` | Multipart FormData — image + text message |
| `GET`  | `/api/documents` | List indexed documents in ChromaDB |
| `POST` | `/api/documents` | Upload & ingest document into ChromaDB |
| `DELETE`| `/api/documents/{doc_id}` | Delete indexed document |
| `GET`  | `/api/tasks` | List recent tasks with status, step counts, and timestamps |
| `GET`  | `/api/tasks/{task_id}` | Full task details, step breakdown, and audit results |
| `POST` | `/api/tasks/{task_id}/approve` | Resumes a paused task following operator approval/rejection |
| `POST` | `/api/tasks/{task_id}/cancel` | Cancels an active or paused task |
| `GET`  | `/api/tasks/{task_id}/approvals` | Approval audit trail for a task |
| `GET`  | `/api/tools` | Returns tools with `risk_level` and `requires_approval` |
| `GET`  | `/api/models` | Returns models with `capabilities` and `capability_routing` |
| `GET`  | `/api/health/live` | Health liveness probe |
| `GET`  | `/api/health/ready` | Health readiness probe checking Ollama and ChromaDB |

---

## Running the SIH Evaluation Suite
To execute the consolidated 8-suite benchmark runner:

```bash
python -m eval.run_all
```

Outputs are automatically generated and saved in `eval/results/`:
- `consolidated_report_latest.md`: Human-readable Markdown scorecard.
- `consolidated_report_latest.json`: Machine-readable benchmark telemetry.

## Running Backend Unit & Integration Tests

```bash
python -m pytest tests/ -v
```

Expected: **325 passed, 1 skipped (100% pass rate)**.


