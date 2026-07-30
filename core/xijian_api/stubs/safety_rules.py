"""Stub safety-rule service — A5.1 in the function list v2.
存根安全规则服务 — 功能清单 v2 中的 A5.1。

The rulebook that drives A5.1's output-safety scans.  Each rule
is one of three flavours:

驱动 A5.1 输出安全扫描的规则手册。每条规则是三种风格之一：

* ``ooc_pattern``        — regex matched against the assistant's
  reply.  A hit means the model has broken character (replied as
  the system, or out of the persona's voice).  Severity 1..5.
  — 针对助手回复的正则匹配。命中表示模型已破坏角色
  （以系统身份回复，或超出角色声音）。严重级别 1..5。
* ``injection_pattern``  — regex matched against the **user's
  input** before it's handed to the model.  A hit is "this looks
  like a prompt-injection attempt" — block early so the model
  never even sees the bad payload.
  — 在交给模型之前针对**用户输入**的正则匹配。命中表示
  "这看起来像提示注入尝试"——提前阻止，使模型从不见到恶意载荷。
* ``forbidden_word``     — literal (case-insensitive) substring
  matched against either side.  The catch-all for content the
  operator doesn't want surfaced (e.g. a character's hard taboo
  topic, or a content-policy carve-out).
  — 针对任一侧的逐字（不区分大小写）子串匹配。运营商不希望
  露面的内容的兜底（例如角色的硬禁忌话题或内容策略例外）。

Data model (mirrors §A5.1 SQL schema)
======================================

数据模型（镜像 §A5.1 SQL 模式）
======================================

* ``id``         — ``rule_<12 hex>`` (PK)
* ``rule_kind``  — one of the three flavours / 三种风格之一
* ``pattern``    — the regex / literal string / 正则/字面字符串
* ``severity``   — 1..5; 1 = advisory, 5 = hard block / 1=建议, 5=硬阻止
* ``is_active``  — bool; inactive rules are skipped without being
  deleted (operator A/B switch)
  bool；非活跃规则跳过但不删除（运营 A/B 开关）
* ``created_at`` / ``updated_at`` — bookkeeping / 记账

The hot-path matcher is :func:`match_active_rules` — it walks
only active rules and returns the first match (severity
descending).  The cold-path CRUD lives below.

热路径匹配器是 :func:`match_active_rules`——它仅遍历活跃规则
并返回第一个匹配（严重级别降序）。冷路径 CRUD 在下面。

Severity semantics
==================

严重级别语义
==================

Severity is treated as an *ordering* key on the hot path.  The
audit layer uses it to label entries (1-2 = ``warn``, 3-4 =
``block``, 5 = ``hard_block``), and operators can tune which
severity threshold actually causes a block via the world's
``safety_threshold`` (managed by :mod:`safety`).

严重级别在热路径上被视为*排序*键。审计层用其标记条目
（1-2 = ``warn``，3-4 = ``block``，5 = ``hard_block``），
运营人员可通过世界的 ``safety_threshold``（由 :mod:`safety` 管理）
调整哪个严重级别阈值实际导致阻止。

Test surface
============

测试面
============

* :func:`create` / :func:`get` / :func:`list_active` /
  :func:`list_all` / :func:`update` / :func:`delete`
* :func:`match_active_rules` — hot path / 热路径
* :func:`seed_default` / :func:`reset_for_testing`
"""

from __future__ import annotations

import logging
import re
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_safety_rule_id
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.safety_rules")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 常量
# ---------------------------------------------------------------------------

#: Rule kinds.  Forward-compat: unknown kinds are accepted at the
#: stub level (operators may add new categories) but the
#: matcher skips them — safety scans should never blow up
#: because a typo slipped into the rulebook.
#: 规则种类。前向兼容：存根级别接受未知种类（运营可能添加新类别），
#: 但匹配器跳过它们——安全扫描不应因为规则簿中混入错字而爆炸。
KIND_OOC_PATTERN = "ooc_pattern"
KIND_INJECTION_PATTERN = "injection_pattern"
KIND_FORBIDDEN_WORD = "forbidden_word"

VALID_KINDS: frozenset[str] = frozenset({
    KIND_OOC_PATTERN, KIND_INJECTION_PATTERN, KIND_FORBIDDEN_WORD,
})

#: Severity range.  1 = advisory (warn only), 5 = hard block.
#: 严重级别范围。1 = 建议（仅警告），5 = 硬阻止。
MIN_SEVERITY = 1
MAX_SEVERITY = 5

#: Default severity when callers don't specify one.
#: 调用者未指定时的默认严重级别。
DEFAULT_SEVERITY = 3

