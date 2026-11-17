"""Assistant-history authentication with temporary data and synthetic in-memory keys."""
from copy import deepcopy
import json
from uuid import uuid4

import httpx
import pytest

from backend.agent.conversation_registry import ConversationRegistry
from backend.agent.memory import ConversationMemory
from backend.auth.request_context import effective_owner
from test_a21_conversation_offline import immediate, make_app, setup_engine


@pytest.fixture
def owner():
    token = effective_owner.set('a')
    try:
        yield
    finally:
        effective_owner.reset(token)


def reopen(tmp_path):
    return ConversationMemory(tmp_path / 'memory.json',
                              security_registry=ConversationRegistry(tmp_path / 'registry.db'))


def test_same_key_record_reload_and_key_loss(tmp_path, owner):
    memory = reopen(tmp_path)
    identity = memory.prepare_new_chat('s', 'a')
    for status in (None, 'interrupted', 'failed'):
        message = memory.record_plain_response('s', identity, 'synthetic ' + str(status),
                                               producer='fake/model', status=status)
        assert memory.verify_assistant_provenance('s', message)
    path = tmp_path / 'memory.json'
    saved = path.read_bytes()
    restored = reopen(tmp_path)
    messages = restored.get_history('s')
    assert ['completed', 'interrupted', 'failed'] == [
        m.extra_fields.get('response_state_v1', 'completed') for m in messages
    ]
    assert all(not restored.verify_assistant_provenance('s', m) for m in messages)

    # Simulated protected-provider result only. The store never persists or loads a key.
    key_id, key = memory._provenance_key
    restored._provenance_key = (key_id, bytes(b ^ 1 for b in key))
    assert all(not restored.verify_assistant_provenance('s', m) for m in messages)
    restored._provenance_key = (uuid4().hex, key)
    assert all(not restored.verify_assistant_provenance('s', m) for m in messages)
    restored._provenance_key = memory._provenance_key
    assert all(restored.verify_assistant_provenance('s', m) for m in messages)
    assert path.read_bytes() == saved

    # Simulate retiring the previous key without rewriting its records.
    rotated = reopen(tmp_path)
    latest = rotated.record_plain_response('s', identity, 'new key fixture', producer='fake/model')
    assert rotated.verify_assistant_provenance('s', latest)
    assert all(not rotated.verify_assistant_provenance('s', m) for m in messages)
    assert rotated.get_history('s')[:-1] == messages


def test_v3_tamper_and_order_verification(tmp_path, owner):
    memory = reopen(tmp_path)
    identity = memory.prepare_new_chat('s', 'a')
    first = memory.record_plain_response('s', identity, 'same text', producer='fake/model')
    second = memory.record_plain_response('s', identity, 'same text', producer='fake/model')
    key = 'access_provenance_v1'
    assert first.extra_fields[key]['mac'] != second.extra_fields[key]['mac']
    for field, value in (
        ('key_id', None), ('version', 4), ('mac', 'é' * 64), ('producer', 'other/model'),
        ('kind', 'deterministic_calculation'), ('position', 1),
    ):
        forged = deepcopy(first)
        forged.extra_fields[key][field] = value
        assert not memory.verify_assistant_provenance('s', forged)
    for field, value in (('role', 'user'), ('content', 'modified'), ('created_at', 'other')):
        forged = deepcopy(first)
        setattr(forged, field, value)
        assert not memory.verify_assistant_provenance('s', forged)
    for field, value in (('response_state_v1', 'failed'), ('tool_events_v1', [])):
        forged = deepcopy(first)
        forged.extra_fields[field] = value
        assert not memory.verify_assistant_provenance('s', forged)
    forged = deepcopy(second)
    forged.extra_fields[key] = first.extra_fields[key]
    assert not memory.verify_assistant_provenance('s', forged)

    path = tmp_path / 'memory.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['s']['messages'].reverse()
    path.write_text(json.dumps(data), encoding='utf-8')
    assert all(not memory.verify_assistant_provenance('s', m) for m in memory.get_history('s'))


