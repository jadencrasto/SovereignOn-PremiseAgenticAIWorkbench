"""Deterministic, local conversion of common engineering units."""
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Unit = Literal['mm', 'cm', 'm', 'km', 'g', 'kg', 's', 'min', 'h']
_UNITS = {
    'mm': ('length', Decimal('0.001')), 'cm': ('length', Decimal('0.01')),
    'm': ('length', Decimal('1')), 'km': ('length', Decimal('1000')),
    'g': ('mass', Decimal('0.001')), 'kg': ('mass', Decimal('1')),
    's': ('time', Decimal('1')), 'min': ('time', Decimal('60')),
    'h': ('time', Decimal('3600')),
}

class UnitConversionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: float = Field(ge=-1e12, le=1e12, allow_inf_nan=False, strict=True, description='Numeric input value')
    from_unit: Unit
    to_unit: Unit

class UnitConversionOutput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: float
    from_unit: Unit
    to_unit: Unit
    result: float = Field(allow_inf_nan=False)


def unit_scale_factors(from_unit: str, to_unit: str) -> tuple[Decimal, Decimal]:
    """Return the approved scales to the same base unit."""
    source, target = _UNITS.get(from_unit), _UNITS.get(to_unit)
    if source is None or target is None or source[0] != target[0]:
        raise ValueError('Incompatible or invalid units')
    return source[1], target[1]


def convert_units(value: float, from_unit: str, to_unit: str) -> float:
    if not isfinite(value):
        raise ValueError('Incompatible or invalid units')
    source, target = unit_scale_factors(from_unit, to_unit)
    try:
        result = float(Decimal(str(value)) * source / target)
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError('Conversion unavailable') from exc
    if not isfinite(result) or abs(result) > 1e18:
        raise ValueError('Conversion result outside supported bounds')
    return result

async def execute_unit_conversion(args: UnitConversionInput) -> dict:
    return {'value': args.value, 'from_unit': args.from_unit, 'to_unit': args.to_unit,
            'result': convert_units(args.value, args.from_unit, args.to_unit)}

def verify_unit_conversion(arguments: dict, output: dict) -> bool:
    try:
        inputs = UnitConversionInput.model_validate(arguments)
        return (output['value'] == inputs.value and output['from_unit'] == inputs.from_unit
                and output['to_unit'] == inputs.to_unit and type(output['result']) is float
                and output['result'] == convert_units(inputs.value, inputs.from_unit, inputs.to_unit))
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def register_unit_conversion(registry, *, enabled=True, risk_level='low', requires_approval=False):
    """Register the production conversion capability without starting services."""
    from backend.tools.registry import ToolDefinition
    registry.register(ToolDefinition(
        name='unit_conversion',
        description='Convert numeric values between compatible length, mass or time units: mm, cm, m, km, g, kg, s, min, h.',
        input_schema=UnitConversionInput, execute_fn=execute_unit_conversion,
        category='Computation', read_only=True, risk_level=risk_level,
        requires_approval=requires_approval, enabled=enabled,
        result_schema=UnitConversionOutput, result_verifier=verify_unit_conversion,
    ))
