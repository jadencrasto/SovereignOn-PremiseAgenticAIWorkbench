"""Scoped record-adapter regressions with independently composed synthetic approvals."""

import asyncio
from contextlib import aclosing
from dataclasses import replace
import hashlib

import pytest

from backend.agent.document_workflow import run_server_source_document_workflow
from backend.auth.models import ClearanceLevel
from backend.multimodal.document_reasoning import ReasoningError
from backend.multimodal.document_source_authority import ApprovedLocalSource
from backend.multimodal.document_source_policy import LocalSourceRecord
from backend.multimodal.document_source_records import ServerSourceRecord, open_server_source_policy
from backend.multimodal.document_transport import DocumentGrant, has_active_transport
from backend.security.provenance_foundation import QuarantinedSources, document_content_allowed
from test_a22_async_offline import run_offline
from test_r842_document_extraction_offline import sample_docx, sample_pdf, save_office
from test_r848_document_agent_lease_offline import PASSAGE, Upload, app_engine, host  # noqa: F401


CONVERSATION = 'a-conversation'


class FixtureProvider:
    def __init__(self, records=()):
        self.records = {item.record.reference: item for item in records}
        self.calls = []

    def get_approved_source(self, reference, owner_id, conversation_id, generation):
        self.calls.append((reference, owner_id, conversation_id, generation))
        return self.records.get(reference)


def server_record(engine, filename, content, reference='report'):
    owner, generation = engine._memory.stream_identity(CONVERSATION)
    approved = ApprovedLocalSource(
        owner, 'fixture-revision', DocumentGrant(filename, len(content), hashlib.sha256(content).hexdigest(), ClearanceLevel.L2),
    )
    return ServerSourceRecord(
        LocalSourceRecord(reference, CONVERSATION, generation, approved),
        expires_at=asyncio.get_running_loop().time() + 60, revoked=False,
    )


def assert_denial(events, code):
    terminal, = events
    assert terminal['type'] == 'task_failed'
    assert terminal['error_code'] == code and 'answer' not in terminal and terminal['partial'] is None


@pytest.mark.parametrize('case', ['unconfigured', 'quarantined', 'missing', 'provider_error', 'unauthenticated'])
def test_missing_authority_never_admits_or_reads_request(host, app_engine, case):
    content = sample_pdf()
    upload = Upload(host, [('report.pdf', content)])

    class UnavailableProvider:
        def get_approved_source(self, *args):
            raise RuntimeError('private provider details')

    async def run():
        if case != 'unauthenticated':
            await upload.authenticate()
        provider = {
            'quarantined': QuarantinedSources.__new__(QuarantinedSources),
            'missing': FixtureProvider(),
            'provider_error': UnavailableProvider(),
            'unauthenticated': FixtureProvider(),
        }.get(case)
        events = await run_server_source_document_workflow(
            upload.request, CONVERSATION, PASSAGE, provider=provider, sources=(('report', content),),
        )
        assert_denial(events, 'AUTHORIZATION_UNAVAILABLE')
        assert upload.reads == 0 and 'private' not in events[0]['error']
        if case == 'unauthenticated':
            assert provider.calls == []
        assert document_content_allowed() is False

    run_offline(run())


@pytest.mark.parametrize('mismatch', [
    'owner', 'conversation', 'generation', 'reference', 'clearance', 'revoked', 'expired', 'nonfinite', 'raw_metadata',
])
def test_adapter_rejects_ineligible_provider_records(host, app_engine, mismatch):
    content = sample_pdf()
    upload = Upload(host, [('report.pdf', content)], headers=[(b'x-user-id', b'b'), (b'x-clearance', b'L3')])

    async def run():
        await upload.authenticate()
        good = server_record(app_engine, 'report.pdf', content)
        record, approval = good.record, good.record.approval
        bad = {
            'owner': replace(good, record=replace(record, approval=replace(approval, owner_id='b'))),
            'conversation': replace(good, record=replace(record, conversation_id='b-conversation')),
            'generation': replace(good, record=replace(record, generation='old')),
            'reference': replace(good, record=replace(record, reference='other')),
            'clearance': replace(good, record=replace(record, approval=replace(
                approval, grant=replace(approval.grant, clearance=ClearanceLevel.L3),
            ))),
            'revoked': replace(good, revoked=True),
            'expired': replace(good, expires_at=asyncio.get_running_loop().time()),
            'nonfinite': replace(good, expires_at=float('inf')),
            'raw_metadata': replace(good, record=replace(record, approval=replace(
                approval, grant=replace(approval.grant, filename=content),
            ))),
        }[mismatch]
        provider = FixtureProvider()
        provider.records['report'] = bad
        events = await run_server_source_document_workflow(
            upload.request, CONVERSATION, PASSAGE, provider=provider, sources=(('report', content),),
        )
        assert_denial(events, 'AUTHORIZATION_UNAVAILABLE')
        assert provider.calls == [('report', 'a', CONVERSATION, record.generation)]
        assert upload.reads == 0

    run_offline(run())


