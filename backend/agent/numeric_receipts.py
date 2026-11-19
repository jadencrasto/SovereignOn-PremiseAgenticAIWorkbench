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
_UNSIGNED_NUMBER = r"(?:[0-9]{1,18}(?:\.[0-9]{1,18})?|\.[0-9]{1,18})"
_NUMBER = rf"[+-]?{_UNSIGNED_NUMBER}"
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


def arithmetic_expression(message: str) -> str | None:
    """
    Recognize simple arithmetic expressions for the validated calculator plan.

    Returns the expression, or None if the message
    is not a simple arithmetic query.

    Handles patterns like:
      - "What is 25 + 17?"
      - "calculate 125 * 840 * 1.18"
      - "25 + 17"
      - "What is 3.14 * 2?"

    """
    import re as _re

    if not message or len(message) > 200:
        return None

    q = message.strip()
    q_lower = q.lower()

    # Pattern 1: "What is <expr>?" / "What's <expr>?"
    m = _re.match(
        r"^(?:what(?:'s|\s+is)\s+)([\d\s\+\-\*\/\.\(\)\^%x×]+)\s*\??$",
        q_lower,
    )
    expr = None
    if m:
        expr = m.group(1).strip()

    # Pattern 2: "calculate <expr>" / "compute <expr>"
    if expr is None:
        m = _re.match(
            r"^(?:calculate|compute|eval(?:uate)?)\s+([\d\s\+\-\*\/\.\(\)\^%x×]+)\s*\??$",
            q_lower,
        )
        if m:
            expr = m.group(1).strip()

    # Pattern 3: Pure arithmetic expression (e.g. "25 + 17", "3 * 4.5")
    if expr is None:
        m = _re.match(
            r"^([\d\s\+\-\*\/\.\(\)\^%x×]+)$",
            q.strip(),
        )
        if m:
            expr = m.group(1).strip()

    if expr is None or not expr:
        return None

    # Normalize: replace × with *, x with * (when between digits)
    expr = _re.sub(r"×", "*", expr)
    expr = _re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", expr)
    expr = _re.sub(r"\^", "**", expr)

    # Must contain at least one operator and one digit
    if not _re.search(r"[\+\-\*\/\%]", expr):
        return None
    if not _re.search(r"\d", expr):
        return None

    return expr


def numeric_request_steps(message: str, *, local_calculator=False):
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
            elif local_calculator:
                next_phrase = re.sub(r'\bthe\s+result\b', 'that result', next_phrase)
                for pattern, operator in _ARITHMETIC_PATTERNS:
                    if match := re.fullmatch(pattern, next_phrase):
                        second = f"@step_1.result {operator} ({match[1]})"
                        break
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


def local_numeric_phrase(message: str) -> str:
    phrase = _phrase(re.sub(r'^\s*(?:can|could|would) you\s+', '', message, flags=re.IGNORECASE))
    # Named changes take an unsigned magnitude. A decrease has negative sign.
    return re.sub(
        rf'\b(?:an?\s+)?(?:temperature\s+)?(increase|decrease|difference)\s+of\s+({_UNSIGNED_NUMBER})\s*'
        rf'({_LOCAL_TEMPERATURE_SCALE})',
        lambda m: f"{'-' if m[1] == 'decrease' else ''}{m[2]} delta {m[3]}", phrase,
    ) if len(phrase) <= 300 else phrase


