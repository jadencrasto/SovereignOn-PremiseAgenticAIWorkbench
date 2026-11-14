"""Fail-closed integration checks; no native storage or service is started."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from backend.api.artifacts import list_artifacts, preview_artifact, download_artifact
from backend.auth.request_context import effective_owner, effective_clearance
from backend.rag.service import DocumentService
from backend.security.execution_policy import require_protected_storage_writer
from backend.security.provenance_foundation import QuarantinedSources
from test_a21_conversation_offline import immediate


def test_document_cannot_write_with_fabricated_storage_flags(monkeypatch):
    monkeypatch.setattr('backend.rag.store.persistent_security_guarantees_available', lambda: True)
    service = DocumentService.__new__(DocumentService)
    service._source_registry = MagicMock()
    service._store = MagicMock(backend_status='verified_persistent')
    service._settings = SimpleNamespace(max_file_size_mb=1)
    with pytest.raises(RuntimeError, match='Verified persistent index unavailable'):
        immediate(service.ingest_document('fixture.txt', b'synthetic'))
    service._source_registry.reserve_document.assert_not_called()
    service._store._maintenance_document_chunk_count.assert_not_called()
    with pytest.raises(TypeError): require_protected_storage_writer({'verified': True})


def test_revocation_does_not_touch_untrusted_index_or_file(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.rag.store.persistent_security_guarantees_available', lambda: True)
    token = effective_owner.set('owner'); clearance = effective_clearance.set('operator')
    try:
        registry = QuarantinedSources(tmp_path/'sources.sqlite'); sid = registry.reserve_document(b'synthetic')
        store = MagicMock(backend_status='verified_persistent')
        service = DocumentService.__new__(DocumentService)
        service._source_registry = registry; service._store = store
        service._settings = SimpleNamespace(upload_dir=tmp_path)
        with pytest.raises(RuntimeError, match='cleanup unavailable'):
            service.delete_document(sid)
        assert registry.read(sid)['state'] == 'revoked'
        store._maintenance_document_chunk_count.assert_not_called()
        store.delete_document.assert_not_called()
        assert not registry.retrieval_eligible(sid)
    finally:
        effective_clearance.reset(clearance); effective_owner.reset(token)


def test_blocked_artifact_list_does_not_create_or_scan_directory(tmp_path):
    path = tmp_path/'uncreated'
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(sandbox_dir=path))))
    with pytest.raises(HTTPException) as result:
        immediate(list_artifacts(request, MagicMock()))
    assert result.value.status_code == 503
    assert result.value.headers['X-Error-Code'] == 'ARTIFACT_UNAVAILABLE'
    assert not path.exists()


@pytest.mark.parametrize('endpoint', [preview_artifact, download_artifact])
def test_artifact_content_cannot_be_read_by_owner_or_admin_without_provenance(tmp_path, endpoint):
    path = tmp_path/'uncreated'
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(sandbox_dir=path))))
    for role in ('operator', 'admin'):
        with pytest.raises(HTTPException) as result:
            immediate(endpoint('synthetic.pdf', request, SimpleNamespace(role=role, id='owner')))
        assert result.value.status_code == 404
        assert not path.exists()


def test_artifact_dispatch_uses_shared_denial_before_approval_consumption(monkeypatch):
    from backend.tools.registry import ToolDefinition, ToolRegistry
    from backend.tools.docx_create import DocxCreateInput
    called = MagicMock(side_effect=PermissionError('synthetic'))
    monkeypatch.setattr('backend.security.execution_policy.require_protected_storage_writer', called)
    async def generator(args): pytest.fail('blocked generator invoked')
    registry = ToolRegistry(); store = MagicMock(); store.get_artifact.return_value = None
    approval = MagicMock(); approval.verify_approval_for_execution.return_value = True
    registry.set_task_store(store); registry.set_approval_manager(approval)
    registry.register(ToolDefinition(name='docx_create', description='fixture', input_schema=DocxCreateInput,
        execute_fn=generator, read_only=False, requires_approval=True))
    token = effective_owner.set('owner')
    try:
        result = immediate(registry.execute('docx_create', {'filename':'x.docx','title':'Report','content':'synthetic'},
            user_id='owner', user_role='admin', task_id='task', step_id='step', approval_id='approval'))
        assert result.success is False and result.error == 'Verified artifact generation is unavailable.'
        called.assert_called_once_with()
        approval.consume_approval_for_execution.assert_not_called()
        store.save_artifact.assert_not_called()
    finally: effective_owner.reset(token)
