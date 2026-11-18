"""Exercise the server-owned policy with authenticated synthetic local documents."""

import asyncio
from contextlib import aclosing
from dataclasses import replace
import hashlib

import pytest

from backend.agent.document_workflow import run_local_document_workflow
from backend.auth.models import ClearanceLevel, Permission
from backend.multimodal import document_source_policy, document_transport
from backend.multimodal.document_reasoning import ReasoningError
from backend.multimodal.document_source_authority import ApprovedLocalSource
from backend.multimodal.document_source_policy import LocalSourcePolicy, LocalSourceRecord
from backend.multimodal.document_transport import DocumentGrant, open_document_transport
from backend.security.provenance_foundation import document_content_allowed
from test_a22_async_offline import run_offline
from test_r842_document_extraction_offline import sample_docx, sample_pdf, save_office
from test_r848_document_agent_lease_offline import Upload, app_engine, host  # noqa: F401


CONVERSATION = 'a-conversation'
QUESTION = 'Find the section about pump vibration in source 1'


def record(engine, reference, filename, data, *, owner='a', clearance=ClearanceLevel.L2):
    _, generation = engine._memory.stream_identity(CONVERSATION)
    grant = DocumentGrant(filename, len(data), hashlib.sha256(data).hexdigest(), clearance)
    return LocalSourceRecord(reference, CONVERSATION, generation, ApprovedLocalSource(owner, 'approved-v1', grant))


async def workflow(upload, policy, sources, *, conversation=CONVERSATION, **options):
    return await run_local_document_workflow(
        upload.request, conversation, QUESTION, source_policy=policy, sources=sources, **options,
    )


def assert_withheld(events, code):
    terminal = events[-1]
    assert terminal['error_code'] == code
    assert terminal['type'] == ('task_cancelled' if code == 'CANCELLED' else 'task_failed')
    assert all('answer' not in event and event.get('partial') is None for event in events)


def test_policy_registered_sources_keep_order_and_deliver_after_cleanup(host, app_engine, monkeypatch, tmp_path, caplog):
    pdf = sample_pdf()
    docx = save_office(sample_docx())
    upload = Upload(host, [('report.pdf', pdf), ('notes.docx', docx)])
    original = app_engine.run_document_task
    seen = []
    baseline = (tmp_path / 'memory.json').read_bytes()

    async def observe(lease, *args, **kwargs):
        assert kwargs['handles'] is lease.sources()
        assert [handle.filename for handle in kwargs['handles']] == ['report.pdf', 'notes.docx']
        seen.append(lease)
        async with aclosing(original(lease, *args, **kwargs)) as events:
            async for event in events:
                assert document_transport.has_active_transport()
                yield event

    def no_provider(*args, **kwargs):
        pytest.fail('The local document workflow reached a model provider')

    monkeypatch.setattr(app_engine, 'run_document_task', observe)
    monkeypatch.setattr(app_engine._router, 'get_provider_for_model', no_provider)

    async def run():
        await upload.authenticate()
        # Catalog insertion order does not determine ordinal upload slots.
        policy = LocalSourcePolicy(host[0], (
            record(app_engine, 'notes', 'notes.docx', docx),
            record(app_engine, 'report', 'report.pdf', pdf),
        ))
        events = await workflow(upload, policy, (('report', pdf), ('notes', docx)))
        assert not document_transport.has_active_transport()
        assert len(seen) == 1 and seen[0]._scope is None and seen[0]._files == ()
        assert all('answer' not in event and 'partial' not in event for event in events[:-1])
        final = events[-1]
        assert final['type'] == 'task_completed'
        passage, = final['answer']['findings']
        assert passage['evidence'][0]['location'] == {'page': 1}
        assert '7.1 mm/s' in passage['evidence'][0]['value']
        assert app_engine._memory.numeric_receipt(CONVERSATION) is None
        assert document_content_allowed() is False

    run_offline(run())
    assert (tmp_path / 'memory.json').read_bytes() == baseline
    assert not (tmp_path / 'tasks.db').exists()
    assert '7.1 mm/s' not in caplog.text


