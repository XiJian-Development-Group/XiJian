"""World audit log — append-only ledger of operator / system actions.
世界审计日志 — 操作员/系统操作的仅追加分类账。

A4.2 spec defines a `world_audit_log` table that captures *every*
non-trivial world-level event: reset / patch / npc_create /
transition / environment_change / etc.  Operators can inspect the
log to reconstruct "what happened to my world" without grepping
through deeper observability tooling.

A4.2 规格定义了 `world_audit_log` 表，捕获*每个*重要的世界级事件：
重置/修补/npc_创建/转换/环境变更等。运营人员可以检查日志以重构
"我的世界发生了什么"，无需深入可观测性工具。

Scope is intentionally narrow:

范围有意保持狭窄：

* Append-only — there is no ``delete`` or ``update``.  An audit
  trail that can be edited is not a trail.
  仅追加——没有 ``delete`` 或 ``update``。可编辑的审计轨迹不是轨迹。
* Per-world — the route layer filters by ``world_id``.
  按世界——路由层按 ``world_id`` 过滤。
* Best-effort failures stay in DEBUG — a write failure must NOT
  block the operation being audited; if the ledger is unhealthy
  we still want the user action to land.
  尽力而为的失败保留在 DEBUG 中——写入失败不得阻塞被审计的操作；
  如果分类账不健康，我们仍希望用户操作落地。

The log is bounded by ``AUDIT_KEEP_PER_WORLD`` per world, with a
FIFO trim that matches the A4.1 / A3.2 pattern.

日志由每世界的 ``AUDIT_KEEP_PER_WORLD`` 限制，使用与 A4.1 / A3.2
模式匹配的 FIFO 裁剪。
"""

from __future__ import annotations

import logging
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_world_audit_id
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.world_audit")

#: FIFO cap per world (matches the A3.2 character-state log policy).
#: 每世界 FIFO 上限（匹配 A3.2 角色状态日志策略）。
AUDIT_KEEP_PER_WORLD = 1000

#: Valid ``action`` values.  Anything outside this set still gets
#: recorded (forward-compat) but the routes validate on known ones.
#: 有效的 ``action`` 值。此集合外的值仍会被记录（前向兼容），
#: 但路由会对已知值进行验证。
ACTIONS: frozenset[str] = frozenset(
    {
        "create",
        "update",
        "patch",
        "delete",
        "reset",
        "reset_preview",
        "reset_confirmed",
        "reset_finalized",
        "transition",
        "npc_create",
        "npc_update",
        "npc_delete",
        "npc_suspend",
        "npc_resume",
        "tier_change",
        "environment_update",
    }
)

#: Valid ``actor`` values.  We accept other strings for forward-compat
#: (the future B/C chapters might introduce new agent types) but
#: anything outside this set is logged as a warning at write time.
#: 有效的 ``actor`` 值。为前向兼容接受其他字符串（未来的 B/C 章节
#: 可能引入新代理类型），但集合外的值在写入时记录警告。
ACTORS: frozenset[str] = frozenset({"user", "system", "scheduler", "overload"})


def _now_or(value: float | None) -> float:
    return float(value) if value is not None else now_ts()


def record(
    *,
    world_id: str,
    action: str,
    actor: str = "user",
    payload: dict[str, Any] | None = None,
    log_id: str | None = None,
    now: float | None = None,
) -> dict | None:
    """Append a record; return the stored dict or ``None`` on validation failure.

    Silent ``None`` rather than raising because audit must not block the
    audited action.  Failures are noisy-DEBUG-logged so production can
    grep without filling INFO/WARNING channels.
    追加记录；验证失败时返回存储的字典或 ``None``。

    静默返回 ``None`` 而非抛出异常，因为审计不得阻塞被审计的操作。
    失败通过 DEBUG 日志记录，以便生产环境 grep 而不填充 INFO/WARNING 通道。
    """
    if not isinstance(world_id, str) or not world_id:
        _LOGGER.debug("audit skipped: missing world_id")
        return None
    if not isinstance(action, str) or not action:
        _LOGGER.debug("audit skipped: missing action")
        return None
    if actor not in ACTORS:
        _LOGGER.warning(
            "world_audit: unknown actor %r for action %r", actor, action
        )
    record_id = log_id or gen_world_audit_id()
    entry = {
        "id": record_id,
        "world_id": world_id,
        "action": action,
        "actor": actor,
        "payload": dict(payload or {}),
        "created_at": _now_or(now),
    }
    state.world_audit_log[record_id] = entry
    _trim(world_id)
    return entry


def _trim(world_id: str) -> None:
    """Bound the per-world audit log FIFO-style.
    以 FIFO 方式限制每世界审计日志。
    """
    bucket = [
        e
        for e in state.world_audit_log.values()
        if e.get("world_id") == world_id
    ]
    excess = len(bucket) - AUDIT_KEEP_PER_WORLD
    if excess <= 0:
        return
    bucket.sort(key=lambda e: e.get("created_at", 0.0))
    for entry in bucket[:excess]:
        state.world_audit_log.pop(entry["id"], None)


def list_log(
    *,
    world_id: str | None = None,
    action: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return audit entries newest-first, optionally filtered.
    返回审计条目，新者优先，可选过滤。
    """
    out: list[dict] = []
    for entry in state.world_audit_log.values():
        if world_id is not None and entry.get("world_id") != world_id:
            continue
        if action is not None and entry.get("action") != action:
            continue
        out.append(entry)
    out.sort(key=lambda e: e.get("created_at", 0.0), reverse=True)
    if limit < 1:
        limit = 1
    return out[:limit]


def count_for(world_id: str) -> int:
    """Count entries for a world — handy for tests and dashboards.
    统计一个世界的条目数——方便测试和仪表盘。
    """
    return sum(
        1
        for e in state.world_audit_log.values()
        if e.get("world_id") == world_id
    )


def reset_for_world(world_id: str) -> int:
    """Remove every audit entry for a world.  Returns count removed.

    Operators may want this when retiring a world.  System action.
    移除一个世界的所有审计条目。返回移除数量。

    运营人员可能在退役世界时需要此操作。系统操作。
    """
    removed = 0
    for entry in list(state.world_audit_log.values()):
        if entry.get("world_id") == world_id:
            state.world_audit_log.pop(entry["id"], None)
            removed += 1
    return removed


def reset_for_testing() -> None:
    """Clear the audit log (test-only).
    清空审计日志（仅测试）。
    """
    state.world_audit_log.clear()


__all__ = [
    "AUDIT_KEEP_PER_WORLD",
    "ACTIONS",
    "ACTORS",
    "record",
    "list_log",
    "count_for",
    "reset_for_world",
    "reset_for_testing",
]