#: Upper bound on the pattern length.  Beyond a few KB the rule
#: itself is almost certainly misconfigured.
#: 模式长度上限。超过几 KB 则规则本身几乎肯定是配置错误。
MAX_PATTERN_LEN = 4_096


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

# 异常
# ---------------------------------------------------------------------------


class SafetyRuleError(ValueError):
    """Raised on rule validation errors.
    规则验证错误时抛出。
    """


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

# 纯辅助函数
# ---------------------------------------------------------------------------


def _validate_kind(kind: Any) -> str:
    """Validate a rule kind.
    验证规则种类。
    """
    if not isinstance(kind, str) or not kind:
        raise SafetyRuleError("rule_kind is required")
    if kind not in VALID_KINDS:
        raise SafetyRuleError(
            "rule_kind must be one of %s, got %r"
            % (sorted(VALID_KINDS), kind)
        )
    return kind


def _validate_pattern(pattern: Any) -> str:
    """Validate a rule pattern.
    验证规则模式。
    """
    if not isinstance(pattern, str) or not pattern:
        raise SafetyRuleError("pattern is required")
    if len(pattern) > MAX_PATTERN_LEN:
        raise SafetyRuleError(
            "pattern too long: %d > %d"
            % (len(pattern), MAX_PATTERN_LEN)
        )
    return pattern


def _validate_severity(severity: Any) -> int:
    """Validate a severity value.
    验证严重级别值。
    """
    if isinstance(severity, bool) or not isinstance(severity, int):
        raise SafetyRuleError(
            "severity must be an int, got %s" % type(severity).__name__
        )
    if severity < MIN_SEVERITY or severity > MAX_SEVERITY:
        raise SafetyRuleError(
            "severity must be in [%d, %d], got %d"
            % (MIN_SEVERITY, MAX_SEVERITY, severity)
        )
    return severity


