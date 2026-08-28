# Sovereign On-Premise Agentic AI Workbench

> **SIH26117** — A sovereign, on-premise agentic AI workbench for local document reasoning, tool execution, and provider-agnostic model routing.

## Quick Start

> ⚠️ **Pre-requisite:** Ensure [Ollama](https://ollama.com) is running with at least one model pulled:
> ```bash
> ollama pull llama3
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

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for full architecture documentation.

See [docs/MVP_SCOPE.md](docs/MVP_SCOPE.md) for feature scope and prioritization.

## Project Structure

```
frontend/       React + Vite chat UI
backend/        FastAPI server (agent, RAG, tools, model routing)
agents/         Agent definitions (YAML + system prompts)
config/         Shared configuration (model routing)
data/           Runtime data (uploads, vector store, sandbox)
tests/          Test suite
docs/           Documentation
scripts/        Dev & ops scripts
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18, Vite |
| Backend | Python 3.13, FastAPI, Uvicorn |
| Vector DB | ChromaDB (embedded) |
| Local Models | Ollama |
| Remote Models | OpenAI-compatible API (optional) |

## License

Internal use only.
