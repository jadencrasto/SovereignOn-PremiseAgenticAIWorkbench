import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import artifacts


@pytest.mark.parametrize('handler', [artifacts.preview_artifact, artifacts.download_artifact])
def test_artifact_path_error_is_not_exposed(monkeypatch, tmp_path, handler):
    monkeypatch.setattr(artifacts, '_is_artifact_accessible', lambda *args: True)
    def fail(*args):
        raise ValueError('SECRET C:/private/storage')
    monkeypatch.setattr(artifacts, 'validate_path_within', fail)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(sandbox_dir=tmp_path))))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(handler('synthetic.txt', request, object()))
    assert caught.value.status_code == 400
    assert 'SECRET' not in str(caught.value.detail)
    assert 'private' not in str(caught.value.detail)


def test_preview_parser_error_is_not_exposed(monkeypatch, tmp_path):
    monkeypatch.setattr(artifacts, '_is_artifact_accessible', lambda *args: True)
    target = tmp_path / 'synthetic.docx'
    target.write_bytes(b'not a document')
    import docx
    def fail(*args):
        raise RuntimeError('SECRET C:/private/storage')
    monkeypatch.setattr(docx, 'Document', fail)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(sandbox_dir=tmp_path))))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(artifacts.preview_artifact(target.name, request, object()))
    assert caught.value.status_code == 422
    assert 'SECRET' not in str(caught.value.detail)
    assert 'private' not in str(caught.value.detail)