_LOCAL_REFERENCE = rf"(?:{_ARITHMETIC_REFERENCE}|the\s+(?:result|answer|total))"
_LOCAL_FOLLOWUP = tuple(
    (pattern.replace(_ARITHMETIC_REFERENCE, _LOCAL_REFERENCE), operator)
    for pattern, operator in _ARITHMETIC_PATTERNS
) + ((rf"subtract\s+({_NUMBER})", "-"),)
_LOCAL_UNIT = r"(?:[a-z°δ]+(?:[ \t]+[a-z°δ]+){0,5}?)"
_LOCAL_TEMPERATURE_SCALE = r"(?:°[cf]\b|degrees?\s+(?:[cf]|celsius|fahrenheit)\b|(?:celsius|fahrenheit|k|kelvin)\b)"
_LOCAL_CONVERT = rf"(?:convert\s+|what(?:'s|\s+is)\s+|how much is\s+)?({_NUMBER})\s*({_LOCAL_UNIT})\s*(?:\s+(?:to|into|in)\s+|→\s*)({_LOCAL_UNIT})"
_LOCAL_CONVERT_REFERENCE = rf"convert\s+{_LOCAL_REFERENCE}(?:\s+from\s+({_LOCAL_UNIT}))?\s+(?:back\s+)?(?:to|into)\s+({_LOCAL_UNIT})"
_LOCAL_NAMED_REFERENCE = r"(?:(?:the\s+)?(?:result\s+from|answer\s+to)\s+)?(?:(?:the|my)\s+)?(first|second|third)\s+(calculation|conversion)(?:\s+result)?"
_LOCAL_UNIQUE_REFERENCE = r"(?:the\s+)?(?:earlier\s+)?(calculation|conversion)\s+result"
_LOCAL_TEMPERATURE_READING = r"(?:(?:the|my)\s+)?(first|second|third)\s+temperature\s+reading"
_LOCAL_REFERENCE_TOOL = {'calculation': 'calculator', 'conversion': 'unit_conversion'}
_LOCAL_ORDINAL = {'first': 1, 'second': 2, 'third': 3}
_LOCAL_REFERENCE_HINT = re.compile(
    r'\b(?:that|those|it|them|results?|answers?|previous|earlier|above|first|second|third|last|'
    r'former|latter|calculation|conversion|total|value|reading)\b'
)
_LOCAL_ALIASES = {
    **_UNIT_NAMES,
    'milligram': 'mg', 'inch': 'in', 'inches': 'in', 'foot': 'ft', 'feet': 'ft',
    'mile': 'mi', 'ounce': 'oz', 'pound': 'lb', 'lb': 'lb',
    '°c': '°C', 'celsius': '°C', '°f': '°F', 'fahrenheit': '°F',
    'k': 'K', 'kelvin': 'K', 'ml': 'mL', 'l': 'L',
    'milliliter': 'mL', 'millilitre': 'mL', 'liter': 'L', 'litre': 'L',
    'us fl oz': 'US fl oz', 'us fluid ounce': 'US fl oz',
    'us cup': 'US cup', 'us customary cup': 'US cup',
    'us gal': 'US gal', 'us gallon': 'US gal', 'us liquid gallon': 'US gal',
}


def local_unit_name(unit: str) -> str:
    from backend.tools.unit_conversion import LocalUnit

    unit = ' '.join(unit.lower().split())
    explicit = re.match(r'^(?:(δ)\s*|(delta|absolute)\s+)', unit)
    difference = bool(explicit and 'absolute' not in explicit.groups())
    if explicit:
        unit = unit[explicit.end():]
    suffix = re.search(r'\s+(?:temperature\s+)?(?:difference|increase)s?$', unit)
    if suffix:
        if explicit and not difference:
            raise ValueError('Conflicting temperature types')
        difference = True
        unit = unit[:suffix.start()]
    unit = re.sub(r'^degrees?\s+([cf])$', r'°\1', unit)
    unit = re.sub(r'^degrees?\s+', '', unit)
    if difference and unit in ('c', 'f'):
        unit = f'°{unit}'
    canonical = _LOCAL_ALIASES.get(unit, _LOCAL_ALIASES.get(unit.removesuffix('s'), unit))
    if explicit or suffix:
        if canonical not in ('°C', '°F', 'K'):
            raise ValueError('Expected a temperature unit')
        if difference:
            canonical = f'Δ{canonical}'
    if canonical not in get_args(LocalUnit):
        raise ValueError('Unsupported or ambiguous unit')
    return canonical


def local_conversion_target(unit: str, origin: str) -> str:
    """A plain destination scale inherits the source's temperature difference type."""
    target = local_unit_name(unit)
    if origin.startswith('Δ') and target in ('°C', '°F', 'K') and not unit.lower().startswith('absolute '):
        return f'Δ{target}'
    return target


def format_local_numeric_value(value: int | float, unit: str | None) -> str:
    if unit is None:
        return str(value)
    label = ' (temperature difference)' if unit.startswith('Δ') else ''
    return f'{value:.12g} {unit}{label}'


