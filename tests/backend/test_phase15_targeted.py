import asyncio
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, UploadFile

from backend.api.documents import upload_document
from backend.config import Settings
from backend.utils.config_validation import ConfigValidator


def test_binding_requires_auth_for_external_host(tmp_path):
    def result(host, auth):
        cfg = Settings(backend_host=host, auth_enabled=auth, upload_dir=tmp_path / 'u',
                       log_dir=tmp_path / 'l', sandbox_dir=tmp_path / 's',
                       chroma_persist_dir=tmp_path / 'c', tasks_dir=tmp_path / 't')
        return next(r for r in ConfigValidator(cfg).validate()[1] if r['rule'] == 'external_binding_auth')['status']
    assert Settings().backend_host == '127.0.0.1'
    assert result('127.0.0.1', False) == 'PASS'
    assert result('0.0.0.0', False) == 'FAIL'
    assert result('0.0.0.0', True) == 'PASS'


def test_production_override_cannot_disable_auth(tmp_path):
    cfg = Settings(app_env='production', auth_enabled=False, auth_dev_override=True,
                   upload_dir=tmp_path / 'u', log_dir=tmp_path / 'l',
                   sandbox_dir=tmp_path / 's', chroma_persist_dir=tmp_path / 'c',
                   tasks_dir=tmp_path / 't')
    results = ConfigValidator(cfg).validate()[1]
    assert next(r for r in results if r['rule'] == 'prod_auth_enabled')['status'] == 'FAIL'


def test_upload_limit_and_cleanup():
    async def run():
        service = SimpleNamespace(_settings=SimpleNamespace(max_file_size_mb=1), ingest_document=AsyncMock())
        user = SimpleNamespace(role='admin')
        upload = UploadFile(filename='large.txt', file=BytesIO(b'x' * (1024 * 1024 + 1)))
        with pytest.raises(HTTPException) as exc:
            await upload_document(file=upload, doc_service=service, current_user=user)
        assert exc.value.status_code == 413
        assert upload.file.closed
        service.ingest_document.assert_not_awaited()
    asyncio.run(run())


def test_no_remote_fonts():
    root = Path(__file__).resolve().parents[2]
    assert 'fonts.googleapis.com' not in (root / 'frontend/index.html').read_text(encoding='utf8')
    assert 'fonts.googleapis.com' not in (root / 'frontend/src/index.css').read_text(encoding='utf8')
    graph = (root / 'frontend/src/components/graph/CompanyKnowledgeGraph.tsx').read_text(encoding='utf8')
    assert 'Seeded from verified MRPL refinery' not in graph
    assert 'Demonstration topology; not a verified refinery asset registry.' in graph


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
async def test_multimodal_tool_dispatch_respects_flag(monkeypatch, enabled):
    from backend.agent.engine import AgentEngine
    from backend.agent.memory import ConversationMemory
    from backend.models.base import ChatChunk
    from backend.tools.registry import ToolResult
    from backend.multimodal.service import MultimodalService

    class Provider:
        provider_name = 'test'
        async def chat_stream(self, request):
            yield ChatChunk(delta='{"name":"calculator","arguments":{"expression":"1+1"}}', done=True)

    provider = Provider()
    router = SimpleNamespace(default_model_id='test/model',
                             resolve_vision_model=lambda: (provider, 'vision'),
                             resolve_chat_model=lambda: (provider, 'chat'))
    registry = MagicMock()
    registry.execute = AsyncMock(return_value=ToolResult(tool='calculator', success=True, result='2'))
    registry.format_tools_for_prompt.return_value = 'tools'
    registry.list_enabled_tools.return_value = []
    engine = AgentEngine(Settings(), router, ConversationMemory(persist=False), tool_registry=registry)
    engine._max_tool_iterations = 1
    monkeypatch.setattr(MultimodalService, 'analyze_image', AsyncMock(return_value='A machine.'))
    engine._retrieve_context = AsyncMock(return_value=[])
    async for _ in engine.chat_stream_with_tools_multimodal('session', 'describe', 'image',
                                                            user_role='admin', tools_enabled=enabled):
        pass
    assert registry.execute.await_count == int(enabled)


@pytest.mark.parametrize(('passed', 'failed', 'unavailable', 'expected'), [
    (1, 0, 0, 'PASS'), (0, 1, 0, 'FAIL'), (1, 0, 1, 'INCOMPLETE'), (0, 0, 1, 'BLOCKED'),
])
def test_evaluation_status(tmp_path, monkeypatch, passed, failed, unavailable, expected):
    import eval.run_all as runner
    from eval.common import EvaluationSuiteResult
    monkeypatch.setattr(runner, 'RESULTS_DIR', tmp_path)
    suite = EvaluationSuiteResult('test', 'now', 'test', passed + failed + unavailable,
                                  passed, failed, unavailable, 0.0)
    report, md = runner.generate_consolidated_report([suite], {}, 0, expected_suites=1)
    assert json.loads(report.read_text(encoding='utf8'))['overall_status'] == expected
    assert 'Not verified' in md.read_text(encoding='utf8')


def test_missing_evaluation_suite_is_incomplete(tmp_path, monkeypatch):
    import eval.run_all as runner
    from eval.common import EvaluationSuiteResult
    monkeypatch.setattr(runner, 'RESULTS_DIR', tmp_path)
    suite = EvaluationSuiteResult('test', 'now', 'test', 1, 1, 0, 0, 0.0)
    report, _ = runner.generate_consolidated_report([suite], {}, 0)
    assert json.loads(report.read_text(encoding='utf8'))['overall_status'] == 'INCOMPLETE'
