"""Upload, confirm and resume through real local ASGI intake and workflow routes."""

import asyncio
import io
import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from docx import Document

from backend.auth.models import User
from backend.auth.security import hash_password
from backend.config import Settings
from backend.main import create_app
from test_l2_local_turns import api_client, cancel_path, history_path, login, new_turn, workbench
from test_l3_durable_history import durable_environment
from test_l9_local_knowledge import upload
from test_l15_local_documents import _docx, _pdf, _xlsx
from test_l16_local_images import VISION, image_bytes, vision_inventory, wire_provider
from test_l17_grounded_drafts import export
from test_l18_local_workflow import preview, source
from test_l19_workflow_recovery import assert_done, reply, turn


OBJECTIVE = 'Compare these maintenance documents for operating pressure and prepare a cited DOCX report.'


async def supply(client, body, filename, content, *, revision=1, kind='document'):
    return await client.post(f"{history_path(body)}/turns/{body['turn_id']}/clarify/attachment",
                             params={'revision': revision, 'kind': kind, 'filename': filename}, content=content,
                             headers={'Content-Type': 'application/octet-stream'})


async def pause(client, objective=OBJECTIVE, **inputs):
    body = await new_turn(client, message=objective)
    body.update(inputs)
    assert_done(await client.post('/api/chat', json=body))
    paused = await turn(client, body)
    assert paused['state'] == 'waiting_clarification' and paused['plan_revision'] == 1
    return body, paused['clarification']


@pytest.mark.parametrize(('extension', 'content', 'tag', 'attribute', 'existing_value', 'uploaded_value'), [
    ('txt', lambda: b'P-502 normal operating pressure is 12 bar.', 'P-502', 'pressure', '8 bar', '12 bar'),
    ('md', lambda: b'# Limits\n\nP-502 normal operating pressure is 12 bar.', 'P-502', 'pressure', '8 bar', '12 bar'),
    ('pdf', _pdf, 'P-501', 'pressure', '16 bar', '8 bar'),
    ('docx', _docx, 'P-502', 'pressure', '8 bar', '12 bar'),
    ('xlsx', _xlsx, 'P-503', 'temperature', '44 \N{DEGREE SIGN}C', '53 \N{DEGREE SIGN}C'),
])
def test_documents_are_confirmed_on_the_same_turn_and_cited(
    workbench, capsys, extension, content, tag, attribute, existing_value, uploaded_value,
):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            existing = await source(client, 'original.txt', f'P-500 normal operating {attribute} is {existing_value}.')
            objective = OBJECTIVE.replace('operating pressure', f'normal {attribute} of P-500 and {tag}')
            body, clarification = await pause(client, objective,
                                              knowledge_source_ids=[existing['source_id']])
            assert clarification['reason'] == 'sources' and len(clarification['options']) == 1
            name = f'added.{extension}'
            raw = content()
            added = await supply(client, body, name, raw)
            assert added.status_code == 201, added.text
            pending = added.json()['turn']
            assert pending['state'] == 'waiting_clarification' and pending['attempt'] == 1
            assert pending['plan_revision'] == 2 and 'workflow_progress' not in pending
            entry = added.json()['attachment']
            ref = entry['source_id']
            if extension in {'pdf', 'docx', 'xlsx'}:
                assert entry['document']['format'] == extension
            assert (await supply(client, body, name, raw)).status_code == 409
            if extension == 'txt':
                duplicated = await supply(client, body, 'same-content.txt', raw, revision=2)
                assert duplicated.status_code == 201 and duplicated.json()['attachment']['source_id'] == ref
                assert duplicated.json()['turn']['plan_revision'] == 2
            assert (await turn(client, body))['clarification']['options'][-1] == {'id': ref, 'label': name}
            assert (await client.get(history_path(body) + '/drafts')).json()['drafts'] == []
            assert (await reply(client, body, 1, existing['source_id'], ref)).status_code == 409
            other = await new_turn(client, message='What is the operating pressure?')
            assert (await client.post('/api/chat', json={**other, 'knowledge_source_ids': [ref]})).status_code == 404
            status = (await client.get('/api/chat/local-knowledge')).json()
            assert {item['source_id'] for item in status['sources']} == {existing['source_id']}
            assert_done(await reply(client, body, 2, existing['source_id'], ref))
            final = await turn(client, body)
            assert final['state'] == 'completed' and final['attempt'] == 1 and final['plan_revision'] == 3
            assert {item['source_id'] for item in final['knowledge_scope']['sources']} == {existing['source_id'], ref}
            assert (await reply(client, body, 2, existing['source_id'], ref)).status_code == 409
            path, draft = await preview(client, body)
            finding, = [item for item in draft['findings'] if item['kind'] == 'source_value'
                        and item['source']['source_id'] == ref]
            if extension in {'pdf', 'docx', 'xlsx'}:
                assert finding['field']['document_locator']['format'] == extension
            if extension in {'docx', 'xlsx'}:
                value, unit = uploaded_value.split(' ', 1)
                assert finding['text'] == value and finding['field']['unit'] == unit
            else:
                assert finding['text'] == uploaded_value
            if extension == 'txt':
                document = Document(io.BytesIO(await export(client, path, 1)))
                paragraphs = ' '.join(item.text for item in document.paragraphs)
                tables = ' '.join(cell.text for table in document.tables for row in table.rows for cell in row.cells)
                assert name in paragraphs + tables and uploaded_value in paragraphs + tables
                replacement = await client.put(f'/api/chat/local-knowledge/sources/{ref}', params={'filename': name, 'revision': 1},
                                               content=b'P-502 normal operating pressure is 13 bar.',
                                               headers={'Content-Type': 'text/plain'})
                assert replacement.status_code == 200, replacement.text
                for operation in ('approve', 'export'):
                    assert (await client.post(f'{path}/{operation}', json={'revision': 1})).status_code == 409
                assert (await client.get(path + '/download', params={'revision': 1})).status_code == 409

    asyncio.run(exercise())


