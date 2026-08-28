"""
Seed sample documents into data/uploads/ for testing.

Usage:
    python scripts/seed_data.py

Creates a few sample text/markdown files that can be ingested
into the RAG pipeline during development.
"""

import os

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "uploads")


SAMPLE_DOCS = {
    "sample_project_overview.md": """\
# Sovereign On-Premise Agentic AI Workbench

## Overview
This project implements a sovereign, on-premise AI workbench that keeps all data
under the operator's control. It supports local models via Ollama, local document
ingestion via ChromaDB, and a provider-agnostic architecture.

## Key Features
- **Data Sovereignty**: No data leaves the host unless explicitly configured.
- **Provider Agnostic**: Supports Ollama, OpenAI-compatible APIs, and OpenRouter.
- **RAG Pipeline**: Upload documents and ask questions grounded in their content.
- **Tool Execution**: The agent can read/write files and execute Python code.

## Architecture
The system uses a React/Vite frontend communicating with a FastAPI backend.
The backend orchestrates an agent loop that can call tools, query documents,
and route requests to the appropriate model provider.
""",
    "sample_faq.txt": """\
Frequently Asked Questions — Sovereign AI Workbench

Q: What models does the workbench support?
A: Any model available through Ollama (llama3, mistral, codellama, etc.),
   plus any OpenAI-compatible API endpoint.

Q: Does the workbench send data to external servers?
A: Only if you explicitly configure an external provider (OpenAI, OpenRouter).
   When using Ollama, everything stays local.

Q: What document formats can I upload?
A: PDF, TXT, Markdown (.md), and DOCX files.

Q: How does the RAG system work?
A: Documents are parsed, split into chunks, embedded using a local model,
   and stored in ChromaDB. When you ask a question, relevant chunks are
   retrieved and injected into the prompt for grounded responses.

Q: Can the agent run code?
A: Yes. The agent can execute Python snippets in a sandboxed subprocess
   within the data/sandbox/ directory, with a configurable timeout.
""",
}


def main():
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    for filename, content in SAMPLE_DOCS.items():
        filepath = os.path.join(UPLOAD_DIR, filename)
        if os.path.exists(filepath):
            print(f"[SKIP] {filepath} already exists")
            continue
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[OK] Created {filepath}")

    print("\nSeed data ready. Ingest these documents via the upload API.")


if __name__ == "__main__":
    main()
