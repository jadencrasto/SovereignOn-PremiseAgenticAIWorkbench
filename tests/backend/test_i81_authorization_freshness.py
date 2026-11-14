"""Deterministic stream handoffs with the production SQLite identity store."""
import json
import pytest

from backend.auth.dependencies import _bind_dispatch_session
from backend.auth.models import AuthStore, User
from backend.auth.security import SessionManager
from backend.auth.request_context import dispatch_identity_resolver, effective_owner, request_tools_enabled
from backend.tools.calculator import execute_calculator
from backend.tools.registry import ToolDefinition
from backend.tools.calculator import CalculatorInput
from unittest.mock import MagicMock
from test_a21_conversation_offline import immediate
from test_a31_planning_offline import setup_workflow


PLAN = json.dumps([
    {'description':'Add', 'tool_name':'calculator', 'arguments':{'expression':'25 + 17'}},
    {'description':'Multiply', 'tool_name':'calculator', 'arguments':{'expression':'@step_1.result * 3'}},
])


@pytest.mark.parametrize('change,after_first,expected', [
    ('viewer', False, []), ('disable', False, []), ('revoke', False, []),
    ('unavailable', False, []), ('viewer', True, ['25 + 17']),
    ('operator', False, ['25 + 17', '(42) * 3']),
    ('none', False, ['25 + 17', '(42) * 3']),
])
def test_dispatch_uses_current_account_and_session(tmp_path, change, after_first, expected):
    calls = []
    async def calculate(args):
        calls.append(args.expression)
        return await execute_calculator(args)
    engine = setup_workflow(tmp_path, PLAN, calculate)
    store = AuthStore(tmp_path / 'auth.db')
    store.create_user(User(id='a', username='a', password_hash='x', role='admin', clearance='L3',
                           is_active=True, must_change_password=False, created_at='2026-01-01T00:00:00+00:00'))
    sessions = SessionManager(store)
    raw_token, _ = sessions.create_session('a')
    owner = effective_owner.set('a'); enabled = request_tools_enabled.set(True)
    resolver_token = dispatch_identity_resolver.set(None)
    try:
        _bind_dispatch_session(sessions, store, raw_token, 'a')
        engine._memory.prepare_new_chat('s', 'a', 'Be helpful.')
        async def run():
            events = []; changed = False
            async for event in engine.run_agent_task('s', 'Calculate and then multiply', user_role='admin', user_id='a'):
                events.append(event)
                if not isinstance(event, dict) or changed:
                    continue
                trigger = event.get('type') == ('tool_result' if after_first else 'plan_created')
                if trigger:
                    changed = True
                    if change in ('viewer', 'operator'):
                        store.update_user('a', role=change)
                    elif change == 'disable':
                        store.update_user('a', is_active=False)
                    elif change == 'revoke':
                        sessions.revoke_session(raw_token)
                    elif change == 'unavailable':
                        def unavailable(_): raise RuntimeError('synthetic unavailable store')
                        store.get_user_by_id = unavailable
            return events
        events = immediate(run())
        assert calls == expected
        terminal = 'task_completed' if change in ('operator', 'none') else 'task_failed'
        assert any(isinstance(e, dict) and e.get('type') == terminal for e in events)
        if change not in ('operator', 'none'):
            assert not any(isinstance(e, dict) and e.get('type') == 'task_completed' for e in events)
    finally:
        dispatch_identity_resolver.reset(resolver_token)
        request_tools_enabled.reset(enabled); effective_owner.reset(owner)


def test_missing_authorization_context_fails_closed(tmp_path):
    calls = []
    async def calculate(args):
        calls.append(args.expression)
        return await execute_calculator(args)
    engine = setup_workflow(tmp_path, PLAN, calculate)
    owner = effective_owner.set('a'); enabled = request_tools_enabled.set(True)
    resolver_token = dispatch_identity_resolver.set(None)
    try:
        engine._memory.prepare_new_chat('s', 'a', 'Be helpful.')
        async def run():
            return [e async for e in engine.run_agent_task('s', 'Calculate then multiply', user_role='admin', user_id='a')]
        events = immediate(run())
        assert not calls
        assert any(isinstance(e, dict) and e.get('type') == 'task_failed' for e in events)
    finally:
        dispatch_identity_resolver.reset(resolver_token)
        request_tools_enabled.reset(enabled); effective_owner.reset(owner)


