"""Exercise the local history projection through real chat and draft APIs."""

import asyncio
import io
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from docx import Document

from backend.agent import local_workflow
from backend.agent.engine import LocalToolError
from backend.auth.models import User
from backend.auth.security import hash_password
from backend.config import Settings
from backend.main import create_app
from test_l2_local_turns import api_client, history_path, login, new_turn, workbench
from test_l3_durable_history import durable_environment
from test_l16_local_images import VISION, attach, vision_inventory, wire_provider
from test_l17_grounded_drafts import export
from test_l18_local_workflow import source
from test_l19_workflow_recovery import assert_done, reply
from test_l21_paused_attachment import supply
from test_l23_artifact_refinement import one_draft, refine, source as replace_source


LIBRARY = '/api/chat/workflows'
OBJECTIVE = 'Compare normal pressure in the two maintenance reports and prepare a DOCX report.'
DONE = 'completed'
WAITING = 'waiting_clarification'


def path(body):
    return f"{history_path(body)}/workflows/{body['turn_id']}"


async def setup_sources(client):
    rows = [await source(client, f'maintenance-{label}.txt', f'P-101 normal pressure is {value} bar.')
            for label, value in [('first', 8), ('second', 9)]]
    return [row['source_id'] for row in rows]


async def detail(client, body):
    response = await client.get(path(body))
    assert response.status_code == 200, response.text
    return response.json()


def test_history_completion_reuse_and_exact_revision_download(workbench, capsys, monkeypatch):
    dispatch = local_workflow._dispatch
    calls = []

    async def counted(*args):
        calls.append(args[-1].id)
        return await dispatch(*args)

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            source_ids = await setup_sources(client)
            original = await new_turn(client, message=OBJECTIVE)
            original['knowledge_source_ids'] = source_ids
            monkeypatch.setattr(local_workflow, '_dispatch', counted)
            assert_done(await client.post('/api/chat', json=original))
            draft = await one_draft(client, original)
            edit, draft = await refine(client, original, 'Add a conclusion.', draft)
            draft_path = f"{history_path(original)}/drafts/{draft['id']}"
            original_bytes = await export(client, draft_path, draft['revision'])
            assert 'Conclusion' in [p.text for p in Document(io.BytesIO(original_bytes)).paragraphs]
            count = len(calls)
            results = (await client.get(LIBRARY, params={'status': DONE, 'limit': 1})).json()
            assert [row['id'] for row in results['workflows']] == [edit['turn_id']]
            assert results['next_offset'] == 1
            results = (await client.get(LIBRARY, params={'status': DONE, 'limit': 1, 'offset': 1})).json()
            assert [row['id'] for row in results['workflows']] == [original['turn_id']]
            assert results['workflows'][0]['export_recorded']
            assert results['next_offset'] is None
            assert (await client.get(LIBRARY, params={'q': 'NORMAL PRESSURE'})).json()['workflows'][0]['id'] == original['turn_id']
            assert (await client.get(LIBRARY, params={'q': '9 bar'})).json()['workflows'] == []
            assert (await client.get(LIBRARY, params={'limit': 31})).status_code == 422
            saved = await detail(client, original)
            assert saved['state'] == DONE and saved['steps'] == ['retrieve', 'draft']
            assert not saved['can_resume'] and not saved['can_retry']
            assert saved['drafts'][0]['revisions'] == [1]
            assert saved['drafts'][0]['downloadable'] and saved['drafts'][0]['revision'] == 2
            assert (await client.get(draft_path + '/revisions/1')).json()['historical']
            downloaded = await client.get(draft_path + '/download', params={'revision': 2})
            assert downloaded.content == original_bytes
            assert (await client.get(draft_path + '/download', params={'revision': 1})).status_code == 409
            assert (await client.post(path(original) + '/reuse', json={'source_ids': ['a' * 32]})).status_code == 409
            prepared = await client.post(path(original) + '/reuse', json={'source_ids': source_ids})
            assert prepared.status_code == 200, prepared.text
            template = prepared.json()
            assert set(template) == {'turn_id', 'message', 'knowledge_source_ids', 'requires_image_selection'}
            assert template['turn_id'] != original['turn_id'] and template['message'] == original['message']
            assert len(calls) == count
            fresh = await new_turn(client)
            fresh.update({key: template[key] for key in ('turn_id', 'message', 'knowledge_source_ids')})
            preview = await client.post('/api/chat/workflow-preview', json=fresh)
            assert preview.status_code == 200 and len(calls) == count
            assert_done(await client.post('/api/chat', json={**fresh, 'workflow_preview': preview.json()['token']}))
            assert len(calls) > count and (await one_draft(client, fresh))['state'] == 'draft'
            assert len((await client.get(LIBRARY)).json()['workflows']) == 3

            old = next(row for row in saved['sources'] if row['label'] == 'maintenance-first.txt')
            await replace_source(client, old['label'], 'P-101 normal pressure is 10 bar.',
                                 previous={'source_id': old['id'], 'revision': old['revision']})
            stale = await detail(client, original)
            assert not stale['dependencies_current'] and not stale['drafts'][0]['downloadable']
            assert {ref['id'] for ref in stale['sources'] if ref['current']} == {source_ids[1]}
            assert (await client.post(path(original) + '/reuse', json={'source_ids': source_ids})).status_code == 409
            assert (await client.get(draft_path + '/download', params={'revision': 2})).status_code == 409
            assert (await client.delete(history_path(original))).status_code == 200
            assert (await client.get(path(original))).status_code == 404
            assert all(row['session_id'] != original['session_id'] for row in (await client.get(LIBRARY)).json()['workflows'])
            assert len((await client.get('/api/chat/local-knowledge')).json()['sources']) == 2

    asyncio.run(exercise())


