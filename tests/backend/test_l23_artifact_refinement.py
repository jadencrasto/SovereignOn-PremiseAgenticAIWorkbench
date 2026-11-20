"""Owned conversational revisions, evidence checks, and genuine output."""

import asyncio
import io
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader

from backend.auth.models import User
from backend.auth.security import hash_password
from backend.models.base import ChatChunk
from test_l2_local_turns import api_client, cancel_path, history_path, login, new_turn, parse_events, workbench
from test_l3_durable_history import durable_environment
from test_l9_local_knowledge import upload
from test_l15_local_documents import _import
from test_l16_local_images import VISION, attach, vision_inventory, wire_provider
from test_l17_grounded_drafts import converse, export
from test_l20_application_workflow import workbook


async def source(client, label, text, previous=None):
    response = await upload(client, label, text.encode('utf-8'), previous=previous)
    assert response.status_code in (200, 201), response.text
    return response.json()


async def one_draft(client, session):
    rows = (await client.get(history_path(session) + '/drafts')).json()['drafts']
    assert len(rows) == 1
    return rows[0]


async def create_comparison(client, session, sources):
    await converse(client, session, 'Compare the operating temperatures of P-101 and P-102 and create a report.',
                   knowledge_source_ids=[row['source_id'] for row in sources])
    draft = await one_draft(client, session)
    assert len([row for row in draft['findings'] if row['kind'] == 'source_value']) == 2, draft
    return draft


def request_edit(session, text, draft=None, **fields):
    target = {} if draft is None else {'draft_target': {'id': draft['id'], 'revision': draft['revision']}}
    return {'session_id': session['session_id'], 'turn_id': str(uuid4()), 'message': text, **target, **fields}


async def refine(client, session, text, draft=None, **fields):
    command = request_edit(session, text, draft, **fields)
    result = await client.post('/api/chat', json=command)
    assert result.status_code == 200 and parse_events(result)[-1]['type'] == 'done', result.text
    current = await one_draft(client, session)
    if draft:
        assert current['id'] == draft['id'] and current['revision'] == draft['revision'] + 1
    assert current['state'] == 'draft'
    return command, current


async def workbook_sources(client):
    responses = [await _import(client, f'{tag}.xlsx', workbook(tag, value))
                 for tag, value in [('P-101', 80), ('P-102', 90)]]
    assert all(row.status_code == 201 for row in responses), [row.text for row in responses]
    return [row.json() for row in responses]