@pytest.mark.parametrize('case', [
    'unauthenticated', 'wrong_conversation_owner', 'wrong_source_owner', 'stale_generation',
    'same_owner_other_conversation', 'viewer', 'clearance', 'fake_policy', 'absent_record',
])
def test_policy_denies_unapproved_or_spoofed_sources_before_body_read(host, app_engine, case):
    pdf = sample_pdf()
    upload = Upload(host, [('report.pdf', pdf)], headers=[(b'x-user-id', b'b'), (b'x-clearance', b'L3')])

    async def run():
        authenticated = Upload(host) if case == 'unauthenticated' else upload
        await authenticated.authenticate()
        source = record(app_engine, 'report', 'report.pdf', pdf, owner='b' if case == 'wrong_source_owner' else 'a',
                        clearance=ClearanceLevel.L3 if case == 'clearance' else ClearanceLevel.L2)
        if case == 'stale_generation':
            source = replace(source, generation='retired-generation')
        policy = LocalSourcePolicy(host[0], () if case == 'absent_record' else (source,))
        if case == 'viewer':
            host[0].state.auth_store.update_user('a', role='viewer')
        if case == 'fake_policy':
            policy = {'owner_id': 'a', 'role': 'admin', 'clearance': 'L3', 'report': source}
        if case == 'same_owner_other_conversation':
            app_engine._memory.prepare_new_chat('a-other', 'a', 'New conversation')
        conversation = {
            'wrong_conversation_owner': 'b-conversation',
            'same_owner_other_conversation': 'a-other',
        }.get(case, CONVERSATION)
        assert_withheld(await workflow(upload, policy, (('report', pdf),), conversation=conversation),
                        'AUTHORIZATION_UNAVAILABLE')
        assert upload.reads == 0

    run_offline(run())


@pytest.mark.parametrize(('change', 'code', 'reads'), [
    ('registration_length', 'SOURCE_UNAVAILABLE', False),
    ('registration_digest', 'SOURCE_UNAVAILABLE', False),
    ('transport_digest', 'SOURCE_UNAVAILABLE', True),
    ('transport_length', 'SOURCE_UNAVAILABLE', True),
    ('swapped_slots', 'SOURCE_UNAVAILABLE', True),
])
def test_transport_keeps_independent_approval_byte_and_order_binding(host, app_engine, change, code, reads):
    pdf = sample_pdf()
    altered = pdf[:-1] + bytes([pdf[-1] ^ 1])
    docx = save_office(sample_docx())
    body = [
        ('report.pdf', {'transport_digest': altered, 'transport_length': pdf[:-1]}.get(change, pdf)),
        ('notes.docx', docx),
    ]
    upload = Upload(host, list(reversed(body)) if change == 'swapped_slots' else body)

    async def run():
        await upload.authenticate()
        policy = LocalSourcePolicy(host[0], (
            record(app_engine, 'report', 'report.pdf', pdf),
            record(app_engine, 'notes', 'notes.docx', docx),
        ))
        supplied = {'registration_length': pdf[:-1], 'registration_digest': altered}.get(change, pdf)
        assert_withheld(await workflow(upload, policy, (('report', supplied), ('notes', docx))), code)
        assert (upload.reads > 0) is reads

    run_offline(run())


def test_policy_grants_cannot_be_substituted_into_a_second_request(host, app_engine):
    pdf = sample_pdf()
    first, second = (Upload(host, [('report.pdf', pdf)]) for _ in range(2))

    async def run():
        await first.authenticate()
        policy = LocalSourcePolicy(host[0], (record(app_engine, 'report', 'report.pdf', pdf),))
        with policy.open_authority(first.request, CONVERSATION) as authority:
            authority.register('report', pdf)
            with pytest.raises(ReasoningError) as wrong_request:
                async with open_document_transport(second.request, CONVERSATION, source_authority=authority):
                    pytest.fail('Foreign request accepted the original authority')
            assert wrong_request.value.code == 'REQUEST_CLOSED'
            grants = authority('a', CONVERSATION)
            await second.authenticate()
            with pytest.raises(ReasoningError) as replay:
                async with open_document_transport(second.request, CONVERSATION, source_authority=lambda *_: grants):
                    pytest.fail('Copied grants accepted')
            assert replay.value.code == 'AUTHORIZATION_UNAVAILABLE'
            assert first.reads == second.reads == 0

    run_offline(run())