def test_two_tabs_observe_same_clarification_and_retry_with_one_execution(workbench, capsys, monkeypatch):
    dispatch = local_workflow._dispatch
    invocations = []

    async def transient(*args):
        invocations.append(args[-1].id)
        if len(invocations) == 1:
            raise LocalToolError('MODEL_TIMEOUT')
        return await dispatch(*args)

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as first, api_client(workbench) as second:
            assert (await first.get(LIBRARY)).status_code == 401
            await login(first, capsys)
            second.cookies.update(first.cookies)
            ids = await setup_sources(first)
            corpus = workbench.state.local_knowledge
            retrieve = corpus.contexts
            retrievals = []

            def count_retrieval(*args):
                retrievals.append(args[-1])
                return retrieve(*args)

            monkeypatch.setattr(corpus, 'contexts', count_retrieval)
            body = await new_turn(first, message=OBJECTIVE)
            assert_done(await first.post('/api/chat', json=body))
            observed = await detail(second, body)
            assert observed['state'] == WAITING and observed['can_resume'] and not observed['can_retry']
            assert observed['plan_revision'] == 1 and observed['clarification']['question']
            assert (await first.get(LIBRARY, params={'status': WAITING})).json()['workflows'][0]['id'] == body['turn_id']
            monkeypatch.setattr(local_workflow, '_dispatch', transient)
            response = await reply(first, body, 1, *ids)
            assert response.status_code == 200
            failed = await detail(second, body)
            assert failed['state'] == 'failed' and failed['can_retry'] and not failed['can_resume']
            assert failed['plan_revision'] == 2 and failed['last_error'] == 'MODEL_TIMEOUT'
            assert 'retrieve' in failed['steps']
            assert (await reply(second, body, 1, *ids)).status_code == 409
            retry = {**body, 'attempt': 2}
            responses = await asyncio.gather(first.post('/api/chat', json=retry), second.post('/api/chat', json=retry))
            assert sorted(response.status_code for response in responses) == [200, 409]
            assert_done(next(response for response in responses if response.status_code == 200))
            assert invocations == ['draft', 'draft'] and len(retrievals) == 1
            completed = await detail(second, body)
            assert completed['state'] == DONE and completed['attempt'] == 2 and not completed['can_retry']

            async def forbidden(*_):
                raise LocalToolError('LOCAL_TOOL_UNAUTHORIZED')

            monkeypatch.setattr(local_workflow, '_dispatch', forbidden)
            blocked = await new_turn(first, message=OBJECTIVE)
            blocked['knowledge_source_ids'] = ids
            assert (await first.post('/api/chat', json=blocked)).status_code == 200
            assert not (await detail(second, blocked))['can_retry']
            assert (await second.post('/api/chat', json={**blocked, 'attempt': 2})).status_code == 403
            transcript = workbench.state.settings.conversation_memory_path
            raw = json.loads(transcript.read_text(encoding='utf-8'))
            saved = raw[blocked['session_id']]['messages'][-1]['local_turn_v1']
            assert saved['workflow_checkpoint']['mac'] != '0' * 64
            saved['workflow_checkpoint']['mac'] = '0' * 64
            transcript.write_text(json.dumps(raw), encoding='utf-8')
            assert (await second.get(path(blocked))).status_code == 503

    asyncio.run(exercise())


