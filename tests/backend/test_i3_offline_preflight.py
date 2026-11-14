from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace

from backend.utils.offline_preflight import inspect_prerequisites


def config(root):
    return SimpleNamespace(app_env='development', backend_host='127.0.0.1', backend_port=8000,
        auth_enabled=False, auth_dev_override=False, ollama_base_url='http://127.0.0.1:11434',
        ollama_default_model='qwen2.5:7b', upload_dir=root/'upload', log_dir=root/'logs',
        sandbox_dir=root/'sandbox', chroma_persist_dir=root/'chroma', tasks_dir=root/'tasks')


def test_missing_storage_is_reported_without_creation(tmp_path):
    cfg = config(tmp_path)
    result = inspect_prerequisites(cfg, version_lookup=lambda _: '1.0')
    assert result['ready'] is False
    assert any(c['name'] == 'storage:upload_dir' and c['state'] == 'fail' for c in result['checks'])
    assert list(tmp_path.iterdir()) == []
    assert next(c for c in result['checks'] if c['name'] == 'security_prerequisites')['state'] == 'unknown'


def test_existing_storage_does_not_imply_verified_security(tmp_path):
    cfg = config(tmp_path)
    for name in ('upload_dir', 'log_dir', 'sandbox_dir', 'chroma_persist_dir', 'tasks_dir'):
        getattr(cfg, name).mkdir()
    result = inspect_prerequisites(cfg, version_lookup=lambda _: '1.0')
    assert result['ready'] is False
    assert all(c['state'] == 'unknown' for c in result['checks'] if c['name'].startswith('storage:'))


def test_invalid_inputs_and_missing_dependency_fail_safely(tmp_path):
    cfg = config(tmp_path)
    cfg.app_env='production'; cfg.auth_enabled=False; cfg.auth_dev_override=True
    cfg.ollama_base_url='http://user:secret@localhost:11434'
    cfg.ollama_default_model=''; cfg.backend_port=0
    def missing(_): raise PackageNotFoundError('synthetic')
    result = inspect_prerequisites(cfg, version_lookup=missing)
    checks = {c['name']: c['state'] for c in result['checks']}
    assert all(checks[name] == 'fail' for name in ('identity_configuration', 'ollama_endpoint', 'model_configuration', 'backend_port', 'dependency:fastapi'))
    assert 'secret' not in str(result)
    assert list(tmp_path.iterdir()) == []


def test_external_binding_requires_authenticated_non_override_mode(tmp_path):
    cfg = config(tmp_path); cfg.backend_host='0.0.0.0'
    assert inspect_prerequisites(cfg, version_lookup=lambda _: '1')['checks'][0]['state'] == 'fail'
