"""Deterministic launch and offline wheelset admission regressions."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from backend.multimodal import document_windows_supervisor as windows
from backend.runtime import windows_backend_readiness as runtime
from test_r873_custody_deployment_readiness_offline import _verify, wheelset  # noqa: F401


@pytest.mark.parametrize(('version', 'platform', 'free_threaded', 'accepted'), [
    ((3, 13, 7), 'win-amd64', 0, True),
    ((3, 12, 9), 'win-amd64', 0, False),
    ((3, 13, 7), 'win-arm64', 0, False),
    ((3, 13, 7), 'win-amd64', 1, False),
])
def test_supervisor_command_matches_offline_target(monkeypatch, tmp_path, version, platform, free_threaded, accepted):
    executable = tmp_path / 'python.exe'
    executable.touch()
    fake = SimpleNamespace(platform='win32', version_info=version, executable=str(executable),
                           implementation=SimpleNamespace(name='cpython'),
                           _base_executable=str(executable))
    monkeypatch.setattr(windows, 'sys', fake)
    monkeypatch.setattr(runtime, 'sys', fake)
    monkeypatch.setattr(runtime.sysconfig, 'get_platform', lambda: platform)
    monkeypatch.setattr(runtime.sysconfig, 'get_config_var', lambda name: free_threaded)
    if accepted:
        command = windows._worker_command()
        assert command[:3] == (str(executable.resolve()), '-I', '-B')
        assert command[3].endswith('document_supervisor_worker.py')
    else:
        with pytest.raises(RuntimeError, match='CPython 3.13 x64'):
            windows._worker_command()


@pytest.mark.parametrize('change', ['extra_entry', 'modified_verified_wheel'])
def test_wheelset_rejects_changes_after_one_wheel_was_verified(wheelset, monkeypatch, change):
    directory, _, lock = wheelset
    first, second = sorted(lock['wheels'])[:2]
    original = runtime._artifact

    @contextmanager
    def artifact(path, limit):
        if path.name == second:
            if change == 'extra_entry':
                (directory / 'unreviewed-1.0-py3-none-any.whl').write_bytes(b'extra artifact')
            else:
                (directory / first).write_bytes(b'modified after its hash was verified')
        with original(path, limit) as contents:
            yield contents

    monkeypatch.setattr(runtime, '_artifact', artifact)
    result = _verify(wheelset)
    assert result['status'] == 'BLOCKED'
    assert result['blockers'] == ['WHEEL_DIRECTORY_CHANGED']