def test_deleted_image_and_foreign_owner_cannot_reopen_dependencies(workbench, capsys, monkeypatch, vision_inventory):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as owner:
            await login(owner, capsys)

            async def observation(_):
                return httpx.Response(200, json={'model': VISION, 'done': True, 'message': {
                    'role': 'assistant', 'content': 'A blue panel is visible.',}})

            wire_provider(workbench, monkeypatch, observation)
            note = await source(owner, 'inspection.txt', 'P-501 inspection interval is 30 days.')
            source_id = note['source_id']
            body = await new_turn(owner, message='Find the inspection interval for P-501 in the document, '
                                                 'use this image, and prepare a short report.')
            attachment = await attach(owner, body)
            body.update(knowledge_source_ids=[source_id], image_id=attachment['attachment_id'], model='ollama/' + VISION)
            assert_done(await owner.post('/api/chat', json=body))
            row = await detail(owner, body)
            assert row['image']['current'] and row['dependencies_current'] and row['drafts']
            await owner.delete(f"{history_path(body)}/images/{attachment['attachment_id']}")
            row = await detail(owner, body)
            assert not row['image']['current'] and not row['dependencies_current'] and row['drafts'][0]['stale']
            workbench.state.auth_store.create_user(User(id=str(uuid4()), username='history-outsider', role='viewer',
                                                        clearance='L1', password_hash=hash_password('History24!'),
                                                        created_at=datetime.now(timezone.utc).isoformat()))
            async with api_client(workbench) as other:
                assert (await other.post('/api/auth/login', json={'username': 'history-outsider',
                                                                  'password': 'History24!'})).status_code == 200
                assert (await other.get(LIBRARY)).status_code == 403
                assert (await other.get(path(body))).status_code == 403
                assert (await other.post(path(body) + '/reuse', json={'source_ids': [source_id]})).status_code == 403
                assert (await other.get(f"{history_path(body)}/drafts/{row['drafts'][0]['id']}/download",
                                        params={'revision': 1})).status_code in (403, 404)
            assert (await owner.post('/api/chat', files={'file': ('upload.pdf', b'%PDF')})).status_code == 415
            assert (await owner.get('/api/documents')).status_code == 503
        production = create_app(Settings(_env_file=None, app_env='production', SOVEREIGNAI_RUN_PROFILE=None))
        async with api_client(production) as other:
            assert (await other.get(LIBRARY)).status_code == 404

    asyncio.run(exercise())


