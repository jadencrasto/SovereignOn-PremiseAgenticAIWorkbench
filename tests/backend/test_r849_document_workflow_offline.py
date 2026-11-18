"""Exercise verified delivery through the real lease, engine and presentation adapter."""

import asyncio
from contextlib import aclosing
import json

import pytest

from backend.agent.document_workflow import run_document_workflow
from backend.auth.request_context import request_tools_enabled
from backend.multimodal import document_transport as transport
from test_a22_async_offline import run_offline
from test_r842_document_extraction_offline import sample_docx, sample_pdf, save_office
from test_r848_document_agent_lease_offline import COMPOUND, PASSAGE, Upload, app_engine, host, workbook  # noqa: F401


async def execute(upload, objective=PASSAGE, *, authenticate=True, conversation='a-conversation', **options):
    if authenticate:
        await upload.authenticate()
    def grants(owner, target):
        return upload.grants if (owner, target) == ('a', 'a-conversation') else ()

    return await run_document_workflow(upload.request, conversation, objective, source_authority=grants, **options)


@pytest.mark.filterwarnings('error')
def test_delivers_ordered_verified_events_after_cleanup(host, app_engine, monkeypatch, tmp_path, caplog):
    upload = Upload(host, [('first.pdf', sample_pdf()), ('second.xlsx', workbook(values=(70, '=B2*2', 80)))])
    original = app_engine.run_document_task
    seen = []
    memory = (tmp_path / 'memory.json').read_bytes()

    async def observe(lease, objective, *, handles, present):
        seen.append(lease)
        assert handles is lease.sources()
        assert [handle.filename for handle in handles] == ['first.pdf', 'second.xlsx']
        assert present is True
        async with aclosing(original(lease, objective, handles=handles, present=present)) as events:
            async for event in events:
                if event['type'] == 'plan_created':
                    event['plan']['internal_evidence'] = 'DO NOT DELIVER'
                    event['plan']['steps'][0]['internal_evidence'] = 'DO NOT DELIVER'
                if event['type'] == 'plan_step':
                    event['internal_evidence'] = 'DO NOT DELIVER'
                yield event

    monkeypatch.setattr(app_engine, 'run_document_task', observe)

    def forbid_model(*args, **kwargs):
        pytest.fail('Document workflow accessed a model provider')

    monkeypatch.setattr(app_engine._router, 'get_provider_for_model', forbid_model)
    monkeypatch.setattr(app_engine._router, 'resolve_model', forbid_model)

    async def run():
        events = await execute(upload, COMPOUND)
        assert len(events) == 9
        assert [e['status'] for e in events if e['type'] == 'plan_step'] == ['running', 'completed'] * 3
        assert len({e['task_id'] for e in events}) == 1
        assert all('answer' not in e and 'partial' not in e for e in events[:-1])
        assert 'DO NOT DELIVER' not in json.dumps(events)
        assert '7.1 mm/s' not in json.dumps(events[:-1]) and 'first.pdf' not in json.dumps(events[:-1])
        assert events[-1]['type'] == 'task_completed'
        answer = events[-1]['answer']
        first, second = answer['sources']
        assert (first['filename'], second['filename']) == ('first.pdf', 'second.xlsx')
        assert {'code': 'PDF_EMPTY_PAGE', 'location': {'page': 2}} in first['warnings']
        passage, calculation = answer['findings']
        assert passage['evidence'][0]['source_id'] == first['source_id']
        assert passage['evidence'][0]['location'] == {'page': 1}
        assert '7.1 mm/s' in passage['evidence'][0]['value']
        assert calculation['value'] == '75.00' and calculation['excluded']['formulas'] == 1
        assert {item['source_id'] for item in calculation['evidence']} == {second['source_id']}
        assert {(item['location']['sheet'], item['location']['cell']) for item in calculation['evidence']} == {
            ('Sheet1', 'B1'), ('Sheet1', 'B2'), ('Sheet1', 'B4'),
        }
        assert not transport.has_active_transport()
        assert seen[0]._scope is None and not seen[0]._files
        assert app_engine._memory.numeric_receipt('a-conversation') is None

    run_offline(run())
    assert (tmp_path / 'memory.json').read_bytes() == memory
    assert not (tmp_path / 'tasks.db').exists()
    assert '7.1 mm/s' not in caplog.text and 'second.xlsx' not in caplog.text


PARTIAL = 'Find the passage mentioning recorded values, then find the section about no-such-phrase'


def test_delivers_only_permitted_earlier_findings_on_query_failure(host, app_engine):
    upload = Upload(host, [('report.docx', save_office(sample_docx()))])
    events = run_offline(execute(upload, PARTIAL))
    final = events[-1]
    assert final['type'] == 'task_failed' and final['error_code'] == 'NO_MATCH'
    assert 'answer' not in final
    assert final['partial']['findings'][0]['evidence'][0]['location'] == {'body': 4, 'paragraph': 3}
    assert any(event.get('status') == 'failed' for event in events[:-1])


