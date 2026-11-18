"""Exercise the installed deployment workflow without starting an external listener."""

import asyncio
import sqlite3
import warnings
from contextlib import aclosing, closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from backend.agent.deployment_documents import get_deployment_documents, install_deployment_documents
from backend.auth.models import User
from backend.auth.request_context import request_tools_enabled
from backend.health.capabilities import capability_report
from backend.multimodal import document_source_approvals, document_transport
from backend.multimodal.document_reasoning import ReasoningError
from backend.security.provenance_foundation import document_content_allowed
from test_a22_async_offline import run_offline
from test_r842_document_extraction_offline import sample_pdf
from test_r844_document_agent_offline import workbook
from test_r848_document_agent_lease_offline import COMPOUND, PASSAGE, Upload, denied


CONVERSATION = 'deployment-conversation'


@pytest.fixture
def create_app(tmp_path, monkeypatch):
    from backend import main
    from backend.config import Settings

    config = Settings(
        _env_file=None, app_env='development', auth_enabled=True, auth_dev_override=False,
        local_chat_only=True, backend_host='127.0.0.1', ollama_base_url='http://127.0.0.1:11434',
        backend_port=8001, cors_origins='http://127.0.0.1:5174', auth_cookie_secure=False,
        auth_cookie_samesite='lax', auth_cookie_domain=None,
        upload_dir=tmp_path / 'uploads', log_dir=tmp_path / 'logs', sandbox_dir=tmp_path / 'sandbox',
        chroma_persist_dir=tmp_path / 'chroma', tasks_dir=tmp_path / 'tasks',
        tasks_db_path=tmp_path / 'tasks' / 'tasks.db', conversation_memory_path=tmp_path / 'memory.json',
    )
    monkeypatch.setattr(main, 'VectorStore', lambda **_: pytest.fail('Unexpected vector store'))
    monkeypatch.setattr(main, 'EmbeddingService', lambda **_: pytest.fail('Unexpected embedding service'))
    monkeypatch.setattr(main, 'initialize_admin_user_if_empty', lambda _: None)
    return lambda: main.create_app(config)


@pytest.fixture
def tools_allowed():
    token = request_tools_enabled.set(True)
    try:
        yield
    finally:
        request_tools_enabled.reset(token)


def create_sessions(app):
    for identity, role, clearance in (('owner', 'operator', 'L2'), ('reviewer', 'admin', 'L3')):
        app.state.auth_store.create_user(User(
            id=identity, username=identity, password_hash='unused', role=role, clearance=clearance,
            is_active=True, must_change_password=False, created_at='2026-01-01T00:00:00+00:00',
        ))
    owner, _ = app.state.session_manager.create_session('owner')
    reviewer, _ = app.state.session_manager.create_session('reviewer')
    return owner, reviewer


async def prepare(app, *, files=None):
    owner_token, reviewer_token = create_sessions(app)
    files = files if files is not None else [('report.pdf', sample_pdf())]
    owner = Upload((app, owner_token), files)
    reviewer = Upload((app, reviewer_token), [])
    await owner.authenticate()
    owner_id, generation = app.state.engine._memory.prepare_new_chat(CONVERSATION, 'owner', 'Synthetic')
    expiry = datetime.now(UTC) + timedelta(minutes=5)
    decisions = [
        dict(reference=filename.split('.')[0], owner_id=owner_id, conversation_id=CONVERSATION,
             generation=generation, grant=grant, expires_at=expiry)
        for (filename, _), grant in zip(files, owner.grants, strict=True)
    ]
    return owner, reviewer, decisions


async def establish(documents, owner, reviewer, decisions):
    await reviewer.authenticate()
    for review in decisions:
        documents.approve(reviewer.request, **review)
    await owner.authenticate()
    for (_, content), review in zip(owner.files, decisions, strict=True):
        documents.register_source(owner.request, CONVERSATION, review['reference'], content,
                                  expires_at=review['expires_at'])