def test_revocation_checks_write_permission_and_rejects_future_registration(host, app_engine, monkeypatch):
    pdf = sample_pdf()
    first, later = (Upload(host, [('report.pdf', pdf)]) for _ in range(2))
    original = document_source_policy.has_permission

    async def run():
        await first.authenticate()
        policy = LocalSourcePolicy(host[0], (record(app_engine, 'report', 'report.pdf', pdf),))
        with monkeypatch.context() as no_write:
            no_write.setattr(document_source_policy, 'has_permission',
                             lambda role, permission: permission is not Permission.EXECUTE_WRITE_TOOLS
                             and original(role, permission))
            with pytest.raises(ReasoningError) as denied:
                policy.revoke(first.request, CONVERSATION, 'report')
            assert denied.value.code == 'AUTHORIZATION_UNAVAILABLE'
        with policy.open_authority(first.request, CONVERSATION) as authority:
            authority.register('report', pdf)
            assert len(authority('a', CONVERSATION)) == 1
        policy.revoke(first.request, CONVERSATION, 'report')
        await later.authenticate()
        assert_withheld(await workflow(later, policy, (('report', pdf),)), 'AUTHORIZATION_UNAVAILABLE')
        assert later.reads == 0

    run_offline(run())


@pytest.mark.parametrize(('change', 'code'), [
    ('revoked_source', 'AUTHORIZATION_UNAVAILABLE'), ('session', 'AUTHORIZATION_UNAVAILABLE'),
    ('generation', 'AUTHORIZATION_UNAVAILABLE'), ('ownership', 'AUTHORIZATION_UNAVAILABLE'),
    ('clearance', 'AUTHORIZATION_UNAVAILABLE'), ('expiry', 'TIMEOUT'), ('cancel', 'CANCELLED'),
    ('cleanup', 'OPERATION_UNAVAILABLE'),
])
def test_late_policy_changes_discard_buffered_evidence(host, app_engine, monkeypatch, change, code):
    pdf = sample_pdf()
    upload = Upload(host, [('report.pdf', pdf)])
    engine_run = app_engine.run_document_task

    async def run():
        await upload.authenticate()
        policy = LocalSourcePolicy(host[0], (record(app_engine, 'report', 'report.pdf', pdf),))
        checked = []

        async def observe(lease, *args, **kwargs):
            access = lease._access
            async with aclosing(engine_run(lease, *args, **kwargs)) as events:
                async for event in events:
                    yield event
                    if event['type'] != 'task_completed':
                        continue
                    checked.append(event['type'])
                    if change == 'revoked_source':
                        policy.revoke(upload.request, CONVERSATION, 'report')
                    elif change == 'session':
                        host[0].state.session_manager.revoke_session(host[1])
                    elif change in {'generation', 'ownership'}:
                        owner = 'b' if change == 'ownership' else 'a'
                        monkeypatch.setattr(app_engine._memory, 'stream_identity', lambda _: (owner, 'new-generation'))
                    elif change == 'clearance':
                        host[0].state.auth_store.update_user('a', clearance='L1')
                    elif change == 'expiry':
                        access.deadline = asyncio.get_running_loop().time()
                    elif change == 'cancel':
                        upload.cancelled = True
                    else:
                        close = document_transport.DocumentTransport._close

                        def broken(transport):
                            close(transport)
                            raise RuntimeError('Private cleanup failure')

                        monkeypatch.setattr(document_transport.DocumentTransport, '_close', broken)

        monkeypatch.setattr(app_engine, 'run_document_task', observe)
        result = await workflow(upload, policy, (('report', pdf),), cancelled=lambda: upload.cancelled)
        assert checked == ['task_completed']
        assert not document_transport.has_active_transport()
        assert_withheld(result, code)
        assert len(result) == 1 and 'Private' not in result[0]['error']

    run_offline(run())
