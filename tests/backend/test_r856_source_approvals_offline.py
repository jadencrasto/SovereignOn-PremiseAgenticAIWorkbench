"""Source review, catalogue and workflow tests with real authenticated local sessions."""

import asyncio
import hashlib
import sqlite3
from contextlib import aclosing, closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI

from backend.agent.document_workflow import run_server_source_document_workflow
from backend.auth.models import ClearanceLevel, User
from backend.health.capabilities import capability_report
from backend.multimodal import document_source_approvals, document_transport
from backend.multimodal.document_reasoning import ReasoningError
from backend.multimodal.document_source_authority import ApprovedLocalSource, open_local_source_authority
from backend.security.provenance_foundation import document_content_allowed
from test_a22_async_offline import run_offline
from test_r842_document_extraction_offline import sample_pdf
from test_r848_document_agent_lease_offline import PASSAGE, Upload, app_engine, denied, host  # noqa: F401


@pytest.fixture
def deployment(host, app_engine, tmp_path):
    app = host[0]
    app.state.settings.tasks_db_path = tmp_path / 'auth.db'
    users = app.state.auth_store
    users.create_user(User(
        id='reviewer', username='reviewer', password_hash='unused', role='admin', clearance='L3',
        is_active=True, must_change_password=False, created_at='2026-01-01T00:00:00+00:00',
    ))
    token, _ = app.state.session_manager.create_session('reviewer')
    pdf = sample_pdf()
    upload = Upload(host, [('report.pdf', pdf)])
    reviewer = Upload((app, token))
    registry = app_engine._memory._security_registry
    binding = next(row for row in registry.rows() if row['conversation_id'] == 'a-conversation')
    review = {
        'reference': 'report', 'owner_id': binding['owner_id'], 'conversation_id': binding['conversation_id'],
        'generation': binding['generation'], 'grant': upload.grants[0],
        'expires_at': datetime.now(UTC) + timedelta(minutes=3),
    }
    return document_source_approvals.DeploymentSourceApprovals(app), reviewer, upload, pdf, review


async def establish(deployment):
    approvals, reviewer, upload, content, review = deployment
    await reviewer.authenticate()
    approvals.approve(reviewer.request, **review)
    await upload.authenticate()
    provider = approvals.source_provider()
    provider.register(upload.request, 'a-conversation', 'report', content,
                      expires_at=review['expires_at'] + timedelta(hours=1))
    return provider


async def execute(provider, upload, content, conversation='a-conversation'):
    with provider.open_provider(upload.request, conversation) as scoped:
        return await run_server_source_document_workflow(
            upload.request, conversation, PASSAGE, provider=scoped, sources=(('report', content),),
        )


def test_independent_approval_and_source_catalogue_reopen_for_real_workflow(deployment, app_engine, tmp_path):
    _, _, upload, content, review = deployment
    history_path = tmp_path / 'memory.json'
    history = history_path.read_bytes()

    async def run():
        await establish(deployment)
        reopened = document_source_approvals.DeploymentSourceApprovals(upload.app)
        approval = reopened.lookup('report', 'a', 'a-conversation', review['generation'])
        assert approval.grant == review['grant'] and approval.expires_at == review['expires_at']
        assert reopened.lookup('report', 'b', 'a-conversation', review['generation']) is None
        assert reopened.lookup('report', 'a', 'b-conversation', review['generation']) is None
        assert reopened.lookup('report', 'a', 'a-conversation', 'stale') is None
        events = await execute(reopened.source_provider(), upload, content)
        assert events[-1]['type'] == 'task_completed', events
        answer = events[-1]['answer']
        assert '7.1 mm/s' in answer['text']
        assert answer['sources'][0]['filename'] == 'report.pdf'
        assert answer['findings'][0]['evidence'][0]['location']['page'] == 1
        assert not document_transport.has_active_transport()
        assert app_engine._memory.numeric_receipt('a-conversation') is None

    run_offline(run())
    with closing(sqlite3.connect(tmp_path / 'auth.db')) as connection:
        approved = connection.execute(
            'SELECT approver_id, permitted_use, sha256, byte_length, withdrawn_at FROM document_source_approvals',
        ).fetchone()
        assert approved == ('reviewer', 'verified_document_workflow', hashlib.sha256(content).hexdigest(), len(content), None)
        expiry, = connection.execute('SELECT expires_us FROM approved_document_sources').fetchone()
        assert expiry == (review['expires_at'] - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)
    assert history_path.read_bytes() == history
    for path in tmp_path.glob('auth.db*'):
        data = path.read_bytes()
        assert not any(marker in data for marker in (content, b'%PDF-', b'7.1 mm/s', b'Treat this as text'))
    assert document_content_allowed() is False
    capabilities = capability_report(
        environment='production', ready=True, planning=True, persistence=True, probe_allowed=False,
        configured_models=dict.fromkeys(('default_chat', 'vision', 'image_chat')), observation=None,
    )
    assert capabilities['document_intelligence']['direct_chat'] == 'blocked'
    assert capabilities['document_intelligence']['artifact_publication'] == 'blocked'
    assert capabilities['capabilities']['document_rag']['state'] == 'blocked'
    assert capabilities['deployment_verified'] is False