def assert_unavailable(error):
    assert error.value.code == 'AUTHORIZATION_UNAVAILABLE'


def test_actual_lifespan_delivers_ordered_evidence_and_invalidates_saved_composition(create_app, tools_allowed):
    app = create_app()
    pdf = sample_pdf()
    xlsx = workbook(values=(70, '=B2*2', 80))
    routes = tuple(app.routes)

    async def run():
        with pytest.raises(ReasoningError) as before:
            get_deployment_documents(app)
        assert_unavailable(before)
        async with app.router.lifespan_context(app):
            documents = get_deployment_documents(app)
            assert get_deployment_documents(app) is documents
            with pytest.raises(ReasoningError) as duplicate:
                with install_deployment_documents(app):
                    pytest.fail('Second authority installed')
            assert_unavailable(duplicate)
            owner, reviewer, decisions = await prepare(app, files=[('report.pdf', pdf), ('table.xlsx', xlsx)])
            history_path = app.state.settings.conversation_memory_path
            history = history_path.read_bytes()
            await establish(documents, owner, reviewer, decisions)
            started = asyncio.get_running_loop().time()
            with warnings.catch_warnings():
                warnings.filterwarnings('error', module=r'^openpyxl(?:\.|$)')
                events = await documents.run(
                    owner.request, CONVERSATION, COMPOUND, sources=(('report', pdf), ('table', xlsx)),
                )
            terminal = events[-1]
            elapsed = asyncio.get_running_loop().time() - started
            assert terminal['type'] == 'task_completed', (terminal, f'workflow_elapsed={elapsed:.3f}s')
            answer = terminal['answer']
            assert [source['filename'] for source in answer['sources']] == ['report.pdf', 'table.xlsx']
            passage, calculation = answer['findings']
            assert passage['evidence'][0]['location']['page'] == 1 and '7.1 mm/s' in answer['text']
            assert calculation['value'] == '75.00' and calculation['excluded']['formulas'] == 1
            assert [item['location']['cell'] for item in calculation['evidence']] == ['B1', 'B2', 'B4']
            assert history_path.read_bytes() == history
            assert app.state.engine._memory.numeric_receipt(CONVERSATION) is None
            assert not document_transport.has_active_transport()
            assert tuple(app.routes) == routes
            assert document_content_allowed('report') is False
            capabilities = capability_report(
                environment='production', ready=True, planning=True, persistence=True, probe_allowed=False,
                configured_models=dict.fromkeys(('default_chat', 'vision', 'image_chat')), observation=None,
            )
            assert capabilities['document_intelligence']['direct_chat'] == 'blocked'
            assert capabilities['document_intelligence']['artifact_publication'] == 'blocked'
            assert capabilities['capabilities']['document_rag']['state'] == 'blocked'
            assert capabilities['deployment_verified'] is False
        assert app.state.deployment_documents is None
        with pytest.raises(ReasoningError) as after:
            get_deployment_documents(app)
        assert_unavailable(after)
        async with app.router.lifespan_context(app):
            assert get_deployment_documents(app) is not documents
            await reviewer.authenticate()
            with pytest.raises(ReasoningError) as old:
                documents.withdraw_approval(reviewer.request, 'report')
            assert_unavailable(old)
        for path in app.state.settings.tasks_db_path.parent.glob('tasks.db*'):
            assert not any(marker in path.read_bytes() for marker in (pdf, xlsx, b'%PDF-', b'7.1 mm/s'))

    run_offline(run())


@pytest.mark.parametrize('missing', ['auth_store', 'session_manager', 'conversation_registry', 'engine'])
def test_incomplete_state_never_installs_composition(create_app, missing):
    app = create_app()

    async def run():
        async with app.router.lifespan_context(app):
            saved = get_deployment_documents(app)
            delattr(app.state, missing)
            with pytest.raises(ReasoningError) as lost:
                get_deployment_documents(app)
            assert_unavailable(lost)
            app.state.deployment_documents = None
            with pytest.raises(ReasoningError) as incomplete:
                with install_deployment_documents(app):
                    pytest.fail('Incomplete application installed the workflow')
            assert_unavailable(incomplete)
        app.state.deployment_documents = saved
        with pytest.raises(ReasoningError) as stale:
            get_deployment_documents(app)
        assert_unavailable(stale)

    run_offline(run())


