"""Deterministic, local conversion of common engineering units."""
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
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
LocalUnit = Literal[
    'mm', 'cm', 'm', 'km', 'in', 'ft', 'mi', 'mg', 'g', 'kg', 'oz', 'lb',
    '°C', '°F', 'K', 's', 'min', 'h', 'mL', 'L', 'US fl oz', 'US cup', 'US gal',
]
# NIST Handbook 44 Appendix C and Handbook 130 customary conversion factors.
# The US customary cup and fluid ounce are 1/16 and 1/128 US liquid gallon.
_LOCAL_UNITS = {
    **_UNITS,
    'in': ('length', Decimal('0.0254')), 'ft': ('length', Decimal('0.3048')),
    'mi': ('length', Decimal('1609.344')),
    'mg': ('mass', Decimal('0.000001')), 'oz': ('mass', Decimal('0.028349523125')),
    'lb': ('mass', Decimal('0.45359237')),
    'mL': ('volume', Decimal('0.001')), 'L': ('volume', Decimal('1')),
    'US fl oz': ('volume', Decimal('0.0295735295625')),
    'US cup': ('volume', Decimal('0.2365882365')), 'US gal': ('volume', Decimal('3.785411784')),
}
_TEMPERATURE = {'°C', '°F', 'K'}

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

class LocalUnitConversionInput(UnitConversionInput):
    from_unit: LocalUnit
    to_unit: LocalUnit

class LocalUnitConversionOutput(UnitConversionOutput):
    from_unit: LocalUnit
    to_unit: LocalUnit

def unit_scale_factors(from_unit: str, to_unit: str, *, local=False) -> tuple[Decimal, Decimal]:
    """Return the approved scales to the same base unit."""
    units = _LOCAL_UNITS if local else _UNITS
    source, target = units.get(from_unit), units.get(to_unit)
    if source is None or target is None or source[0] != target[0]:
        raise ValueError('Incompatible or invalid units')
    return source[1], target[1]


def _convert_temperature(value: Decimal, source: str, target: str) -> Decimal:
    if source == target:
        return value
    if source == '°C':
        value += Decimal('273.15')
    elif source == '°F':
        value = (value + Decimal('459.67')) * 5 / 9
    if target == '°C':
        return value - Decimal('273.15')
    if target == '°F':
        return value * 9 / 5 - Decimal('459.67')
    return value


def convert_units(value: float, from_unit: str, to_unit: str, *, local=False) -> float:
    if not isfinite(value):
        raise ValueError('Incompatible or invalid units')
    try:
        if local:
            with localcontext(Context(prec=40, rounding=ROUND_HALF_EVEN)):
                if from_unit in _TEMPERATURE and to_unit in _TEMPERATURE:
                    result = float(_convert_temperature(Decimal(str(value)), from_unit, to_unit))
                else:
                    source, target = unit_scale_factors(from_unit, to_unit, local=True)
                    result = float(Decimal(str(value)) * source / target)
        else:
            source, target = unit_scale_factors(from_unit, to_unit)
            result = float(Decimal(str(value)) * source / target)
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError('Conversion unavailable') from exc
    if not isfinite(result) or abs(result) > 1e18:
        raise ValueError('Conversion result outside supported bounds')
    return result

async def execute_unit_conversion(args: UnitConversionInput) -> dict:
    return {'value': args.value, 'from_unit': args.from_unit, 'to_unit': args.to_unit,
            'result': convert_units(args.value, args.from_unit, args.to_unit,
                                    local=isinstance(args, LocalUnitConversionInput))}

def _verify_conversion(arguments: dict, output: dict, *, local: bool) -> bool:
    try:
        schema = LocalUnitConversionInput if local else UnitConversionInput
        inputs = schema.model_validate(arguments)
        return (output['value'] == inputs.value and output['from_unit'] == inputs.from_unit
                and output['to_unit'] == inputs.to_unit and type(output['result']) is float
                and output['result'] == convert_units(inputs.value, inputs.from_unit, inputs.to_unit, local=local))
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def verify_unit_conversion(arguments: dict, output: dict) -> bool:
    return _verify_conversion(arguments, output, local=False)


def verify_local_unit_conversion(arguments: dict, output: dict) -> bool:
    return _verify_conversion(arguments, output, local=True)


def register_unit_conversion(registry, *, enabled=True, risk_level='low', requires_approval=False, local=False):
    """Register the conversion capability with the profile's supported units."""
    from backend.tools.registry import ToolDefinition
    registry.register(ToolDefinition(
        name='unit_conversion',
        description=('Convert numeric values between compatible length, mass, temperature, time or volume units.'
                     if local else
                     'Convert numeric values between compatible length, mass or time units: mm, cm, m, km, g, kg, s, min, h.'),
        input_schema=LocalUnitConversionInput if local else UnitConversionInput, execute_fn=execute_unit_conversion,
        category='Computation', read_only=True, risk_level=risk_level,
        requires_approval=requires_approval, enabled=enabled,
        result_schema=LocalUnitConversionOutput if local else UnitConversionOutput,
        result_verifier=verify_local_unit_conversion if local else verify_unit_conversion,
    ))