def test_chat_refines_same_grounded_draft_and_reopens_pinned_docx_and_xlsx(workbench, capsys):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            inputs = await workbook_sources(client)
            session = await new_turn(client)
            original = await create_comparison(client, session, inputs)
            path = f"{history_path(session)}/drafts/{original['id']}"
            assert (await client.post(path + '/approve', json={'revision': 1})).status_code == 200
            short_turn, short = await refine(client, session, 'Make the summary shorter.')
            assert len(short['summary']) < len(original['summary'])
            assert short['id'] == original['id'] and short['findings'] == original['findings']
            assert (await client.post(path + '/export', json={'revision': 2})).status_code == 409
            earlier = (await client.get(path + '/revisions/1')).json()
            assert earlier['historical'] and earlier['state'] == 'approved'
            assert (await client.post('/api/chat', json=short_turn)).status_code == 200
            assert (await one_draft(client, session))['revision'] == 2
            assert (await client.post('/api/chat', json=request_edit(session, 'Use a formal tone.', original))).status_code == 409

            _, current = await refine(client, session, 'Add a table comparing the operating temperatures.', short)
            assert current['layout']['comparison']
            _, current = await refine(client, session, 'Add a conclusion.', current)
            _, current = await refine(client, session, 'Move the conclusion first.', current)
            assert current['layout']['sections'][0]['id'] == 'conclusion'
            _, current = await refine(client, session, 'Add recommendations: Review the original worksheets.', current)
            _, current = await refine(client, session, 'Remove the recommendations section.', current)
            _, current = await refine(client, session, 'Include the source citations below each finding.', current)
            assert current['layout']['inline_references']
            assert len(current['history']) == 5
            assert (await client.get(path + '/revisions/1')).status_code == 404
            doc = Document(io.BytesIO(await export(client, path, current['revision'])))
            assert [p.text for p in doc.paragraphs if p.style.name == 'Heading 1'][0] == 'Conclusion'
            assert len(doc.tables) == 2
            compare_cells = [cell.text for row in doc.tables[1].rows for cell in row.cells]
            assert '80' in compare_cells and '90' in compare_cells
            assert any('C2' in cell and 'P-101.xlsx' in cell for cell in compare_cells)
            assert all('Recommendations' != p.text for p in doc.paragraphs)
            assert (await client.post(path + '/approve', json={'revision': current['revision'] - 1})).status_code == 409

            _, current = await refine(client, session, 'Change the format to XLSX.', current)
            _, current = await refine(client, session, 'Move reference column first.', current)
            _, current = await refine(client, session, 'Rename reference column to Source cell', current)
            assert current['layout']['columns'][0] == 'Source cell'
            stale_export = current['revision'] - 3
            assert (await client.get(path + '/download', params={'revision': stale_export})).status_code == 409
            file = load_workbook(io.BytesIO(await export(client, path, current['revision'])), read_only=True)
            try:
                assert 'Comparison' in file.sheetnames
                rows = list(file['Comparison'].values)
                assert rows[3][0] == 'Source cell'
                assert 'P-101.xlsx' in rows[4][0] and 'C2' in rows[4][0]
                assert all(cell.data_type != 'f' for row in file['Comparison'].iter_rows() for cell in row)
            finally:
                file.close()

    asyncio.run(exercise())


def test_verified_new_finding_and_stale_source_regeneration_export_ascii_pdf(workbench, capsys):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            inputs = await workbook_sources(client)
            third = await source(client, 'pressure.txt', 'P-303 normal pressure is 8 bar.')
            session = await new_turn(client)
            draft = await create_comparison(client, session, inputs)
            path = f"{history_path(session)}/drafts/{draft['id']}"
            invalid = request_edit(session, 'Add a finding: What is the weight of V-999?', draft,
                                   knowledge_source_ids=[third['source_id']])
            error = parse_events(await client.post('/api/chat', json=invalid))[-1]
            assert error['error_code'] == 'LOCAL_DRAFT_UNSUPPORTED_EVIDENCE'
            assert await one_draft(client, session) == draft
            _, current = await refine(client, session, 'Add a finding: What is normal pressure for P-303?', draft,
                                      knowledge_source_ids=[third['source_id']])
            added = next(row for row in current['findings'] if row.get('source', {}).get('label') == 'pressure.txt')
            assert added['kind'] == 'source_value' and added['source']['revision'] == 1 and '8' in added['text']
            assert (await client.post(path + '/approve', json={'revision': current['revision']})).status_code == 200
            _, changed = await refine(client, session, 'Change finding 1 to A user estimate of 999.', current)
            assert changed['findings'][0]['kind'] == 'user_edited' and changed['change']['unverified'] == 1
            assert changed['findings'][0]['source'] == current['findings'][0]['source']
            assert (await client.post(path + '/export', json={'revision': changed['revision']})).status_code == 409
            replacement = await source(client, 'pressure.txt', 'P-303 normal pressure is 11 bar.', third)
            stale = await one_draft(client, session)
            assert stale['state'] == 'stale'
            prior = (await client.get(path + f"/revisions/{current['revision']}")).json()
            assert all(row.get('source', {}).get('label') != 'pressure.txt' for row in prior['findings'])
            assert (await client.post(path + '/approve', json={'revision': stale['revision']})).status_code == 409
            _, current = await refine(client, session,
                                      'Regenerate the draft from selected sources: What is normal pressure for P-303?',
                                      stale, knowledge_source_ids=[replacement['source_id']])
            assert all(row['source']['revision'] == 2 for row in current['findings'])
            _, current = await refine(client, session, 'Change the format to PDF.', current)
            doc = PdfReader(io.BytesIO(await export(client, path, current['revision'])))
            text = '\n'.join(page.extract_text() for page in doc.pages)
            assert '11 bar' in text and 'pressure.txt' in text and 'revision 2 SHA-256' in text
            assert '999' not in text
            assert (await client.get('/api/documents')).status_code == 503
            assert (await client.post('/api/chat', files={'file': ('x.pdf', b'%PDF-')})).status_code == 415

    asyncio.run(exercise())