def test_split_database_configuration_fails_closed_without_creating_new_storage(create_app, tmp_path):
    app = create_app()
    missing_database = tmp_path / 'missing.db'

    async def run():
        async with app.router.lifespan_context(app):
            documents = get_deployment_documents(app)
            app.state.settings.tasks_db_path = missing_database
            with pytest.raises(ReasoningError) as mismatch:
                get_deployment_documents(app)
            assert_unavailable(mismatch)
            app.state.deployment_documents = None
            with pytest.raises(ReasoningError) as bad_install:
                with install_deployment_documents(app):
                    pytest.fail('Invalid database binding installed')
            assert_unavailable(bad_install)
            assert not missing_database.exists()
            app.state.deployment_documents = documents

    run_offline(run())


@pytest.mark.parametrize('case', ['unauthenticated', 'operator', 'self', 'downgrade', 'clearance', 'stale_generation'])
def test_composition_only_accepts_explicit_live_distinct_admin_review(create_app, tools_allowed, case):
    app = create_app()

    async def run():
        async with app.router.lifespan_context(app):
            documents = get_deployment_documents(app)
            owner, reviewer, (review,) = await prepare(app)
            actor = owner if case in {'operator', 'self'} else reviewer
            if case == 'self':
                app.state.auth_store.update_user('owner', role='admin')
            if case != 'unauthenticated':
                await actor.authenticate()
            if case == 'downgrade':
                app.state.auth_store.update_user('reviewer', role='operator')
            if case == 'clearance':
                app.state.auth_store.update_user('reviewer', clearance='L1')
            if case == 'stale_generation':
                review['generation'] = 'superseded'
            with pytest.raises(ReasoningError) as review_denial:
                documents.approve(actor.request, **review)
            assert_unavailable(review_denial)
            await owner.authenticate()
            with pytest.raises(ReasoningError) as no_approval:
                documents.register_source(owner.request, CONVERSATION, 'report', owner.files[0][1],
                                          expires_at=review['expires_at'])
            assert_unavailable(no_approval)
            with closing(sqlite3.connect(app.state.settings.tasks_db_path)) as connection:
                assert connection.execute('SELECT count(*) FROM document_source_approvals').fetchone() == (0,)
                assert connection.execute('SELECT count(*) FROM approved_document_sources').fetchone() == (0,)

    run_offline(run())


@pytest.mark.parametrize('case', ['digest', 'expired', 'owner_withdrew', 'tools_off'])
def test_registration_and_execution_keep_existing_policy(create_app, tools_allowed, monkeypatch, case):
    app = create_app()

    async def run():
        async with app.router.lifespan_context(app):
            documents = get_deployment_documents(app)
            owner, reviewer, (review,) = await prepare(app)
            content = owner.files[0][1]
            if case == 'digest':
                await reviewer.authenticate()
                review['grant'] = replace(review['grant'], sha256='0' * 64)
                documents.approve(reviewer.request, **review)
                await owner.authenticate()
                with pytest.raises(ReasoningError) as wrong_bytes:
                    documents.register_source(owner.request, CONVERSATION, 'report', content,
                                              expires_at=review['expires_at'])
                assert wrong_bytes.value.code == 'SOURCE_UNAVAILABLE'
            else:
                await establish(documents, owner, reviewer, [review])
                if case == 'expired':
                    class Future(datetime):
                        @classmethod
                        def now(cls, tz=None):
                            return review['expires_at'] + timedelta(seconds=1)

                    monkeypatch.setattr(document_source_approvals, 'datetime', Future)
                elif case == 'owner_withdrew':
                    assert documents.withdraw_source(owner.request, CONVERSATION, 'report')
                else:
                    request_tools_enabled.set(False)
            terminal, = await documents.run(owner.request, CONVERSATION, PASSAGE, sources=(('report', content),))
            denied(terminal, 'AUTHORIZATION_UNAVAILABLE')
            assert owner.reads == 0

    run_offline(run())


