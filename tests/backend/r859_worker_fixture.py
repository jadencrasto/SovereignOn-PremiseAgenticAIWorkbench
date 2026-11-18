"""Disposable, socket-denying worker for the explicitly enabled R8.59 process tests."""

import asyncio
import base64
from contextlib import aclosing, closing, redirect_stdout
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import platform
import site
import socket
import sqlite3
import sys


def _offline(event, args):
    if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', 'os.startfile', 'os.startfile/2'}:
        raise AssertionError('Worker blocked network or process operation')
    if event in {'ctypes.dlsym', 'ctypes.dlsym/handle'} and args[1] in {
        'CreateProcessW', 'CreateJobObjectW', 'CreateNamedPipeW', 'ConnectNamedPipe',
        'CryptProtectData', 'CryptUnprotectData', 'CreateFileW',
    }:
        raise AssertionError('Worker blocked protected native operation')


def _emit(**message):
    print(json.dumps(message), file=sys.__stdout__, flush=True)


def _receive():
    line = sys.stdin.readline(8 * 1024 * 1024)
    return json.loads(line) if line else {'op': 'quit'}


async def _serve(root):
    from pytest import MonkeyPatch

    from backend.agent.deployment_documents import get_deployment_documents
    from backend.auth.request_context import request_tools_enabled
    from backend.multimodal import document_source_approvals, document_transport
    from backend.multimodal.document_reasoning import ReasoningError
    from backend.multimodal.document_source_authority import LocalSourceAuthority
    from backend.multimodal.document_source_recovery import quarantine_source_authority
    from backend.security.provenance_foundation import document_content_allowed
    from test_r848_document_agent_lease_offline import PASSAGE, Upload
    from test_r857_deployment_composition_offline import CONVERSATION, create_app, establish, prepare

    with MonkeyPatch.context() as startup:
        app = create_app.__wrapped__(root, startup)()
        routes = tuple(app.routes)
        request_tools_enabled.set(True)
        async with app.router.lifespan_context(app):
            try:
                documents = get_deployment_documents(app)
            except ReasoningError as exc:
                assert exc.code == 'AUTHORIZATION_UNAVAILABLE'
                documents = None
            assert tuple(app.routes) == routes and not document_content_allowed()
            _emit(state='ready' if documents else 'quarantined', pid=os.getpid())
            owner_token = reviewer_token = content = decisions = None
            while (command := _receive())['op'] != 'quit':
                operation = command['op']
                if operation == 'seed':
                    owner, reviewer, decisions = await prepare(app)
                    await establish(documents, owner, reviewer, decisions)
                    owner_token, reviewer_token = owner.token, reviewer.token
                    content = owner.files[0][1]
                    _emit(tokens=(owner_token, reviewer_token), content=base64.b64encode(content).decode('ascii'))
                    continue
                if operation == 'attach':
                    owner_token, reviewer_token = command['tokens']
                    content = base64.b64decode(command['content'], validate=True)
                    _emit(state='attached')
                    continue
                if operation == 'quarantine':
                    quarantine_source_authority(app)
                    _emit(state='quarantined')
                    continue
                if documents is None and operation == 'run':
                    try:
                        get_deployment_documents(app)
                    except ReasoningError as exc:
                        _emit(event='task_failed', code=exc.code, evidence=False, reads=0)
                        continue
                    raise AssertionError('A disabled lifespan started authorizing sources')
                if operation == 'lock':
                    with closing(sqlite3.connect(app.state.settings.tasks_db_path, timeout=1)) as connection:
                        connection.execute('BEGIN IMMEDIATE')
                        _emit(state='locked')
                        assert _receive()['op'] == 'unlock'
                        connection.rollback()
                    _emit(state='unlocked')
                    continue
                if operation == 'revoke_generation':
                    app.state.conversation_registry.revoke(CONVERSATION)
                    _emit(state='revoked')
                    continue
                if operation == 'lower_clearance':
                    app.state.auth_store.update_user('owner', clearance='L1')
                    _emit(state='lowered')
                    continue

                use_reviewer = operation in {'withdraw_approval', 'reapprove'} or command.get('reviewer')
                upload = Upload((app, reviewer_token if use_reviewer else owner_token), [('report.pdf', content)])
                await upload.authenticate()
                try:
                    if operation == 'withdraw_approval':
                        _emit(changed=documents.withdraw_approval(upload.request, 'report'))
                    elif operation == 'withdraw_source':
                        _emit(changed=documents.withdraw_source(upload.request, CONVERSATION, 'report'))
                    elif operation == 'reapprove':
                        documents.approve(upload.request, **decisions[0])
                        _emit(state='approved')
                    elif operation == 'register':
                        documents.register_source(
                            upload.request, CONVERSATION, 'report', content,
                            expires_at=datetime.now(UTC) + timedelta(minutes=5),
                        )
                        _emit(state='registered')
                    elif operation in {'run', 'expired', 'pause_release'}:
                        original_run = app.state.engine.run_document_task
                        original_close = LocalSourceAuthority.close
                        computed = False

                        async def watch(*args, **kwargs):
                            nonlocal computed
                            async with aclosing(original_run(*args, **kwargs)) as stream:
                                async for event in stream:
                                    computed |= event['type'] == 'task_completed'
                                    yield event

                        def pause_after_issuer_check(authority):
                            claimed = authority._claimed and authority._access is not None
                            original_close(authority)
                            if claimed and computed:
                                _emit(state='pending')
                                assert _receive()['op'] == 'continue'

                        class FutureClock(datetime):
                            @classmethod
                            def now(cls, tz=None):
                                return datetime.now(UTC) + timedelta(days=1)

                        with MonkeyPatch.context() as stage:
                            if operation == 'pause_release':
                                stage.setattr(app.state.engine, 'run_document_task', watch)
                                stage.setattr(LocalSourceAuthority, 'close', pause_after_issuer_check)
                            if operation == 'expired':
                                stage.setattr(document_source_approvals, 'datetime', FutureClock)
                            events = await documents.run(
                                upload.request, CONVERSATION, PASSAGE, sources=(('report', content),),
                            )
                        terminal = events[-1]
                        assert not document_transport.has_active_transport()
                        has_evidence = any('answer' in event or event.get('partial') is not None for event in events)
                        _emit(event=terminal['type'], code=terminal.get('error_code'), evidence=has_evidence,
                              reads=upload.reads, computed=computed)
                    else:
                        raise AssertionError('Unknown fixture operation')
                except ReasoningError as exc:
                    _emit(code=exc.code)
        assert getattr(app.state, 'deployment_documents', None) is None


if __name__ == '__main__':
    root = Path(sys.argv[1]).resolve(strict=True)
    socket.gethostname = lambda: 'r859-offline-worker'
    platform.win32_ver = lambda *args, **kwargs: ('11', '10.0.26100', '', '')
    sys.addaudithook(_offline)
    os.environ.update(
        LOG_DIR=str(root / f'worker-{os.getpid()}'), PROVENANCE_KEY_MODE='ephemeral',
        ANONYMIZED_TELEMETRY='False', CHROMA_OTEL_GRANULARITY='none', OTEL_SDK_DISABLED='true',
    )
    # -S defers site initialization until the worker audit hook is in place.
    site.main()
    sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parent)]
    from test_a22_async_offline import run_offline

    with redirect_stdout(sys.stderr):
        run_offline(_serve(root))
