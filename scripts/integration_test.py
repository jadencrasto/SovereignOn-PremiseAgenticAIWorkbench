"""Quick integration test — run after starting the backend server."""

import httpx
import json
import sys

BASE = "http://localhost:8000"


def test_health():
    print("=== GET /api/health ===")
    r = httpx.get(f"{BASE}/api/health", timeout=5.0)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    data = r.json()
    print(json.dumps(data, indent=2))
    assert data["status"] == "ok"
    print("[OK]\n")


def test_chat_sync():
    print("=== POST /api/chat (non-streaming) ===")
    r = httpx.post(f"{BASE}/api/chat", json={
        "message": "In one sentence: what is 2+2?",
        "stream": False,
    }, timeout=60.0)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    print(json.dumps(data, indent=2))
    assert data["message"]["role"] == "assistant"
    print("[OK]\n")
    return data["session_id"]


def test_chat_multi_turn(session_id):
    print("=== POST /api/chat (multi-turn follow-up) ===")
    r = httpx.post(f"{BASE}/api/chat", json={
        "message": "What was my previous question about?",
        "session_id": session_id,
        "stream": False,
    }, timeout=60.0)
    assert r.status_code == 200
    data = r.json()
    print(json.dumps(data, indent=2))
    print("[OK]\n")


def test_chat_stream():
    print("=== POST /api/chat (SSE streaming) ===")
    full_text = []
    session_id = None
    model_used = None

    with httpx.stream("POST", f"{BASE}/api/chat", json={
        "message": "Say hello and describe yourself in two sentences.",
        "stream": True,
    }, timeout=90.0) as r:
        print(f"Status: {r.status_code}")
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
        print("Response: ", end="", flush=True)

        for line in r.iter_lines():
            if line.startswith("data: "):
                chunk = json.loads(line[6:])
                if chunk["type"] == "delta":
                    print(chunk["content"], end="", flush=True)
                    full_text.append(chunk["content"])
                elif chunk["type"] == "done":
                    session_id = chunk.get("session_id")
                    model_used = chunk.get("model_used")
                elif chunk["type"] == "error":
                    print(f"\n[STREAM ERROR] {chunk['content']}")
                    sys.exit(1)

    print(f"\n\nTotal chars: {sum(len(t) for t in full_text)}")
    print(f"session_id: {session_id}")
    print(f"model_used: {model_used}")
    print("[OK]\n")


def test_models():
    print("=== GET /api/models ===")
    r = httpx.get(f"{BASE}/api/models", timeout=10.0)
    assert r.status_code == 200
    data = r.json()
    print(json.dumps(data, indent=2))
    print("[OK]\n")


if __name__ == "__main__":
    print("=" * 60)
    print("Sovereign Workbench — Live Integration Test")
    print("=" * 60)
    print()
    try:
        test_health()
        sid = test_chat_sync()
        test_chat_multi_turn(sid)
        test_chat_stream()
        test_models()
        print("=" * 60)
        print("ALL TESTS PASSED")
        print("=" * 60)
    except Exception as e:
        print(f"\n[FAILED] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
