"""Structured condition DSL parser, unit normalizer, and evaluator.

Supported language:
- Boolean operators: AND, OR, NOT (case-insensitive) and parentheses (...)
- Missing value checks: MISSING(field), PRESENT(field), field == None, field != None
- Comparisons: ==, !=, <, <=, >, >=
- Literals: numbers (int/float), quoted strings ("urgent", 'vip'), booleans (true/false), None
- Declared time units on numeric literals: seconds/sec/s, minutes/min/m, hours/hr/h
  (normalized to canonical seconds for exact cross-unit comparison).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple, Union


TIME_UNIT_FACTORS: Dict[str, float] = {
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "m": 60.0,
    "min": 60.0,
    "mins": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hrs": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
}

# Field-name unit suffixes for bare numeric comparisons (e.g. waiting_minutes >= 30)
FIELD_UNIT_MAP: Dict[str, Tuple[str, float]] = {
    "_seconds": ("time", 1.0),
    "_sec": ("time", 1.0),
    "_minutes": ("time", 60.0),
    "_min": ("time", 60.0),
    "_hours": ("time", 3600.0),
    "_hr": ("time", 3600.0),
}


class DSLParseError(ValueError):
    """Raised when a condition string falls outside the supported DSL."""


@dataclass(frozen=True)
class Expr:
    pass


@dataclass(frozen=True)
class BoolLit(Expr):
    value: bool


@dataclass(frozen=True)
class NotExpr(Expr):
    operand: Expr


@dataclass(frozen=True)
class BinBoolExpr(Expr):
    op: str  # "AND" | "OR"
    left: Expr
    right: Expr


@dataclass(frozen=True)
class MissingExpr(Expr):
    field: str
    negated: bool = False  # True means PRESENT(field)


@dataclass(frozen=True)
class CompareExpr(Expr):
    field: str
    op: str  # ==, !=, <, <=, >, >=
    value: Any
    unit_kind: Optional[str] = None  # "time" if normalized to seconds
    canonical_field: str = ""


def canonicalize_field(field: str) -> Tuple[str, Optional[str], float]:
    """Return (canonical_field_name, unit_kind, factor_to_canonical)."""
    for suffix, (kind, factor) in FIELD_UNIT_MAP.items():
        if field.endswith(suffix):
            base = field[: -len(suffix)] + "_seconds"
            return base, kind, factor
    return field, None, 1.0


_TOKEN_SPEC = [
    ("SKIP", r"[ \t\r\n]+"),
    ("LPAREN", r"\("),
    ("RPAREN", r"\)"),
    ("OP", r"==|!=|<=|>=|<|>"),
    ("STRING", r'"[^"]*"|\'[^\']*\''),
    ("NUMBER_UNIT", r"\d+(?:\.\d+)?\s*(?:seconds|second|secs|sec|s|minutes|minute|mins|min|m|hours|hour|hrs|hr|h)\b"),
    ("NUMBER", r"\d+(?:\.\d+)?"),
    ("IDENT", r"[A-Za-z_][A-Za-z0-9_]*"),
    ("MISMATCH", r"."),
]
_TOKEN_RE = re.compile("|".join(f"(?P<{name}>{pat})" for name, pat in _TOKEN_SPEC), re.IGNORECASE)


def _tokenize(text: str) -> List[Tuple[str, str]]:
    tokens: List[Tuple[str, str]] = []
    for m in _TOKEN_RE.finditer(text):
        kind = m.lastgroup
        val = m.group()
        if kind == "SKIP":
            continue
        if kind == "MISMATCH":
            raise DSLParseError(f"Unsupported token '{val}' in expression: {text}")
        tokens.append((kind, val))
    return tokens


class _Parser:
    def __init__(self, tokens: List[Tuple[str, str]], raw: str):
        self.tokens = tokens
        self.pos = 0
        self.raw = raw

    def peek(self) -> Optional[Tuple[str, str]]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def consume(self) -> Tuple[str, str]:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse(self) -> Expr:
        if not self.tokens:
            raise DSLParseError("Empty condition expression")
        expr = self._parse_or()
        if self.pos < len(self.tokens):
            raise DSLParseError(f"Unexpected trailing token {self.tokens[self.pos]} in: {self.raw}")
        return expr

    def _parse_or(self) -> Expr:
        left = self._parse_and()
        while (tok := self.peek()) and tok[0] == "IDENT" and tok[1].upper() == "OR":
            self.consume()
            right = self._parse_and()
            left = BinBoolExpr("OR", left, right)
        return left

    def _parse_and(self) -> Expr:
        left = self._parse_not()
        while (tok := self.peek()) and tok[0] == "IDENT" and tok[1].upper() == "AND":
            self.consume()
            right = self._parse_not()
            left = BinBoolExpr("AND", left, right)
        return left

    def _parse_not(self) -> Expr:
        tok = self.peek()
        if tok and tok[0] == "IDENT" and tok[1].upper() == "NOT":
            self.consume()
            return NotExpr(self._parse_not())
        return self._parse_atom()

    def _parse_atom(self) -> Expr:
        tok = self.peek()
        if not tok:
            raise DSLParseError(f"Unexpected end of expression: {self.raw}")
        if tok[0] == "LPAREN":
            self.consume()
            inner = self._parse_or()
            closing = self.peek()
            if not closing or closing[0] != "RPAREN":
                raise DSLParseError(f"Missing closing ')' in: {self.raw}")
            self.consume()
            return inner

        if tok[0] == "IDENT":
            ident = tok[1]
            upper = ident.upper()
            if upper == "TRUE":
                self.consume()
                return BoolLit(True)
            if upper == "FALSE":
                self.consume()
                return BoolLit(False)
            if upper in ("MISSING", "PRESENT"):
                self.consume()
                lp = self.peek()
                if not lp or lp[0] != "LPAREN":
                    raise DSLParseError(f"Expected '(' after {upper} in: {self.raw}")
                self.consume()
                fld = self.peek()
                if not fld or fld[0] != "IDENT":
                    raise DSLParseError(f"Expected field name in {upper}(...) in: {self.raw}")
                self.consume()
                rp = self.peek()
                if not rp or rp[0] != "RPAREN":
                    raise DSLParseError(f"Expected ')' in {upper}(...) in: {self.raw}")
                self.consume()
                return MissingExpr(field=fld[1], negated=(upper == "PRESENT"))

            # Comparison: field OP literal
            self.consume()
            op_tok = self.peek()
            if not op_tok or op_tok[0] != "OP":
                raise DSLParseError(f"Expected comparison operator after '{ident}' in: {self.raw}")
            op = self.consume()[1]
            val_tok = self.peek()
            if not val_tok:
                raise DSLParseError(f"Expected value after '{ident} {op}' in: {self.raw}")
            self.consume()

            c_field, field_unit_kind, field_factor = canonicalize_field(ident)

            if val_tok[0] == "STRING":
                val: Any = val_tok[1][1:-1]
                return CompareExpr(field=ident, op=op, value=val, unit_kind=None, canonical_field=c_field)
            if val_tok[0] == "IDENT":
                v_up = val_tok[1].upper()
                if v_up == "NONE" or v_up == "NULL":
                    if op == "==":
                        return MissingExpr(field=ident, negated=False)
                    if op == "!=":
                        return MissingExpr(field=ident, negated=True)
                    raise DSLParseError(f"Unsupported operator '{op}' with None in: {self.raw}")
                if v_up == "TRUE":
                    return CompareExpr(field=ident, op=op, value=True, unit_kind=None, canonical_field=c_field)
                if v_up == "FALSE":
                    return CompareExpr(field=ident, op=op, value=False, unit_kind=None, canonical_field=c_field)
                raise DSLParseError(f"Unsupported bare identifier value '{val_tok[1]}' in: {self.raw}")
            if val_tok[0] == "NUMBER_UNIT":
                m = re.match(r"(\d+(?:\.\d+)?)\s*([A-Za-z]+)", val_tok[1])
                assert m is not None
                num = float(m.group(1))
                unit = m.group(2).lower()
                sec = num * TIME_UNIT_FACTORS[unit]
                return CompareExpr(field=ident, op=op, value=sec, unit_kind="time", canonical_field=c_field)
            if val_tok[0] == "NUMBER":
                num = float(val_tok[1])
                if field_unit_kind == "time":
                    return CompareExpr(
                        field=ident,
                        op=op,
                        value=num * field_factor,
                        unit_kind="time",
                        canonical_field=c_field,
                    )
                return CompareExpr(field=ident, op=op, value=num, unit_kind=None, canonical_field=c_field)

        raise DSLParseError(f"Unexpected token {tok} in: {self.raw}")


def parse_condition(text: str) -> Expr:
    tokens = _tokenize(text)
    return _Parser(tokens, text).parse()


def get_ticket_field(ticket: Dict[str, Any], field: str, unit_kind: Optional[str]) -> Tuple[bool, Any]:
    """Retrieve field from ticket, normalizing time units if needed. Returns (is_present, value)."""
    if field in ticket:
        val = ticket[field]
        if val is None or (isinstance(val, str) and val.strip() == ""):
            return False, None
        _, f_kind, f_factor = canonicalize_field(field)
        if unit_kind == "time" and f_kind == "time" and isinstance(val, (int, float)):
            return True, float(val) * f_factor
        return True, val

    # Check equivalent time-unit field names in ticket
    c_field, _, _ = canonicalize_field(field)
    for k, val in ticket.items():
        kc, k_kind, k_factor = canonicalize_field(k)
        if kc == c_field:
            if val is None or (isinstance(val, str) and val.strip() == ""):
                return False, None
            if k_kind == "time" and isinstance(val, (int, float)):
                return True, float(val) * k_factor
            return True, val
    return False, None


def eval_expr(expr: Expr, ticket: Dict[str, Any]) -> bool:
    if isinstance(expr, BoolLit):
        return expr.value
    if isinstance(expr, NotExpr):
        return not eval_expr(expr.operand, ticket)
    if isinstance(expr, BinBoolExpr):
        if expr.op == "AND":
            return eval_expr(expr.left, ticket) and eval_expr(expr.right, ticket)
        return eval_expr(expr.left, ticket) or eval_expr(expr.right, ticket)
    if isinstance(expr, MissingExpr):
        present, _ = get_ticket_field(ticket, expr.field, None)
        return present if expr.negated else (not present)
    if isinstance(expr, CompareExpr):
        present, actual = get_ticket_field(ticket, expr.field, expr.unit_kind)
        if not present:
            return expr.op == "!="
        target = expr.value
        if isinstance(actual, (int, float)) and isinstance(target, (int, float)) and not isinstance(actual, bool):
            a, t = float(actual), float(target)
            if expr.op == "==":
                return a == t
            if expr.op == "!=":
                return a != t
            if expr.op == "<":
                return a < t
            if expr.op == "<=":
                return a <= t
            if expr.op == ">":
                return a > t
            if expr.op == ">=":
                return a >= t
        if expr.op == "==":
            return actual == target
        if expr.op == "!=":
            return actual != target
        return False
    raise DSLParseError(f"Unknown expression node: {expr}")


def collect_domain_hints(
    expr: Expr,
    numeric_bounds: Dict[str, Set[float]],
    string_values: Dict[str, Set[Any]],
    fields: Set[str],
) -> None:
    if isinstance(expr, NotExpr):
        collect_domain_hints(expr.operand, numeric_bounds, string_values, fields)
    elif isinstance(expr, BinBoolExpr):
        collect_domain_hints(expr.left, numeric_bounds, string_values, fields)
        collect_domain_hints(expr.right, numeric_bounds, string_values, fields)
    elif isinstance(expr, MissingExpr):
        cf, _, _ = canonicalize_field(expr.field)
        fields.add(cf)
    elif isinstance(expr, CompareExpr):
        cf = expr.canonical_field or canonicalize_field(expr.field)[0]
        fields.add(cf)
        if isinstance(expr.value, (int, float)) and not isinstance(expr.value, bool):
            numeric_bounds.setdefault(cf, set()).add(float(expr.value))
        else:
            string_values.setdefault(cf, set()).add(expr.value)
