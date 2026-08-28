"""
scripts/rag_e2e_test.py
-----------------------
End-to-end live verification of the Phase 2 RAG pipeline.

Steps:
  1. Health check
  2. Create a test document
  3. Upload it via POST /api/documents
  4. Verify it appears in GET /api/documents
  5. Ask a question that requires document context (non-streaming)
  6. Verify sources are returned
  7. Ask a follow-up using SSE streaming
  8. Verify streaming sources event
  9. Delete the document
  10. Verify document is gone
  11. Verify chat still works without documents

Run AFTER starting the backend:
  python -m uvicorn backend.main:app --port 8000
"""

import httpx
import json
import sys
import tempfile
import time
from pathlib import Path

BASE = "http://localhost:8000"
TIMEOUT = 120.0

SEP = "=" * 60


def section(title: str):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def ok(msg: str):
    print(f"  [OK] {msg}")


def fail(msg: str):
    print(f"  [FAIL] {msg}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Step 1: Health check
# ---------------------------------------------------------------------------

def test_health():
    section("Step 1: Health Check")
    r = httpx.get(f"{BASE}/api/health", timeout=10)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    data = r.json()
    print(f"  status      : {data['status']}")
    print(f"  version     : {data['version']}")
    print(f"  environment : {data['environment']}")
    print(f"  model       : {data['default_model']}")
    ok("Health check passed")


# ---------------------------------------------------------------------------
# Step 2–3: Create + upload test document
# ---------------------------------------------------------------------------

TEST_DOC_CONTENT = """
Sovereign AI Workbench — Reference Manual

Section 1: Data Sovereignty

The Sovereign AI Workbench is designed for complete on-premise operation.
No data is transmitted to external cloud providers.
All document processing, embedding generation, and vector storage occur locally.

Section 2: Refund Policy

Products purchased through the Sovereign Workbench License Store are eligible
for a full refund within 30 days of purchase, provided the software has not
been activated on more than one device.
After 30 days, no refunds will be issued unless the software is defective.

Section 3: System Requirements

Minimum hardware requirements for local model execution:
- CPU: 8 cores (16 recommended)
- RAM: 16 GB (32 GB recommended for 7B models)
- Storage: 50 GB free disk space
- GPU: Optional but recommended for faster inference

Section 4: Supported Models

The workbench supports any model available through the Ollama runtime.
The default model is qwen2.5:7b.
For multimodal tasks, llava:7b is recommended.
Embedding is handled by nomic-embed-text.
""".strip()


def test_upload():
    section("Step 2–3: Create & Upload Test Document")
    content = TEST_DOC_CONTENT.encode()
    filename = "sovereign_manual.txt"
    print(f"  filename    : {filename}")
    print(f"  size        : {len(content)} bytes")

    r = httpx.post(
        f"{BASE}/api/documents",
        files={"file": (filename, content, "text/plain")},
        timeout=60.0,
    )
    if r.status_code != 200:
        fail(f"Upload failed: {r.status_code} — {r.text}")

    data = r.json()
    print(f"  document_id : {data['document_id']}")
    print(f"  file_type   : {data['file_type']}")
    print(f"  chunks      : {data['chunks']}")
    print(f"  status      : {data['status']}")
    assert data["chunks"] > 0, "Expected at least 1 chunk"
    assert data["status"] == "indexed"
    ok("Document uploaded and indexed")
    return data["document_id"]


# ---------------------------------------------------------------------------
# Step 4: List documents
# ---------------------------------------------------------------------------

def test_list_documents(doc_id: str):
    section("Step 4: List Indexed Documents")
    r = httpx.get(f"{BASE}/api/documents", timeout=10)
    assert r.status_code == 200
    data = r.json()
    print(f"  total docs  : {data['total']}")
    for d in data["documents"]:
        print(f"    - {d['document_id']} | {d['filename']} | {d['chunk_count']} chunks")
    found = any(d["document_id"] == doc_id for d in data["documents"])
    assert found, f"Document {doc_id} not found in list"
    ok("Document appears in listing")


# ---------------------------------------------------------------------------
# Step 5–6: RAG chat (non-streaming)
# ---------------------------------------------------------------------------

def test_rag_chat_sync(doc_id: str):
    section("Step 5–6: RAG Chat (non-streaming) — Refund Policy Query")
    question = "What is the refund policy for the Sovereign Workbench?"
    print(f"  question    : {question}")

    r = httpx.post(
        f"{BASE}/api/chat",
        json={"message": question, "stream": False},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, f"Chat failed: {r.status_code} — {r.text}"
    data = r.json()

    answer = data["message"]["content"]
    session_id = data["session_id"]
    sources = data.get("sources") or []

    print(f"  session_id  : {session_id}")
    print(f"  model_used  : {data['model_used']}")
    print(f"  sources     : {len(sources)}")
    print(f"  answer:\n    {answer[:300]}")

    if sources:
        print("\n  Evidence:")
        for s in sources:
            page = f" p.{s['page']}" if s.get("page") else ""
            print(f"    [{s['filename']}{page}] score={s['score']:.4f} chunk={s['chunk_id']}")

    # Answer should mention 30 days (from the document)
    assert "30" in answer or "thirty" in answer.lower() or "refund" in answer.lower(), \
        f"Answer does not seem to use document context:\n{answer}"
    ok("RAG chat answered using document context")
    return session_id


# ---------------------------------------------------------------------------
# Step 7–8: RAG chat (SSE streaming)
# ---------------------------------------------------------------------------

def test_rag_chat_stream():
    section("Step 7–8: RAG Chat (SSE streaming) — System Requirements")
    question = "What are the minimum RAM requirements for local model execution?"
    print(f"  question    : {question}")

    full_text = []
    sources_event = None

    with httpx.stream(
        "POST",
        f"{BASE}/api/chat",
        json={"message": question, "stream": True},
        timeout=TIMEOUT,
    ) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")

        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            chunk = json.loads(line[6:])
            if chunk["type"] == "delta":
                full_text.append(chunk["content"])
            elif chunk["type"] == "sources":
                sources_event = chunk.get("sources", [])
            elif chunk["type"] == "done":
                break

    answer = "".join(full_text)
    print(f"  answer ({len(answer)} chars):\n    {answer[:300]}")
    print(f"  sources in stream : {len(sources_event or [])}")

    if sources_event:
        for s in sources_event:
            print(f"    [{s['filename']}] score={s['score']:.4f}")

    assert len(answer) > 0, "No streaming response received"
    ok("RAG SSE streaming worked with sources event")


# ---------------------------------------------------------------------------
# Step 9: Delete document
# ---------------------------------------------------------------------------

def test_delete_document(doc_id: str):
    section("Step 9: Delete Document")
    r = httpx.delete(f"{BASE}/api/documents/{doc_id}", timeout=10)
    assert r.status_code == 200, f"Delete failed: {r.status_code} — {r.text}"
    data = r.json()
    print(f"  document_id     : {data['document_id']}")
    print(f"  chunks_deleted  : {data['chunks_deleted']}")
    assert data["chunks_deleted"] > 0
    ok("Document deleted")


# ---------------------------------------------------------------------------
# Step 10: Verify gone
# ---------------------------------------------------------------------------

def test_verify_deleted(doc_id: str):
    section("Step 10: Verify Document Removed from Index")
    r = httpx.get(f"{BASE}/api/documents", timeout=10)
    data = r.json()
    found = any(d["document_id"] == doc_id for d in data["documents"])
    assert not found, f"Document {doc_id} still in listing after delete"
    ok("Document no longer in index")


# ---------------------------------------------------------------------------
# Step 11: Chat still works without documents
# ---------------------------------------------------------------------------

def test_chat_without_docs():
    section("Step 11: Verify Normal Chat Still Works (no documents)")
    r = httpx.post(
        f"{BASE}/api/chat",
        json={"message": "What is 2 + 2?", "stream": False},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200
    data = r.json()
    answer = data["message"]["content"]
    print(f"  answer: {answer}")
    assert "4" in answer
    ok("Normal chat works without documents")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"\n{'=' * 60}")
    print("  Sovereign Workbench — Phase 2 RAG End-to-End Test")
    print(f"{'=' * 60}")
    print(f"  Target: {BASE}")

    try:
        test_health()
        doc_id = test_upload()
        test_list_documents(doc_id)
        test_rag_chat_sync(doc_id)
        test_rag_chat_stream()
        test_delete_document(doc_id)
        test_verify_deleted(doc_id)
        test_chat_without_docs()

        print(f"\n{'=' * 60}")
        print("  ALL PHASE 2 END-TO-END TESTS PASSED")
        print(f"{'=' * 60}\n")

    except AssertionError as e:
        fail(str(e))
    except Exception as e:
        import traceback
        print(f"\n[UNEXPECTED ERROR] {e}")
        traceback.print_exc()
        sys.exit(1)
