"""Keep offline pip's Windows version detection compatible with process denial."""

import platform
import runpy
import subprocess
import sys

import pytest

from backend.runtime import windows_backend_readiness as runtime


def test_offline_installer_denial_allows_platform_version_fallback(monkeypatch):
    hooks = []
    subprocess_events = []

    def check_output(*args, **kwargs):
        subprocess_events.append('subprocess.Popen')
        hooks[0]('subprocess.Popen', ())
        pytest.fail('An unexpected subprocess would have started')

    def run_module(name, *, run_name, alter_sys):
        assert (name, run_name, alter_sys) == ('pip', '__main__', True)
        assert platform._syscmd_ver('Windows', '11', '10.0.26100', supported_platforms=(sys.platform,)) == (
            'Windows', '11', '10.0.26100'
        )
        assert subprocess_events
        for event in ('socket.connect', 'socket.getaddrinfo', 'subprocess.Popen'):
            with pytest.raises(PermissionError):
                hooks[0](event, ())

    monkeypatch.setattr(sys, 'addaudithook', hooks.append)
    monkeypatch.setattr(sys, 'argv', ['-c', 'fixture-site-packages'])
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(subprocess, 'check_output', check_output)
    monkeypatch.setattr(runpy, 'run_module', run_module)
    exec(runtime._OFFLINE_PIP, {})