def test_owned_target_clarification_and_explicit_image_provenance(workbench, capsys, monkeypatch, vision_inventory):
    async def respond(request):
        return httpx.Response(200, json={'model': VISION, 'done': True, 'message': {
            'role': 'assistant', 'content': 'A blue surface is visible. Its condition is uncertain.',
        }})

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            inputs = await workbook_sources(client)
            session = await new_turn(client)
            draft = await create_comparison(client, session, inputs)
            await converse(client, session, 'Compare the operating temperatures of P-101 and P-102 and create a report.',
                           knowledge_source_ids=[row['source_id'] for row in inputs])
            ambiguous = await client.post('/api/chat', json=request_edit(session, 'Make it shorter.'))
            assert ambiguous.status_code == 409 and 'Choose which draft' in ambiguous.text
            other_session = await new_turn(client)
            assert (await client.post('/api/chat', json=request_edit(other_session, 'Make it shorter.', draft))).status_code == 404

            wire_provider(workbench, monkeypatch, respond)
            photo = request_edit(session, 'Describe this image.')
            image = await attach(client, photo)
            assert parse_events(await client.post('/api/chat', json=photo))[-1]['type'] == 'done'
            command = request_edit(session, 'Add the visual observations.', draft, draft_turn_ids=[photo['turn_id']])
            assert parse_events(await client.post('/api/chat', json=command))[-1]['type'] == 'done'
            path = f"{history_path(session)}/drafts/{draft['id']}"
            current = (await client.get(path + '/revisions/2')).json()
            observation, = [row for row in current['findings'] if row['kind'] == 'visual_interpretation']
            assert observation['image']['sha256'] == image['sha256'] and observation.get('source') is None
            assert (await client.delete(history_path(session) + f"/images/{image['attachment_id']}")).status_code == 200
            assert (await client.post(path + '/approve', json={'revision': 2})).status_code == 409
            assert all(row.get('image') is None for row in (await client.get(path + '/revisions/2')).json()['findings'])
            workbench.state.auth_store.create_user(User(
                id=str(uuid4()), username='draft-outsider', password_hash=hash_password('localTest123$'), role='viewer',
                clearance='L1', created_at=datetime.now(timezone.utc).isoformat(),
            ))
            async with api_client(workbench) as outsider:
                assert (await outsider.post('/api/auth/login', json={'username': 'draft-outsider', 'password': 'localTest123$'})).status_code == 200
                assert (await outsider.get(path + '/revisions/1')).status_code == 403
                assert (await outsider.post('/api/chat', json=request_edit(session, 'Make it shorter.', draft))).status_code == 404

    asyncio.run(exercise())


def test_shorter_after_an_ordinary_chat_reply_does_not_edit_a_draft(workbench, capsys, monkeypatch):
    async def reply(request):
        answer = 'Short answer.' if request.messages[-1].content == 'Make it shorter.' else 'A longer ordinary response.'
        yield ChatChunk(answer, True)

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            monkeypatch.setattr(workbench.state.model_router.get_provider('ollama'), 'chat_stream', reply)
            session = await new_turn(client)
            draft = await create_comparison(client, session, await workbook_sources(client))
            await converse(client, session, 'What is a haiku?')
            await converse(client, session, 'Make it shorter.')
            messages = (await client.get(history_path(session))).json()['messages']
            assert messages[-1]['content'] == 'Short answer.'
            assert (await one_draft(client, session)) == draft

    asyncio.run(exercise())


