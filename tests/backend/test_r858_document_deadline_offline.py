"""Use the serving loop's clock to exercise deadline and release boundaries."""

import asyncio
from contextlib import aclosing, closing
import sqlite3
from types import SimpleNamespace

import pytest

from backend.agent.document_workflow import run_document_workflow, run_server_source_document_workflow
from backend.multimodal import document_transport as transport
from backend.multimodal.document_source_authority import LocalSourceAuthority
from test_a22_async_offline import run_offline
from test_r848_document_agent_lease_offline import PASSAGE, Upload, app_engine, denied, host  # noqa: F401
from test_r856_source_approvals_offline import deployment, establish  # noqa: F401


CONVERSATION = 'a-conversation'


def controlled_clock(monkeypatch):
    loop = asyncio.get_running_loop()
    now = loop.time()
    clock = SimpleNamespace(now=now, deadline=now + 10)
    monkeypatch.setattr(loop, 'time', lambda: clock.now)
    return clock


def assert_closed(lease):
    assert not transport.has_active_transport()
    assert lease._scope is None and lease._access is None and not lease._files and not lease._extracted


@pytest.mark.parametrize('stage', ['before_ingress', 'authority', 'source_lookup', 'preflight'])
def test_elapsed_deadline_takes_precedence_over_boundary_failure(host, app_engine, monkeypatch, stage):
    upload = Upload(host)

    async def run():
        await upload.authenticate()
        clock = controlled_clock(monkeypatch)

        def fail_late(*_):
            clock.now = clock.deadline
            raise ValueError('Sensitive synthetic diagnostic')

        if stage == 'source_lookup':
            provider = SimpleNamespace(get_approved_source=fail_late)
            terminal, = await run_server_source_document_workflow(
                upload.request, CONVERSATION, PASSAGE, sources=(('report', upload.files[0][1]),),
                provider=provider, deadline=clock.deadline,
            )
        else:
            if stage == 'before_ingress':
                clock.now = clock.deadline
            if stage == 'preflight':
                monkeypatch.setattr(transport, 'open_bounded_pdf', fail_late)
            terminal, = await run_document_workflow(
                upload.request, CONVERSATION, PASSAGE, deadline=clock.deadline,
                source_authority=fail_late if stage == 'authority' else lambda *_: upload.grants,
            )
        denied(terminal, 'TIMEOUT')
        assert 'Sensitive' not in terminal['error']
        assert bool(upload.reads) == (stage == 'preflight')
        assert not transport.has_active_transport()

    run_offline(run())


@pytest.mark.parametrize('stage', ['before_decode', 'late_decode_success', 'late_decode_failure', 'between_steps'])
def test_transport_scope_observes_same_deadline_and_cleans_evidence(host, app_engine, monkeypatch, stage):
    upload = Upload(host)
    original_parser = transport.DocumentParser.parse
    original_engine = app_engine.run_document_task
    leases, parses, finished_steps = [], [], []
    objective = PASSAGE + ', then find the passage mentioning recorded values in source 1'

    async def run():
        await upload.authenticate()
        clock = controlled_clock(monkeypatch)

        def parse(name, data):
            parses.append(name)
            if stage == 'late_decode_failure':
                clock.now = clock.deadline
                raise ValueError('Sensitive synthetic diagnostic')
            result = original_parser(name, data)
            if stage == 'late_decode_success':
                clock.now = clock.deadline
            return result

        async def observe(lease, *args, **kwargs):
            leases.append(lease)
            if stage == 'before_decode':
                clock.now = clock.deadline
            async with aclosing(original_engine(lease, *args, **kwargs)) as stream:
                async for event in stream:
                    yield event
                    if event['type'] == 'plan_step' and event['status'] == 'completed':
                        finished_steps.append(event['step_id'])
                        if stage == 'between_steps':
                            clock.now = clock.deadline

        monkeypatch.setattr(transport.DocumentParser, 'parse', parse)
        monkeypatch.setattr(app_engine, 'run_document_task', observe)
        events = await run_document_workflow(
            upload.request, CONVERSATION, objective, deadline=clock.deadline, source_authority=lambda *_: upload.grants,
        )
        denied(events[-1], 'TIMEOUT')
        assert all('answer' not in event and event.get('partial') is None for event in events)
        assert 'Sensitive' not in events[-1]['error']
        assert len(parses) == (0 if stage == 'before_decode' else 1)
        assert len(finished_steps) == (1 if stage == 'between_steps' else 0)
        lease, = leases
        assert_closed(lease)

    run_offline(run())


@pytest.mark.parametrize('boundary', ['approval_storage', 'issuer_cleanup'])
def test_buffered_success_is_withheld_on_sqlite_failure_or_late_cleanup(deployment, app_engine, monkeypatch, boundary):
    _, _, upload, content, _ = deployment
    original_engine = app_engine.run_document_task
    original_close = LocalSourceAuthority.close
    pending, leases = [], []

    async def run():
        provider = await establish(deployment)
        clock = controlled_clock(monkeypatch)

        async def observe(lease, *args, **kwargs):
            leases.append(lease)
            async with aclosing(original_engine(lease, *args, **kwargs)) as stream:
                async for event in stream:
                    yield event
                    if event['type'] == 'task_completed':
                        pending.append(event['type'])
                        if boundary == 'approval_storage':
                            with closing(sqlite3.connect(upload.app.state.settings.tasks_db_path)) as connection:
                                connection.execute('ALTER TABLE document_source_approvals RENAME TO unavailable_approvals')
                                connection.commit()

        def close(authority):
            claimed = authority._claimed and authority._access is not None
            original_close(authority)
            if claimed and boundary == 'issuer_cleanup':
                clock.now = clock.deadline

        monkeypatch.setattr(app_engine, 'run_document_task', observe)
        monkeypatch.setattr(LocalSourceAuthority, 'close', close)
        with provider.open_provider(upload.request, CONVERSATION, deadline=clock.deadline) as scoped:
            terminal, = await run_server_source_document_workflow(
                upload.request, CONVERSATION, PASSAGE, sources=(('report', content),),
                provider=scoped, deadline=clock.deadline,
            )
        assert pending == ['task_completed']
        denied(terminal, 'TIMEOUT' if boundary == 'issuer_cleanup' else 'AUTHORIZATION_UNAVAILABLE')
        lease, = leases
        assert_closed(lease)

    run_offline(run())