def local_conversion_intent(phrase: str):
    """Return explicit conversion operands or identify an unsupported conversion request."""
    if len(phrase) <= 300:
        if match := re.fullmatch(_LOCAL_CONVERT, phrase):
            return match.groups()
        if match := re.fullmatch(rf"how many\s+({_LOCAL_UNIT})\s+(?:are\s+)?in\s+({_NUMBER})\s*({_LOCAL_UNIT})", phrase):
            target, value, source = match.groups()
            return value, source, target
    if re.match(r'^convert\b', phrase) or '→' in phrase:
        raise ValueError('Unsupported conversion request')
    return None


@dataclass(frozen=True)
class LocalNumericReference:
    kind: str = 'adjacent'
    tool: str | None = None
    ordinal: int | None = None
    quantity: str | None = None


@dataclass(frozen=True)
class LocalNumericFollowup:
    operation: str
    reference: LocalNumericReference
    operand: str | None = None
    unit: str | None = None
    other_reference: LocalNumericReference | None = None


def _local_reference(phrase: str):
    if re.fullmatch(_LOCAL_REFERENCE, phrase):
        return LocalNumericReference()
    if match := re.fullmatch(_LOCAL_NAMED_REFERENCE, phrase):
        return LocalNumericReference('ordinal', _LOCAL_REFERENCE_TOOL[match[2]], _LOCAL_ORDINAL[match[1]])
    if match := re.fullmatch(_LOCAL_UNIQUE_REFERENCE, phrase):
        return LocalNumericReference('unique', _LOCAL_REFERENCE_TOOL[match[1]])
    if match := re.fullmatch(_LOCAL_TEMPERATURE_READING, phrase):
        return LocalNumericReference('ordinal', ordinal=_LOCAL_ORDINAL[match[1]], quantity='absolute_temperature')
    if re.fullmatch(r'(?:the\s+)?earlier\s+distance\s+conversion(?:\s+result)?', phrase):
        return LocalNumericReference('unique', 'unit_conversion', quantity='length')
    if re.fullmatch(r'(?:the\s+)?previous\s+temperature\s+difference', phrase):
        return LocalNumericReference('latest', quantity='temperature_difference')
    if re.fullmatch(r"(?:the\s+)?(?:earlier (?:value|result)|value we calculated before)", phrase):
        return LocalNumericReference('unique', 'calculator')
    raise ValueError('Unclear numeric reference')


def local_followup_intent(phrase: str):
    """Parse a whole reference request or flag one that needs clarification."""
    if len(phrase) <= 300:
        if match := re.fullmatch(
            rf'subtract\s+(?P<right>{_LOCAL_TEMPERATURE_READING})\s+from\s+'
            rf'(?P<left>{_LOCAL_TEMPERATURE_READING}|(?:the\s+)?(?:first|second|third))', phrase,
        ):
            left = match['left']
            if not left.endswith('reading'):
                left += ' temperature reading'
            return LocalNumericFollowup('-', _local_reference(left), other_reference=_local_reference(match['right']))
        for pattern, operator in _LOCAL_FOLLOWUP:
            if match := re.fullmatch(pattern, phrase):
                return LocalNumericFollowup(operator, LocalNumericReference(), match[1])
        if match := re.fullmatch(_LOCAL_CONVERT_REFERENCE, phrase):
            return LocalNumericFollowup('convert', LocalNumericReference(), match[1], match[2])
        if re.fullmatch(r"what (?:was|is) the (?:answer|result) before that", phrase):
            return LocalNumericFollowup('recall', LocalNumericReference('before'))
        if match := re.fullmatch(r"(?:use|show(?: me)?)\s+(.+)", phrase):
            if _LOCAL_REFERENCE_HINT.search(match[1]):
                return LocalNumericFollowup('recall', _local_reference(match[1]))
        for action, connector, operator in (('add', 'to', '+'), ('subtract', 'from', '-')):
            if match := re.fullmatch(rf"{action}\s+({_NUMBER})(?:\s*({_LOCAL_UNIT}))??\s+{connector}\s+(.+)", phrase):
                if _LOCAL_REFERENCE_HINT.search(match[3]):
                    return LocalNumericFollowup(operator, _local_reference(match[3]), match[1], match[2])
        if match := re.fullmatch(rf"(multiply|divide)\s+(.+)\s+by\s+({_NUMBER})", phrase):
            action, reference, value = match.groups()
            if _LOCAL_REFERENCE_HINT.search(reference):
                return LocalNumericFollowup('*' if action == 'multiply' else '/', _local_reference(reference), value)
        if match := re.fullmatch(rf"convert\s+(.+?)\s+(?:back\s+)?to\s+({_LOCAL_UNIT})", phrase):
            if _LOCAL_REFERENCE_HINT.search(match[1]):
                return LocalNumericFollowup('convert', _local_reference(match[1]), unit=match[2])
        if match := re.fullmatch(r"convert\s+(.+)", phrase):
            if _LOCAL_REFERENCE_HINT.search(match[1]):
                return LocalNumericFollowup('convert', _local_reference(match[1]))
    if re.match(rf'^(?:calculate|compute)\s+{_LOCAL_REFERENCE}\b', phrase) or (
        re.match(r'^(?:add|subtract|multiply|divide|convert|use)\b', phrase) and _LOCAL_REFERENCE_HINT.search(phrase)
    ):
        raise ValueError('Unclear numeric reference')
    return None