def test_turn_scoped_attachment_remains_available_only_to_its_workflow(workbench, capsys):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            original = await source(client, 'registered.txt', 'P-101 normal pressure is 8 bar.')
            body = await new_turn(client, message=OBJECTIVE)
            assert_done(await client.post('/api/chat', json=body))
            supplied = await supply(client, body, 'task-only.txt', b'P-102 normal pressure is 9 bar.')
            assert supplied.status_code == 201
            scoped = supplied.json()['attachment']['source_id']
            revised = await detail(client, body)
            assert revised['can_resume'] and revised['plan_revision'] == 2
            assert_done(await reply(client, body, 2, original['source_id'], scoped))
            completed = await detail(client, body)
            assert completed['state'] == DONE
            assert completed['dependencies_current']
            assert {source['id'] for source in completed['sources'] if source['reusable']} == {original['source_id']}
            assert (await client.post(path(body) + '/reuse', json={'source_ids': [scoped]})).status_code == 409

    asyncio.run(exercise())


def test_separate_process_reopens_durable_library_without_dispatch(durable_environment, capsys):
    script = r"""
import contextlib, io, json, re, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from backend import local_history
from backend.agent import local_workflow
from backend.health import routes as health
from backend.main import create_app
from test_a22_async_offline import run_offline
from test_l1_local_dev import local_settings
from test_l2_local_turns import api_client

root = Path(sys.argv[2])
local_history.history_location = lambda: root / 'retained'
async def no_model(*args, **kwargs):
    return health.ModelObservation('unavailable', 'inventory_unavailable', ())
async def no_dispatch(*args, **kwargs):
    raise AssertionError('History must not dispatch any workflow step')
health.observe_ollama = no_model
local_workflow._dispatch = no_dispatch

async def exercise():
    app = create_app(local_settings(SOVEREIGNAI_LOCAL_HISTORY='durable'), local_frontend=root / 'frontend')
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        async with app.router.lifespan_context(app), api_client(app) as client:
            password = re.search(r"this run's password: (\S+)", output.getvalue())[1]
            assert (await client.post('/api/auth/login', json={'username': 'local_dev', 'password': password})).status_code == 200
            response = await client.get('/api/chat/workflows', params={'limit': 1})
            response.raise_for_status()
            assert response.json()['next_offset'] == 1
            states = {}
            for status in ('completed', 'waiting_clarification'):
                page = (await client.get('/api/chat/workflows', params={'status': status, 'q': 'normal pressure'})).json()
                item, = page['workflows']
                base = '/api/chat/sessions/' + item['session_id']
                view = (await client.get(base + '/workflows/' + item['id'])).json()
                states[status] = (view['id'], view['can_resume'])
                if status == 'completed':
                    draft, = view['drafts']
                    assert draft['downloadable']
                    assert (await client.get(base + '/drafts/' + draft['id'] + '/download', params={'revision': draft['revision']})).content.startswith(b'PK')
                    assert (await client.get(base + '/drafts/' + draft['id'] + '/revisions/1')).json()['historical']
    print(json.dumps(states))
run_offline(exercise())
"""
    root, make_app = durable_environment

    async def seed():
        app = make_app('durable')
        async with app.router.lifespan_context(app), api_client(app) as client:
            await login(client, capsys)
            ids = await setup_sources(client)
            complete = await new_turn(client, message=OBJECTIVE)
            assert_done(await client.post('/api/chat', json={**complete, 'knowledge_source_ids': ids}))
            _, draft = await refine(client, complete, 'Add a conclusion.')
            await export(client, f"{history_path(complete)}/drafts/{draft['id']}", draft['revision'])
            paused = await new_turn(client, message=OBJECTIVE)
            assert_done(await client.post('/api/chat', json=paused))
            # Filter the revision turn out with the original objective in the restart checks.
            return complete, paused

    complete, paused = asyncio.run(seed())
    result = subprocess.run([sys.executable, '-B', '-c', script, str(Path(__file__).resolve().parent), str(root.parent)],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        DONE: [complete['turn_id'], False], WAITING: [paused['turn_id'], True],
    }
