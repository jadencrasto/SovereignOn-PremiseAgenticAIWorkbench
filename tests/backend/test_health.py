"""
tests/backend/test_health.py
----------------------------
Phase 7 tests for Health & Readiness Observability routes.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

from backend.main import app


@pytest.mark.asyncio
async def test_liveness_probe():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health/live")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "alive"
        assert "timestamp" in data


@pytest.mark.asyncio
async def test_readiness_probe_success():
    transport = ASGITransport(app=app)
    mock_tags = {"models": [{"name": "qwen2.5:7b"}, {"name": "nomic-embed-text"}, {"name": "llava:7b"}]}
    
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_tags

    orig_get = AsyncClient.get

    async def mock_get_fn(self, url, *args, **kwargs):
        if "/api/tags" in str(url):
            return mock_resp
        return await orig_get(self, url, *args, **kwargs)

    with patch.object(AsyncClient, "get", mock_get_fn):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/health/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert data["ready"] is True
            comp_names = [c["name"] for c in data["components"]]
            assert "sqlite_database" in comp_names
            assert "sandbox_filesystem" in comp_names
            assert "ollama_models" in comp_names