def test_bound_policy_cannot_be_reused_after_request_or_context_exit(host, app_engine):
    content = sample_pdf()
    first, other, closed = (Upload(host, [('report.pdf', content)]) for _ in range(3))

    async def run():
        await first.authenticate()
        provider = FixtureProvider((server_record(app_engine, 'report.pdf', content),))
        with open_server_source_policy(first.request, CONVERSATION, ('report',), provider=provider) as policy:
            with policy.open_authority(first.request, CONVERSATION) as authority:
                authority.register('report', content)
                assert len(authority('a', CONVERSATION)) == 1
            await other.authenticate()
            with policy.open_authority(other.request, CONVERSATION) as authority:
                with pytest.raises(ReasoningError) as rejected:
                    authority.register('report', content)
                assert rejected.value.code == 'AUTHORIZATION_UNAVAILABLE'
        await closed.authenticate()
        with open_server_source_policy(closed.request, CONVERSATION, ('report',), provider=provider) as closed_policy:
            assert closed_policy is not policy
        with closed_policy.open_authority(closed.request, CONVERSATION) as authority:
            with pytest.raises(ReasoningError) as rejected:
                authority.register('report', content)
            assert rejected.value.code == 'AUTHORIZATION_UNAVAILABLE'
        assert first.reads == other.reads == closed.reads == 0

    run_offline(run())


@pytest.mark.parametrize('change', ['success', 'revoked', 'expired', 'revision', 'digest', 'length'])
def test_workflow_uses_fresh_server_metadata_and_existing_byte_binding(host, app_engine, monkeypatch, tmp_path, change):
    pdf = sample_pdf()
    docx = save_office(sample_docx())
    upload = Upload(host, [('report.pdf', pdf), ('notes.docx', docx)])
    original = app_engine.run_document_task
    history = (tmp_path / 'memory.json').read_bytes()

    async def run():
        await upload.authenticate()
        approved = server_record(app_engine, 'report.pdf', pdf)
        other = server_record(app_engine, 'notes.docx', docx, 'notes')
        provider = FixtureProvider((other, approved))
        engine_completed = []

        async def observe(lease, *args, **kwargs):
            assert [handle.filename for handle in lease.sources()] == ['report.pdf', 'notes.docx']
            async with aclosing(original(lease, *args, **kwargs)) as events:
                async for event in events:
                    yield event
                    if event['type'] == 'task_completed':
                        engine_completed.append(event['type'])
                        if change == 'revoked':
                            provider.records['report'] = replace(approved, revoked=True)
                        elif change == 'expired':
                            provider.records['report'] = replace(approved, expires_at=asyncio.get_running_loop().time())
                        elif change == 'revision':
                            provider.records['report'] = replace(approved, record=replace(
                                approved.record, approval=replace(approved.record.approval, revision='new-revision'),
                            ))

        monkeypatch.setattr(app_engine, 'run_document_task', observe)
        supplied = {
            'digest': pdf[:-1] + bytes([pdf[-1] ^ 1]),
            'length': pdf[:-1],
        }.get(change, pdf)
        events = await run_server_source_document_workflow(
            upload.request, CONVERSATION, PASSAGE, provider=provider, sources=(('report', supplied), ('notes', docx)),
        )
        if change == 'success':
            assert events[-1]['type'] == 'task_completed'
            assert '7.1 mm/s' in events[-1]['answer']['text']
            assert engine_completed == ['task_completed'] and len(provider.calls) > 2
        elif change in {'digest', 'length'}:
            assert_denial(events, 'SOURCE_UNAVAILABLE')
            assert upload.reads == 0 and engine_completed == []
        else:
            assert engine_completed == ['task_completed']
            assert_denial(events, 'AUTHORIZATION_UNAVAILABLE')
        assert not has_active_transport() and app_engine._memory.numeric_receipt(CONVERSATION) is None

    run_offline(run())
    assert (tmp_path / 'memory.json').read_bytes() == history
    assert not (tmp_path / 'tasks.db').exists()
