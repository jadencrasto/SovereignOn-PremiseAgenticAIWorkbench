"""Release-boundary regressions for deployed source authorization."""

import asyncio
import base64
from contextlib import ExitStack, closing, contextmanager
import json
import os
from pathlib import Path
from queue import Empty, Queue
import sqlite3
import subprocess
import sys
from threading import Thread

import pytest

from backend.agent.document_workflow import run_server_source_document_workflow
from backend.multimodal.document_source_authority import LocalSourceAuthority
from test_a22_async_offline import run_offline
from test_r842_document_extraction_offline import sample_docx, save_office
from test_r848_document_agent_lease_offline import PASSAGE, Upload, app_engine, denied, host  # noqa: F401
from test_r856_source_approvals_offline import deployment, establish, execute  # noqa: F401


@pytest.mark.parametrize('table, column, marker', [
    ('document_source_approvals', 'withdrawn_at', '2026-01-01T00:00:00+00:00'),
    ('approved_document_sources', 'withdrawn_us', 1),
])
def test_withdrawal_during_issuer_cleanup_denies_release(deployment, monkeypatch, table, column, marker):
    """Commit on a separate connection after the previous final transport check."""
    _, _, owner, content, _ = deployment
    close = LocalSourceAuthority.close
    commits = []

    def withdraw_on_close(authority):
        live_claim = authority._claimed and authority._access is not None
        close(authority)
        if live_claim:
            with closing(sqlite3.connect(owner.app.state.settings.tasks_db_path)) as connection, connection:
                changed = connection.execute(
                    f'UPDATE {table} SET {column} = ? WHERE reference = ?', (marker, 'report'),
                ).rowcount
            commits.append(changed)

    async def run():
        provider = await establish(deployment)
        monkeypatch.setattr(LocalSourceAuthority, 'close', withdraw_on_close)
        events = await execute(provider, owner, content)
        assert commits == [1]
        terminal, = events
        denied(terminal, 'AUTHORIZATION_UNAVAILABLE')

    run_offline(run())


@pytest.mark.parametrize('downgrade', [False, True])
def test_final_snapshot_checks_all_sources_and_reviewer(deployment, monkeypatch, downgrade):
    approvals, reviewer, owner, pdf, review = deployment
    notes = save_office(sample_docx())
    batch = Upload((owner.app, owner.token), [('report.pdf', pdf), ('notes.docx', notes)])
    close = LocalSourceAuthority.close

    def close_with_change(authority):
        live_claim = authority._claimed and authority._access is not None
        close(authority)
        if live_claim and downgrade:
            owner.app.state.auth_store.update_user('reviewer', role='operator')

    async def run():
        provider = await establish(deployment)
        await reviewer.authenticate()
        approvals.approve(reviewer.request, **(review | {'reference': 'notes', 'grant': batch.grants[1]}))
        await batch.authenticate()
        provider.register(batch.request, 'a-conversation', 'notes', notes, expires_at=review['expires_at'])
        loop = asyncio.get_running_loop()
        now = loop.time()
        monkeypatch.setattr(loop, 'time', lambda: now)
        monkeypatch.setattr(LocalSourceAuthority, 'close', close_with_change)
        objective = PASSAGE + ', then find the passage mentioning recorded values in source 2'
        with provider.open_provider(batch.request, 'a-conversation') as scoped:
            events = await run_server_source_document_workflow(
                batch.request, 'a-conversation', objective, provider=scoped,
                sources=(('report', pdf), ('notes', notes)),
            )
        if downgrade:
            terminal, = events
            denied(terminal, 'AUTHORIZATION_UNAVAILABLE')
        else:
            terminal = events[-1]
            assert terminal['type'] == 'task_completed', terminal
            assert [source['filename'] for source in terminal['answer']['sources']] == ['report.pdf', 'notes.docx']

    run_offline(run())


# The ordinary offline runner rejects subprocess launches. Enable only in an
# independently authorized process-test environment.
process_tests = pytest.mark.skipif(
    os.environ.get('SOVEREIGNAI_R859_PROCESS_TESTS') != '1',
    reason='independent process execution requires explicit offline authorization',
)
WORKER = Path(__file__).with_name('r859_worker_fixture.py').resolve()