def test_revocation_and_cancellation_before_dispatch(tmp_path):
    calls = []
    async def calculate(args):
        calls.append(args.expression)
        return await execute_calculator(args)
    engine = setup_workflow(tmp_path, PLAN, calculate)
    store = AuthStore(tmp_path / 'auth.db')
    store.create_user(User(id='a', username='a', password_hash='x', role='admin', clearance='L3',
                           is_active=True, must_change_password=False, created_at='2026-01-01T00:00:00+00:00'))
    sessions = SessionManager(store); raw_token, _ = sessions.create_session('a')
    owner = effective_owner.set('a'); enabled = request_tools_enabled.set(True)
    resolver_token = dispatch_identity_resolver.set(None)
    try:
        _bind_dispatch_session(sessions, store, raw_token, 'a')
        engine._memory.prepare_new_chat('s', 'a', 'Be helpful.')
        async def run():
            events = []
            async for event in engine.run_agent_task('s', 'Calculate then multiply', user_role='admin', user_id='a'):
                events.append(event)
                if isinstance(event, dict) and event.get('type') == 'plan_created':
                    store.update_user('a', is_active=False)
                    engine._task_manager.cancel_task(event['task_id'])
            return events
        events = immediate(run())
        assert not calls
        assert any(isinstance(e, dict) and e.get('type') == 'task_cancelled' for e in events)
        assert not any(isinstance(e, dict) and e.get('type') == 'task_completed' for e in events)
    finally:
        dispatch_identity_resolver.reset(resolver_token)
        request_tools_enabled.reset(enabled); effective_owner.reset(owner)


def test_invalidated_approval_and_wrong_owner_never_dispatch(tmp_path):
    calls = []
    async def synthetic(args):
        calls.append(args.expression)
        return await execute_calculator(args)
    engine = setup_workflow(tmp_path, PLAN)
    engine._tool_registry.register(ToolDefinition(name='synthetic_approved', description='test',
        input_schema=CalculatorInput, execute_fn=synthetic, requires_approval=True))
    approvals = MagicMock()
    approvals.verify_approval_for_execution.return_value = False
    engine._tool_registry.set_approval_manager(approvals)
    store = AuthStore(tmp_path / 'auth.db')
    store.create_user(User(id='a', username='a', password_hash='x', role='admin', clearance='L3',
                           is_active=True, must_change_password=False, created_at='2026-01-01T00:00:00+00:00'))
    sessions = SessionManager(store); raw_token, _ = sessions.create_session('a')
    owner = effective_owner.set('a'); enabled = request_tools_enabled.set(True)
    resolver_token = dispatch_identity_resolver.set(None)
    try:
        _bind_dispatch_session(sessions, store, raw_token, 'a')
        engine._memory.prepare_new_chat('s', 'a', 'Be helpful.')
        task = engine._task_manager.create_task('s', 'synthetic', user_id='a', user_role='admin')
        async def run():
            common = dict(session_id='s', user_role='admin', task_id=task.task_id, step_id='step_1', approval_id='invalid')
            rejected = await engine._dispatch_tool('synthetic_approved', {'expression':'1+1'}, user_id='a', **common)
            wrong_owner = await engine._dispatch_tool('synthetic_approved', {'expression':'1+1'}, user_id='b', **common)
            return rejected, wrong_owner
        rejected, wrong_owner = immediate(run())
        assert not rejected.success and not wrong_owner.success and calls == []
        assert approvals.verify_approval_for_execution.call_count == 1
        approvals.consume_approval_for_execution.assert_not_called()
    finally:
        dispatch_identity_resolver.reset(resolver_token)
        request_tools_enabled.reset(enabled); effective_owner.reset(owner)