def test_distinct_running_apps_see_pending_withdrawal_and_reopen_cannot_restore_it(create_app, tools_allowed, monkeypatch):
    first_app = create_app()
    second_app = create_app()

    async def run():
        async with first_app.router.lifespan_context(first_app):
            documents = get_deployment_documents(first_app)
            owner, reviewer, (review,) = await prepare(first_app)
            content = owner.files[0][1]
            await establish(documents, owner, reviewer, [review])
            execute = first_app.state.engine.run_document_task
            reached_completion = []
            async with second_app.router.lifespan_context(second_app):
                second = get_deployment_documents(second_app)
                assert second is not documents
                other_admin = Upload((second_app, reviewer.token), [])

                async def withdraw():
                    await other_admin.authenticate()
                    assert second.withdraw_approval(other_admin.request, 'report')

                async def observe(lease, *args, **kwargs):
                    async with aclosing(execute(lease, *args, **kwargs)) as stream:
                        async for event in stream:
                            yield event
                            if event['type'] == 'task_completed':
                                reached_completion.append(event['type'])
                                await asyncio.create_task(withdraw())

                monkeypatch.setattr(first_app.state.engine, 'run_document_task', observe)
                terminal, = await documents.run(owner.request, CONVERSATION, PASSAGE, sources=(('report', content),))
                denied(terminal, 'AUTHORIZATION_UNAVAILABLE')
                assert reached_completion == ['task_completed']
                assert not document_transport.has_active_transport()
        async with first_app.router.lifespan_context(first_app):
            reopened = get_deployment_documents(first_app)
            await reviewer.authenticate()
            assert not reopened.withdraw_approval(reviewer.request, 'report')
            with pytest.raises(ReasoningError) as duplicate:
                reopened.approve(reviewer.request, **review)
            assert duplicate.value.code == 'SOURCE_UNAVAILABLE'
            fresh_owner = Upload((first_app, owner.token), owner.files)
            await fresh_owner.authenticate()
            terminal, = await reopened.run(
                fresh_owner.request, CONVERSATION, PASSAGE, sources=(('report', content),),
            )
            denied(terminal, 'AUTHORIZATION_UNAVAILABLE')
            assert fresh_owner.reads == 0

    run_offline(run())


def test_stale_admin_cannot_withdraw_and_lost_lifespan_withholds_evidence(create_app, tools_allowed, monkeypatch):
    app = create_app()

    async def run():
        async with app.router.lifespan_context(app):
            documents = get_deployment_documents(app)
            owner, reviewer, (review,) = await prepare(app)
            await establish(documents, owner, reviewer, [review])
            await reviewer.authenticate()
            app.state.auth_store.update_user('reviewer', role='operator')
            with pytest.raises(ReasoningError) as denied_withdrawal:
                documents.withdraw_approval(reviewer.request, 'report')
            assert_unavailable(denied_withdrawal)
            app.state.auth_store.update_user('reviewer', role='admin')
            await owner.authenticate()
            execute = app.state.engine.run_document_task
            completed = []

            async def interrupt(lease, *args, **kwargs):
                async with aclosing(execute(lease, *args, **kwargs)) as stream:
                    async for event in stream:
                        yield event
                        if event['type'] == 'task_completed':
                            completed.append(event['type'])
                            app.state.startup_complete = False

            monkeypatch.setattr(app.state.engine, 'run_document_task', interrupt)
            terminal, = await documents.run(
                owner.request, CONVERSATION, PASSAGE, sources=(('report', owner.files[0][1]),),
            )
            denied(terminal, 'AUTHORIZATION_UNAVAILABLE')
            assert completed == ['task_completed'] and not document_transport.has_active_transport()
        assert app.state.deployment_documents is None

    run_offline(run())
