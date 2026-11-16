"""Exercise real session revocation with different credential combinations."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from backend.auth.models import AuthStore, User
from backend.auth.routes import router
from backend.auth.security import SessionManager
from test_a22_async_offline import run_offline


@pytest.mark.parametrize('credential_mode', ['cookie', 'bearer', 'matching', 'different'])
def test_logout_revokes_the_session_used_to_authenticate(tmp_path, credential_mode):
    store = AuthStore(tmp_path / 'auth.db')
    for account in ('alice', 'bob'):
        store.create_user(User(id=account, username=account, password_hash='unused', role='operator',
                               created_at='2026-11-16T00:00:00Z'))
    sessions = SessionManager(store)
    alice, _ = sessions.create_session('alice')
    bob, _ = sessions.create_session('bob')
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(auth_enabled=True, app_env='development', auth_cookie_secure=False,
                                         auth_cookie_domain=None, auth_cookie_samesite='lax')
    app.state.auth_store = store
    app.state.session_manager = sessions

    headers = {}
    if credential_mode != 'bearer':
        headers['Cookie'] = f"session_token={bob if credential_mode == 'different' else alice}"
    if credential_mode != 'cookie':
        headers['Authorization'] = f'Bearer {alice}'

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            signed_in = await client.get('/api/auth/me', headers=headers)
            assert signed_in.status_code == 200 and signed_in.json()['username'] == 'alice'
            if credential_mode == 'cookie':
                assert (await client.post('/api/auth/logout', headers=headers)).status_code == 403
                headers['X-Requested-With'] = 'XMLHttpRequest'
            logged_out = await client.post('/api/auth/logout', headers=headers)
            assert logged_out.status_code == 200
            expired = await client.get('/api/auth/me', headers={'Authorization': f'Bearer {alice}'})
            assert expired.status_code == 401
            still_active = await client.get('/api/auth/me', headers={'Cookie': f'session_token={bob}'})
            assert still_active.status_code == 200 and still_active.json()['username'] == 'bob'
            if credential_mode == 'different':
                assert 'Set-Cookie' not in logged_out.headers
            elif 'Cookie' in headers:
                assert 'Max-Age=0' in logged_out.headers['Set-Cookie']

    run_offline(exercise())