@pytest.mark.parametrize(('change', 'terminal_type', 'code'), [
    ('session', 'task_failed', 'AUTHORIZATION_UNAVAILABLE'),
    ('cancel', 'task_cancelled', 'CANCELLED'),
])
def test_interruption_after_start_delivers_terminal_without_plan(host, app_engine, monkeypatch, change, terminal_type, code):
    upload = Upload(host)
    original = app_engine.run_document_task

    async def interrupt_after_start(lease, *args, **kwargs):
        async with aclosing(original(lease, *args, **kwargs)) as events:
            async for event in events:
                yield event
                if event['type'] == 'task_started':
                    if change == 'session':
                        host[0].state.session_manager.revoke_session(host[1])
                    else:
                        upload.cancelled = True

    monkeypatch.setattr(app_engine, 'run_document_task', interrupt_after_start)
    started, terminal = run_offline(execute(upload, cancelled=lambda: upload.cancelled))
    assert started['type'] == 'task_started' and terminal['task_id'] == started['task_id']
    assert terminal['type'] == terminal_type and terminal['error_code'] == code
    assert terminal['operation'] is None and terminal['partial'] is None and 'answer' not in terminal


@pytest.mark.parametrize(('change', 'code'), [
    ('session', 'AUTHORIZATION_UNAVAILABLE'), ('source', 'AUTHORIZATION_UNAVAILABLE'),
    ('timeout', 'TIMEOUT'), ('cancel', 'CANCELLED'), ('cleanup', 'OPERATION_UNAVAILABLE'),
])
@pytest.mark.parametrize('partial', [False, True])
def test_outer_cleanup_and_final_policy_control_delivery(host, app_engine, monkeypatch, change, code, partial):
    upload = Upload(host, [('report.docx', save_office(sample_docx()))] if partial else None)
    original = transport.DocumentTransport._close
    engine_run = app_engine.run_document_task
    access = []
    outer_exits = []

    def capture(lease, *args, **kwargs):
        access.append(lease._access)
        return engine_run(lease, *args, **kwargs)

    def close(lease):
        outer_exit = lease._evidence_released and lease._access is None
        original(lease)
        if not outer_exit:
            return
        outer_exits.append(lease)
        if change == 'session':
            host[0].state.session_manager.revoke_session(host[1])
        elif change == 'source':
            upload.grants = ()
        elif change == 'cancel':
            upload.cancelled = True
        elif change == 'timeout':
            access[0].deadline = asyncio.get_running_loop().time()
        else:
            raise RuntimeError('Confidential cleanup diagnostic')

    monkeypatch.setattr(app_engine, 'run_document_task', capture)
    monkeypatch.setattr(transport.DocumentTransport, '_close', close)

    async def run():
        events = await execute(upload, PARTIAL if partial else PASSAGE, cancelled=lambda: upload.cancelled)
        assert not transport.has_active_transport()
        return events

    events = run_offline(run())
    assert len(outer_exits) == 1
    final, = events
    assert final['type'] == ('task_cancelled' if change == 'cancel' else 'task_failed')
    assert final['error_code'] == code and final['partial'] is None and 'answer' not in final
    assert 'Confidential' not in final['error']


@pytest.mark.parametrize('denial', ['unauthenticated', 'other_owner', 'no_grant', 'no_tools', 'elapsed'])
def test_refuses_before_read(host, app_engine, denial):
    upload = Upload(host)

    async def run():
        if denial == 'no_grant':
            upload.grants = ()
        elif denial == 'no_tools':
            request_tools_enabled.set(False)
        return await execute(upload, authenticate=denial != 'unauthenticated',
                             conversation='b-conversation' if denial == 'other_owner' else 'a-conversation',
                             deadline=asyncio.get_running_loop().time() if denial == 'elapsed' else None)

    final, = run_offline(run())
    assert final['type'] == 'task_failed' and final['partial'] is None and upload.reads == 0
    assert final['error_code'] == ('TIMEOUT' if denial == 'elapsed' else 'AUTHORIZATION_UNAVAILABLE')


def test_task_cancel_while_terminal_buffered_closes_transport_without_delivering(host, app_engine, monkeypatch):
    upload = Upload(host)
    original = app_engine.run_document_task
    leases = []

    async def run():
        buffered = asyncio.Event()

        async def stall(lease, *args, **kwargs):
            leases.append(lease)
            async with aclosing(original(lease, *args, **kwargs)) as events:
                async for event in events:
                    yield event
                    if event['type'] == 'task_completed':
                        buffered.set()
                        await asyncio.Future()

        monkeypatch.setattr(app_engine, 'run_document_task', stall)
        task = asyncio.create_task(execute(upload))
        try:
            async with asyncio.timeout(2):
                await buffered.wait()
            assert not task.done()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert leases[0]._scope is None and not leases[0]._sources

    run_offline(run())


@pytest.mark.parametrize('case', ['no_checkpoint', 'extra_terminal', 'missing_terminal', 'other_task'])
def test_rejects_incomplete_or_unbound_delivery(host, app_engine, monkeypatch, case):
    upload = Upload(host)
    original = app_engine.run_document_task

    async def invalid(lease, *args, **kwargs):
        if case == 'no_checkpoint':
            yield {'type': 'task_completed', 'task_id': 'invalid', 'answer': {'text': 'unverified'}}
            return
        async with aclosing(original(lease, *args, **kwargs)) as events:
            async for event in events:
                if event['type'] == 'task_completed':
                    if case == 'missing_terminal':
                        continue
                    if case == 'other_task':
                        yield {**event, 'task_id': 'different-task'}
                        continue
                    yield event
                yield event

    monkeypatch.setattr(app_engine, 'run_document_task', invalid)
    final, = run_offline(execute(upload))
    assert final['type'] == 'task_failed' and final['partial'] is None and 'answer' not in final
    assert final['error_code'] == ('REQUEST_CLOSED' if case == 'no_checkpoint' else 'OPERATION_UNAVAILABLE')
