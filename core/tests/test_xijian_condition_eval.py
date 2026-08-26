"""P0-1 统一条件求值器测试。"""

import pytest

from xijian_api.stubs import condition_eval as ce


# ---------------------------------------------------------------------------
# dict 条件（向后兼容）
# ---------------------------------------------------------------------------


def test_dict_eq():
    assert ce.eval_any({"field": "a", "op": "eq", "value": 1}, {"a": 1}) is True
    assert ce.eval_any({"field": "a", "op": "eq", "value": 2}, {"a": 1}) is False


def test_dict_contains_str():
    # plot 语义：字符串包含
    assert ce.eval_any({"field": "txt", "op": "contains", "value": "lo"}, {"txt": "hello"}) is True
    assert ce.eval_any({"field": "txt", "op": "contains", "value": "zz"}, {"txt": "hello"}) is False


def test_dict_in_list():
    assert ce.eval_any({"field": "x", "op": "in", "value": [1, 2, 3]}, {"x": 2}) is True
    assert ce.eval_any({"field": "x", "op": "in", "value": [1, 2, 3]}, {"x": 9}) is False


def test_dict_missing_field():
    assert ce.eval_any({"field": "nope", "op": "eq", "value": 1}, {"a": 1}) is False


def test_dict_none_cond():
    assert ce.eval_any(None, {}) is True


# ---------------------------------------------------------------------------
# 字符串表达式
# ---------------------------------------------------------------------------


def test_expr_compare():
    ctx = {"affinity": 5}
    assert ce.eval_expr("affinity >= 5", ctx) is True
    assert ce.eval_expr("affinity > 5", ctx) is False
    assert ce.eval_expr("affinity < 10", ctx) is True


def test_expr_dotted_key():
    ctx = {"flag": {"metHero": 1}}
    assert ce.eval_expr("flag.metHero == 1", ctx) is True
    assert ce.eval_expr("flag.metHero == 0", ctx) is False


def test_expr_logical_and():
    ctx = {"a": 5, "b": 3}
    assert ce.eval_expr("a >= 5 && b >= 3", ctx) is True
    assert ce.eval_expr("a >= 5 && b >= 9", ctx) is False


def test_expr_logical_or():
    ctx = {"a": 1, "b": 9}
    assert ce.eval_expr("a >= 5 || b >= 5", ctx) is True
    assert ce.eval_expr("a >= 5 || b >= 99", ctx) is False


def test_expr_not():
    ctx = {"flag": {"boss": False}}
    assert ce.eval_expr("!flag.boss", ctx) is True
    # 明确意图：flag.boss 不是 true（即非 boss 状态）
    assert ce.eval_expr("flag.boss == false", ctx) is True


def test_expr_string_literal():
    ctx = {"name": "alice"}
    assert ce.eval_expr('name == "alice"', ctx) is True
    assert ce.eval_expr('name == "bob"', ctx) is False


def test_expr_bool_literal():
    ctx = {}
    assert ce.eval_expr("true", ctx) is True
    assert ce.eval_expr("false", ctx) is False


def test_expr_parentheses():
    ctx = {"a": 1, "b": 2, "c": 3}
    assert ce.eval_expr("(a == 1 || b == 9) && c == 3", ctx) is True
    assert ce.eval_expr("(a == 9 || b == 9) && c == 3", ctx) is False


def test_expr_complex():
    ctx = {"affinity": 8, "flag": {"metHero": True}, "inv_sword": 1}
    expr = "affinity >= 5 && flag.metHero == true && inv_sword > 0"
    assert ce.eval_expr(expr, ctx) is True


def test_eval_any_str_routes():
    ctx = {"affinity": 8}
    assert ce.eval_any("affinity >= 5", ctx) is True


def test_expr_unsafe_no_injection():
    # 确认不使用 eval：传入看似代码的内容不应执行
    ctx = {}
    # 这个字符串不是合法比较，应安全返回 False 而非报错/执行
    assert ce.eval_expr("__import__('os')", ctx) is False
