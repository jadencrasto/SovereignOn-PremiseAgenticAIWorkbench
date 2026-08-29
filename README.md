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

---

## Phase 6 — Enterprise Agent Planning & Human Approval

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

### Core Security & Governance Guarantees
- **The LLM is Never Self-Authorizing:** Proposing a tool call is merely a proposal. The deterministic backend evaluates every plan before execution.
- **Deterministic Complexity Heuristic:** Bypasses planning for simple questions / single actions and invokes the planner only for multi-step dependent workflows.
- **Cryptographic Approval Binding:** Human approvals are bound via SHA-256 to `(task_id, step_id, tool_name, arguments)`. If arguments or context are modified after approval, execution is rejected.
- **Persistent Task Execution:** All task states and pending approvals are persisted in SQLite WAL mode and survive backend restarts.
- **Mandatory Approval for Mutating Operations:** High-risk actions (`file_write`) can never execute autonomously without operator authorization.

### API Endpoints

| Method | Endpoint | Notes |
|--------|----------|-------|
| `POST` | `/api/chat` | JSON — supports `planning_enabled` flag (defaults to true) |
| `POST` | `/api/chat/multimodal` | Multipart FormData — image + text message |
| `GET`  | `/api/tasks` | List recent tasks with status, step counts, and timestamps |
| `GET`  | `/api/tasks/{task_id}` | Full task details, step breakdown, and audit results |
| `POST` | `/api/tasks/{task_id}/approve` | Resumes a paused task following operator approval/rejection |
| `POST` | `/api/tasks/{task_id}/cancel` | Cancels an active or paused task |
| `GET`  | `/api/tasks/{task_id}/approvals` | Approval audit trail for a task |
| `GET`  | `/api/tools` | Returns tools with `risk_level` and `requires_approval` |
| `GET`  | `/api/models` | Returns models with `capabilities` and `capability_routing` |

---

## Project Structure

```
frontend/
  src/components/
    agent/          PlanTimeline & ApprovalCard components
    tasks/          TaskHistoryView persistent task dashboard
    chat/           ChatView, MessageList, MessageItem with plan/approval support
    tools/          ToolsView with risk classifications
    settings/       SettingsView with orchestration runtime stats
backend/
  agent/
    planner.py        AgentPlanner & deterministic complexity heuristic
    plan_validator.py PlanValidator deterministic safety validator
    task.py           TaskManager & strict state machine
    task_store.py     SQLite WAL persistence layer
    approval.py       ApprovalManager with SHA-256 hash binding
    engine.py         AgentEngine with run_agent_task() & resume_agent_task()
  api/
    chat.py           Chat endpoints & planning SSE generator
    tasks.py          Tasks & approvals REST endpoints
    tools.py          Tool registry endpoints
data/
  tasks/tasks.db      SQLite persistent database for tasks & approvals
  uploads/images/     Isolated image store (UUID filenames)
  chromadb/           Persistent vector store
tests/backend/        Full test suite (245+ tests, all passing)
```

## Running Tests

```bash
python -m pytest tests/ -v
```

Expected: **245+ passed, 0 failed**
