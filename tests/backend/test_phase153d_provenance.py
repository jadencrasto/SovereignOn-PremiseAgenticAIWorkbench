import sqlite3
import pytest
from backend.agent.task import TaskManager, TaskStatus
from backend.agent.task_store import TaskStore
from backend.auth.request_context import effective_owner
from backend.security.task_access import task_content_revalidation_available


def test_new_task_gets_atomic_server_quarantine_metadata_and_survives_restart(tmp_path):
    path = tmp_path/'tasks.db'
    token = effective_owner.set('user-a')
    try:
        store = TaskStore(path)
        task = TaskManager(store).create_task('conversation-a', 'synthetic', user_id='user-a')
        security = store.get_task_security_provenance(task.task_id)
        assert security['task_id'] == task.task_id and security['owner_id'] == 'user-a'
        assert security['conversation_id'] == 'conversation-a' and security['schema_version'] == 1
        assert security['verification_state'] == 'unverified' and security['dependency_state'] == 'unknown'
        assert security['dependencies_json'] is None and security['effective_classification'] is None
        assert task_content_revalidation_available() is False
        TaskManager(store).update_status(task.task_id, TaskStatus.PLANNING)
        reopened = TaskStore(path)
        assert reopened.get_task_security_provenance(task.task_id) == security
        assert reopened.get_task(task.task_id)['status'] == TaskStatus.PLANNING
    finally:
        effective_owner.reset(token)


def test_task_owner_cannot_be_forged_against_bound_principal(tmp_path):
    token = effective_owner.set('user-a')
    try:
        store = TaskStore(tmp_path/'tasks.db')
        with pytest.raises(PermissionError):
            TaskManager(store).create_task('other', 'synthetic', user_id='user-b')
        assert store.list_tasks() == []
        task = TaskManager(store).create_task('own', 'synthetic')
        assert task.user_id == 'user-a'
        assert store.get_task_security_provenance(task.task_id)['owner_id'] == 'user-a'
    finally:
        effective_owner.reset(token)


def test_legacy_and_background_are_not_silently_trusted(tmp_path):
    store = TaskStore(tmp_path/'tasks.db')
    store.save_task({'task_id':'legacy','session_id':'s','user_request':'synthetic','status':'pending','user_id':'a'})
    assert store.get_task_security_provenance('legacy') is None
    task = TaskManager(store).create_task('s','synthetic',user_id='a')
    record = store.get_task_security_provenance(task.task_id)
    assert record['owner_id'] is None and record['verification_state'] == 'unverified'


def test_duplicate_provenance_insert_rolls_back_task_replacement(tmp_path):
    store = TaskStore(tmp_path/'tasks.db')
    token = effective_owner.set('a')
    try:
        record = {'task_id':'t','session_id':'s','user_request':'original','status':'pending','user_id':'a'}
        store.save_task(record, initialize_security=True)
        with pytest.raises(sqlite3.IntegrityError):
            store.save_task({**record, 'user_request':'tampered'}, initialize_security=True)
        assert store.get_task('t')['user_request'] == 'original'
        assert store.get_task_security_provenance('t')['verification_state'] == 'unverified'
    finally:
        effective_owner.reset(token)


def test_new_unverified_metadata_does_not_grant_task_detail_access(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.tasks import router
    from backend.auth.dependencies import get_current_user
    from backend.auth.models import User
    store = TaskStore(tmp_path/'tasks.db')
    token = effective_owner.set('user-a')
    try:
        task = TaskManager(store).create_task('s', 'SYNTHETIC_PRIVATE', user_id='user-a')
    finally:
        effective_owner.reset(token)
    app = FastAPI()
    app.include_router(router)
    app.state.task_manager = TaskManager(store)
    app.dependency_overrides[get_current_user] = lambda: User(id='user-a', username='user-a', password_hash='synthetic', role='operator', clearance='L1', created_at='2026-01-01T00:00:00Z')
    with TestClient(app) as client:
        response = client.get(f'/api/tasks/{task.task_id}')
        assert response.status_code == 200
        assert 'SYNTHETIC_PRIVATE' not in response.text
        assert response.json()['plan'] is None and response.json()['result'] is None


def test_new_task_owner_and_conversation_are_immutable(tmp_path):
    store = TaskStore(tmp_path/'tasks.db')
    token = effective_owner.set('a')
    try:
        task = TaskManager(store).create_task('session-a', 'synthetic', user_id='a')
        original = store.get_task(task.task_id)
        for field, value in [('user_id','b'), ('session_id','session-b')]:
            with pytest.raises(ValueError):
                store.save_task({**original, field:value})
            assert store.get_task(task.task_id)[field] == original[field]
    finally:
        effective_owner.reset(token)


def test_quarantine_envelope_cannot_claim_verified_dependencies(tmp_path):
    store = TaskStore(tmp_path/'tasks.db')
    token = effective_owner.set('a')
    try:
        task = TaskManager(store).create_task('s', 'synthetic', user_id='a')
        with sqlite3.connect(str(tmp_path/'tasks.db')) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE task_security_provenance SET verification_state='verified' WHERE task_id=?", (task.task_id,))
        assert store.get_task_security_provenance(task.task_id)['verification_state'] == 'unverified'
    finally:
        effective_owner.reset(token)


def test_concurrent_task_creation_keeps_owners_separate(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path/'tasks.db'
    TaskStore(path)
    def create(i):
        owner = f'user-{i}'
        token = effective_owner.set(owner)
        try:
            store = TaskStore(path)
            task = TaskManager(store).create_task(f'session-{i}', 'synthetic', user_id=owner)
            return task.task_id, owner
        finally:
            effective_owner.reset(token)
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(create, range(8)))
    store = TaskStore(path)
    assert len({task_id for task_id, _ in records}) == 8
    for task_id, owner in records:
        provenance = store.get_task_security_provenance(task_id)
        assert provenance['owner_id'] == owner and provenance['verification_state'] == 'unverified'


def test_envelope_write_failure_rolls_back_new_task(tmp_path):
    path = tmp_path/'tasks.db'
    store = TaskStore(path)
    with sqlite3.connect(str(path)) as conn:
        conn.execute("CREATE TRIGGER fail_provenance BEFORE INSERT ON task_security_provenance BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
    token = effective_owner.set('a')
    try:
        with pytest.raises(sqlite3.IntegrityError):
            TaskManager(store).create_task('s', 'synthetic', user_id='a')
        assert store.list_tasks() == []
    finally:
        effective_owner.reset(token)