def test_restart_reconciles_saved_unacknowledged_revision_exactly_once(durable_environment, capsys, monkeypatch):
    _, make_app = durable_environment

    async def exercise():
        app = make_app('durable')
        async with app.router.lifespan_context(app), api_client(app) as client:
            await login(client, capsys)
            inputs = await workbook_sources(client)
            session = await new_turn(client)
            draft = await create_comparison(client, session, inputs)
            path = f"{history_path(session)}/drafts/{draft['id']}"
            command = request_edit(session, 'Make the summary shorter.', draft)

            def interrupted(*args, **kwargs):
                raise RuntimeError('fixture: interrupted after saving draft revision')

            with monkeypatch.context() as fixture:
                fixture.setattr(app.state.engine._memory, 'record_plain_response', interrupted)
                assert parse_events(await client.post('/api/chat', json=command))[-1]['type'] == 'error'
            before = await one_draft(client, session)
            assert before['revision'] == 1 and before['pending_revision']
            assert (await client.post(path + '/approve', json={'revision': 2})).status_code == 409

        app = make_app('durable')
        async with app.router.lifespan_context(app), api_client(app) as client:
            await login(client, capsys)
            assert (await one_draft(client, session))['pending_revision']
            attempt = {**command, 'attempt': 2}
            for _ in range(2):
                assert parse_events(await client.post('/api/chat', json=attempt))[-1]['type'] == 'done'
            current = await one_draft(client, session)
            assert current['revision'] == 2 and not current['pending_revision']
            assert current['history'] == [1] and current['revision_turn_id'] == command['turn_id']
            assert (await client.get(path + '/revisions/1')).json()['findings'] == draft['findings']
            assert len(Document(io.BytesIO(await export(client, path, 2))).tables) == 1

    asyncio.run(exercise())


@pytest.mark.parametrize('cancel', [True, False])
def test_stop_or_source_change_at_commit_fence_leaves_no_revision(workbench, capsys, monkeypatch, cancel):
    original_sleep = asyncio.sleep

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            inputs = [await source(client, 'P-101.txt', 'P-101 normal operating temperature is 80 \N{DEGREE SIGN}C.'),
                      await source(client, 'P-102.txt', 'P-102 normal operating temperature is 90 \N{DEGREE SIGN}C.')]
            session = await new_turn(client)
            draft = await create_comparison(client, session, inputs)
            command = request_edit(session, 'Make the summary shorter.', draft)
            command['attempt'] = 1
            paused, resume = asyncio.Event(), asyncio.Event()

            async def pause(delay):
                run = workbench.state.local_chat_runs.active.get((session['session_id'], command['turn_id'], 1))
                if delay == 0 and run and run.task is asyncio.current_task():
                    paused.set()
                    await resume.wait()
                await original_sleep(delay)

            with monkeypatch.context() as fixture:
                fixture.setattr(asyncio, 'sleep', pause)
                task = asyncio.create_task(client.post('/api/chat', json=command))
                try:
                    await asyncio.wait_for(paused.wait(), 4)
                    assert (await one_draft(client, session))['revision'] == 1
                    if cancel:
                        assert (await client.post(cancel_path(command))).status_code == 200
                    else:
                        await source(client, 'P-101.txt', 'P-101 normal operating temperature is 82 \N{DEGREE SIGN}C.', inputs[0])
                finally:
                    resume.set()
                    response = await task
            final = parse_events(response)[-1]
            assert final['type'] == 'error' and final['error_code'] == ('TURN_CANCELLED' if cancel else 'LOCAL_KNOWLEDGE_CHANGED')
            assert (await one_draft(client, session))['revision'] == 1

    asyncio.run(exercise())
