"""Exercise the task preview, evidence review and export through the local app."""

import asyncio
import io
import json
from uuid import uuid4

import httpx
from docx import Document
from openpyxl import Workbook, load_workbook
from pypdf import PdfReader

from test_l2_local_turns import api_client, history_path, login, new_turn, parse_events, workbench
from test_l9_local_knowledge import upload
from test_l15_local_documents import _import
from test_l16_local_images import VISION, attach, vision_inventory, wire_provider
from test_l17_grounded_drafts import export
from test_l18_local_workflow import preview, source
from test_l19_workflow_recovery import assert_done, reply, turn


async def task_preview(client, body):
    response = await client.post('/api/chat/workflow-preview', json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def assert_started(client, body, task):
    response = await client.post('/api/chat', json={**body, 'workflow_preview': task['token']})
    assert_done(response)
    return parse_events(response)


def workbook(tag, value):
    output = io.BytesIO()
    file = Workbook()
    sheet = file.active
    sheet.title = 'Equipment Records'
    sheet.append(['Equipment', 'Condition', 'Temperature (\N{DEGREE SIGN}C)'])
    sheet.append([tag, 'Normal', value])
    file.save(output)
    file.close()
    return output.getvalue()


def test_policy_comparison_is_a_cited_chat_answer_and_optional_pdf(workbench, capsys):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            a = await source(client, 'weekday-policy.txt', 'Visitors must sign in with reception.')
            b = await source(client, 'weekend-policy.txt', 'Visitors shall have an employee escort.')
            await source(client, 'unselected.txt', 'Visitors must obtain a parking sticker.')
            body = await new_turn(client, message='Compare the requirements in these two policy documents and summarize their differences.')
            body['knowledge_source_ids'] = [a['source_id'], b['source_id']]
            task = await task_preview(client, body)
            assert task['output'] == 'Chat answer' and task['question'] is None
            assert [item['label'] for item in task['sources']] == ['weekday-policy.txt', 'weekend-policy.txt']
            assert not (await client.get(history_path(body))).json()['messages']
            assert (await client.get(history_path(body) + '/drafts')).json()['drafts'] == []
            events = await assert_started(client, body, task)
            assert any(event['content'] == 'comparing_evidence' for event in events)
            assert not any(event['content'] == 'drafting' for event in events)
            stored = (await client.get(history_path(body))).json()['messages'][-1]
            assert 'weekday-policy.txt: "Visitors must sign in with reception."' in stored['content']
            assert 'weekend-policy.txt: "Visitors shall have an employee escort."' in stored['content']
            assert 'parking' not in stored['content']
            assert {item['label'] for item in stored['local_knowledge']['citations']} == {
                'weekday-policy.txt', 'weekend-policy.txt',
            }
            assert (await client.get(history_path(body) + '/drafts')).json()['drafts'] == []
            report = {**body, 'turn_id': str(uuid4()), 'message': 'Create a PDF report.', 'draft_turn_ids': [body['turn_id']]}
            report.pop('knowledge_source_ids')
            task = await task_preview(client, report)
            assert task['output'] == 'PDF draft'
            await assert_started(client, report, task)
            path, draft = await preview(client, report)
            assert draft['state'] == 'draft'
            assert (await client.post(path + '/export', json={'revision': 1})).status_code == 409
            reader = PdfReader(io.BytesIO(await export(client, path, 1)))
            text = ''.join(page.extract_text() for page in reader.pages)
            assert 'weekday-policy.txt' in text and 'employee escort' in text and 'parking' not in text

    asyncio.run(exercise())


def test_ordinary_comparison_clarifies_a_target_on_the_same_turn(workbench, capsys):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            a = await source(client, 'first.txt', 'Employees must keep a visitor log.')
            b = await source(client, 'second.txt', 'Employees must retain the visitor log for 30 days.')
            body = await new_turn(client, message='Compare these two policy documents.')
            body['knowledge_source_ids'] = [a['source_id'], b['source_id']]
            task = await task_preview(client, body)
            assert task['question'] == 'What should I compare or report?'
            await assert_started(client, body, task)
            before = await turn(client, body)
            assert before['clarification']['reason'] == 'measurement'
            assert_done(await reply(client, body, 1, 'requirements'))
            after = await turn(client, body)
            assert after['state'] == 'completed' and after['plan_revision'] == 2 and after['attempt'] == 1
            assert (await client.get(history_path(body) + '/drafts')).json()['drafts'] == []
            assert (await reply(client, body, 1, 'requirements')).status_code == 409

    asyncio.run(exercise())


def test_equipment_report_preview_rejects_revision_change_and_exports_genuine_docx(workbench, capsys):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            a = await source(client, 'first-limit.txt', 'P-101 maximum operating temperature is 80 \N{DEGREE SIGN}C.')
            b = await source(client, 'second-limit.txt', 'P-102 maximum operating temperature is 194 \N{DEGREE SIGN}F.')
            body = await new_turn(client, message='Compare operating limits for P-101 and P-102 using these maintenance documents, '
                                                   'identify important differences and create a report.')
            body['knowledge_source_ids'] = [a['source_id'], b['source_id']]
            task = await task_preview(client, body)
            assert task['output'] == 'DOCX draft' and task['question'] is None
            changed = await upload(client, 'first-limit.txt',
                                   'P-101 maximum operating temperature is 81 \N{DEGREE SIGN}C.'.encode(), previous=a)
            assert changed.status_code == 200
            result = await client.post('/api/chat', json={**body, 'workflow_preview': task['token']})
            assert result.status_code == 409 and result.headers['X-Error-Code'] == 'LOCAL_WORKFLOW_PREVIEW_CHANGED'
            assert not (await client.get(history_path(body))).json()['messages']
            await assert_started(client, body, await task_preview(client, body))
            path, draft = await preview(client, body)
            sourced = [item for item in draft['findings'] if item['kind'] == 'source_value']
            assert {item['source']['label'] for item in sourced} == {'first-limit.txt', 'second-limit.txt'}
            assert {tag for item in sourced for tag in item['field']['equipment']} == {'P-101', 'P-102'}
            assert any('less than' in item['text'] for item in draft['findings'] if item['kind'] == 'comparison')
            assert (await client.post(path + '/approve', json={'revision': 2})).status_code == 409
            doc = Document(io.BytesIO(await export(client, path, 1)))
            assert any('81' in cell.text for table in doc.tables for row in table.rows for cell in row.cells)
            assert any('first-limit.txt' in paragraph.text for paragraph in doc.paragraphs)

    asyncio.run(exercise())


def test_workbook_comparison_keeps_sheet_cell_provenance_in_xlsx_export(workbench, capsys):
    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            sources = []
            for tag, value in (('P-101', 40), ('P-102', 49)):
                response = await _import(client, f'{tag}.xlsx', workbook(tag, value))
                assert response.status_code == 201, response.text
                sources.append(response.json()['source_id'])
            body = await new_turn(client, message='Compare these equipment records and make a spreadsheet of the differences.')
            body['knowledge_source_ids'] = sources
            task = await task_preview(client, body)
            assert task['output'] == 'XLSX draft' and task['question']
            await assert_started(client, body, task)
            assert_done(await reply(client, body, 1, 'temperature'))
            path, draft = await preview(client, body)
            values = [item for item in draft['findings'] if item['kind'] == 'source_value']
            assert {item['field']['value'] for item in values} == {'40', '49'}
            assert all(item['field']['document_locator']['cell'] == 'C2' for item in values)
            file = load_workbook(io.BytesIO(await export(client, path, 1)), read_only=True)
            try:
                cells = [str(cell) for sheet in file for row in sheet.values for cell in row if cell is not None]
                assert any('Equipment Records!C2' in cell for cell in cells)
                assert any('P-101.xlsx' in cell for cell in cells) and any('P-102.xlsx' in cell for cell in cells)
            finally:
                file.close()

    asyncio.run(exercise())


def test_image_report_separates_fixture_observations_from_document_facts(workbench, capsys, monkeypatch, vision_inventory):
    async def respond(request):
        payload = json.loads(request.content)
        assert payload['model'] == VISION and payload['messages'][-1]['images']
        return httpx.Response(200, json={'model': VISION, 'done': True, 'message': {
            'role': 'assistant', 'content': 'Fixture observation: a blue housing is visible. Its condition is uncertain.',
        }})

    async def exercise():
        async with workbench.router.lifespan_context(workbench), api_client(workbench) as client:
            await login(client, capsys)
            calls = wire_provider(workbench, monkeypatch, respond)
            note = await source(client, 'inspection.txt', 'P-501 inspection interval is 30 days.')
            body = await new_turn(client, message='Use this inspection image and the attached document to write an observation summary.')
            body['knowledge_source_ids'] = [note['source_id']]
            image = await attach(client, body)
            task = await task_preview(client, body)
            assert task['images'] and not calls
            await assert_started(client, body, task)
            assert_done(await reply(client, body, 1, 'inspection_interval'))
            path, draft = await preview(client, body)
            assert len(calls) == 1
            assert any(item['kind'] == 'source_value' and '30 days' in item['text'] for item in draft['findings'])
            visual, = [item for item in draft['findings'] if item['kind'] == 'visual_interpretation']
            assert visual['image']['sha256'] == image['sha256'] and 'Fixture observation' in visual['text']
            assert (await client.delete(history_path(body) + '/images/' + image['attachment_id'])).status_code == 200
            assert (await client.post(path + '/approve', json={'revision': 1})).status_code == 409

    asyncio.run(exercise())
