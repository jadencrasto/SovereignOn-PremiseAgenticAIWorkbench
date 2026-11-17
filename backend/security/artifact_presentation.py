"""Allowlisted artifact information for model and user presentation.

This is not an authorization decision and does not resolve or open artifacts.
"""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict

ARTIFACT_TOOLS = frozenset(('file_write', 'docx_create', 'xlsx_report', 'artifact_verifier'))

# Labels are fixed by the server, not derived from model-generated descriptions.
SAFE_TOOL_LABELS = {
    'document_search': 'Search documents', 'file_read': 'Read a document',
    'file_list': 'List available documents', 'calculator': 'Calculate', 'unit_conversion': 'Convert units',
    'docx_create': 'Create Word document', 'xlsx_report': 'Create spreadsheet',
    'file_write': 'Create text file', 'artifact_verifier': 'Verify artifact',
    'code_execution': 'Run code', 'knowledge_graph': 'Search knowledge graph',
    'model_scan': 'Inspect local models', 'hardware_status': 'Check hardware',
    'security_diagnostics': 'Check security status',
}


class PublicArtifact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    artifact_type: Literal['docx', 'xlsx', 'file']
    artifact_id: str | None = None
    display_name: str | None = None


class PublicPlanStep(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str | None = None
    description: str
    tool_name: str | None = None
    requires_approval: bool = False
    status: str | None = None


class PublicPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    objective: str
    steps: list[PublicPlanStep]


class PublicPlannerEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['plan_created', 'plan_step', 'approval_required', 'approval_granted',
                  'approval_rejected', 'task_started', 'task_completed', 'task_failed', 'task_cancelled']
    task_id: str | None = None
    step_id: str | None = None
    approval_id: str | None = None
    status: str | None = None
    risk_level: str | None = None
    tool_name: str | None = None
    steps_completed: int | None = None
    total_steps: int | None = None
    expires_at: str | None = None
    error: str | None = None
    reason: str | None = None
    plan: PublicPlan | None = None


def public_tool_name(tool):
    return tool if isinstance(tool, str) and tool in SAFE_TOOL_LABELS else 'unknown'


def public_agent_status(status):
    return status if isinstance(status, str) and status in {
        'thinking', 'analyzing_image', 'reasoning', 'vision_error', 'planning', 'executing'
    } else 'thinking'


def public_tool_arguments(tool, arguments):
    if tool == 'calculator' and isinstance(arguments, dict):
        expression = arguments.get('expression')
        if isinstance(expression, str) and len(expression) <= 256 and re.fullmatch(r'[0-9+*/().%\-\s]+', expression):
            return {'expression': expression}
    return {}


def public_tool_summary(tool, summary, success):
    if isinstance(tool, str) and tool in ARTIFACT_TOOLS:
        return 'Artifact operation completed' if success else 'Artifact operation failed'
    if (tool == 'unit_conversion' and success and isinstance(summary, str)
            and len(summary) <= 100 and re.fullmatch(r'Result: [0-9+.,eE\-]+ (?:mm|cm|m|km|g|kg|s|min|h)', summary)):
        return summary
    if (tool == 'calculator' and success and isinstance(summary, str)
            and len(summary) <= 100 and re.fullmatch(r'Result: [0-9+.,eE\-]+(?: (?:mm|cm|m|km|g|kg|s|min|h))?', summary)):
        return summary
    return 'Tool completed' if success else 'Tool failed'


def artifact_presentation(tool: str, raw):
    """Copy only explicitly allowed scalar fields; nested payloads are ignored."""
    output = {'artifact_type': {'docx_create': 'docx', 'xlsx_report': 'xlsx'}.get(tool, 'file')}
    if not isinstance(raw, dict):
        return output
    artifact_id = raw.get('artifact_id')
    if isinstance(artifact_id, str) and re.fullmatch(r'[a-f0-9]{32}', artifact_id):
        output['artifact_id'] = artifact_id
    name = raw.get('display_name')
    if (isinstance(name, str) and 0 < len(name) <= 255 and '/' not in name and '\\' not in name
            and not any(ord(c) < 32 for c in name)):
        output['display_name'] = name
    return PublicArtifact(**output).model_dump(exclude_none=True)


def public_planner_event(event):
    """Project planner events onto a bounded structural schema, never raw results."""
    if not isinstance(event, dict):
        return {'type': 'error', 'content': 'Event unavailable'}
    kind = event.get('type')
    allowed = {'plan_created', 'plan_step', 'approval_required', 'approval_granted',
               'approval_rejected', 'task_started', 'task_completed', 'task_failed', 'task_cancelled'}
    if not isinstance(kind, str) or kind not in allowed:
        return {'type': 'error', 'content': 'Event unavailable'}
    output = {'type': kind}
    for key in ('task_id', 'step_id', 'approval_id', 'status', 'risk_level', 'tool_name'):
        value = event.get(key)
        if (isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value)
                and (key != 'tool_name' or value in SAFE_TOOL_LABELS)):
            output[key] = value
    for key in ('steps_completed', 'total_steps'):
        value = event.get(key)
        if type(value) is int and 0 <= value <= 10000:
            output[key] = value
    expires = event.get('expires_at')
    if isinstance(expires, str) and re.fullmatch(r'[0-9TtZz:+.\-]{1,40}', expires):
        output['expires_at'] = expires
    if kind == 'task_failed':
        output['error'] = 'Task failed'
    if kind == 'approval_required':
        output['reason'] = 'Approval required'
    if kind == 'plan_created':
        plan = event.get('plan')
        steps = plan.get('steps') if isinstance(plan, dict) else []
        safe_steps = []
        if isinstance(steps, list):
            for step in steps[:100]:
                if not isinstance(step, dict):
                    continue
                name = step.get('tool_name')
                label = SAFE_TOOL_LABELS.get(name, 'Task step') if isinstance(name, str) else 'Task step'
                item = {'description': label, 'requires_approval': step.get('requires_approval') is True}
                for key in ('id', 'tool_name', 'status'):
                    value = step.get(key)
                    if (isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value)
                            and (key != 'tool_name' or value in SAFE_TOOL_LABELS)):
                        item[key] = value
                safe_steps.append(item)
        labels = list(dict.fromkeys(step['description'] for step in safe_steps if step['description'] != 'Task step'))
        output['plan'] = {'objective': 'Plan: ' + '; '.join(labels[:3]) if labels else 'Task plan', 'steps': safe_steps}
    return PublicPlannerEvent(**output).model_dump(exclude_none=True)
