from tempfile import SpooledTemporaryFile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, UploadFile
from backend.api.documents import upload_document
from backend.auth.models import User
from backend.rag.service import DocumentService
from test_a21_conversation_offline import immediate


def test_upload_route_denies_without_verified_persistence():
    service = DocumentService.__new__(DocumentService)
    service._settings = SimpleNamespace(max_file_size_mb=1)
    service._source_registry = MagicMock()
    service._store = MagicMock(backend_status='unverified')
    user = User(id='synthetic', username='synthetic', password_hash='', role='operator', created_at='2026-01-01')
    data = SpooledTemporaryFile(max_size=1024)
    data.write(b'synthetic')
    data.seek(0)
    uploaded = UploadFile(filename='synthetic.txt', file=data)
    with pytest.raises(HTTPException) as error:
        immediate(upload_document(uploaded, service, user))
    assert error.value.status_code == 503
    assert error.value.headers['X-Error-Code'] == 'DOCUMENT_UNAVAILABLE'
    assert uploaded.file.closed
    service._source_registry.reserve.assert_not_called()
