"""Isolated verification uses real files; no access or publication is authorized."""
import hashlib
from pathlib import Path
import pytest
from backend.tools.verified_report import generate_verified_report
from test_a71_artifacts_offline import report
from test_a21_conversation_offline import immediate


@pytest.mark.parametrize('fmt',['docx','xlsx','pdf'])
def test_verified_unpublished_real_files(tmp_path,fmt):
    out=immediate(generate_verified_report(report(fmt),{'s1':'source.txt'},staging_parent=tmp_path))
    assert out.state=='verified_unpublished' and out.output_format==fmt
    assert out.sha256==hashlib.sha256(out.content).hexdigest() and out.dependencies==('s1',)
    assert list(tmp_path.iterdir())==[]


def test_unknown_citation_rejected_before_writing(tmp_path):
    with pytest.raises(ValueError): immediate(generate_verified_report(report('docx'),{'other':'source.txt'},staging_parent=tmp_path))
    assert list(tmp_path.iterdir())==[]


def test_digest_mismatch_removes_only_identified_file(tmp_path,monkeypatch):
    from backend.tools import verified_report as module
    real=module.create_docx_create
    def factory(root):
        execute=real(root)
        async def fake(args):
            result=await execute(args);result['sha256_hash']='0'*64;return result
        return fake
    unrelated=tmp_path/'do-not-delete.txt'; unrelated.write_text('unchanged')
    monkeypatch.setattr(module,'create_docx_create',factory)
    with pytest.raises(ValueError,match='digest mismatch'):
        immediate(generate_verified_report(report('docx'),{'s1':'source.txt'},staging_parent=tmp_path))
    assert unrelated.read_text()=='unchanged' and list(tmp_path.iterdir())==[unrelated]


def test_failed_generator_orphan_is_quarantined_not_deleted(tmp_path,monkeypatch):
    from backend.tools import verified_report as module
    def factory(root):
        async def fail(args):
            (root/'unknown.tmp').write_text('unverified')
            raise OSError('interrupted')
        return fail
    monkeypatch.setattr(module,'create_docx_create',factory)
    with pytest.raises(OSError): immediate(generate_verified_report(report('docx'),{'s1':'source.txt'},staging_parent=tmp_path))
    stages=list(tmp_path.iterdir());assert len(stages)==1 and (stages[0]/'unknown.tmp').read_text()=='unverified'


def test_invalid_report_and_parent_do_not_create_files(tmp_path):
    value=report('xlsx'); value.tables[0].rows[0].append('invalid')
    with pytest.raises(ValueError): immediate(generate_verified_report(value,{'s1':'source.txt'},staging_parent=tmp_path))
    assert list(tmp_path.iterdir())==[]

def test_invalid_structure_cannot_return_success(tmp_path,monkeypatch):
    from backend.tools import verified_report as module
    real=module.create_docx_create
    def factory(root):
        execute=real(root)
        async def corrupt(args):
            result=await execute(args); data=b'incomplete docx';(root/result['filename']).write_bytes(data)
            result['sha256_hash']=hashlib.sha256(data).hexdigest();return result
        return corrupt
    monkeypatch.setattr(module,'create_docx_create',factory)
    with pytest.raises(Exception): immediate(generate_verified_report(report('docx'),{'s1':'source.txt'},staging_parent=tmp_path))
    assert list(tmp_path.iterdir())==[]


def test_cancellation_removes_empty_staging_directory(tmp_path,monkeypatch):
    import asyncio
    from backend.tools import verified_report as module
    from test_a22_async_offline import run_offline
    def factory(root):
        async def wait(args): await asyncio.sleep(1)
        return wait
    monkeypatch.setattr(module,'create_docx_create',factory)
    async def run():
        task=asyncio.create_task(generate_verified_report(report('docx'),{'s1':'source.txt'},staging_parent=tmp_path))
        await asyncio.sleep(.01); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    run_offline(run())
    assert list(tmp_path.iterdir())==[]

def test_report_request_reaches_planner_and_fails_closed_without_source(tmp_path):
    import json
    from types import SimpleNamespace
    from backend.auth.request_context import effective_owner, request_tools_enabled
    from backend.tools.registry import ToolDefinition
    from backend.tools.docx_create import DocxCreateInput, create_docx_create
    from backend.agent.task import TaskStatus
    from test_a31_planning_offline import setup_workflow, collect
    plan=json.dumps([{'description':'Create report','tool_name':'docx_create','arguments':{'filename':'report.docx','title':'Synthetic','content':'Synthetic findings'},'requires_approval':False}])
    engine=setup_workflow(tmp_path,plan)
    engine._tool_registry.register(ToolDefinition(name='docx_create',description='Create report',input_schema=DocxCreateInput,execute_fn=create_docx_create(tmp_path/'artifacts'),requires_approval=True,read_only=False))
    engine._approval_manager.request_approval.return_value=SimpleNamespace(approval_id='ap',risk_level='high',expires_at='2099-01-01T00:00:00Z')
    owner=effective_owner.set('a'); enabled=request_tools_enabled.set(True)
    try:
        engine._memory.prepare_new_chat('s','a','Be helpful.')
        events=collect(engine,'Create a technical report with these findings.')
        assert any(isinstance(e,dict) and e.get('type')=='task_failed' for e in events)
        assert engine._task_manager.list_tasks(user_id='a')[0].status==TaskStatus.FAILED
        assert list((tmp_path/'artifacts').iterdir())==[]
        async def resume(): return [e async for e in engine.resume_agent_task('x','ap',True,user_role='admin',user_id='a')]
        assert immediate(resume())[0]['type']=='error'
    finally:
        request_tools_enabled.reset(enabled);effective_owner.reset(owner)
