"""Typed process-local evidence and a bounded grammar for numeric follow-ups."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class NumericReceipt:
    owner: str
    generation: str
    task_id: str
    invocation_id: str
    message_id: str
    value: int | float
    unit: str | None
    expires_at: float


_REFERENCE = r"(?:that\s+(?:result|number|answer|value)|(?:the\s+)?previous\s+(?:result|number|answer|value)|it)"
_NUMBER = r"[+-]?(?:[0-9]{1,18}(?:\.[0-9]{1,18})?|\.[0-9]{1,18})"
_UNIT_NAMES = {
    "kilometre": "km", "kilometer": "km", "metre": "m", "meter": "m",
    "centimetre": "cm", "centimeter": "cm", "millimetre": "mm", "millimeter": "mm",
    "gram": "g", "kilogram": "kg", "second": "s", "minute": "min", "hour": "h",
}


def resolve_numeric_followup(message: str, receipt: NumericReceipt):
    """Return exact tool arguments or raise if the phrase or units are unsupported."""
    from backend.tools.unit_conversion import UnitConversionInput, convert_units

    phrase = re.sub(r"^please\s+", "", message.strip().lower()).rstrip(".?")
    value = receipt.value
    for pattern, operator in (
        (rf"multiply\s+{_REFERENCE}\s+by\s+({_NUMBER})", "*"),
        (rf"divide\s+{_REFERENCE}\s+by\s+({_NUMBER})", "/"),
        (rf"add\s+({_NUMBER})\s+to\s+{_REFERENCE}", "+"),
        (rf"subtract\s+({_NUMBER})\s+from\s+{_REFERENCE}", "-"),
    ):
        match = re.fullmatch(pattern, phrase)
        if match and receipt.unit is None:
            return "calculator", {"expression": f"({value!r}) {operator} ({match[1]})"}
    match = re.fullmatch(rf"convert\s+{_REFERENCE}\s+from\s+([a-z]+)\s+to\s+([a-z]+)", phrase)
    if match:
        source, target = (_UNIT_NAMES.get(unit.removesuffix("s"), unit) for unit in match.groups())
        if receipt.unit is not None and receipt.unit != source:
            raise ValueError("Source unit differs from the verified result")
        args = UnitConversionInput(value=value, from_unit=source, to_unit=target)
        if args.value != value:
            raise ValueError("Numeric conversion would lose precision")
        convert_units(args.value, args.from_unit, args.to_unit)
        return "unit_conversion", args.model_dump()
    raise ValueError("Unsupported numeric reference")