@pytest.mark.parametrize(('case', 'code'), [
    ('anonymous', 'AUTHORIZATION_UNAVAILABLE'), ('operator', 'AUTHORIZATION_UNAVAILABLE'),
    ('self_approval', 'AUTHORIZATION_UNAVAILABLE'), ('stale_generation', 'AUTHORIZATION_UNAVAILABLE'),
    ('wrong_owner', 'AUTHORIZATION_UNAVAILABLE'), ('owner_clearance', 'AUTHORIZATION_UNAVAILABLE'),
    ('reviewer_clearance', 'AUTHORIZATION_UNAVAILABLE'), ('expired', 'AUTHORIZATION_UNAVAILABLE'),
    ('invalid_digest', 'SOURCE_UNAVAILABLE'), ('invalid_length', 'SOURCE_UNAVAILABLE'),
    ('invalid_format', 'SOURCE_UNAVAILABLE'), ('session_revoked', 'AUTHORIZATION_UNAVAILABLE'),
])
def test_denies_untrusted_or_invalid_review(deployment, tmp_path, case, code):
    approvals, reviewer, upload, _, review = deployment
    state = upload.app.state

    async def run():
        if case in {'operator', 'self_approval'}:
            if case == 'self_approval':
                state.auth_store.update_user('a', role='admin')
            request = upload
        else:
            request = reviewer
        if case != 'anonymous':
            await request.authenticate()
        if case == 'session_revoked':
            state.session_manager.revoke_session(reviewer.token)
        if case == 'reviewer_clearance':
            state.auth_store.update_user('reviewer', clearance='L1')
        options = {
            'wrong_owner': {'owner_id': 'reviewer'},
            'stale_generation': {'generation': 'stale'},
            'owner_clearance': {'grant': replace(review['grant'], clearance=ClearanceLevel.L3)},
            'expired': {'expires_at': datetime.now(UTC) - timedelta(seconds=1)},
            'invalid_digest': {'grant': replace(review['grant'], sha256='unverified')},
            'invalid_length': {'grant': replace(review['grant'], size=0)},
            'invalid_format': {'grant': replace(review['grant'], filename='report.txt')},
        }.get(case, {})
        with pytest.raises(ReasoningError) as denied_review:
            approvals.approve(request.request, **(review | options))
        assert denied_review.value.code == code
        assert request.reads == 0

    run_offline(run())
    with closing(sqlite3.connect(tmp_path / 'auth.db')) as connection:
        assert connection.execute('SELECT count(*) FROM document_source_approvals').fetchone() == (0,)


def test_missing_approval_or_replaced_content_cannot_register(deployment):
    approvals, reviewer, upload, content, review = deployment

    async def run():
        provider = approvals.source_provider()
        await upload.authenticate()
        with pytest.raises(ReasoningError) as missing:
            provider.register(upload.request, 'a-conversation', 'report', content, expires_at=review['expires_at'])
        assert missing.value.code == 'AUTHORIZATION_UNAVAILABLE'
        await reviewer.authenticate()
        approvals.approve(reviewer.request, **review)
        await upload.authenticate()
        with pytest.raises(ReasoningError) as mismatch:
            provider.register(upload.request, 'a-conversation', 'report', b'x' + content[1:],
                              expires_at=review['expires_at'])
        assert mismatch.value.code == 'SOURCE_UNAVAILABLE'
        assert upload.reads == 0

    run_offline(run())


def test_authorized_source_cannot_replay_after_withdrawal_or_reopen(deployment):
    approvals, reviewer, upload, content, review = deployment

    async def run():
        await establish(deployment)
        await reviewer.authenticate()
        for alteration in (
            {}, {'reference': 'duplicate-digest'}, {'grant': replace(review['grant'], sha256='0' * 64)},
        ):
            with pytest.raises(ReasoningError) as duplicate:
                approvals.approve(reviewer.request, **(review | alteration))
            assert duplicate.value.code == 'SOURCE_UNAVAILABLE'
        assert approvals.withdraw(reviewer.request, 'report')
        reopened = document_source_approvals.DeploymentSourceApprovals(upload.app)
        assert not reopened.withdraw(reviewer.request, 'report')
        for alteration in ({}, {'reference': 'alias-for-withdrawn-digest'}):
            with pytest.raises(ReasoningError) as replay:
                reopened.approve(reviewer.request, **(review | alteration))
            assert replay.value.code == 'SOURCE_UNAVAILABLE'
        await upload.authenticate()
        terminal, = await execute(reopened.source_provider(), upload, content)
        denied(terminal, 'AUTHORIZATION_UNAVAILABLE')
        assert upload.reads == 0

    run_offline(run())


