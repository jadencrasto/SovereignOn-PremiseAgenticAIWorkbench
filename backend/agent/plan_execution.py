"""Execution checks shared by stored plans and request-local evidence plans."""

import math

from backend.agent.planner import StepStatus
from backend.tools.registry import ToolResult


def bind_verified_arguments(step, target, results, states):
    if not step.bindings:
        return dict(step.arguments)
    if not target or not target.enabled or not target.read_only or target.requires_approval:
        raise ValueError('Dependency unavailable')
    resolved = dict(step.arguments)
    bound_unit = None
    for argument, binding in step.bindings.items():
        prior = next((item for item in results if item.get('step_id') == binding.step_id), None)
        if (argument in resolved or not prior or prior.get('evidence_verified') is not True
                or prior.get('success') is not True or states.get(binding.step_id) != StepStatus.completed.value):
            raise ValueError('Dependency unavailable')
        output = prior.get('result')
        if not isinstance(output, dict) or binding.field not in output:
            raise ValueError('Dependency unavailable')
        value = output[binding.field]
        if step.tool_name == 'unit_conversion' and argument == 'value' and prior.get('tool') == 'unit_conversion' and binding.field == 'result':
            bound_unit = output.get('to_unit')
            if not bound_unit:
                raise ValueError('Dependency unit unavailable')
        if (type(value) not in (str, int, float, bool) or (isinstance(value, str) and len(value) > 4096)
                or (type(value) is int and value.bit_length() > 850)
                or (type(value) is float and not math.isfinite(value))):
            raise ValueError('Dependency unavailable')
        resolved[argument] = value
    if bound_unit is not None and resolved.get('from_unit') != bound_unit:
        raise ValueError('Dependency unit mismatch')
    target.input_schema.model_validate(resolved)
    return resolved


def verify_plan_result(name, arguments, result, definition):
    # Generator claims are insufficient to produce an artifact receipt.
    if name in {'docx_create', 'xlsx_report', 'file_write', 'artifact_verifier'} and result.success:
        return ToolResult(tool=name, success=False, error='Verified artifact execution is unavailable.'), False
    if not result.success or not definition or not definition.result_schema or not definition.result_verifier:
        return result, False
    try:
        payload = definition.result_schema.model_validate(result.result, strict=True).model_dump()
        if definition.result_verifier(arguments, payload) is not True:
            raise ValueError('Result verification failed')
        result.result = payload
        return result, bool(definition.read_only and not definition.requires_approval)
    except Exception:
        return ToolResult(tool=name, success=False, error='Tool result verification failed'), False


async def execute_plan_action(name, arguments, invoke, get_definition, *, current=None):
    result = await invoke()
    if current is not None and not current():
        return ToolResult(tool=name, success=False,
                          error='Tool registration changed during execution; outcome cannot be verified.'), False
    return verify_plan_result(name, arguments, result, get_definition(name))