def test_invalid_uploads_stay_paused_and_cancel_fences_late_bytes(workbench, capsys):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            body, clarification = await pause(client)
            assert clarification['options'] == []
            assert (await supply(client, body, 'wrong.png', image_bytes(), kind='image')).status_code == 409
            assert (await supply(client, body, 'wrong.png', b'not text')).status_code == 415
            assert (await supply(client, body, 'broken.pdf', b'not a PDF')).status_code == 415
            assert (await supply(client, body, 'large.txt', b'a' * 2049)).status_code == 413
            assert (await turn(client, body))['clarification'] == clarification

            first_chunk = asyncio.Event()
            finish_upload = asyncio.Event()

            async def chunks():
                yield b'P-501 '
                first_chunk.set()
                await finish_upload.wait()
                yield b'normal operating pressure is 8 bar.'

            transfer = asyncio.create_task(supply(client, body, 'late.txt', chunks()))
            try:
                await asyncio.wait_for(first_chunk.wait(), timeout=5)
                stopped = await client.post(cancel_path(body))
                assert stopped.json()['state'] == 'cancelled'
            finally:
                finish_upload.set()
            assert (await transfer).status_code == 409
            assert (await turn(client, body))['state'] == 'cancelled'
            assert (await client.get('/api/chat/local-knowledge')).json()['sources'] == []
            assert not workbench.state.local_knowledge._sources

    asyncio.run(exercise())


def test_records_in_one_selected_file_do_not_require_a_second_file(workbench, capsys):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            existing = await source(client, 'records.txt', 'P-501 normal pressure is 8 bar.\nP-502 normal pressure is 12 bar.')
            body, clarification = await pause(client, 'Compare these equipment records and create a report.',
                                              knowledge_source_ids=[existing['source_id']])
            assert clarification['reason'] == 'measurement'
            assert_done(await reply(client, body, 1, 'pressure'))
            assert (await turn(client, body))['state'] == 'completed'

    asyncio.run(exercise())


def test_registered_selection_survives_durable_restart_but_deleted_file_cannot_continue(durable_environment, capsys):
    _, make_app = durable_environment

    async def exercise():
        original = make_app('durable')
        async with original.router.lifespan_context(original), api_client(original) as client:
            await login(client, capsys)
            body, _ = await pause(client)
            first = await supply(client, body, 'first.txt', b'P-501 normal pressure is 8 bar.')
            assert first.status_code == 201, first.text
            second = await supply(client, body, 'second.txt', b'P-502 normal pressure is 12 bar.', revision=2)
            assert second.status_code == 201, second.text
            left, right = (response.json()['attachment']['source_id'] for response in (first, second))
            expected = second.json()['turn']
        reopened = make_app('durable')
        async with reopened.router.lifespan_context(reopened), api_client(reopened) as client:
            await login(client, capsys)
            assert await turn(client, body) == expected
            assert (await client.delete(f'/api/chat/local-knowledge/sources/{right}', params={'revision': 1})).status_code == 200
            assert (await reply(client, body, 3, left, right)).status_code == 409
            assert (await turn(client, body))['state'] == 'waiting_clarification'
            new = await supply(client, body, 'second.txt', b'P-502 normal pressure is 13 bar.', revision=3)
            assert new.status_code == 201, new.text
            assert_done(await reply(client, body, 4, left, new.json()['attachment']['source_id']))
            assert (await turn(client, body))['attempt'] == 1
            assert (await client.delete(history_path(body))).status_code == 200
            assert not reopened.state.local_knowledge._sources

    asyncio.run(exercise())


