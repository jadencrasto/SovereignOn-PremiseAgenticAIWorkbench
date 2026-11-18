"""Exercise the existing document executor through real request-bound SQLite sessions."""
import asyncio
from contextlib import aclosing
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch
import warnings

import pytest
from openpyxl import Workbook

from backend.agent import engine as engine_module
from backend.agent.document_plan import DocumentActions
from backend.agent.planner import AgentPlanner
from backend.agent.plan_validator import PlanValidator
from backend.auth.request_context import http_request_active, request_tools_enabled
from backend.multimodal import document_reasoning as reasoning
from backend.tools.registry import ToolRegistry
from test_a22_async_offline import run_offline
from test_r842_document_extraction_offline import sample_docx, sample_pdf, save_office
from test_r843_document_reasoning_offline import table_document
from test_r844_document_agent_offline import workbook
from test_r847_document_transport_offline import Upload, host  # noqa: F401


PASSAGE = 'Find the section about pump vibration in source 1'
AVERAGE = 'Calculate the average temperature in worksheet Sheet1'
COMPOUND = PASSAGE + ', then ' + AVERAGE


@pytest.fixture
def app_engine(host):
    engine = host[0].state.engine
    engine._planner = AgentPlanner(max_plan_steps=5)
    engine._plan_validator = PlanValidator(ToolRegistry(), max_plan_steps=5)
    token = request_tools_enabled.set(True)
    try:
        yield engine
    finally:
        request_tools_enabled.reset(token)


async def collect(stream):
    async with aclosing(stream):
        events = [event async for event in stream]
    terminal, = [e for e in events if e['type'] in {'task_completed', 'task_failed', 'task_cancelled'}]
    assert terminal is events[-1]
    assert all(not {'answer', 'partial'} & e.keys() for e in events[:-1])
    return terminal


def denied(event, code):
    assert event['type'] == ('task_cancelled' if code == 'CANCELLED' else 'task_failed'), event
    assert event['error_code'] == code and 'answer' not in event and event['partial'] is None


@pytest.mark.filterwarnings('error')
def test_compound_reuses_reader_cites_originals_and_leaves_no_history(host, app_engine, tmp_path, caplog):
    files = [('first.pdf', sample_pdf()), ('second.xlsx', workbook(values=(70, '=B2*2', 80)))]
    upload = Upload(host, files)
    before = (tmp_path / 'memory.json').read_bytes()

    async def run():
        await upload.authenticate()
        async with upload.open() as lease:
            handles = lease.sources()
            scope = lease.reasoning()
            with patch.object(reasoning._DocumentRequest, '_load', side_effect=AssertionError('second decode')):
                final = await collect(app_engine.run_document_task(lease, COMPOUND, handles=handles, present=True))
            assert final['type'] == 'task_completed', final
            answer = final['answer']
            assert '7.1 mm/s' in answer['text'] and '75.00' in answer['text']
            passage, calc = answer['findings']
            assert passage['evidence'][0]['location']['page'] == 1
            assert calc['excluded']['formulas'] == 1 and calc['value'] == '75.00'
            assert [e['location']['cell'] for e in calc['evidence']] == ['B1', 'B2', 'B4']
            assert [s['filename'] for s in answer['sources']] == ['first.pdf', 'second.xlsx']
            with pytest.raises(reasoning.ReasoningError, match='closed'):
                scope.sources()
            denied(await collect(app_engine.run_document_task(lease, PASSAGE)), 'REQUEST_CLOSED')
        assert app_engine._memory.numeric_receipt('a-conversation') is None
    run_offline(run())
    assert before == (tmp_path / 'memory.json').read_bytes()
    assert not (tmp_path / 'tasks.db').exists()
    assert '7.1 mm/s' not in caplog.text and 'second.xlsx' not in caplog.text