def test_copy_to_other_conversation_same_position_and_deleted_generation(tmp_path, owner):
    memory = reopen(tmp_path)
    identity = memory.prepare_new_chat('s', 'a')
    answer = memory.record_plain_response('s', identity, 'original', producer='fake/model')
    memory.prepare_new_chat('other', 'a')
    path = tmp_path / 'memory.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['other']['messages'] = deepcopy(data['s']['messages'])
    path.write_text(json.dumps(data), encoding='utf-8')
    copied = memory.get_history('other')[0]
    assert copied.id == answer.id
    assert not memory.verify_assistant_provenance('other', copied)

    assert memory.delete_session('s')
    memory.create_session(session_id='s')
    assert memory.stream_identity('s') != identity
    with pytest.raises(PermissionError):
        memory.record_plain_response('s', identity, 'late', producer='fake/model')
    assert memory.get_history('s') == []


def test_history_route_withholds_forgery_and_requires_current_session(tmp_path, owner):
    engine, _ = setup_engine(tmp_path)
    app = make_app(engine)
    memory = engine._memory
    identity = memory.stream_identity('s')
    valid = memory.record_plain_response('s', identity, 'verified fixture', producer='fake/model')
    memory.add_assistant_message('s', 'unsigned calculator fixture', access_provenance={
        'version': 1, 'kind': 'deterministic_calculation', 'sources': [],
    })
    memory.add_assistant_message('s', 'legacy unsigned fixture')
    memory.add_assistant_message('s', 'old proof fixture', access_provenance={
        'version': 1, 'kind': 'ephemeral_independent_chat', 'sources': [], 'mac': '0' * 64,
    })
    path = tmp_path / 'memory.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    forged = deepcopy(data['s']['messages'][1])
    forged.update(role='user', id='role-altered', content='role forgery fixture')
    data['s']['messages'].append(forged)
    path.write_text(json.dumps(data), encoding='utf-8')

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            url = '/api/chat/sessions/s'
            headers = {'Authorization': 'Bearer a'}
            response = await client.get(url, headers=headers)
            assert response.status_code == 200
            assert [m['withheld'] for m in response.json()['messages']] == [False, True, True, True, True]
            assert valid.content in response.text
            assert 'key_id' not in response.text and valid.extra_fields['access_provenance_v1']['mac'] not in response.text
            assert 'fixture' not in ' '.join(m['content'] for m in response.json()['messages'][1:])
            assert (await client.get(url, headers={'Authorization': 'Bearer b'})).status_code == 404
            app.state.session_manager.validate_session.side_effect = None
            app.state.session_manager.validate_session.return_value = None
            assert (await client.get(url, headers=headers)).status_code == 401
    immediate(exercise())

    data['s']['owner_id'] = 'b'
    path.write_text(json.dumps(data), encoding='utf-8')
    assert not memory.verify_assistant_provenance('s', valid)
    token = effective_owner.set('b')
    try:
        assert not memory.verify_assistant_provenance('s', valid)
    finally:
        effective_owner.reset(token)


def test_finalization_replace_failure_preserves_previous_proof(tmp_path, owner, monkeypatch):
    memory = reopen(tmp_path)
    identity = memory.prepare_new_chat('s', 'a')
    previous = memory.record_plain_response('s', identity, 'previous', producer='fake/model')

    def fail_replace(source, target):
        raise OSError('synthetic replace failure')

    monkeypatch.setattr('backend.agent.memory.os.replace', fail_replace)
    with pytest.raises(OSError, match='synthetic replace failure'):
        memory.record_plain_response('s', identity, 'partial', producer='fake/model', status='failed')
    history = memory.get_history('s')
    assert history == [previous] and memory.verify_assistant_provenance('s', history[0])
    assert list(tmp_path.glob('.memory-*.tmp')) == []
