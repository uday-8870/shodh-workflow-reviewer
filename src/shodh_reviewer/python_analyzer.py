"""Restricted Python subset AST analyzer and safe evaluator (no exec/eval)."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from .dsl import canonicalize_field


class UnsupportedPythonError(ValueError):
    """Raised when a Python snippet uses constructs outside the restricted subset."""


@dataclass
class PythonAnalysisResult:
    snippet_id: str
    syntax_valid: bool
    supported: bool
    unsupported_reasons: List[str]
    comments: List[str]
    function_node: Optional[ast.FunctionDef] = None


ALLOWED_BINOPS = (ast.Mult, ast.Div, ast.Add, ast.Sub)


def extract_comments(code: str) -> List[str]:
    comments: List[str] = []
    for line in code.splitlines():
        stripped = line.strip()
        if "#" in stripped:
            idx = stripped.index("#")
            comments.append(stripped[idx + 1 :].strip())
    return comments


def analyze_python_snippet(snippet_id: str, code: str, expected_fn: str = "route_ticket") -> PythonAnalysisResult:
    comments = extract_comments(code)
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return PythonAnalysisResult(
            snippet_id=snippet_id,
            syntax_valid=False,
            supported=False,
            unsupported_reasons=[f"SyntaxError at line {exc.lineno}: {exc.msg}"],
            comments=comments,
        )

    reasons: List[str] = []
    fn_node: Optional[ast.FunctionDef] = None

    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef) and stmt.name == expected_fn:
            fn_node = stmt
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
            continue  # module docstring
        else:
            reasons.append(f"Top-level statement {type(stmt).__name__} is outside restricted subset.")

    if fn_node is None:
        reasons.append(f"Expected function '{expected_fn}' not found.")
        return PythonAnalysisResult(
            snippet_id=snippet_id,
            syntax_valid=True,
            supported=False,
            unsupported_reasons=reasons,
            comments=comments,
        )

    _validate_block(fn_node.body, reasons)
    return PythonAnalysisResult(
        snippet_id=snippet_id,
        syntax_valid=True,
        supported=(len(reasons) == 0),
        unsupported_reasons=reasons,
        comments=comments,
        function_node=fn_node if len(reasons) == 0 else None,
    )


def _validate_block(stmts: List[ast.stmt], reasons: List[str]) -> None:
    for s in stmts:
        if isinstance(s, ast.If):
            _validate_expr(s.test, reasons)
            _validate_block(s.body, reasons)
            _validate_block(s.orelse, reasons)
        elif isinstance(s, ast.Return):
            if s.value is None:
                reasons.append("Bare return without route string is unsupported.")
            else:
                _validate_expr(s.value, reasons)
        elif isinstance(s, ast.Assign):
            if len(s.targets) != 1 or not isinstance(s.targets[0], ast.Name):
                reasons.append("Only simple single-variable assignment is supported.")
            _validate_expr(s.value, reasons)
        elif isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str):
            continue  # docstring
        else:
            reasons.append(f"Statement '{type(s).__name__}' is outside the restricted Python subset.")


def _validate_expr(node: ast.AST, reasons: List[str]) -> None:
    if isinstance(node, ast.BoolOp):
        for v in node.values:
            _validate_expr(v, reasons)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub)):
        _validate_expr(node.operand, reasons)
    elif isinstance(node, ast.Compare):
        _validate_expr(node.left, reasons)
        for comp in node.comparators:
            _validate_expr(comp, reasons)
        for op in node.ops:
            if not isinstance(op, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot)):
                reasons.append(f"Comparison operator '{type(op).__name__}' is unsupported.")
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ALLOWED_BINOPS):
        _validate_expr(node.left, reasons)
        _validate_expr(node.right, reasons)
    elif isinstance(node, ast.Call):
        # Only allow ticket.get("field") or ticket.get("field", default)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and len(node.args) in (1, 2)
            and not node.keywords
        ):
            for a in node.args:
                _validate_expr(a, reasons)
        else:
            reasons.append("Function calls other than ticket.get(...) are outside the restricted subset.")
    elif isinstance(node, ast.Subscript):
        _validate_expr(node.value, reasons)
        _validate_expr(node.slice, reasons)
    elif isinstance(node, (ast.Name, ast.Constant)):
        return
    else:
        reasons.append(f"Expression '{type(node).__name__}' is outside the restricted Python subset.")


def eval_python_ast(fn_node: ast.FunctionDef, ticket: Dict[str, Any]) -> Optional[str]:
    """Safely interpret a validated restricted-subset function on a ticket dict."""
    env: Dict[str, Any] = {"ticket": _NormalizedTicketView(ticket)}
    return _exec_block(fn_node.body, env)


class _NormalizedTicketView:
    """Provides dict-like .get() and [] access with unit normalization for time fields."""

    def __init__(self, ticket: Dict[str, Any]):
        self._ticket = ticket

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._ticket:
            return self._ticket[key]
        # Unit conversion if ticket stores canonical/other time unit
        kc, k_kind, k_factor = canonicalize_field(key)
        if k_kind == "time":
            for tk, tv in self._ticket.items():
                tc, t_kind, t_factor = canonicalize_field(tk)
                if tc == kc and t_kind == "time":
                    if tv is None:
                        return None
                    return float(tv) * t_factor / k_factor
        return default

    def __getitem__(self, key: str) -> Any:
        return self.get(key, None)


def _exec_block(stmts: List[ast.stmt], env: Dict[str, Any]) -> Optional[str]:
    for s in stmts:
        if isinstance(s, ast.If):
            cond = bool(_eval_node(s.test, env))
            res = _exec_block(s.body if cond else s.orelse, env)
            if res is not None:
                return res
        elif isinstance(s, ast.Return):
            val = _eval_node(s.value, env) if s.value is not None else None
            return str(val) if val is not None else None
        elif isinstance(s, ast.Assign):
            name = s.targets[0].id  # type: ignore[attr-defined]
            env[name] = _eval_node(s.value, env)
    return None


def _eval_node(node: ast.AST, env: Dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id, None)
    if isinstance(node, ast.UnaryOp):
        val = _eval_node(node.operand, env)
        if isinstance(node.op, ast.Not):
            return not bool(val)
        if isinstance(node.op, ast.USub):
            return -val
    if isinstance(node, ast.BinOp):
        l = _eval_node(node.left, env)
        r = _eval_node(node.right, env)
        if l is None or r is None:
            return None
        if isinstance(node.op, ast.Mult):
            return l * r
        if isinstance(node.op, ast.Div):
            return l / r
        if isinstance(node.op, ast.Add):
            return l + r
        if isinstance(node.op, ast.Sub):
            return l - r
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for v in node.values:
                if not _eval_node(v, env):
                    return False
            return True
        if isinstance(node.op, ast.Or):
            for v in node.values:
                if _eval_node(v, env):
                    return True
            return False
    if isinstance(node, ast.Call):
        # ticket.get(...)
        target = _eval_node(node.func.value, env)  # type: ignore[attr-defined]
        key = _eval_node(node.args[0], env)
        default = _eval_node(node.args[1], env) if len(node.args) == 2 else None
        return target.get(key, default)
    if isinstance(node, ast.Subscript):
        target = _eval_node(node.value, env)
        key = _eval_node(node.slice, env)
        return target[key]
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, env)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval_node(comp, env)
            if isinstance(op, ast.Is):
                ok = left is right
            elif isinstance(op, ast.IsNot):
                ok = left is not right
            elif left is None or right is None:
                if isinstance(op, ast.Eq):
                    ok = left == right
                elif isinstance(op, ast.NotEq):
                    ok = left != right
                else:
                    ok = False
            elif isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            else:
                ok = False
            if not ok:
                return False
            left = right
        return True
    raise UnsupportedPythonError(f"Cannot evaluate AST node {type(node).__name__}")
