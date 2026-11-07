from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.agent.engine import AgentEngine
from backend.agent.memory import ConversationMemory
from backend.auth.models import resolve_user_clearance
from backend.config import Settings
from backend.models.base import ChatChunk, ChatResponse


class Provider:
    provider_name = 'test'

    async def chat(self, request):
        return ChatResponse(content='ok', model='test', provider='test')

    async def chat_stream(self, request):
        yield ChatChunk(delta='ok', done=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('role,clearance,expected', [
    ('admin', 'L3', 'admin'), ('admin', 'L1', 'viewer'), ('viewer', 'L3', 'viewer'),
])
async def test_plain_chat_retrieval_uses_verified_clearance(role, clearance, expected):
    provider = Provider()
    router = SimpleNamespace(default_model_id='test', get_provider_for_model=lambda _: (provider, 'test'))
    engine = AgentEngine(Settings(), router, ConversationMemory(persist=False))
    engine._retrieve_context = AsyncMock(return_value=[])
    verified = resolve_user_clearance(SimpleNamespace(role=role, clearance=clearance))
    await engine.chat('one', 'fixture question', user_clearance=verified)
    engine._retrieve_context.assert_awaited_with('fixture question', user_clearance=expected)
    engine._retrieve_context.reset_mock()
    async for _ in engine.chat_stream('two', 'fixture question', user_clearance=verified):
        pass
    engine._retrieve_context.assert_awaited_with('fixture question', user_clearance=expected)


@pytest.mark.asyncio
async def test_role_headers_do_not_select_local_principal_by_default():
    from backend.auth.dependencies import get_current_user
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=Settings(auth_enabled=False))),
                              cookies={}, method='GET', state=SimpleNamespace())
    user = await get_current_user(request, bearer=None, x_requested_with=None,
                                  x_user_role='viewer', x_clearance='L1')
    assert user.id == 'user_dev_admin'
