"""Safe AST-evaluated mathematical calculator tool (no eval/exec)."""

from __future__ import annotations

import ast
import math
import operator
from typing import Callable

MAX_EXPRESSION_LENGTH = 256
MAX_AST_NODES = 64
MAX_POWER_EXPONENT = 1000.0
MAX_RESULT_ABS = 1e100

_BINARY_OPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}

_UNARY_OPS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_SAFE_FUNCTIONS: dict[str, tuple[Callable[..., float], tuple[int, int]]] = {
    "abs": (abs, (1, 1)),
    "round": (lambda x, ndigits=0: round(x, int(ndigits)), (1, 2)),
    "sqrt": (math.sqrt, (1, 1)),
    "sin": (math.sin, (1, 1)),
    "cos": (math.cos, (1, 1)),
    "tan": (math.tan, (1, 1)),
    "log": (math.log, (1, 2)),
    "log10": (math.log10, (1, 1)),
    "exp": (math.exp, (1, 1)),
    "floor": (math.floor, (1, 1)),
    "ceil": (math.ceil, (1, 1)),
    "min": (min, (1, 16)),
    "max": (max, (1, 16)),
}

_SAFE_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}


class CalculatorError(ValueError):
    """Raised when an arithmetic expression is invalid or unsafe."""


def _safe_pow(base: float, exponent: float) -> float:
    if abs(exponent) > MAX_POWER_EXPONENT:
        raise CalculatorError("Exponent exceeds safe calculation bounds.")
    if isinstance(base, (int, float)) and abs(base) > 1e12 and exponent > 12:
        raise CalculatorError("Power magnitude exceeds safe calculation bounds.")
    result = math.pow(base, exponent) if isinstance(exponent, float) and not exponent.is_integer() else (base ** exponent)
    if isinstance(result, complex):
        raise CalculatorError("Complex numbers are not supported.")
    return float(result)


def _eval_node(node: ast.AST, counter: list[int]) -> float | int:
    counter[0] += 1
    if counter[0] > MAX_AST_NODES:
        raise CalculatorError("Expression is too complex.")

    if isinstance(node, ast.Expression):
        return _eval_node(node.body, counter)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorError("Only numeric literals are allowed.")
        return node.value

    if isinstance(node, ast.Name):
        if node.id in _SAFE_CONSTANTS:
            return _SAFE_CONSTANTS[node.id]
        raise CalculatorError(f"Unsupported identifier in expression: {node.id}")

    if isinstance(node, ast.UnaryOp):
        op_fn = _UNARY_OPS.get(type(node.op))
        if op_fn is None:
            raise CalculatorError("Unsupported unary operator.")
        return op_fn(_eval_node(node.operand, counter))

    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, counter)
        right = _eval_node(node.right, counter)
        if isinstance(node.op, ast.Pow):
            res = _safe_pow(left, right)
        else:
            op_fn = _BINARY_OPS.get(type(node.op))
            if op_fn is None:
                raise CalculatorError("Unsupported arithmetic operator.")
            if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
                raise CalculatorError("Division by zero.")
            res = op_fn(left, right)
        if not math.isfinite(float(res)) or abs(float(res)) > MAX_RESULT_ABS:
            raise CalculatorError("Calculation result out of safe range.")
        return res

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.keywords:
            raise CalculatorError("Only simple pure math function calls are allowed.")
        entry = _SAFE_FUNCTIONS.get(node.func.id)
        if entry is None:
            raise CalculatorError(f"Unsupported math function: {node.func.id}")
        fn, (min_args, max_args) = entry
        if not (min_args <= len(node.args) <= max_args):
            raise CalculatorError(f"Invalid argument count for {node.func.id}.")
        args = [_eval_node(arg, counter) for arg in node.args]
        try:
            res = fn(*args)
        except (ValueError, ZeroDivisionError, OverflowError) as exc:
            raise CalculatorError(str(exc)) from exc
        if not math.isfinite(float(res)) or abs(float(res)) > MAX_RESULT_ABS:
            raise CalculatorError("Calculation result out of safe range.")
        return res

    raise CalculatorError(f"Unsupported syntax in expression: {type(node).__name__}")


def evaluate_expression(expression: str) -> float | int:
    """Safely evaluate a mathematical expression using an AST whitelist."""
    if not isinstance(expression, str):
        raise CalculatorError("Expression must be a string.")
    cleaned = expression.strip()
    if not cleaned:
        raise CalculatorError("Expression cannot be empty.")
    if len(cleaned) > MAX_EXPRESSION_LENGTH:
        raise CalculatorError(f"Expression exceeds {MAX_EXPRESSION_LENGTH} characters.")
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"Invalid math syntax: {exc.msg}") from exc
    return _eval_node(tree, [0])


def calculate(expression: str) -> str:
    """Evaluate a safe mathematical expression and format the exact result."""
    value = evaluate_expression(expression)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value)
