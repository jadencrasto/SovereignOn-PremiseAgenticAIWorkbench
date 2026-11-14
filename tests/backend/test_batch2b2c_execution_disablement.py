from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from backend.config import Settings
from backend.tools.code_execution import CodeExecutionInput, create_code_execution, _run_subprocess_worker, _run_docker_worker
from backend.tools.registry import ToolDefinition, ToolRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize('environment,mode', [('development','subprocess'), ('production','subprocess'), ('development','docker'), ('production','docker')])
async def test_direct_execution_denied_before_backend(tmp_path, environment, mode):
    cfg = Settings(app_env=environment, code_exec_isolation=mode)
    fn = create_code_execution(tmp_path, cfg=cfg)
    with patch('backend.tools.code_execution._run_subprocess_worker') as process, patch('backend.tools.code_execution._run_docker_worker') as docker:
        with pytest.raises(PermissionError):
            await fn(CodeExecutionInput(code='print(1)'))
        process.assert_not_called()
        docker.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_denied_even_when_enabled_and_approved():
    registry = ToolRegistry()
    execute = AsyncMock()
    registry.register(ToolDefinition(name='code_execution', description='synthetic', input_schema=CodeExecutionInput,
                                     execute_fn=execute, requires_approval=True))
    approvals = MagicMock()
    approvals.verify_approval_for_execution.return_value = True
    registry.set_approval_manager(approvals)
    result = await registry.execute('code_execution', {'code':'print(1)'}, user_role='admin',
                                    task_id='task', step_id='step', approval_id='approval')
    assert not result.success
    execute.assert_not_awaited()
    approvals.verify_approval_for_execution.assert_not_called()


def test_direct_workers_do_not_launch_subprocess(tmp_path):
    with patch('backend.tools.code_execution.subprocess.Popen') as popen, patch('backend.tools.code_execution.subprocess.run') as run:
        with pytest.raises(PermissionError):
            _run_subprocess_worker(tmp_path / 'synthetic.py', tmp_path, {}, 1)
        with pytest.raises(PermissionError):
            _run_docker_worker('print(1)', 'synthetic', 1)
        popen.assert_not_called()
        run.assert_not_called()


def test_code_tool_cannot_be_reenabled():
    registry = ToolRegistry()
    registry.register(ToolDefinition(name='code_execution', description='synthetic', input_schema=CodeExecutionInput,
                                     execute_fn=AsyncMock(), enabled=True))
    assert not registry.get('code_execution').enabled
    assert not registry.enable('code_execution')


@pytest.mark.asyncio
async def test_replayed_plan_cannot_bypass_disabled_flag():
    registry = ToolRegistry()
    execute = AsyncMock()
    registry.register(ToolDefinition(name='code_execution', description='synthetic', input_schema=CodeExecutionInput,
                                     execute_fn=execute))
    registry.get('code_execution').enabled = True
    result = await registry.execute('code_execution', {'code':'print(1)'}, user_role='admin', task_id='old-task')
    assert not result.success
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_calculator_still_available():
    from backend.tools.calculator import CalculatorInput, execute_calculator
    registry = ToolRegistry()
    registry.register(ToolDefinition(name='calculator', description='synthetic', input_schema=CalculatorInput,
                                     execute_fn=execute_calculator))
    result = await registry.execute('calculator', {'expression':'2+2'}, user_role='operator')
    assert result.success and result.result['result'] == 4


@pytest.mark.parametrize('mode', ['subprocess', 'docker'])
@pytest.mark.parametrize('environment', ['development', 'production'])
def test_validation_reports_disabled_without_probing_backend(tmp_path, mode, environment):
    from backend.utils.config_validation import ConfigValidator
    cfg = Settings(app_env=environment, auth_enabled=True, code_exec_isolation=mode, upload_dir=tmp_path / 'uploads',
                   sandbox_dir=tmp_path / 'sandbox', log_dir=tmp_path / 'logs',
                   chroma_persist_dir=tmp_path / 'chroma', tasks_dir=tmp_path / 'tasks')
    with patch('backend.tools.code_execution.check_docker_daemon_available') as probe:
        _, checks = ConfigValidator(cfg).validate()
    assert any(c['rule'] == 'prod_code_exec_isolation' and 'disabled' in c['message'] for c in checks)
    probe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_dispatch_denies_plan_before_registry(tmp_path):
    from backend.agent.engine import AgentEngine
    from backend.agent.memory import ConversationMemory
    registry = MagicMock()
    registry.execute = AsyncMock()
    engine = AgentEngine(Settings(), MagicMock(), ConversationMemory(tmp_path / 'memory.json'), tool_registry=registry)
    result = await engine._dispatch_tool('code_execution', {'code':'print(1)'}, user_role='admin')
    assert not result.success
    registry.execute.assert_not_awaited()


def test_registration_uses_active_custom_settings_and_hides_code_tool(tmp_path):
    from backend.main import _register_tools
    cfg = Settings(app_env='development', code_exec_isolation='docker', sandbox_dir=tmp_path / 'sandbox',
                   upload_dir=tmp_path / 'uploads')
    registry = ToolRegistry()
    with patch('backend.main.create_code_execution', return_value=AsyncMock()) as factory:
        _register_tools(registry, MagicMock(), cfg=cfg, model_router=MagicMock(), auth_store=MagicMock())
    assert factory.call_args.kwargs['cfg'] is cfg
    assert not registry.get('code_execution').enabled
    assert 'code_execution' not in [tool.name for tool in registry.list_enabled_tools()]