def test_docx_location_and_allowed_partial(host, app_engine):
    upload = Upload(host, [('report.docx', save_office(sample_docx()))])

    async def run():
        await upload.authenticate()
        async with upload.open() as lease:
            final = await collect(app_engine.run_document_task(
                lease, 'Find the passage mentioning recorded values, then find the section about no-such-phrase',
                present=True,
            ))
            assert final['type'] == 'task_failed' and final['error_code'] == 'NO_MATCH'
            partial = final['partial']
            assert partial['sources'][0]['filename'] == 'report.docx'
            assert partial['findings'][0]['evidence'][0]['location'] == {'body': 4, 'paragraph': 3}
            assert lease._scope is None and lease._files == ()
    run_offline(run())


@pytest.mark.parametrize('change', ['session', 'generation', 'role', 'clearance', 'grant', 'expired',
                                    'cancelled', 'tools', 'closed'])
def test_loss_between_verified_step_and_next_step(host, app_engine, change, monkeypatch):
    upload = Upload(host)
    state = host[0].state

    async def run():
        await upload.authenticate()
        async with upload.open() as lease:
            events = []
            async with aclosing(app_engine.run_document_task(lease, PASSAGE)) as stream:
                async for event in stream:
                    events.append(event)
                    if event.get('status') != 'completed':
                        continue
                    if change == 'session':
                        state.session_manager.revoke_session(host[1])
                    elif change == 'generation':
                        monkeypatch.setattr(app_engine._memory, 'stream_identity', lambda _: ('a', 'replacement'))
                    elif change == 'role':
                        state.auth_store.update_user('a', role='admin')
                    elif change == 'clearance':
                        state.auth_store.update_user('a', clearance='L3')
                    elif change == 'grant':
                        upload.grants = ()
                    elif change == 'expired':
                        lease._access.deadline = asyncio.get_running_loop().time()
                    elif change == 'cancelled':
                        upload.cancelled = True
                    elif change == 'tools':
                        request_tools_enabled.set(False)
                    else:
                        lease._close()
            code = {'expired': 'TIMEOUT', 'cancelled': 'CANCELLED', 'closed': 'REQUEST_CLOSED'}.get(
                change, 'AUTHORIZATION_UNAVAILABLE')
            denied(events[-1], code)
            assert sum(e.get('status') == 'running' for e in events) == 1
            assert all(e['type'] != 'task_completed' for e in events)
            assert lease._scope is None
    run_offline(run())


@pytest.mark.parametrize('change', ['revoked', 'expired', 'missing_opt_in', 'other_engine',
                                    'foreign', 'copied', 'reordered', 'override'])
def test_reject_before_planning_or_decoding(host, app_engine, change, monkeypatch):
    data = sample_pdf()
    upload = Upload(host, [('same.pdf', data), ('same.pdf', data)])
    other = Upload(host)
    monkeypatch.setattr(reasoning._DocumentRequest, '_load', lambda *_: pytest.fail('Unauthorized decode'))

    async def run():
        await other.authenticate()
        async with other.open() as previous:
            foreign = previous.sources()[0]
        await upload.authenticate()
        async with upload.open() as lease:
            handles = lease.sources()
            engine = app_engine
            extra = {}
            if change == 'revoked':
                host[0].state.session_manager.revoke_session(host[1])
            elif change == 'expired':
                lease._access.deadline = 0
            elif change == 'missing_opt_in':
                request_tools_enabled.set(False)
            elif change == 'other_engine':
                engine = type(app_engine).__new__(type(app_engine))
            elif change == 'foreign':
                handles = (foreign, handles[1])
            elif change == 'copied':
                handles = (replace(handles[0]), handles[1])
            elif change == 'reordered':
                handles = tuple(reversed(handles))
            else:
                extra['user_id'] = 'a'
            code = ('SOURCE_UNAVAILABLE' if change in ('foreign', 'copied', 'reordered') else
                    'TIMEOUT' if change == 'expired' else 'AUTHORIZATION_UNAVAILABLE')
            denied(await collect(engine.run_document_task(lease, PASSAGE, handles=handles, **extra)), code)
            assert lease._files == ()
    run_offline(run())