class Worker:
    def __init__(self, process):
        self.process = process
        self.messages = Queue()
        self.reader = Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.process.stdout:
            self.messages.put(line)
        self.messages.put(None)

    def send(self, op, **values):
        self.process.stdin.write(json.dumps(dict(op=op, **values)) + '\n')
        self.process.stdin.flush()

    def receive(self):
        try:
            line = self.messages.get(timeout=60)
        except Empty:
            pytest.fail('Fixture worker did not answer before the IPC deadline.')
        assert line is not None, 'Fixture worker exited before replying. Inspect its temporary stderr log.'
        return json.loads(line)

    def call(self, op, **values):
        self.send(op, **values)
        return self.receive()


@contextmanager
def open_worker(directory, *, startup_state='ready'):
    with (directory / 'worker-stderr.log').open('ab') as stderr:
        process = subprocess.Popen(
            [sys.executable, '-B', '-S', str(WORKER), str(directory)],
            executable=sys.executable, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=stderr, text=True, encoding='utf-8',
        )
        worker = Worker(process)
        completed = False
        try:
            assert worker.receive() == {'state': startup_state, 'pid': process.pid}
            yield worker
            completed = True
        finally:
            try:
                if process.poll() is None:
                    worker.send('quit')
                    process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            worker.reader.join(timeout=5)
            process.stdin.close()
            process.stdout.close()
            if completed:
                assert process.returncode == 0, 'Fixture worker did not exit cleanly.'


def assert_no_evidence(result):
    assert result['event'] == 'task_failed'
    assert result['code'] == 'AUTHORIZATION_UNAVAILABLE'
    assert result['evidence'] is False


@process_tests
@pytest.mark.parametrize('change', ['withdraw_approval', 'withdraw_source', 'revoke_generation', 'lock'])
def test_independent_workers_revalidate_buffered_release(tmp_path, change):
    with open_worker(tmp_path) as first:
        seed = first.call('seed')
        assert first.call('run')['evidence'] is True
        with open_worker(tmp_path) as second:
            assert first.process.pid != second.process.pid
            assert second.call('attach', **seed) == {'state': 'attached'}
            assert second.call('run')['evidence'] is True
            assert second.call('pause_release') == {'state': 'pending'}
            result = first.call(change)
            expect = {
                'withdraw_approval': {'changed': True}, 'withdraw_source': {'changed': True},
                'revoke_generation': {'state': 'revoked'}, 'lock': {'state': 'locked'},
            }
            assert result == expect[change]
            pending = second.call('continue')
            assert pending['computed'] is True
            assert_no_evidence(pending)
            if change == 'lock':
                assert first.call('unlock') == {'state': 'unlocked'}
                assert second.call('run')['evidence'] is True
            else:
                subsequent = second.call('run')
                assert_no_evidence(subsequent)
                assert subsequent['reads'] == 0
        if change.startswith('withdraw_'):
            if change == 'withdraw_approval':
                assert first.call('reapprove') == {'code': 'SOURCE_UNAVAILABLE'}
            stale = first.call('register')
            assert stale == {'code': 'AUTHORIZATION_UNAVAILABLE' if change == 'withdraw_approval'
                             else 'SOURCE_UNAVAILABLE'}
            with open_worker(tmp_path) as restarted:
                assert restarted.call('attach', **seed) == {'state': 'attached'}
                replay = restarted.call('run')
                assert_no_evidence(replay)
                assert replay['reads'] == 0
        content = base64.b64decode(seed['content'], validate=True)
        for path in (tmp_path / 'tasks').glob('tasks.db*'):
            assert content not in path.read_bytes() and b'7.1 mm/s' not in path.read_bytes()
        assert b'7.1 mm/s' not in (tmp_path / 'memory.json').read_bytes()


@process_tests
def test_independent_worker_enforces_expiry_ownership_and_updated_clearance(tmp_path):
    with ExitStack() as workers:
        first = workers.enter_context(open_worker(tmp_path))
        seed = first.call('seed')
        second = workers.enter_context(open_worker(tmp_path))
        assert first.process.pid != second.process.pid
        assert second.call('attach', **seed) == {'state': 'attached'}
        assert second.call('run')['evidence'] is True
        for command, values in (('expired', {}), ('run', {'reviewer': True})):
            result = second.call(command, **values)
            assert_no_evidence(result)
            assert result['reads'] == 0
        assert first.call('lower_clearance') == {'state': 'lowered'}
        result = second.call('run')
        assert_no_evidence(result)
        assert result['reads'] == 0
