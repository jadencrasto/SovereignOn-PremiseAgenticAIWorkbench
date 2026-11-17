"""Typed process-local evidence and a bounded grammar for numeric follow-ups."""
from dataclasses import dataclass
import re
from typing import get_args


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
_ARITHMETIC_REFERENCE = rf"(?:{_REFERENCE}|that)"
_ARITHMETIC_PATTERNS = (
    (rf"multiply\s+{_ARITHMETIC_REFERENCE}\s+by\s+({_NUMBER})", "*"),
    (rf"divide\s+{_ARITHMETIC_REFERENCE}\s+by\s+({_NUMBER})", "/"),
    (rf"add\s+({_NUMBER})\s+to\s+{_ARITHMETIC_REFERENCE}", "+"),
    (rf"subtract\s+({_NUMBER})\s+from\s+{_ARITHMETIC_REFERENCE}", "-"),
    (rf"add\s+({_NUMBER})", "+"),
)
_UNIT_NAMES = {
    "kilometre": "km", "kilometer": "km", "metre": "m", "meter": "m",
    "centimetre": "cm", "centimeter": "cm", "millimetre": "mm", "millimeter": "mm",
    "gram": "g", "kilogram": "kg", "second": "s", "minute": "min", "hour": "h",
}
_UNIT = r"[a-z]{1,16}"
_CONVERSION = rf"convert\s+({_NUMBER})\s*({_UNIT})\s+to\s+({_UNIT})"
_TOTAL = rf"(?:calculate|find|compute)\s+the\s+(?:total|sum)\s+of\s+({_NUMBER}(?:\s*,\s*{_NUMBER}){{0,10}}(?:\s*,?\s+and\s+{_NUMBER})?)"
_EXPRESSION = r"(?:calculate|compute)\s+([0-9x×÷+*/().%\-\s]{1,180})"
_SCALE = rf"@([A-Za-z0-9_]+)\.result\s*\*\s*{_NUMBER}"


def _phrase(message: str) -> str:
    return re.sub(r"^(?:now,?\s+)?(?:please\s+)?", "", message.strip().lower()).rstrip(".?").rstrip()


def _normalize_unit(unit: str) -> str:
    return _UNIT_NAMES.get(unit.removesuffix("s"), unit)


def terminal_scale_reference(expression: str) -> str | None:
    """A single verified quantity multiplied by a dimensionless scalar."""
    match = re.fullmatch(_SCALE, expression)
    return match[1] if match else None


def numeric_request_steps(message: str):
    """Build plans only for whole requests covered by the bounded grammar."""
    from backend.agent.planner import PlanStep
    from backend.tools.unit_conversion import Unit

    if len(message) > 300:
        return None
    phrase = _phrase(message)
    initial, *rest = re.split(r"\s*,?\s+then\s+", phrase, maxsplit=1)
    next_phrase = re.sub(r"\s+and\s+tell me the final answer$", "", rest[0]) if rest else ""
    first = second = None
    conversion = re.fullmatch(_CONVERSION, initial)
    if conversion:
        value, source, target = conversion.groups()
        source, target = _normalize_unit(source), _normalize_unit(target)
        if source not in get_args(Unit) or target not in get_args(Unit):
            return None
        first = PlanStep(id="step_1", description="Convert the requested value", tool_name="unit_conversion",
                         arguments={"value": float(value), "from_unit": source, "to_unit": target})
        if re.fullmatch(r"(?:calculate|compute|find)\s+twice\s+(?:that|the)\s+(?:distance|value|result)", next_phrase):
            second = "@step_1.result * 2"
        else:
            scale = re.fullmatch(rf"multiply\s+{_ARITHMETIC_REFERENCE}\s+by\s+({_NUMBER})", next_phrase)
            if scale:
                second = f"@step_1.result * {scale[1]}"
    else:
        total = re.fullmatch(_TOTAL, initial)
        expression = re.fullmatch(_EXPRESSION, initial) if rest else None
        if total:
            values = re.split(r"\s*,?\s+and\s+|\s*,\s*", total[1])
            if len(values) < 2:
                return None
            first = PlanStep(id="step_1", description="Find the total", tool_name="calculator",
                             arguments={"expression": " + ".join(f"({value})" for value in values)})
            if re.fullmatch(r"(?:calculate|compute|find)\s+the\s+(?:average|mean)", next_phrase):
                second = f"@step_1.result / {len(values)}"
        elif expression and re.search(r"[+*/%x×÷-]", expression[1]):
            first = PlanStep(id="step_1", description="Calculate the initial value", tool_name="calculator",
                             arguments={"expression": expression[1].translate(str.maketrans("x×÷", "**/")).strip()})
            subtract = re.fullmatch(rf"subtract\s+({_NUMBER})", next_phrase)
            if subtract:
                second = f"@step_1.result - ({subtract[1]})"
    if first is None or (rest and second is None):
        return None
    if second is None:
        return [first]
    return [first, PlanStep(id="step_2", description="Calculate from the verified result", tool_name="calculator",
                            arguments={"expression": second})]


def requires_prior_numeric_receipt(message: str) -> bool:
    """Route supported shorthand and explicit references away from model guessing."""
    phrase = _phrase(message)
    if any(re.fullmatch(pattern, phrase) for pattern, _ in _ARITHMETIC_PATTERNS):
        return True
    # Also withhold explicit references outside the resolver's supported grammar.
    reference = rf"(?:{_REFERENCE}|the\s+result\s+from\s+before)"
    return bool(re.match(rf"(?:multiply|divide|add|subtract|convert|calculate|compute|use)\s+{reference}\b", phrase)
                or re.match(r"(?:multiply|divide)\s+that\s+by\b", phrase)
                or re.match(rf"(?:add|subtract)\s+\S+\s+(?:to|from)\s+(?:{reference}\b|that$)", phrase))


def resolve_numeric_followup(message: str, receipt: NumericReceipt):
    """Return exact tool arguments or raise if the phrase or units are unsupported."""
    from backend.tools.unit_conversion import UnitConversionInput, convert_units

    phrase = _phrase(message)
    value = receipt.value
    for pattern, operator in _ARITHMETIC_PATTERNS:
        match = re.fullmatch(pattern, phrase)
        if match and receipt.unit is None:
            return "calculator", {"expression": f"({value!r}) {operator} ({match[1]})"}
    match = re.fullmatch(rf"convert\s+{_REFERENCE}\s+from\s+([a-z]+)\s+to\s+([a-z]+)", phrase)
    if match:
        source, target = map(_normalize_unit, match.groups())
        if receipt.unit is not None and receipt.unit != source:
            raise ValueError("Source unit differs from the verified result")
        args = UnitConversionInput(value=value, from_unit=source, to_unit=target)
        if args.value != value:
            raise ValueError("Numeric conversion would lose precision")
        convert_units(args.value, args.from_unit, args.to_unit)
        return "unit_conversion", args.model_dump()
    raise ValueError("Unsupported numeric reference")
