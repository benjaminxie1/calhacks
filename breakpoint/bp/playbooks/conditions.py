"""A tiny, safe condition language for playbook branches.

Conditions are Python-like boolean expressions over context facts only:
names, numbers, ==, !=, <, <=, >, >=, and, or, not, +, -, *. No calls, no
attributes, no subscripts. Facts are Decimals, bools, ints or strings.
"""
import ast
from decimal import Decimal

_ALLOWED = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.Compare,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Name, ast.Load, ast.Constant,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult,
)


class ConditionError(ValueError):
    pass


def parse(expr: str) -> ast.Expression:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ConditionError(f"syntax error in condition {expr!r}: {e.msg}") from None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ConditionError(f"{type(node).__name__} is not allowed in conditions ({expr!r})")
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            raise ConditionError(f"float literal in condition {expr!r}; write an integer or quote a decimal")
    return tree


def names(expr: str) -> set[str]:
    return {n.id for n in ast.walk(parse(expr)) if isinstance(n, ast.Name)} - {"True", "False"}


def _ev(node, facts):
    if isinstance(node, ast.Expression):
        return _ev(node.body, facts)
    if isinstance(node, ast.Constant):
        v = node.value
        return Decimal(v) if isinstance(v, int) and not isinstance(v, bool) else v
    if isinstance(node, ast.Name):
        if node.id in ("True", "False"):
            return node.id == "True"
        if node.id not in facts:
            raise ConditionError(f"unknown fact {node.id!r}")
        v = facts[node.id]
        return Decimal(v) if isinstance(v, int) and not isinstance(v, bool) else v
    if isinstance(node, ast.BoolOp):
        vals = [_ev(v, facts) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp):
        v = _ev(node.operand, facts)
        return (not v) if isinstance(node.op, ast.Not) else -v
    if isinstance(node, ast.BinOp):
        a, b = _ev(node.left, facts), _ev(node.right, facts)
        if a is None or b is None:
            return None
        return a + b if isinstance(node.op, ast.Add) else a - b if isinstance(node.op, ast.Sub) else a * b
    if isinstance(node, ast.Compare):
        left = _ev(node.left, facts)
        for op, comp in zip(node.ops, node.comparators):
            right = _ev(comp, facts)
            if left is None or right is None:
                return False
            ok = {
                ast.Eq: lambda: left == right, ast.NotEq: lambda: left != right,
                ast.Lt: lambda: left < right, ast.LtE: lambda: left <= right,
                ast.Gt: lambda: left > right, ast.GtE: lambda: left >= right,
            }[type(op)]()
            if not ok:
                return False
            left = right
        return True
    raise ConditionError(f"cannot evaluate {type(node).__name__}")


def evaluate(expr: str, facts: dict) -> bool:
    return bool(_ev(parse(expr), facts))