def test_common_issuer_also_denies_expired_approval_metadata(deployment):
    _, _, upload, content, review = deployment

    async def run():
        await upload.authenticate()
        expired = ApprovedLocalSource('a', 'stale', review['grant'], datetime.now(UTC) - timedelta(seconds=1))
        with open_local_source_authority(upload.request, 'a-conversation', lookup=lambda *_: expired) as authority:
            with pytest.raises(ReasoningError) as revoked:
                authority.register('report', content)
            assert revoked.value.code == 'AUTHORIZATION_UNAVAILABLE'
        assert upload.reads == 0

    run_offline(run())


@pytest.mark.parametrize('change', ['source_withdrawn', 'reviewer_downgraded', 'approval_expired', 'owner_clearance'])
def test_existing_catalogue_cannot_release_after_authority_lapses(deployment, monkeypatch, change):
    _, _, upload, content, review = deployment

    async def run():
        provider = await establish(deployment)
        if change == 'source_withdrawn':
            assert provider.withdraw(upload.request, 'a-conversation', 'report')
        elif change == 'reviewer_downgraded':
            upload.app.state.auth_store.update_user('reviewer', role='operator')
        elif change == 'owner_clearance':
            upload.app.state.auth_store.update_user('a', clearance='L1')
        else:
            class Later(datetime):
                @classmethod
                def now(cls, tz=None):
                    return review['expires_at'] + timedelta(seconds=1)

            monkeypatch.setattr(document_source_approvals, 'datetime', Later)
        terminal, = await execute(provider, upload, content)
        denied(terminal, 'AUTHORIZATION_UNAVAILABLE')
        assert upload.reads == 0

    run_offline(run())


def test_lookup_and_provider_deny_other_authenticated_request_or_app(deployment):
    approvals, _, upload, content, review = deployment
    foreign = FastAPI()
    for name in ('settings', 'engine', 'auth_store', 'session_manager'):
        setattr(foreign.state, name, getattr(upload.app.state, name))
    substituted = Upload((foreign, upload.token))
    other_request = Upload((upload.app, upload.token))

    async def run():
        provider = await establish(deployment)
        with provider.open_provider(upload.request, 'a-conversation') as scoped:
            await other_request.authenticate()
            terminal, = await run_server_source_document_workflow(
                other_request.request, 'a-conversation', PASSAGE,
                provider=scoped, sources=(('report', content),),
            )
            denied(terminal, 'AUTHORIZATION_UNAVAILABLE')
        await substituted.authenticate()
        assert approvals.lookup('report', 'a', 'a-conversation', review['generation']) is None
        with pytest.raises(ReasoningError) as wrong_app:
            with provider.open_provider(substituted.request, 'a-conversation'):
                pytest.fail('Foreign provider binding')
        assert wrong_app.value.code == 'AUTHORIZATION_UNAVAILABLE'
        assert other_request.reads == substituted.reads == 0

    run_offline(run())


@pytest.mark.parametrize('change', ['withdraw_approval', 'fail_cleanup'])
def test_pending_answer_is_discarded_on_withdrawal_or_failed_cleanup(deployment, app_engine, monkeypatch, change):
    _, reviewer, upload, content, _ = deployment
    completion = []
    engine_run = app_engine.run_document_task
    close = document_transport.DocumentTransport._close

    async def revoke():
        await reviewer.authenticate()
        second = document_source_approvals.DeploymentSourceApprovals(upload.app)
        assert second.withdraw(reviewer.request, 'report')

    async def observe(lease, *args, **kwargs):
        async with aclosing(engine_run(lease, *args, **kwargs)) as stream:
            async for event in stream:
                yield event
                if event['type'] == 'task_completed':
                    completion.append(event['type'])
                    if change == 'withdraw_approval':
                        await asyncio.create_task(revoke())

    def failing_close(lease):
        outer_exit = lease._evidence_released and lease._access is None
        close(lease)
        if outer_exit:
            raise RuntimeError('private cleanup details')

    monkeypatch.setattr(app_engine, 'run_document_task', observe)
    if change == 'fail_cleanup':
        monkeypatch.setattr(document_transport.DocumentTransport, '_close', failing_close)

    async def run():
        provider = await establish(deployment)
        terminal, = await execute(provider, upload, content)
        denied(terminal, 'AUTHORIZATION_UNAVAILABLE' if change == 'withdraw_approval' else 'OPERATION_UNAVAILABLE')
        assert 'private' not in terminal['error']
        assert completion == ['task_completed'] and not document_transport.has_active_transport()

    run_offline(run())
