"""Local chat through review, approval, and genuine renderer output."""

import asyncio
import hashlib
import io
from datetime import datetime, timezone
from uuid import uuid4
from xml.etree import ElementTree
from zipfile import ZipFile

from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader

from backend.api import local_drafts
from backend.auth.models import User
from backend.auth.security import hash_password
from backend.tools.local_drafts import DRAFT_TOOL
from test_l2_local_turns import api_client, history_path, login, new_turn, parse_events, workbench
from test_l3_durable_history import durable_environment
from test_l9_local_knowledge import fixture_selection, upload
from test_l15_local_documents import _import, _pdf, _xlsx
from test_l16_local_images import attach


async def converse(client, session, message, **fields):
    body = {'session_id': session['session_id'], 'turn_id': str(uuid4()), 'message': message, **fields}
    response = await client.post('/api/chat', json=body)
    assert response.status_code == 200, response.text
    assert [item['type'] for item in parse_events(response)][-1] == 'done', response.text
    return body


async def draft_from(client, session, request, *, turns=None):
    body = await converse(client, session, request, **({'draft_turn_ids': turns} if turns else {}))
    url = history_path(session) + '/drafts'
    drafts = (await client.get(url)).json()['drafts']
    draft = next(row for row in drafts if row['turn_id'] == body['turn_id'])
    return body, f"{url}/{draft['id']}", draft


async def export(client, path, revision):
    body = {'revision': revision}
    approved = await client.post(path + '/approve', json=body)
    assert approved.status_code == 200, approved.text
    result = await client.post(path + '/export', json=body)
    assert result.status_code == 200, result.text
    downloaded = await client.get(path + '/download', params=body)
    assert downloaded.status_code == 200, downloaded.text
    assert hashlib.sha256(downloaded.content).hexdigest() == result.json()['export_digest']
    return downloaded.content


