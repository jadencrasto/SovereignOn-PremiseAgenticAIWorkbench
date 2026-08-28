"""
backend/tools/calculator.py
----------------------------
AST-based safe arithmetic evaluator.

SECURITY: This tool uses ast.parse() and an explicit whitelist of
node types and operators.  It NEVER calls eval() or exec().

Supported: + - * / // % ** () unary+/- numeric literals
Rejected: function calls, imports, attribute access, variables, strings
"""

from __future__ import annotations

import ast
import logging
import operator
from typing import Union

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Maximum allowed exponent to prevent computational abuse
_MAX_EXPONENT = 1000
# Maximum expression length
_MAX_EXPR_LEN = 500


# ---------------------------------------------------------------------------
# Pydantic input schema
# ---------------------------------------------------------------------------

class CalculatorInput(BaseModel):
    """Input schema for the calculator tool."""
    expression: str = Field(
        ...,
        min_length=1,
        max_length=_MAX_EXPR_LEN,
        description="Arithmetic expression to evaluate, e.g. '125 * 840 * 1.18'",
    )


# ---------------------------------------------------------------------------
# AST Evaluator
# ---------------------------------------------------------------------------

# Allowed binary operators
_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

# Allowed unary operators
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _safe_eval_node(node: ast.AST) -> Union[int, float]:
    """
    Recursively evaluate an AST node.

    Only allows:
        - Numeric literals (int, float)
        - Binary operations (+, -, *, /, //, %, **)
        - Unary operations (+, -)

    Raises ValueError for anything else.
    """
    # --- Numeric literal ---
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Non-numeric constant: {type(node.value).__name__}")

    # --- Binary operation ---
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BINARY_OPS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")

        left = _safe_eval_node(node.left)
        right = _safe_eval_node(node.right)

        # Exponent safety check
        if op_type is ast.Pow:
            if isinstance(right, (int, float)) and abs(right) > _MAX_EXPONENT:
                raise ValueError(
                    f"Exponent too large: {right}. Maximum allowed: {_MAX_EXPONENT}"
                )

        # Division by zero check
        if op_type in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
            raise ValueError("Division by zero.")

        return _BINARY_OPS[op_type](left, right)

    # --- Unary operation ---
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _UNARY_OPS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
        operand = _safe_eval_node(node.operand)
        return _UNARY_OPS[op_type](operand)

    # --- Expression wrapper ---
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body)

    # --- REJECT everything else ---
    raise ValueError(
        f"Disallowed expression element: {type(node).__name__}. "
        "Only arithmetic operations on numeric values are permitted."
    )


def safe_calculate(expression: str) -> Union[int, float]:
    """
    Parse and evaluate an arithmetic expression safely.

    Args:
        expression: A string containing a mathematical expression.

    Returns:
        The numeric result.

    Raises:
        ValueError: If the expression is invalid or contains disallowed elements.
    """
    if not expression or not expression.strip():
        raise ValueError("Empty expression.")

    expr = expression.strip()

    if len(expr) > _MAX_EXPR_LEN:
        raise ValueError(f"Expression too long ({len(expr)} chars, max {_MAX_EXPR_LEN}).")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid arithmetic syntax: {exc.msg}")

    result = _safe_eval_node(tree)

    # Check for infinity / NaN
    if isinstance(result, float):
        if result != result:  # NaN check
            raise ValueError("Result is NaN (not a number).")
        if abs(result) == float("inf"):
            raise ValueError("Result is infinite.")

    return result


# ---------------------------------------------------------------------------
# Tool execute function
# ---------------------------------------------------------------------------

async def execute_calculator(args: CalculatorInput) -> dict:
    """Execute the calculator tool."""
    result = safe_calculate(args.expression)
    return {
        "expression": args.expression,
        "result": result,
    }
