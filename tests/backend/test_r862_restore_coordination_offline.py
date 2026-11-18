"""Exercise restore declaration ordering with separate local SQLite connections."""

from types import SimpleNamespace

import pytest
from starlette.applications import Starlette

from backend.multimodal import document_source_recovery as recovery
from backend.multimodal.document_reasoning import ReasoningError
from backend.multimodal.document_source_approvals import DeploymentSourceApprovals


def _app(database, *, declare=False):
    app = Starlette()
    app.state.settings = SimpleNamespace(tasks_db_path=database, document_source_restore_pending=declare)
    return app


@pytest.fixture
def provisioned(tmp_path):
    database = tmp_path / 'authority.db'
    database.touch()
    app = _app(database)
    DeploymentSourceApprovals(app).source_provider()
    return app, database, recovery.capture_source_checkpoint(app)


@pytest.mark.parametrize('use_current_checkpoint', [True, False])
def test_stale_validator_cannot_reopen_completed_restore(provisioned, use_current_checkpoint):
    app, database, checkpoint = provisioned
    recovery.quarantine_source_authority(app)
    recovery.validate_source_restore(app, trusted_checkpoint=checkpoint)
    ready_approvals = DeploymentSourceApprovals(app)
    path = recovery._coordination_path(database)
    ready = recovery._read_shared_state(path)
    stale = _app(database, declare=True)

    with pytest.raises(ReasoningError) as denied:
        recovery.validate_source_restore(stale, trusted_checkpoint=checkpoint if use_current_checkpoint else None)
    assert denied.value.code == 'AUTHORIZATION_UNAVAILABLE'
    assert recovery._read_shared_state(path) == ready
    ready_approvals.source_provider()


def test_validator_does_not_create_missing_shared_state(provisioned):
    _, database, checkpoint = provisioned
    path = recovery._coordination_path(database)
    path.unlink()
    with pytest.raises(ReasoningError) as denied:
        recovery.validate_source_restore(_app(database, declare=True), trusted_checkpoint=checkpoint)
    assert denied.value.code == 'AUTHORIZATION_UNAVAILABLE'
    assert not path.exists()


@pytest.mark.parametrize('recover_before_startup_resumes', [True, False])
def test_startup_flag_cannot_overwrite_newer_restore_state(provisioned, monkeypatch, recover_before_startup_resumes):
    app, database, checkpoint = provisioned
    path = recovery._coordination_path(database)
    read_state = recovery._read_shared_state
    startup = _app(database, declare=True)
    committed_elsewhere = []

    def read_then_interleave(coordinator):
        seen = read_state(coordinator)
        with monkeypatch.context() as interleaved:
            interleaved.setattr(recovery, '_read_shared_state', read_state)
            recovery.quarantine_source_authority(app)
            if recover_before_startup_resumes:
                recovery.validate_source_restore(app, trusted_checkpoint=checkpoint)
        committed_elsewhere.append(read_state(path))
        return seen

    with monkeypatch.context() as interleaved:
        interleaved.setattr(recovery, '_read_shared_state', read_then_interleave)
        with pytest.raises(ReasoningError) as denied:
            DeploymentSourceApprovals(startup)
        assert denied.value.code == 'AUTHORIZATION_UNAVAILABLE'

    final = read_state(path)
    assert [final] == committed_elsewhere
    assert final.pending == (not recover_before_startup_resumes)
    if recover_before_startup_resumes:
        DeploymentSourceApprovals(app).source_provider()
    else:
        recovery.validate_source_restore(app, trusted_checkpoint=checkpoint)