def test_active_lease_forbids_offline_byte_fallback(host, app_engine):
    upload = Upload(host)

    async def run():
        await upload.authenticate()
        async with upload.open():
            denied(await collect(app_engine.run_document_task('a-conversation', PASSAGE, upload.files,
                         user_id='a', authorize_sources=lambda *_: True)), 'AUTHORIZATION_UNAVAILABLE')
    run_offline(run())


@pytest.mark.parametrize('change', ['calculation', 'citation', 'dependency', 'synthesis'])
@pytest.mark.filterwarnings('error')
def test_substituted_evidence_never_published(host, app_engine, monkeypatch, change):
    upload = Upload(host, [('one.pdf', sample_pdf()), ('two.xlsx', workbook())])
    if change == 'calculation':
        original = reasoning._DocumentRequest.ask_table
        def altered(*args):
            actual = original(*args)
            return replace(actual, calculation=replace(actual.calculation, value=Decimal('999')))
        monkeypatch.setattr(reasoning._DocumentRequest, 'ask_table', altered)
    elif change == 'citation':
        original = reasoning._DocumentRequest.find_passages
        def altered(*args):
            cite, = original(*args)
            return (replace(cite, filename='other.pdf'),)
        monkeypatch.setattr(reasoning._DocumentRequest, 'find_passages', altered)
    elif change == 'dependency':
        original = engine_module.bind_verified_arguments
        def altered(step, *args):
            bound = original(step, *args)
            if step.tool_name == 'document_synthesize':
                bound['finding'] = bound['next_finding']
            return bound
        monkeypatch.setattr(engine_module, 'bind_verified_arguments', altered)
    else:
        original = DocumentActions._synthesize
        async def altered(*args):
            return replace(await original(*args), text='Fabricated conclusion')
        monkeypatch.setattr(DocumentActions, '_synthesize', altered)

    async def run():
        await upload.authenticate()
        async with upload.open() as lease:
            final = await collect(app_engine.run_document_task(lease, COMPOUND, present=True))
            denied(final, 'DEPENDENCY_UNAVAILABLE' if change == 'dependency' else 'CITATION_UNVERIFIED')
    run_offline(run())


@pytest.mark.parametrize('partial', [False, True])
def test_expiration_after_projection_withholds_final_or_partial(host, app_engine, monkeypatch, partial):
    from backend.agent import document_presentation
    upload = Upload(host)
    original = document_presentation.present_answer

    async def run():
        await upload.authenticate()
        async with upload.open() as lease:
            def late(*args):
                output = original(*args)
                lease._access.deadline = 0
                return output
            monkeypatch.setattr(document_presentation, 'present_answer', late)
            question = PASSAGE + (', then find the section about absent-phrase' if partial else '')
            denied(await collect(app_engine.run_document_task(lease, question, present=True)), 'TIMEOUT')
    run_offline(run())


def test_early_close_and_overlapping_claim(host, app_engine):
    upload = Upload(host)

    async def run():
        await upload.authenticate()
        async with upload.open() as lease:
            async with aclosing(app_engine.run_document_task(lease, PASSAGE)) as first:
                assert (await anext(first))['type'] == 'task_started'
                denied(await collect(app_engine.run_document_task(lease, PASSAGE)), 'REQUEST_CLOSED')
            with pytest.raises(reasoning.ReasoningError, match='closed'):
                lease.sources()
    run_offline(run())


def test_parser_warning_cannot_escape_as_evidence_error_or_diagnostic(host, app_engine, caplog, capsys):
    book = Workbook()
    cell = book.active['A1']
    cell.value = 1234567890123
    cell.number_format = 'yyyy-mm-dd'
    upload = Upload(host, [('private.xlsx', save_office(book))])
    book.close()

    async def run():
        await upload.authenticate()
        async with upload.open() as lease:
            policy = list(warnings.filters)
            final = await collect(app_engine.run_document_task(lease, AVERAGE))
            denied(final, 'EXTRACTION_FAILED')
            assert lease._scope is None and lease._files == () and warnings.filters == policy
            return str(final)
    with warnings.catch_warnings(record=True) as captured:
        warnings.filterwarnings('error', module=r'^openpyxl(?:\.|$)')
        output = run_offline(run())
    assert not captured
    assert '1234567890123' not in output + caplog.text + capsys.readouterr().err


