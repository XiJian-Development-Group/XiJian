"""统一条件求值器 — Galgame 引擎共用。

为剧情（plot_runtime）与事件（events）提供一致的条件判断，并新增
字符串布尔表达式支持（Galgame 的 Step 条件、选项可见性、跳转条件）。

两类输入都支持：

1. dict 条件（向后兼容既有 plot/events 格式）::

       {"field": "affinity", "op": "gte", "value": 5}
       {"field": "tags", "op": "in", "value": ["a", "b"]}

2. 字符串布尔表达式（新增）::

       "affinity >= 5 && flag.metHero == 1"
       "inv_sword > 0 || !flag.bossDefeated"

字符串解析走自写 tokenizer + 递归下降，**绝不使用 eval/exec**，
避免任意代码注入。

上下文（ctx）是一个扁平或嵌套的 dict；点号键（如 ``flag.metHero``）
会按嵌套路径解析。
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------


def eval_any(cond: Any, ctx: dict[str, Any]) -> bool:
    """自动判别 dict / str / None 并求值。

    - ``None`` / 空：视为无条件，返回 ``True``
    - ``dict``：走 dict 条件（兼容既有格式）
    - ``str``：走字符串布尔表达式
    """
    if cond is None:
        return True
    if isinstance(cond, dict):
        return eval_dict_condition(cond, ctx)
    if isinstance(cond, str):
        if not cond.strip():
            return True
        return eval_expr(cond, ctx)
    # 其它类型（bool / number）直接当布尔
    return bool(cond)


def eval_dict_condition(cond: dict[str, Any], ctx: dict[str, Any]) -> bool:
    """dict 条件（兼容 plot_runtime / events 既有格式）。

    语义与 plot_runtime._evaluate_condition 保持一致：
    ``contains`` = 字符串包含（str(expected) in str(actual)）。
    """
    if not isinstance(cond, dict):
        return False
    field = cond.get("field")
    op = cond.get("op")
    expected = cond.get("value")

    if field not in ctx:
        return False

    actual = ctx[field]

    try:
        if op == "eq":
            return actual == expected
        elif op == "ne":
            return actual != expected
        elif op == "gt":
            return float(actual) > float(expected)
        elif op == "gte":
            return float(actual) >= float(expected)
        elif op == "lt":
            return float(actual) < float(expected)
        elif op == "lte":
            return float(actual) <= float(expected)
        elif op == "contains":
            return str(expected) in str(actual)
        elif op == "in":
            return actual in expected if isinstance(expected, (list, tuple, set)) else False
        elif op == "not_in":
            return actual not in expected if isinstance(expected, (list, tuple, set)) else True
    except (TypeError, ValueError):
        return False
    return False


# ---------------------------------------------------------------------------
# 字符串表达式求值（tokenizer + 递归下降，无 eval）
# ---------------------------------------------------------------------------

# 合法比较运算符（按长度降序，避免误匹配）
_CMP_OPS = ["==", "!=", ">=", "<=", ">", "<"]
# 逻辑运算符
_LOGICAL = {"&&": "and", "||": "or", "!": "not"}


class _Token:
    __slots__ = ("kind", "value")

    def __init__(self, kind: str, value: str):
        self.kind = kind  # 'num' | 'ident' | 'op' | 'str' | 'lparen' | 'rparen'
        self.value = value


def _tokenize(expr: str) -> list[_Token]:
    """把表达式切成 token 流。"""
    tokens: list[_Token] = []
    i = 0
    n = len(expr)
    while i < n:
        c = expr[i]
        if c.isspace():
            i += 1
            continue
        if c == "(":
            tokens.append(_Token("lparen", "("))
            i += 1
            continue
        if c == ")":
            tokens.append(_Token("rparen", ")"))
            i += 1
            continue
        if c == "&" and i + 1 < n and expr[i + 1] == "&":
            tokens.append(_Token("op", "&&"))
            i += 2
            continue
        if c == "|" and i + 1 < n and expr[i + 1] == "|":
            tokens.append(_Token("op", "||"))
            i += 2
            continue
        if c == "!":
            # != 与 ! 区分
            if i + 1 < n and expr[i + 1] == "=":
                tokens.append(_Token("op", "!="))
                i += 2
                continue
            tokens.append(_Token("op", "!"))
            i += 1
            continue
        if c in (">", "<"):
            if i + 1 < n and expr[i + 1] == "=":
                tokens.append(_Token("op", c + "="))
            else:
                tokens.append(_Token("op", c))
            i += 1 if expr[i + 1:i + 2] != "=" else 2
            continue
        if c == "=":
            # == 或裸 = 都当相等
            if i + 1 < n and expr[i + 1] == "=":
                tokens.append(_Token("op", "=="))
                i += 2
            else:
                tokens.append(_Token("op", "=="))
                i += 1
            continue
        if c == '"' or c == "'":
            # 字符串字面量
            j = i + 1
            buf = []
            while j < n and expr[j] != c:
                buf.append(expr[j])
                j += 1
            tokens.append(_Token("str", "".join(buf)))
            i = j + 1
            continue
        if c.isdigit() or (c == "-" and i + 1 < n and expr[i + 1].isdigit()):
            j = i
            buf = []
            if c == "-":
                buf.append(c)
                j += 1
            while j < n and (expr[j].isdigit() or expr[j] == "."):
                buf.append(expr[j])
                j += 1
            tokens.append(_Token("num", "".join(buf)))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            buf = []
            while j < n and (expr[j].isalnum() or expr[j] in "_."):
                buf.append(expr[j])
                j += 1
            tokens.append(_Token("ident", "".join(buf)))
            i = j
            continue
        # 未知字符：跳过（容错）
        i += 1
    return tokens


def _resolve(ctx: dict[str, Any], dotted: str) -> Any:
    """解析点号键（flag.metHero）到嵌套值；不存在返回 None。"""
    if "." not in dotted:
        return ctx.get(dotted)
    cur: Any = ctx
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _token_value(tok: _Token, ctx: dict[str, Any]) -> Any:
    """把单个 ident/num/str token 解析为运行时值。"""
    if tok.kind == "num":
        try:
            if "." in tok.value:
                return float(tok.value)
            return int(tok.value)
        except ValueError:
            return tok.value
    if tok.kind == "str":
        return tok.value
    if tok.kind == "ident":
        # true/false 布尔
        low = tok.value.lower()
        if low == "true":
            return True
        if low == "false":
            return False
        return _resolve(ctx, tok.value)
    return None


def _compare(op: str, left: Any, right: Any) -> bool:
    """执行比较运算（左右已解析为值）。"""
    try:
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == ">":
            return float(left) > float(right)
        if op == ">=":
            return float(left) >= float(right)
        if op == "<":
            return float(left) < float(right)
        if op == "<=":
            return float(left) <= float(right)
    except (TypeError, ValueError):
        return False
    return False


class _Parser:
    """递归下降解析器：or → and → not → primary。"""

    def __init__(self, tokens: list[_Token], ctx: dict[str, Any]):
        self.tokens = tokens
        self.ctx = ctx
        self.pos = 0

    def _peek(self) -> _Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _next(self) -> _Token | None:
        tok = self._peek()
        if tok is not None:
            self.pos += 1
        return tok

    def parse(self) -> bool:
        if not self.tokens:
            return True
        return self._parse_or()

    def _parse_or(self) -> bool:
        left = self._parse_and()
        while True:
            tok = self._peek()
            if tok and tok.kind == "op" and tok.value == "||":
                self._next()
                right = self._parse_and()
                left = left or right
            else:
                break
        return left

    def _parse_and(self) -> bool:
        left = self._parse_not()
        while True:
            tok = self._peek()
            if tok and tok.kind == "op" and tok.value == "&&":
                self._next()
                right = self._parse_not()
                left = left and right
            else:
                break
        return left

    def _parse_not(self) -> bool:
        tok = self._peek()
        if tok and tok.kind == "op" and tok.value == "!":
            self._next()
            return not self._parse_not()
        return self._parse_primary()

    def _parse_primary(self) -> bool:
        tok = self._peek()
        if tok is None:
            return False
        if tok.kind == "lparen":
            self._next()
            val = self._parse_or()
            nxt = self._next()
            # 忽略多余的 rparen（容错）
            return val
        if tok.kind in ("num", "str", "ident"):
            # 单值：非 None / 非空字符串 / 非零数字 / 非 False 视为真
            val = _token_value(tok, self.ctx)
            self._next()
            # 可能是比较式左值：看下一个是否是 cmp op
            nxt = self._peek()
            if nxt and nxt.kind == "op" and nxt.value in ("==", "!=", ">", "<", ">=", "<="):
                self._next()
                rhs_tok = self._next()
                rhs = _token_value(rhs_tok, self.ctx) if rhs_tok else None
                return _compare(nxt.value, val, rhs)
            return bool(val)
        # 未知 token：跳过
        self._next()
        return False


def eval_expr(expr: str, ctx: dict[str, Any]) -> bool:
    """求值字符串布尔表达式。

    支持 ``&&`` ``||`` ``!``、比较 ``== != > < >= <=``、点号键
    ``flag.metHero``、字符串字面量、数字、``true``/``false``。
    """
    tokens = _tokenize(expr)
    return _Parser(tokens, ctx).parse()


# 合法 Step 条件运算符（供上层校验参考）
STEP_CONDITION_OPS = frozenset(_CMP_OPS + ["==", "!=", "&&", "||", "!"])