def local_output_unit(phrase: str, *, followup=False) -> tuple[str, str | None]:
    """Separate the target of an explicit measurement calculation."""
    connector = 'in' if followup else '(?:in|to)'
    if len(phrase) <= 300 and (match := re.fullmatch(rf"(.+)\s+{connector}\s+({_LOCAL_UNIT})", phrase)):
        return match[1], match[2]
    return phrase, None


def local_measurement_intent(phrase: str):
    """Recognize a single binary measurement expression, with an optional final unit."""
    core, target = local_output_unit(phrase)
    prefix = r"(?:what(?:'s|\s+is)\s+|calculate\s+|compute\s+)?"
    operators = {'plus': '+', 'minus': '-', 'times': '*', 'divided by': '/'}
    expression = (
        rf"{prefix}({_NUMBER})\s*({_LOCAL_UNIT})?\s*([-+*/]|\b(?:plus|minus|times|divided by)\b)"
        rf"\s*({_NUMBER})\s*({_LOCAL_UNIT})?"
    )
    if len(phrase) <= 300:
        if match := re.fullmatch(expression, core):
            left, left_unit, operation, right, right_unit = match.groups()
            if left_unit or right_unit or target:
                return left, left_unit, operators.get(operation, operation), right, right_unit, target
        operand = rf"({_NUMBER})(?:\s*({_LOCAL_UNIT}))??"
        for verb, connector, operation in (
            ('add', 'to', '+'), ('subtract', 'from', '-'), ('multiply', 'by', '*'), ('divide', 'by', '/'),
        ):
            if match := re.fullmatch(rf"{verb}\s+{operand}\s+{connector}\s+{operand}", core):
                left, left_unit, right, right_unit = match.groups()
                if left_unit or right_unit or target:
                    if operation in ('+', '-'):
                        return right, right_unit, operation, left, left_unit, target
                    return left, left_unit, operation, right, right_unit, target
                return None
    # Give malformed quantity arithmetic a deterministic error instead of forwarding
    # the expression to the model as a dimensionless calculator request.
    command = re.match(r'^(?:add|subtract|multiply|divide)\s+(?=[+-]?(?:\d|\.\d))', phrase)
    if (re.search(r'\b(?:calculate|compute|add|subtract|multiply|divide|find|evaluate)\b|'
                  r'\b(?:sum|product|quotient|difference)\s+of\b', phrase)
            and re.search(rf'\d\s*(?:(?:absolute|delta)\s+|δ\s*)?{_LOCAL_TEMPERATURE_SCALE}', phrase)):
        raise ValueError('Malformed temperature arithmetic')
    if (command or re.match(rf"{prefix}[+-]?(?:\d|\.\d)", phrase)) and re.search(
        r"(?:\d\s*[a-z°δ]+|[-+*/]\s*[-+]?\d+(?:\.\d+)?\s*[a-z°δ]+)", phrase,
    ) and re.search(r"[-+*/%^]|\b(?:to|from|by)\b", phrase.lstrip("+-")):
        raise ValueError('Malformed measurement arithmetic')
    return None


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