@pytest.mark.parametrize('partial', [False, True])
def test_transport_exit_rechecks_access_after_evidence_handoff(host, app_engine, partial):
    upload = Upload(host)

    async def run():
        await upload.authenticate()
        with pytest.raises(reasoning.ReasoningError) as refused:
            async with upload.open() as lease:
                question = PASSAGE + (', then find the section about absent-phrase' if partial else '')
                terminal = await collect(app_engine.run_document_task(lease, question))
                assert terminal['partial' if partial else 'answer'] is not None
                assert lease._scope is None and not lease._files
                host[0].state.session_manager.revoke_session(host[1])
        assert refused.value.code == 'AUTHORIZATION_UNAVAILABLE'
    run_offline(run())


def test_task_cancel_during_action_closes_the_only_reader(host, app_engine, monkeypatch):
    upload = Upload(host)
    collected = []
    leases = []

    async def run():
        entered = asyncio.Event()
        async def paused(self, args):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(DocumentActions, '_passage', paused)

        async def consume():
            await upload.authenticate()
            async with upload.open() as lease:
                leases.append(lease)
                async with aclosing(app_engine.run_document_task(lease, PASSAGE)) as stream:
                    async for event in stream:
                        collected.append(event)
        task = asyncio.create_task(consume())
        try:
            async with asyncio.timeout(2):
                await entered.wait()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not any('answer' in event or 'partial' in event for event in collected)
        assert len(leases) == 1 and leases[0]._scope is None and not leases[0]._sources
    run_offline(run())


@pytest.mark.filterwarnings('error')
def test_comparison_preserves_both_lease_sources_and_conversion(host, app_engine):
    upload = Upload(host, [('inspection.docx', table_document(['Equipment', 'Length (m)'], [('Pump A', '0.4')])),
                           ('reading.xlsx', workbook('Length (cm)', (45,)))])

    async def run():
        await upload.authenticate()
        async with upload.open() as lease:
            final = await collect(app_engine.run_document_task(lease, 'Compare Pump A across these two reports.'))
            assert final['type'] == 'task_completed', final
            answer = final['answer']
            assert 'calculated difference 0.05 m' in answer.text
            assert {(c.filename, c.location.row, c.location.cell, c.value) for c in answer.citations} >= {
                ('inspection.docx', 2, None, '0.4'), ('reading.xlsx', 2, 'B2', 45)}
    run_offline(run())


def test_http_context_rejects_raw_byte_entry_without_transport(app_engine):
    async def run():
        token = http_request_active.set(True)
        try:
            denied(await collect(app_engine.run_document_task(
                'a-conversation', PASSAGE, [('report.pdf', sample_pdf())],
                user_id='a', authorize_sources=lambda *_: True)), 'AUTHORIZATION_UNAVAILABLE')
        finally:
            http_request_active.reset(token)
    run_offline(run())


@pytest.mark.filterwarnings('error')
def test_workbook_diagnostic_is_absent_from_exception_chain(host):
    book = Workbook()
    cell = book.active['A1']
    cell.value, cell.number_format = 1234567890123, 'yyyy-mm-dd'
    upload = Upload(host, [('report.xlsx', save_office(book))])
    book.close()

    async def run():
        await upload.authenticate()
        with pytest.raises(reasoning.ReasoningError) as failure:
            async with upload.open() as lease:
                lease.reasoning()
        assert failure.value.code == 'EXTRACTION_FAILED'
        assert failure.value.__cause__ is None and failure.value.__context__ is None
        assert '1234567890123' not in str(failure.value)
    run_offline(run())