def _compile_pattern(rule_kind: str, pattern: str) -> re.Pattern | None:
    """Compile a rule pattern.  ``forbidden_word`` is a literal
    substring (case-insensitive); the two pattern kinds are
    treated as regex.

    Returns ``None`` if the regex doesn't compile.  The caller is
    expected to log + skip — a broken rule must not crash the
    scan.
    编译规则模式。``forbidden_word`` 是字面子串（不区分大小写）；
    两种模式种类视为正则。

    如果正则无法编译，返回 ``None``。调用者应记录并跳过——
    损坏的规则不得使扫描崩溃。
    """
    if rule_kind == KIND_FORBIDDEN_WORD:
        return re.compile(re.escape(pattern), re.IGNORECASE)
    try:
        return re.compile(pattern, re.IGNORECASE | re.DOTALL)
    except re.error as exc:
        _LOGGER.warning(
            "safety rule pattern %r failed to compile: %s", pattern, exc
        )
        return None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create(
    *,
    rule_kind: str,
    pattern: str,
    severity: int = DEFAULT_SEVERITY,
    is_active: bool = True,
    rule_id: str | None = None,
    now: float | None = None,
) -> dict:
    """Create a safety rule and return the record.

    Validates the pattern is a compilable regex (for the pattern
    kinds) or a reasonable literal (for forbidden_word).  Broken
    patterns are *not* rejected at create time — we still store
    them so the operator can fix them later.  The matcher
    :func:`match_active_rules` is the one that skips them.
    创建安全规则并返回记录。

    验证模式是可编译的正则（针对模式种类）或合理的字面量
    （针对 forbidden_word）。损坏的模式在创建时*不*被拒绝——
    我们仍存储它们以便运营人员稍后修复。匹配器
    :func:`match_active_rules` 是跳过它们的那一个。
    """
    _validate_kind(rule_kind)
    _validate_pattern(pattern)
    _validate_severity(severity)
    new_id = rule_id or gen_safety_rule_id()
    if new_id in state.safety_rules:
        raise SafetyRuleError("rule id %r already exists" % new_id)
    timestamp = float(now) if now is not None else now_ts()
    record = {
        "id": new_id,
        "rule_kind": rule_kind,
        "pattern": pattern,
        "severity": int(severity),
        "is_active": bool(is_active),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    state.safety_rules[new_id] = record
    return record


def get(rule_id: str) -> dict | None:
    """Return a rule record or ``None``.
    返回规则记录或 ``None``。
    """
    return state.safety_rules.get(rule_id)


def list_active(
    *,
    rule_kind: str | None = None,
) -> list[dict]:
    """Return every active rule, optionally filtered by kind, sorted
    by severity desc then created_at asc (stable, highest-severity
    first on the hot path).
    返回所有活跃规则，可选按种类过滤，按严重级别降序然后
    created_at 升序排序（稳定，热路径上最高严重级别优先）。
    """
    out = [
        r for r in state.safety_rules.values()
        if r.get("is_active")
        and (rule_kind is None or r.get("rule_kind") == rule_kind)
    ]
    out.sort(key=lambda r: (-int(r.get("severity", 0)), r.get("created_at", 0.0)))
    return out


def list_all() -> list[dict]:
    """Return every rule (active + inactive), sorted by created_at
    asc.  Used by the operator dashboard.
    返回所有规则（活跃+非活跃），按 created_at 升序排序。
    用于运营仪表盘。
    """
    out = list(state.safety_rules.values())
    out.sort(key=lambda r: r.get("created_at", 0.0))
    return out


def update(rule_id: str, patch: dict) -> dict | None:
    """Patch mutable fields.  ``id`` and ``created_at`` are
    immutable; renames of a rule's ``rule_kind`` are allowed (in
    case an operator wants to flip a pattern from ``ooc_pattern``
    to ``injection_pattern``).
    修补可变字段。``id`` 和 ``created_at`` 不可变；
    允许重命名规则的 ``rule_kind``（以防运营人员想将模式从
    ``ooc_pattern`` 翻转为 ``injection_pattern``）。
    """
    record = state.safety_rules.get(rule_id)
    if record is None:
        return None
    if "id" in patch or "created_at" in patch:
        raise SafetyRuleError("id, created_at are immutable")
    for key, value in patch.items():
        if key == "rule_kind":
            record["rule_kind"] = _validate_kind(value)
        elif key == "pattern":
            record["pattern"] = _validate_pattern(value)
        elif key == "severity":
            record["severity"] = _validate_severity(value)
        elif key == "is_active":
            if not isinstance(value, bool):
                raise SafetyRuleError(
                    "is_active must be a bool, got %s"
                    % type(value).__name__
                )
            record["is_active"] = value
    record["updated_at"] = now_ts()
    return record


def delete(rule_id: str) -> bool:
    """Delete a rule.  Returns True if it existed.
    删除规则。存在则返回 True。
    """
    return state.safety_rules.pop(rule_id, None) is not None


# ---------------------------------------------------------------------------
# Hot path
# ---------------------------------------------------------------------------

# 热路径
# ---------------------------------------------------------------------------


def match_active_rules(
    text: str,
    *,
    rule_kind: str,
) -> list[dict]:
    """Walk every active rule of ``rule_kind`` and return those that
    hit ``text``.

    Returns a list of matched rules (sorted by severity desc).
    An empty list means "no hits".  A broken regex is logged and
    skipped — one bad rule must not take down the whole scan.
    遍历 ``rule_kind`` 的每个活跃规则，返回与 ``text`` 匹配的规则。

    返回匹配规则列表（按严重级别降序排序）。空列表表示"无命中"。
    损坏的正则被记录并跳过——一个坏规则不得使整个扫描崩溃。
    """
    if not isinstance(text, str) or not text:
        return []
    out: list[dict] = []
    for rule in list_active(rule_kind=rule_kind):
        compiled = _compile_pattern(rule["rule_kind"], rule["pattern"])
        if compiled is None:
            continue
        if compiled.search(text):
            out.append(rule)
    # Already sorted by severity desc via list_active, but be
    # explicit so callers don't depend on internal order.
    # 已通过 list_active 按严重级别降序排序，但显式以确保
    # 调用者不依赖内部顺序。
    out.sort(key=lambda r: -int(r.get("severity", 0)))
    return out


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

# 生命周期
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.  No rules ship by default — the
    rulebook is operator-curated and may include copyrighted
    pattern libraries.  The :mod:`xijian_api.stubs.seed_all`
    hook is wired so future rule-bundle imports have a stable
    entry point.
    幂等默认种子。默认不附带任何规则——规则簿由运营人员策划，
    可能包含受版权保护的模式库。:mod:`xijian_api.stubs.seed_all`
    钩子已连接，使未来规则包导入有稳定入口点。
    """
    return None


def reset_for_testing() -> None:
    """Wipe every rule.
    清空所有规则。
    """
    state.safety_rules.clear()


__all__ = [
    # Constants
    "KIND_OOC_PATTERN", "KIND_INJECTION_PATTERN", "KIND_FORBIDDEN_WORD",
    "VALID_KINDS", "MIN_SEVERITY", "MAX_SEVERITY", "DEFAULT_SEVERITY",
    "MAX_PATTERN_LEN",
    # Errors
    "SafetyRuleError",
    # Pure helpers
    "_validate_kind", "_validate_pattern", "_validate_severity",
    "_compile_pattern",
    # CRUD
    "create", "get", "list_active", "list_all", "update", "delete",
    # Hot path
    "match_active_rules",
    # Lifecycle
    "seed_default", "reset_for_testing",
]