def test_retry_recovers_registration_when_the_clarification_commit_was_interrupted(durable_environment, capsys, monkeypatch):
    _, make_app = durable_environment

    async def exercise():
        app = make_app('durable')
        filename, raw = 'note.txt', b'P-501 normal pressure is 8 bar.'
        async with app.router.lifespan_context(app), api_client(app) as client:
            await login(client, capsys)
            body, clarification = await pause(client)

            def unavailable():
                raise sqlite3.OperationalError('fixture: interrupted clarification commit')

            with monkeypatch.context() as failure:
                failure.setattr(app.state.engine._memory, '_save', unavailable)
                assert (await supply(client, body, filename, raw)).status_code == 503
            assert (await turn(client, body))['clarification'] == clarification
            original, = app.state.local_knowledge._sources.values()

        reopened = make_app('durable')
        async with reopened.router.lifespan_context(reopened), api_client(reopened) as client:
            await login(client, capsys)
            recovered = await supply(client, body, filename, raw)
            assert recovered.status_code == 201, recovered.text
            assert recovered.json()['attachment']['source_id'] == original.id
            assert recovered.json()['turn']['plan_revision'] == 2
            assert tuple(reopened.state.local_knowledge._sources) == (original.id,)

    asyncio.run(exercise())


def test_image_restarts_paused_and_stays_bound_to_one_turn(durable_environment, capsys, monkeypatch, vision_inventory):
    _, make_app = durable_environment

    async def respond(request):
        payload = json.loads(request.content)
        assert payload['model'] == VISION
        assert len(payload['messages'][-1]['images']) == 1
        return httpx.Response(200, json={'model': VISION, 'done': True, 'message': {
            'role': 'assistant', 'content': 'Fixture observation: a blue panel is visible.',
        }})

    async def exercise():
        original = make_app('durable')
        async with original.router.lifespan_context(original), api_client(original) as client:
            await login(client, capsys)
            calls = wire_provider(original, monkeypatch, respond)
            note = await source(client, 'inspection.txt', 'P-501 inspection interval is 30 days.')
            body, clarification = await pause(
                client, 'Use this inspection image and the attached document to write an observation summary.',
                knowledge_source_ids=[note['source_id']],
            )
            assert clarification['reason'] == 'image' and clarification['options'] == []
            uploaded = await supply(client, body, 'panel.png', image_bytes(), kind='image')
            assert uploaded.status_code == 201, uploaded.text
            image = uploaded.json()['attachment']
            image_id = image['attachment_id']
            assert not calls and (await client.get(history_path(body) + '/images')).json()['images'] == []
            assert (await client.get(history_path(body) + '/images/' + image_id)).status_code == 200
            assert (await reply(client, body, 2, str(uuid4()))).status_code == 409
        reopened = make_app('durable')
        async with reopened.router.lifespan_context(reopened), api_client(reopened) as client:
            await login(client, capsys)
            calls = wire_provider(reopened, monkeypatch, respond)
            assert await turn(client, body) == uploaded.json()['turn']
            assert_done(await reply(client, body, 2, image_id))
            assert (await turn(client, body))['clarification']['reason'] == 'measurement'
            assert not calls
            assert_done(await reply(client, body, 3, 'inspection_interval'))
            assert len(calls) == 1
            path, draft = await preview(client, body)
            visual, = [finding for finding in draft['findings'] if finding['kind'] == 'visual_interpretation']
            assert visual['image']['sha256'] == image['sha256']
            other = {**body, 'turn_id': str(uuid4()), 'message': 'Describe this image.', 'image_id': image_id}
            assert (await client.post('/api/chat', json=other)).status_code == 404
            assert len(calls) == 1
            assert (await client.delete(history_path(body) + '/images/' + image_id)).status_code == 200
            assert (await client.post(path + '/approve', json={'revision': 1})).status_code == 409

    asyncio.run(exercise())


def test_attachment_route_is_local_viewer_and_conversation_owned(workbench, capsys):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            body, _ = await pause(client)
            assert (await supply(client, {**body, 'turn_id': str(uuid4())}, 'note.txt', b'Note')).status_code == 404
            async with api_client(workbench) as outsider:
                assert (await supply(outsider, body, 'note.txt', b'Note')).status_code == 401
                workbench.state.auth_store.create_user(User(id=str(uuid4()), username='other', role='viewer', clearance='L1',
                                                           password_hash=hash_password('other-pass'),
                                                           created_at=datetime.now(timezone.utc).isoformat()))
                logged_in = await outsider.post('/api/auth/login', json={'username': 'other', 'password': 'other-pass'})
                assert logged_in.status_code == 200, logged_in.text
                assert (await supply(outsider, body, 'note.txt', b'Note')).status_code == 403
            assert (await client.post('/api/chat', files={'file': ('file.pdf', b'%PDF')})).status_code == 415
            assert (await client.get('/api/documents')).status_code == 503
        production = create_app(Settings(_env_file=None, app_env='production', auth_enabled=True,
                                         SOVEREIGNAI_RUN_PROFILE=None))
        async with api_client(production) as client:
            assert (await supply(client, body, 'note.txt', b'Note')).status_code == 404

    asyncio.run(exercise())
