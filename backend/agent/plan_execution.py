"""Execution checks shared by stored plans and request-local evidence plans."""

import math
from decimal import Decimal

from backend.agent.planner import StepStatus
from backend.tools.registry import ToolResult


def build_local_measurement_steps(left, left_unit, operation, right, right_unit, target):
    """Compile one deterministic measurement request to registered tool steps."""
    from backend.agent.numeric_receipts import local_conversion_target, local_unit_name
    from backend.agent.planner import PlanStep, StepBinding
    from backend.tools.unit_conversion import (
        absolute_zero_expression, local_unit_dimension, validate_absolute_temperature,
    )

    try:
        left_unit, right_unit = (local_unit_name(unit) if unit else None for unit in (left_unit, right_unit))
    except ValueError:
        raise ValueError('LOCAL_TOOL_UNIT') from None
    if operation == '*' and left_unit is None:
        left, right, left_unit, right_unit = right, left, right_unit, left_unit
    if operation in ('+', '-') and (left_unit is None or right_unit is None):
        raise ValueError('LOCAL_TOOL_REFERENCE_UNIT')
    if left_unit is None or (operation in ('*', '/') and right_unit is not None):
        raise ValueError('LOCAL_TOOL_DIMENSION')

    dimension = local_unit_dimension(left_unit)
    if right_unit and local_unit_dimension(right_unit) != dimension:
        raise ValueError('LOCAL_TOOL_DIMENSION')
    if any(abs(Decimal(str(value))) > (10**12 if isinstance(value, str) else 10**18) for value in (left, right)):
        raise ValueError('LOCAL_TOOL_INVALID')
    if operation == '/' and Decimal(str(right)) == 0:
        raise ValueError('LOCAL_TOOL_ZERO_DIVISION')

    calculation_unit = operand_unit = left_unit
    exact_zero = None
    if dimension == 'temperature':
        left_difference = left_unit.startswith('Δ')
        right_difference = bool(right_unit and right_unit.startswith('Δ'))
        if left_difference:
            if operation not in ('+', '*', '/') or (operation == '+' and not right_difference):
                raise ValueError('LOCAL_TOOL_TEMPERATURE')
        else:
            if operation not in ('+', '-') or (operation == '+' and not right_difference):
                raise ValueError('LOCAL_TOOL_TEMPERATURE')
            validate_absolute_temperature(left, left_unit)
            if right_difference:
                operand_unit = f'Δ{left_unit}'
                exact_zero = absolute_zero_expression(left, left_unit, operation, right, right_unit)
            else:
                validate_absolute_temperature(right, right_unit)
                calculation_unit = f'Δ{left_unit}'

    if target:
        try:
            target = local_conversion_target(target, calculation_unit)
        except ValueError:
            raise ValueError('LOCAL_TOOL_UNIT') from None
        if local_unit_dimension(target) != dimension:
            raise ValueError('LOCAL_TOOL_DIMENSION')
        if dimension == 'temperature' and target.startswith('Δ') != calculation_unit.startswith('Δ'):
            raise ValueError('LOCAL_TOOL_TEMPERATURE')

    steps = []
    operand = f'({right})'
    if exact_zero is None and right_unit and right_unit != operand_unit:
        steps.append(PlanStep(id='step_1', description='Convert the second measurement', tool_name='unit_conversion',
                              arguments={'value': float(right), 'from_unit': right_unit, 'to_unit': operand_unit}))
        operand = '@step_1.result'
    calculated = f'step_{len(steps) + 1}'
    steps.append(PlanStep(id=calculated, description='Calculate the measurement', tool_name='calculator',
                          arguments={'expression': exact_zero or f'({left}) {operation} {operand}'}))
    if target and target != calculation_unit:
        steps.append(PlanStep(id=f'step_{len(steps) + 1}', description='Convert the result', tool_name='unit_conversion',
                              arguments={'from_unit': calculation_unit, 'to_unit': target},
                              bindings={'value': StepBinding(step_id=calculated, field='result')}))
    return steps, calculation_unit


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
