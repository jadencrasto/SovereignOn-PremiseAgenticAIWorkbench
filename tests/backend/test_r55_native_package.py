"""Static and injected tests only; no test workload or native processes run."""
import ast
import json
from pathlib import Path

import pytest

from tools.process_test_supervisor import native_fixture_entry, outer_controller


@pytest.mark.parametrize('scenario', sorted(outer_controller.SCENARIOS))
def test_entry_blocks_before_controller_without_observer(scenario, capsys, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('must not create controller')
    monkeypatch.setattr(outer_controller, 'OuterController', forbidden)
    assert native_fixture_entry.main(['--scenario', scenario]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'EVIDENCE_INCOMPLETE'
    assert result['scenario'] == scenario
    assert result['controller_claim'] is None and result['observer_evidence'] is None
    assert result['reason'] == 'observer_handoff_unavailable'


@pytest.mark.parametrize('args', [[], ['--scenario', 'other'],
    ['--scenario', 'normal', '--pid', '42'], ['--scenario', 'normal', '--command', 'x'],
    ['--scenario', 'normal', '--path', 'x']])
def test_entry_rejects_unknown_inputs_before_controller(args):
    with pytest.raises(SystemExit): native_fixture_entry.main(args)


def test_workload_has_no_network_imports():
    path = Path(__file__).resolve().parents[1] / 'fixtures' / 'i52_processes' / 'non_network_workload.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    imports = {alias.name.split('.')[0] for node in ast.walk(tree)
               if isinstance(node, (ast.Import, ast.ImportFrom))
               for alias in (node.names if isinstance(node, ast.Import) else [])}
    assert not imports.intersection({'socket', 'http', 'urllib', 'requests', 'httpx'})


def test_factory_binds_server_id_and_fixed_script(monkeypatch):
    captured = []
    monkeypatch.setattr(outer_controller, 'WindowsJobDriver', lambda commands: captured.append(commands))
    operation = '9e89c286-0bd0-4e5b-8c65-7a35ae75be5e'
    outer_controller.fixture_driver('normal', operation)
    command = captured[0]['backend']
    assert command[1:3] == ['-I', '-B']
    assert command[3].endswith('non_network_workload.py')
    assert command[4:] == ['--scenario', 'normal', '--operation-id', operation]
    with pytest.raises(ValueError): outer_controller.fixture_driver('other', operation)
    with pytest.raises(ValueError): outer_controller.fixture_driver('normal', 'not-an-id')
