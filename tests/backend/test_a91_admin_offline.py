from test_a21_conversation_offline import immediate
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.auth.dependencies import get_current_user, require_permission, require_role
from backend.audit.logger import AuditLogger
from backend.auth.models import Permission, User, UserRole, has_permission
from backend.auth.routes import CreateUserRequest, get_my_permissions
from backend.config import Settings
from backend.utils.config_validation import ConfigValidator


def user(role):
    return User(id='synthetic', username='synthetic', password_hash='', role=role, created_at='2026-01-01')


def test_server_role_permissions_and_unknown_fail_closed():
    assert not has_permission('viewer', Permission.MANAGE_USERS)
    assert not has_permission('operator', Permission.MANAGE_USERS)
    assert has_permission('operator', Permission.APPROVE_TASKS)
    assert has_permission('admin', Permission.MANAGE_USERS)
    assert all(not has_permission('manager', p) for p in Permission)


def test_permission_response_and_admin_dependency():
    async def run():
        result = await get_my_permissions(user('operator'))
        assert result['role'] == 'operator' and result['permissions']['manage_users'] is False
        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), url=SimpleNamespace(path='/api/auth/users'))
        with pytest.raises(HTTPException) as error:
            await require_role(UserRole.ADMIN)(req, user('operator'))
        assert error.value.status_code == 403
        assert (await require_role(UserRole.ADMIN)(req, user('admin'))).role == 'admin'
    immediate(run())
    with pytest.raises(ValidationError):
        CreateUserRequest(username='synthetic', password='password123', role='manager')


def test_production_override_cannot_disable_authentication():
    cfg = SimpleNamespace(app_env='prod', auth_enabled=False, auth_dev_override=True)
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=cfg)))
    with pytest.raises(HTTPException) as error:
        immediate(get_current_user(req, bearer=None, x_requested_with=None, x_user_role=None, x_clearance=None))
    assert error.value.status_code == 500


def test_disabled_identity_is_rejected():
    cfg = SimpleNamespace(app_env='production', auth_enabled=True)
    inactive = user('viewer').model_copy(update={'is_active': False})
    state = SimpleNamespace(settings=cfg,
        session_manager=SimpleNamespace(validate_session=lambda token: SimpleNamespace(user_id='synthetic')),
        auth_store=SimpleNamespace(get_user_by_id=lambda uid: inactive))
    request = SimpleNamespace(app=SimpleNamespace(state=state), cookies={}, method='GET')
    with pytest.raises(HTTPException) as error:
        immediate(get_current_user(request, bearer=SimpleNamespace(credentials='synthetic'),
                    x_requested_with=None, x_user_role=None, x_clearance=None))
    assert error.value.status_code == 401


def test_audit_filter_pagination_and_permission(tmp_path):
    audit = AuditLogger(tmp_path / 'audit.sqlite')
    audit.log(event_type='synthetic.allowed', user_id='a', success=True)
    audit.log(event_type='synthetic.denied', user_id='b', success=False)
    result = audit.query_events(limit=1, offset=0, success=False)
    assert result['total'] == 1 and result['events'][0]['user_id'] == 'b'
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), url=SimpleNamespace(path='/api/audit/events'))
    with pytest.raises(HTTPException) as error:
        immediate(require_permission(Permission.VIEW_SECURITY)(request, user('viewer')))
    assert error.value.status_code == 403
    assert immediate(require_permission(Permission.VIEW_SECURITY)(request, user('operator'))).role == 'operator'


@pytest.mark.parametrize('url', ['http://user:secret@localhost:11434', 'http://localhost:bad', 'http://localhost/x', 'http://localhost?token=secret'])
def test_invalid_ollama_endpoint_fails_without_echoing_secrets(monkeypatch, url):
    monkeypatch.setattr(Settings, 'ensure_dirs', lambda self: None)
    cfg = Settings(_env_file=None, ollama_base_url=url)
    valid, checks = ConfigValidator(cfg).validate()
    item = next(x for x in checks if x['rule'] == 'ollama_local_url')
    assert not valid and item['status'] == 'FAIL'
    assert 'secret' not in str(checks)


def test_prod_alias_wildcard_cors_fails(monkeypatch):
    monkeypatch.setattr(Settings, 'ensure_dirs', lambda self: None)
    cfg = Settings(_env_file=None, app_env='prod', auth_enabled=True, cors_origins='*')
    assert next(x for x in ConfigValidator(cfg).validate()[1] if x['rule'] == 'cors_wildcard_in_prod')['status'] == 'FAIL'
