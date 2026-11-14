"""Production conversation API and persistence, temporary synthetic data only."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import httpx
import pytest
from backend.agent.conversation_registry import ConversationRegistry, require_registry_for_existing_history
from backend.agent.memory import ConversationMemory
from backend.auth.request_context import effective_owner
from test_a21_conversation_offline import immediate, setup_engine, make_app


def test_owned_lifecycle_and_storage_reinitialization(tmp_path):
    engine,_=setup_engine(tmp_path);app=make_app(engine); headers=lambda user:{'Authorization':'Bearer '+user}
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            created=await client.post('/api/chat/sessions',headers=headers('a'))
            assert created.status_code==201
            session_id=created.json()['session_id']
            assert len(session_id)==36
            assert session_id in (await client.get('/api/chat/sessions',headers=headers('a'))).json()['sessions']
            assert session_id not in (await client.get('/api/chat/sessions',headers=headers('b'))).json()['sessions']
            posted=await client.post('/api/chat',headers=headers('a'),json={'session_id':session_id,'message':'Explain pumps','stream':False,'tools_enabled':False})
            assert posted.status_code==200
            history=await client.get(f'/api/chat/sessions/{session_id}',headers=headers('a'))
            assert [m['role'] for m in history.json()['messages']]==['user','assistant']
            assert history.json()['messages'][1]['withheld'] is False
            assert history.json()['created_at'] and history.json()['updated_at']
            renamed=await client.patch(f'/api/chat/sessions/{session_id}/title',headers=headers('a'),json={'title':'Pump notes'})
            assert renamed.status_code==200
            for method,url,kwargs in [
                ('GET',f'/api/chat/sessions/{session_id}',{}),
                ('PATCH',f'/api/chat/sessions/{session_id}/title',{'json':{'title':'bad'}}),
                ('DELETE',f'/api/chat/sessions/{session_id}',{}),
                ('POST','/api/chat',{'json':{'session_id':session_id,'message':'secret','stream':False}}),
            ]:
                denied=await client.request(method,url,headers=headers('b'),**kwargs)
                assert denied.status_code==404 and 'Pump notes' not in denied.text
            engine._memory=ConversationMemory(tmp_path/'memory.json', security_registry=ConversationRegistry(tmp_path/'registry.db'))
            restored=await client.get(f'/api/chat/sessions/{session_id}',headers=headers('a'))
            assert restored.status_code==200 and restored.json()['title']=='Pump notes'
            assert restored.json()['messages'][0]['content']=='Explain pumps'
            assert restored.json()['messages'][1]['withheld'] is True
            assert 'Pumps move fluid.' not in restored.text
            deleted=await client.delete(f'/api/chat/sessions/{session_id}',headers=headers('a'))
            assert deleted.status_code==200
            assert (await client.get(f'/api/chat/sessions/{session_id}',headers=headers('a'))).status_code==404
            assert (await client.post('/api/chat',headers=headers('a'),json={'session_id':session_id,'message':'again','stream':False})).status_code==404
            return session_id
    immediate(run())


def test_missing_registry_is_not_recreated(tmp_path):
    history=tmp_path/'memory.json';db=tmp_path/'missing.db'
    history.write_text('{}',encoding='utf8')
    with pytest.raises(RuntimeError,match='security registry is missing'):
        require_registry_for_existing_history(history,db)
    assert not db.exists()


def test_missing_active_binding_denies_existing_record(tmp_path):
    engine,_=setup_engine(tmp_path);owner=effective_owner.set('a')
    try:
        memory=engine._memory;memory.prepare_new_chat('s','a','Be helpful.')
        with memory._security_registry.path.open('rb') as f: assert f.read(16)
        # A missing binding is simulated by a second, empty registry without altering the original.
        memory._security_registry=ConversationRegistry(tmp_path/'other.db')
        assert 's' not in memory.list_sessions()
        with pytest.raises(PermissionError):memory.get_history('s')
    finally:effective_owner.reset(owner)


def test_interrupted_atomic_replace_keeps_previous_record(tmp_path):
    engine,_=setup_engine(tmp_path);owner=effective_owner.set('a')
    try:
        memory=engine._memory;memory.prepare_new_chat('s','a','Be helpful.');memory.add_user_message('s','first')
        with patch('backend.agent.memory.os.replace',side_effect=OSError('synthetic interruption')):
            with pytest.raises(OSError):memory.add_user_message('s','not committed')
        restarted=ConversationMemory(tmp_path/'memory.json',security_registry=ConversationRegistry(tmp_path/'registry.db'))
        assert [m.content for m in restarted.get_history('s') if m.role=='user']==['first']
    finally:effective_owner.reset(owner)


def test_corrupt_json_fails_closed(tmp_path):
    engine,_=setup_engine(tmp_path);owner=effective_owner.set('a')
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        (tmp_path/'memory.json').write_text('{broken',encoding='utf8')
        with pytest.raises(json.JSONDecodeError):
            ConversationMemory(tmp_path/'memory.json',security_registry=ConversationRegistry(tmp_path/'registry.db'))
    finally:effective_owner.reset(owner)


def test_concurrent_writers_preserve_messages(tmp_path):
    engine,_=setup_engine(tmp_path);owner=effective_owner.set('a')
    try:engine._memory.prepare_new_chat('s','a','Be helpful.')
    finally:effective_owner.reset(owner)
    first=engine._memory
    second=ConversationMemory(tmp_path/'memory.json',security_registry=ConversationRegistry(tmp_path/'registry.db'))
    def write(memory,label):
        token=effective_owner.set('a')
        try:
            for index in range(4):memory.add_user_message('s',f'{label}-{index}')
        finally:effective_owner.reset(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(write,first,'a'),pool.submit(write,second,'b')]
        for future in futures:future.result()
    token=effective_owner.set('a')
    try:
        values=[m.content for m in first.get_history('s') if m.role=='user']
        assert len(values)==8 and len(set(values))==8
        for label in ('a','b'):assert [v for v in values if v.startswith(label)]==[f'{label}-{i}' for i in range(4)]
    finally:effective_owner.reset(token)


def test_chat_rejects_client_chosen_new_identity(tmp_path):
    engine,_=setup_engine(tmp_path);app=make_app(engine)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            headers={'Authorization':'Bearer a'}
            denied=await client.post('/api/chat',headers=headers,json={'session_id':'attacker-choice','message':'hello','stream':False})
            assert denied.status_code==404
            assert 'attacker-choice' not in (await client.get('/api/chat/sessions',headers=headers)).json()['sessions']
            created=await client.post('/api/chat',headers=headers,json={'message':'hello','stream':False,'tools_enabled':False})
            assert created.status_code==200 and len(created.json()['session_id'])==36
    immediate(run())


def test_missing_registry_during_use_does_not_recreate_database(tmp_path):
    registry=ConversationRegistry(tmp_path/'security.db')
    registry.path.unlink()
    with pytest.raises(Exception):registry.rows()
    assert not registry.path.exists()
    with pytest.raises(Exception):registry.begin('new','a')
    assert not registry.path.exists()


def test_duplicate_message_ids_fail_closed(tmp_path):
    path=tmp_path/'memory.json'
    path.write_text(json.dumps({'s':{'owner_id':'a','messages':[{'id':'same','role':'user','content':'a'},{'id':'same','role':'user','content':'b'}]}}),encoding='utf8')
    with pytest.raises(ValueError,match='duplicate'):
        ConversationMemory(path,security_registry=ConversationRegistry(tmp_path/'registry.db'))


def test_active_stream_revocation_prevents_further_release(tmp_path):
    from backend.api.chat import _stream_sse
    engine,_=setup_engine(tmp_path);owner=effective_owner.set('a')
    try:
        memory=engine._memory;identity=memory.prepare_new_chat('s','a','Be helpful.')
        class Provider:
            provider_name='fake'
            async def chat_stream(self,request):
                from backend.models.base import ChatChunk
                yield ChatChunk('visible first text',False)
                yield ChatChunk('secret after revoke',True)
        engine._router.get_provider_for_model=lambda _: (Provider(),'fake')
        async def exercise():
            stream=_stream_sse(engine,'s','hello',None,'fake',guard_initial_identity=identity)
            first=await stream.__anext__()
            memory.delete_session('s')
            rest=[event async for event in stream]
            assert 'delta' in first and all('secret after revoke' not in event for event in rest)
            assert any('Conversation unavailable' in event for event in rest)
        immediate(exercise())
    finally:effective_owner.reset(owner)


def test_separate_process_restores_user_but_not_assistant_proof(tmp_path):
    import subprocess
    import sys
    engine,_=setup_engine(tmp_path);owner=effective_owner.set('a')
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        immediate(engine.chat('s','synthetic question'))
    finally:effective_owner.reset(owner)
    script = r'''
import json,sys
from pathlib import Path
from backend.agent.memory import ConversationMemory
from backend.agent.conversation_registry import ConversationRegistry
from backend.auth.request_context import effective_owner
token=effective_owner.set('a')
try:
    memory=ConversationMemory(Path(sys.argv[1]),security_registry=ConversationRegistry(Path(sys.argv[2])))
    history=memory.get_history('s')
    print(json.dumps({'user':[m.content for m in history if m.role=='user'],
                      'proof':memory.verify_independent_plain_chat('s',history[-1]),
                      'listed':'s' in memory.list_sessions()}))
finally:effective_owner.reset(token)
'''
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path/'memory.json'),str(tmp_path/'registry.db')],capture_output=True,text=True,timeout=15,check=True)
    assert json.loads(result.stdout)=={'user':['synthetic question'],'proof':False,'listed':True}


def test_missing_and_other_owner_deletion_are_indistinguishable(tmp_path):
    engine,_=setup_engine(tmp_path);app=make_app(engine)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            headers={'Authorization':'Bearer b'}
            other=await client.delete('/api/chat/sessions/s',headers=headers)
            missing=await client.delete('/api/chat/sessions/not-a-session',headers=headers)
            assert other.status_code==missing.status_code==404
            assert other.json()==missing.json()=={'detail':'Conversation not found'}
    immediate(run())