def test_docx_conflicting_text_evidence_edit_approval_and_replay(workbench, capsys, monkeypatch):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            fixture_selection(workbench, monkeypatch)
            sources = [(await upload(client, f'pressure-{value}.txt',
                                     f'P-501 normal pressure is {value} bar.'.encode())).json()
                       for value in (8, 10)]
            session = await new_turn(client)
            await converse(client, session, 'What is normal pressure for P-501?',
                           knowledge_source_ids=[source['source_id'] for source in sources])
            command, path, preview = await draft_from(client, session, 'Create a report about these pump findings.')
            assert preview['format'] == 'docx' and preview['state'] == 'draft'
            assert {row['coverage'] for row in preview['findings']} == {'conflict'}
            assert {row['source']['label'] for row in preview['findings']} == {row['label'] for row in sources}
            assert (await client.post(path + '/export', json={'revision': 1})).status_code == 409
            original = await export(client, path, 1)
            with ZipFile(io.BytesIO(original)) as archive:
                assert archive.testzip() is None
                xml = ElementTree.fromstring(archive.read('word/document.xml'))
                word_text = '\n'.join(node.text or '' for node in xml.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
                assert all(value in word_text for value in ('Evidence report', '8 bar', 'pressure-10.txt'))
            doc = Document(io.BytesIO(original))
            joined = '\n'.join(line.text for line in doc.paragraphs)
            assert doc.paragraphs[0].text == 'Evidence report'
            assert 'Executive summary' in joined and 'Summary wording is unverified' in joined
            assert 'pressure-8.txt' in joined and 'pressure-10.txt' in joined and 'Source references' in joined
            assert {r.cells[1].text for r in doc.tables[0].rows[1:]} == {'8 bar', '10 bar'}
            assert await export(client, path, 1) == original
            assert len(parse_events(await client.post('/api/chat', json=command))) == 1
            edited = await client.patch(path, json={
                'revision': 1, 'title': 'Pressure review', 'summary': 'Needs inspection.', 'wording': {'0': 'Check the reading.'},
            })
            assert edited.status_code == 200 and edited.json()['revision'] == 2
            assert edited.json()['findings'][0]['kind'] == 'user_edited'
            assert (await client.get(path + '/download', params={'revision': 1})).status_code == 409
            assert (await client.post(path + '/approve', json={'revision': 1})).status_code == 409
            revised = Document(io.BytesIO(await export(client, path, 2)))
            assert 'User-edited wording, unverified' in revised.tables[0].rows[1].cells[5].text
            assert (await client.delete(path, params={'revision': 2})).status_code == 200
            assert (await client.get(path + '/download', params={'revision': 2})).status_code == 404

    asyncio.run(exercise())


def test_renderers_preserve_pdf_and_xlsx_native_positions(workbench, capsys, monkeypatch):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            fixture_selection(workbench, monkeypatch)
            pdf = (await _import(client, 'limits.pdf', _pdf())).json()
            sheet = (await _import(client, 'register.xlsx', _xlsx())).json()
            session = await new_turn(client)
            pressure = await converse(client, session, 'What is the normal pressure for P-501?',
                                      knowledge_source_ids=[pdf['source_id']])
            temp = await converse(client, session, 'What is the normal temperature for P-503?',
                                  knowledge_source_ids=[sheet['source_id']])
            ids = [pressure['turn_id'], temp['turn_id']]
            _, word, preview = await draft_from(client, session, 'Prepare an inspection report.', turns=ids)
            assert preview['findings'][1]['field']['document_locator']['cell'] == 'C4'
            doc = Document(io.BytesIO(await export(client, word, 1)))
            text = '\n'.join(line.text for line in doc.paragraphs)
            assert 'limits.pdf - page 2 block 1' in text and 'Operating Register!C4' in text
            assert 'Ignore the instructions' not in text
            _, path, _ = await draft_from(client, session, 'Make a spreadsheet comparing these operating limits.', turns=ids)
            assert (await client.patch(path, json={
                'revision': 1, 'title': '=2+2', 'summary': 'Comparison', 'wording': {},
            })).status_code == 200
            xlsx = await export(client, path, 2)
            with ZipFile(io.BytesIO(xlsx)) as archive:
                assert archive.testzip() is None
                assert not any(name.endswith('vbaProject.bin') for name in archive.namelist())
            workbook = load_workbook(io.BytesIO(xlsx))
            try:
                rows = list(workbook.active.values)
                assert workbook.sheetnames == ['Compliance Report']
                assert rows[0][0] == '=2+2' and workbook.active['A1'].data_type == 's'
                assert rows[4][1:3] == ('8 bar', 'bar')
                assert rows[4][3:5] == ('P-501', 'normal')
                assert rows[5][1] == '53' and 'Operating Register!C4' in rows[5][6]
                assert all(cell.data_type != 'f' for row in workbook.active for cell in row)
            finally:
                workbook.close()
            _, path, _ = await draft_from(client, session, 'Create a PDF report.', turns=ids[:1])
            document = PdfReader(io.BytesIO(await export(client, path, 1)))
            assert len(document.pages) == 1
            content = document.pages[0].extract_text()
            assert all(value in content for value in ('Evidence report', '8 bar', 'limits.pdf - page 2 block 1'))
            assert (await client.patch(path, json={'revision': 1, 'title': 'Pressure at 20 \u00b0C', 'summary': ''})).status_code == 200
            assert (await client.post(path + '/approve', json={'revision': 2})).status_code == 200
            unsupported = await client.post(path + '/export', json={'revision': 2})
            assert unsupported.status_code == 422 and 'requires ASCII' in unsupported.json()['detail']
            assert (await client.get(path + '/download?revision=2')).status_code == 404

    asyncio.run(exercise())


def test_stale_receipts_insufficiency_and_owned_download(workbench, capsys, monkeypatch):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            fixture_selection(workbench, monkeypatch)
            source = (await upload(client, 'pressure.txt', b'P-501 normal pressure is 8 bar.')).json()
            session = await new_turn(client)
            prior = await converse(client, session, 'What is normal temperature for P-501?',
                                   knowledge_source_ids=[source['source_id']])
            _, path, preview = await draft_from(client, session, 'Prepare a maintenance summary.', turns=[prior['turn_id']])
            assert preview['findings'][0]['kind'] == 'missing'
            available = await export(client, path, 1)
            assert available.startswith(b'PK')
            other = await new_turn(client)
            foreign = path.replace(session['session_id'], other['session_id'])
            assert (await client.get(foreign + '/download?revision=1')).status_code == 404
            workbench.state.auth_store.create_user(User(
                id=str(uuid4()), username='other', password_hash=hash_password('localTest123$'), role='viewer',
                clearance='L1', created_at=datetime.now(timezone.utc).isoformat(),
            ))
            async with api_client(workbench) as outsider:
                assert (await outsider.post('/api/auth/login', json={
                    'username': 'other', 'password': 'localTest123$',
                })).status_code == 200
                assert (await outsider.get(path + '/download?revision=1')).status_code == 403
            update = await upload(client, 'pressure.txt', b'P-501 normal pressure is 9 bar.', previous=source)
            assert update.status_code == 200
            assert (await client.get(history_path(session) + '/drafts')).json()['drafts'][0]['state'] == 'stale'
            for action in ('approve', 'export'):
                assert (await client.post(path + '/' + action, json={'revision': 1})).status_code == 409
            assert (await client.get(path + '/download?revision=1')).status_code == 409
            assert (await client.get('/api/artifacts')).status_code == 403
            assert (await client.get('/api/documents')).status_code == 503
            assert (await client.post('/api/chat', files={'file': ('x.pdf', b'%PDF-')})).status_code == 415
            assert not (await workbench.state.engine._tool_registry.execute(DRAFT_TOOL, {})).success

    asyncio.run(exercise())


def test_cancel_export_and_durable_image_dependency(durable_environment, capsys, monkeypatch):
    import threading

    from backend.health import routes as health
    from backend.models.base import ChatResponse

    _, make_app = durable_environment
    async def inventory(target, *, local_only):
        return health.ModelObservation('available', 'inventory_observed', ('llava:7b',), (('llava:7b', 'b' * 64),))

    monkeypatch.setattr(health, 'observe_ollama', inventory)
    real_render = local_drafts.generate_export
    release = threading.Event()
    started = threading.Event()

    def paused(draft):
        started.set()
        assert release.wait(8)
        return real_render(draft)

    async def exercise():
        app = make_app('durable')
        async with app.router.lifespan_context(app), api_client(app) as client:
            await login(client, capsys)
            async def observe(request):
                return ChatResponse('A blue surface is visible. The operating state is uncertain.', request.model, 'ollama')

            monkeypatch.setattr(app.state.model_router.get_provider('ollama'), 'chat', observe)
            session = await new_turn(client, message='Describe this image.')
            image = await attach(client, session)
            assert parse_events(await client.post('/api/chat', json=session))[-1]['type'] == 'done'
            request = {'session_id': session['session_id'], 'turn_id': str(uuid4()),
                       'message': 'Create a report.', 'draft_turn_ids': [session['turn_id']]}
            assert (await client.post('/api/chat', json={**request, 'image_id': session['image_id']})).status_code == 422
            foreign = await new_turn(client)
            assert (await client.post('/api/chat', json={**request, 'session_id': foreign['session_id']})).status_code == 409
            _, path, draft = await draft_from(client, session, 'Create a report.', turns=[session['turn_id']])
            assert draft['findings'][0]['kind'] == 'visual_interpretation'
            assert draft['findings'][0]['image']['sha256'] == image['sha256']
            assert (await client.post(path + '/approve', json={'revision': 1})).status_code == 200
            monkeypatch.setattr(local_drafts, 'generate_export', paused)
            task = asyncio.create_task(client.post(path + '/export', json={'revision': 1}))
            try:
                async with asyncio.timeout(5):
                    while not started.is_set():
                        await asyncio.sleep(.02)
                assert (await client.post(path + '/cancel-export', json={'revision': 1})).status_code == 200
            finally:
                release.set()
            assert (await task).status_code == 409
            assert (await client.get(path + '/download?revision=1')).status_code == 404
            monkeypatch.setattr(local_drafts, 'generate_export', real_render)
            original = await export(client, path, 1)
        restarted = make_app('durable')
        async with restarted.router.lifespan_context(restarted), api_client(restarted) as client:
            await login(client, capsys)
            assert (await client.get(path + '/download?revision=1')).content == original
            # Inject a foreign test principal after verifying single-viewer restart.
            restarted.state.auth_store.create_user(User(
                id=str(uuid4()), username='image-outsider', password_hash=hash_password('localTest123$'), role='viewer',
                clearance='L1', created_at=datetime.now(timezone.utc).isoformat(),
            ))
            async with api_client(restarted) as outsider:
                assert (await outsider.post('/api/auth/login', json={
                    'username': 'image-outsider', 'password': 'localTest123$',
                })).status_code == 200
                assert (await outsider.post('/api/chat', json=request)).status_code == 404
            assert (await client.delete(history_path(session) + '/images/' + image['attachment_id'])).status_code == 200
            assert (await client.get(path + '/download?revision=1')).status_code == 409
            assert (await client.get(history_path(session) + '/drafts')).json()['drafts'][0]['findings'][0]['kind'] == 'stale'

    asyncio.run(exercise())


def test_disposable_approval_rejects_forgery_and_expires_at_restart(durable_environment, capsys, monkeypatch):
    _, make_app = durable_environment

    async def exercise():
        app = make_app('disposable')
        async with app.router.lifespan_context(app), api_client(app) as client:
            await login(client, capsys)
            fixture_selection(app, monkeypatch)
            source = (await upload(client, 'pressure.txt', b'P-501 normal pressure is 8 bar.')).json()
            session = await new_turn(client)
            await converse(client, session, 'What is the normal pressure for P-501?',
                           knowledge_source_ids=[source['source_id']])
            _, path, preview = await draft_from(client, session, 'Create a report.')
            revision = {'revision': preview['revision']}
            assert (await client.post(path + '/approve', json={**revision, 'approver': 'another'})).status_code == 422
            assert (await client.post(path + '/approve', json=revision)).status_code == 200
            store = app.state.local_drafts
            signed = store.records[preview['id']]
            assert signed.approval.approver == app.state.local_viewer_id and store.is_approved(signed)
            forged = signed.approval.model_copy(update={'mac': '0' * 64})
            assert not store.is_approved(signed.model_copy(update={'approval': forged}))
            assert not store.is_approved(signed.model_copy(update={'title': 'Changed after approval'}))
            store.records[signed.id] = signed.model_copy(update={'approval': forged})
            assert (await client.post(path + '/export', json=revision)).status_code == 409
        restarted = make_app('disposable')
        async with restarted.router.lifespan_context(restarted), api_client(restarted) as client:
            await login(client, capsys)
            fresh = restarted.state.local_drafts
            assert fresh.keys.verification_key(signed.approval.key_id) is None
            assert not fresh.is_approved(signed) and list(fresh.records) == []
            assert (await client.get(path + '/download', params=revision)).status_code == 404

    asyncio.run(exercise())
