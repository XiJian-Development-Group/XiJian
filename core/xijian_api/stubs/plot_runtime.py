"""剧情运行时 — C3 剧情设计运行时模块。

从 devkit 编辑器工作目录加载 plot_designs / plot_nodes / plot_edges
（与 :mod:`devkit.plot_editor` 落盘格式一致），按节点触发条件在
模拟世界调度中激活节点，执行奖励/效果，推进剧情状态，并与
worlds / npcs / world_environment / wallets / transactions 等
真实存根 API 集成。

数据模型（与 devkit/plot_editor.py 对齐）
======================================

plot_design (plot.json):
  - id: plot_<hex>
  - name: str
  - description: str
  - genre: str
  - setting: str
  - tags: list[str]
  - status: "draft" | "running" | "completed" | "archived"
  - created_at / updated_at: ISO8601

plot_node (nodes.json):
  - id: node_<hex>
  - plot_id: str
  - type: "start" | "branch" | "event" | "choice" | "reward" | "end"
  - title: str
  - description: str
  - position: {x, y}  # 编辑器坐标
  - trigger: dict     # 触发条件，复用 events 的 trigger_config 结构
  - rewards: list[dict]  # 奖励列表，如 {"type": "currency", "currency_id": "...", "amount": 100}
  - effects: list[dict]  # 状态变更，如 {"type": "npc_mood", "target": "npc_id", "delta": 10}
  - bind_character_id: str | None
  - bind_world_id: str | None
  - bind_event_id: str | None
  - metadata: dict    # 扩展字段

plot_edge (edges.json):
  - id: edge_<hex>
  - plot_id: str
  - source: str       # source node id
  - target: str       # target node id
  - condition: dict   # 边的通过条件（可选）
  - label: str        # 可选标签

运行时状态
==========

DictDB 桶: ``state.plot_runtime_states``（在 :mod:`xijian_api.stubs.state`
中注册，随 ``reset_for_testing`` 清空并重新播种）。
  {plot_runtime_id: {
      "id": plot_runtime_id,
      "plot_id": str,
      "world_id": str,
      "current_node_id": str | None,
      "completed_nodes": list[str],
      "available_edges": list[str],
      "variables": dict,           # 剧情局部变量
      "status": "running" | "completed" | "paused" | "failed",
      "started_at": float,
      "updated_at": float,
      "last_tick_at": float,
      # 冗余存储剧情结构，避免重复读盘（内部字段，序列化时剔除）
      "_nodes": {node_id: node},
      "_edges": [edge, ...],
  }}

与 events 调度集成
==================

节点的 trigger 复用 events 的四类触发器：
  - time: {type: "time", hour, minute, frequency: "daily"|"hourly"}
  - interval: {type: "interval", seconds}
  - probability: {type: "probability", per_tick}
  - condition: {type: "condition", field, op, value}

当 events scheduler 的 tick 运行时，会调用
``evaluate_plot_triggers(world_id)`` 检查该世界下所有运行中剧情的
节点触发条件，符合条件的节点自动激活执行。

REST 端点见 routes/xijian_plot.py。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Any

from xijian_api.stubs import state
from xijian_api.stubs import npcs as npcs_stub
from xijian_api.stubs import transactions as txn_stub
from xijian_api.stubs import wallets as wallets_stub
from xijian_api.stubs import world_currencies as wc_stub
from xijian_api.stubs import worlds as worlds_stub
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.plot_runtime")


# ---------------------------------------------------------------------------
# Constants / 常量
# ---------------------------------------------------------------------------

#: 运行时状态桶名（state.py 中注册的 DictDB 桶）。
PLOT_RUNTIME_BUCKET = "plot_runtime_states"

#: 合法节点类型。
NODE_TYPES = frozenset({"start", "branch", "event", "choice", "reward", "end"})

#: 边条件支持的操作符。
EDGE_CONDITION_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "contains", "in"})

#: 剧情运行时状态键。
PLOT_RUNTIME_STATUS_RUNNING = "running"
PLOT_RUNTIME_STATUS_COMPLETED = "completed"
PLOT_RUNTIME_STATUS_PAUSED = "paused"
PLOT_RUNTIME_STATUS_FAILED = "failed"

#: 节点触发器的合法类型（与 events 的 trigger_config 对齐）。
TRIGGER_TIME = "time"
TRIGGER_INTERVAL = "interval"
TRIGGER_PROBABILITY = "probability"
TRIGGER_CONDITION = "condition"
_VALID_TRIGGER_TYPES = frozenset({
    TRIGGER_TIME, TRIGGER_INTERVAL, TRIGGER_PROBABILITY, TRIGGER_CONDITION,
})

#: 条件触发器/边条件支持的操作符。
_CONDITION_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains"})

#: 道具奖励写入变量时的前缀（如 ``inventory_item_sword``）。
ITEM_VARIABLE_PREFIX = "inventory_"


# ---------------------------------------------------------------------------
# Exceptions / 异常
# ---------------------------------------------------------------------------


class PlotError(ValueError):
    """剧情运行时错误。"""


# ---------------------------------------------------------------------------
# Internal helpers / 内部辅助
# ---------------------------------------------------------------------------


def _get_bucket() -> dict[str, Any]:
    """获取剧情运行时状态桶（DictDB 实例）。

    测试与路由层直接读写该桶（例如原地修改 ``variables``）。
    """
    return state.plot_runtime_states


def _gen_runtime_id() -> str:
    return f"plot_rt_{secrets.token_hex(8)}"


def _load_plot_from_devkit(work_dir: str, plot_id: str) -> dict[str, Any] | None:
    """从 devkit 工作目录加载剧情完整数据（meta + nodes + edges）。

    与 :mod:`devkit.plot_editor` 的落盘格式一致：
    ``<work_dir>/plots/<plot_id>/{plot.json,nodes.json,edges.json}``。
    """
    plot_dir = os.path.join(work_dir, "plots", plot_id)
    if not os.path.isdir(plot_dir):
        return None

    meta_path = os.path.join(plot_dir, "plot.json")
    nodes_path = os.path.join(plot_dir, "nodes.json")
    edges_path = os.path.join(plot_dir, "edges.json")

    meta = None
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    for p, target in (
        (meta_path, "meta"),
        (nodes_path, "nodes"),
        (edges_path, "edges"),
    ):
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if target == "meta":
            meta = data if isinstance(data, dict) else None
        elif target == "nodes":
            nodes = data if isinstance(data, list) else []
        elif target == "edges":
            edges = data if isinstance(data, list) else []

    if not meta:
        return None

    return {"meta": meta, "nodes": nodes, "edges": edges}


def _validate_node(node: dict[str, Any]) -> None:
    """校验节点结构。"""
    if not isinstance(node.get("type"), str) or node["type"] not in NODE_TYPES:
        raise PlotError(
            f"节点类型无效: {node.get('type')}，必须是 {sorted(NODE_TYPES)} 之一"
        )
    if not node.get("id"):
        raise PlotError("节点缺少 id")


def _validate_edge(edge: dict[str, Any]) -> None:
    """校验边结构。"""
    if not edge.get("id"):
        raise PlotError("边缺少 id")
    if not edge.get("source"):
        raise PlotError("边缺少 source")
    if not edge.get("target"):
        raise PlotError("边缺少 target")
    cond = edge.get("condition")
    if cond:
        if not isinstance(cond, dict):
            raise PlotError("边 condition 必须是对象")
        if "field" not in cond or "op" not in cond or "value" not in cond:
            raise PlotError("边 condition 必须包含 field, op, value")
        if cond["op"] not in EDGE_CONDITION_OPS:
            raise PlotError(f"边 condition op 无效: {cond['op']}")


def _evaluate_condition(condition: dict[str, Any], context: dict[str, Any]) -> bool:
    """评估条件表达式（复用 events 的 condition trigger 语义）。"""
    field = condition.get("field")
    op = condition.get("op")
    expected = condition.get("value")

    if field not in context:
        return False

    actual = context[field]

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


def _runtime_context(world_id: str, rt: dict[str, Any]) -> dict[str, Any]:
    """构建触发器/边条件评估上下文：世界环境 + 剧情变量 + 运行时状态。"""
    env: dict[str, Any] = {}
    try:
        from xijian_api.stubs import world_environment as env_stub
        env = dict(env_stub.get(world_id) or {})
    except Exception:  # noqa: BLE001 — 环境缺失时用空上下文
        env = {}
    return {
        **env,
        **rt.get("variables", {}),
        "plot_runtime_status": rt.get("status"),
        "current_node": rt.get("current_node_id"),
        "completed_nodes_count": len(rt.get("completed_nodes", [])),
    }


def _evaluate_time_trigger(trigger: dict[str, Any]) -> bool:
    """评估 time 类型触发器（UTC 墙钟）。"""
    import time as _time

    hour = trigger.get("hour")
    minute = trigger.get("minute", 0)
    if not isinstance(hour, int) or not (0 <= hour <= 23):
        return False
    frequency = trigger.get("frequency", "daily")
    now = _time.gmtime()
    if now.tm_hour != hour:
        return False
    if frequency == "hourly":
        return True
    return now.tm_min == minute


def _evaluate_interval_trigger(trigger: dict[str, Any], rt: dict[str, Any]) -> bool:
    """评估 interval 类型触发器（距上次 tick 的秒数）。"""
    seconds = trigger.get("seconds")
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return False
    last_tick = float(rt.get("last_tick_at") or 0)
    return (now_ts() - last_tick) >= seconds


def _evaluate_probability_trigger(trigger: dict[str, Any], rt: dict[str, Any]) -> bool:
    """评估 probability 类型触发器（确定性哈希，便于测试）。"""
    import hashlib
    import struct

    per_tick = trigger.get("per_tick", 0.0)
    if not isinstance(per_tick, (int, float)):
        return False
    if per_tick <= 0:
        return False
    if per_tick >= 1:
        return True
    # 与 events._stable_hash_unit 同思路：固定盐 + sha256 → [0,1)，
    # 同一运行时同一时刻结果确定。
    key = repr(("plot_prob", rt.get("id"), int(now_ts())))
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    unit = struct.unpack(">I", digest[:4])[0] / 0x100000000
    return unit < per_tick


def _evaluate_node_trigger(
    trigger: dict[str, Any],
    world_id: str,
    rt: dict[str, Any],
) -> bool:
    """评估节点触发条件，复用 events 的四类触发器语义。"""
    if not trigger or not isinstance(trigger, dict):
        return False
    ttype = trigger.get("type")
    if ttype not in _VALID_TRIGGER_TYPES:
        return False

    if ttype == TRIGGER_TIME:
        return _evaluate_time_trigger(trigger)
    if ttype == TRIGGER_INTERVAL:
        return _evaluate_interval_trigger(trigger, rt)
    if ttype == TRIGGER_PROBABILITY:
        return _evaluate_probability_trigger(trigger, rt)
    if ttype == TRIGGER_CONDITION:
        return _evaluate_condition(trigger, _runtime_context(world_id, rt))
    return False


def _resolve_currency_code(world_id: str, currency_id: str) -> str | None:
    """将奖励里的 ``currency_id`` 解析为货币 code。

    编辑器数据中的 ``currency_id`` 可能直接是 code（如 ``gold``），
    也可能是内部 id（``curr_<hex>``）——两种情况都支持。
    """
    if not currency_id:
        return None
    record = wc_stub.get(world_id, currency_id)
    if record is not None:
        return record["code"]
    record = wc_stub.get_by_id(currency_id)
    if record is not None:
        return record["code"]
    return None


def _execute_rewards(
    rewards: list[dict[str, Any]],
    world_id: str,
    rt: dict[str, Any],
) -> list[dict[str, Any]]:
    """执行奖励列表，返回执行结果记录。

    每个奖励都会产生一条结果记录（含 ``ok`` 标志），失败的奖励不会
    中断剧情推进 —— 与 events 的 effects 应用保持相同"尽力而为"姿态。
    """
    results: list[dict[str, Any]] = []
    variables = rt.setdefault("variables", {})

    for reward in rewards:
        rtype = reward.get("type")
        try:
            if rtype == "currency":
                code = _resolve_currency_code(world_id, reward.get("currency_id") or "")
                amount = int(reward.get("amount", 0))
                if code is None or amount <= 0:
                    results.append({
                        "type": rtype, "ok": False,
                        "error": "货币不存在或金额无效",
                    })
                    continue
                target = reward.get("target")
                owner_kind = reward.get("owner_kind", wallets_stub.OWNER_USER)
                owner_id = target or wallets_stub.LOCAL_USER_ID
                wallet = wallets_stub.ensure_wallet(
                    owner_kind, owner_id, world_id, code, initial_balance=0
                )
                before = float(wallet.get("balance", 0))
                wallets_stub.deposit(owner_kind, owner_id, world_id, code, amount)
                after = before + amount
                # A4.4 审计：资金变动必须写入 transactions 表（尽力而为）。
                try:
                    txn_stub.record(
                        world_id=world_id,
                        from_kind="npc",
                        from_id=world_id,
                        to_kind=owner_kind,
                        to_id=owner_id,
                        currency_code=code,
                        amount=float(amount),
                        kind=txn_stub.KIND_REWARD,
                        ref_id=rt.get("id"),
                    )
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("plot reward txn write failed: %s", exc)
                results.append({
                    "type": rtype, "ok": True,
                    "wallet_id": wallet.get("id"),
                    "owner_id": owner_id,
                    "before": before, "after": after,
                    "amount": amount,
                })

            elif rtype == "item":
                item_id = reward.get("item_id")
                qty = int(reward.get("quantity", 1))
                if not item_id or qty <= 0:
                    results.append({
                        "type": rtype, "ok": False, "error": "item_id 缺失或数量无效",
                    })
                    continue
                var_key = f"{ITEM_VARIABLE_PREFIX}{item_id}"
                variables[var_key] = variables.get(var_key, 0) + qty
                results.append({
                    "type": rtype, "ok": True,
                    "item_id": item_id, "quantity": qty,
                    "total": variables[var_key],
                })

            elif rtype == "experience":
                target = reward.get("target")
                amount = int(reward.get("amount", 0))
                if not target or amount <= 0:
                    results.append({
                        "type": rtype, "ok": False, "error": "缺少 target 或金额无效",
                    })
                    continue
                var_key = f"experience_{target}"
                variables[var_key] = variables.get(var_key, 0) + amount
                results.append({
                    "type": rtype, "ok": True,
                    "target": target, "amount": amount,
                    "total": variables[var_key],
                })

            elif rtype == "relationship":
                target = reward.get("target")
                delta = int(reward.get("delta", 0))
                rel_type = reward.get("rel_type", "affinity")
                if not target:
                    results.append({
                        "type": rtype, "ok": False, "error": "缺少 target",
                    })
                    continue
                var_key = f"relationship_{target}_{rel_type}"
                variables[var_key] = variables.get(var_key, 0) + delta
                results.append({
                    "type": rtype, "ok": True,
                    "target": target, "rel_type": rel_type, "delta": delta,
                    "new_value": variables[var_key],
                })

            else:
                results.append({
                    "type": rtype, "ok": False, "error": f"未知奖励类型: {rtype}",
                })

        except Exception as exc:  # noqa: BLE001 — 奖励失败不阻断剧情
            _LOGGER.warning("执行奖励失败: %s", exc)
            results.append({"type": rtype, "ok": False, "error": str(exc)})

    return results


def _execute_effects(
    effects: list[dict[str, Any]],
    world_id: str,
    rt: dict[str, Any],
) -> list[dict[str, Any]]:
    """执行效果列表（状态变更），返回执行结果记录。"""
    results: list[dict[str, Any]] = []
    variables = rt.setdefault("variables", {})

    for effect in effects:
        etype = effect.get("type")
        try:
            if etype == "npc_mood":
                target = effect.get("target")
                delta = int(effect.get("delta", 0))
                if not target:
                    results.append({
                        "type": etype, "ok": False, "error": "缺少 target",
                    })
                    continue
                state_json = npcs_stub.apply_npc_state_effect(
                    target, "mood", delta,
                    reason="plot_runtime",
                    ref_id=rt.get("id"),
                )
                results.append({
                    "type": etype, "ok": True,
                    "target": target, "delta": delta,
                    "new_mood": state_json.get("mood"),
                })

            elif etype == "npc_status":
                target = effect.get("target")
                status = effect.get("status")
                if not target or not status:
                    results.append({
                        "type": etype, "ok": False, "error": "缺少 target 或 status",
                    })
                    continue
                record = npcs_stub.get(target)
                if record is None:
                    results.append({
                        "type": etype, "ok": False, "error": "NPC 不存在",
                    })
                    continue
                state_json = dict(record.get("state_json") or {})
                state_json["status"] = status
                npcs_stub.update(target, {"state_json": state_json})
                results.append({
                    "type": etype, "ok": True, "target": target, "status": status,
                })

            elif etype == "world_state":
                field = effect.get("field")
                value = effect.get("value")
                if field is None:
                    results.append({
                        "type": etype, "ok": False, "error": "缺少 field",
                    })
                    continue
                from xijian_api.stubs import world_environment as env_stub
                env_stub.patch_environment(world_id, {field: value})
                results.append({
                    "type": etype, "ok": True, "field": field, "value": value,
                })

            elif etype == "plot_variable":
                key = effect.get("key")
                value = effect.get("value")
                if key is None:
                    results.append({
                        "type": etype, "ok": False, "error": "缺少 key",
                    })
                    continue
                variables[key] = value
                results.append({
                    "type": etype, "ok": True, "key": key, "value": value,
                })

            elif etype == "unlock_node":
                node_id = effect.get("node_id")
                if not node_id:
                    results.append({
                        "type": etype, "ok": False, "error": "缺少 node_id",
                    })
                    continue
                unlocked = rt.setdefault("unlocked_nodes", [])
                if node_id not in unlocked:
                    unlocked.append(node_id)
                results.append({
                    "type": etype, "ok": True, "node_id": node_id,
                })

            else:
                results.append({
                    "type": etype, "ok": False, "error": f"未知效果类型: {etype}",
                })

        except Exception as exc:  # noqa: BLE001 — 效果失败不阻断剧情
            _LOGGER.warning("执行效果失败: %s", exc)
            results.append({"type": etype, "ok": False, "error": str(exc)})

    return results


def _find_start_node(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """查找剧情的起始节点。"""
    for node in nodes:
        if node.get("type") == "start":
            return node
    return nodes[0] if nodes else None


def _get_outgoing_edges(edges: list[dict[str, Any]], node_id: str) -> list[dict[str, Any]]:
    """获取从指定节点出发的边。"""
    return [e for e in edges if e.get("source") == node_id]


def _serialize_runtime(rt: dict[str, Any]) -> dict[str, Any]:
    """序列化运行时状态（剔除内部 ``_nodes`` / ``_edges`` 字段）。"""
    return {
        "id": rt["id"],
        "plot_id": rt["plot_id"],
        "world_id": rt["world_id"],
        "current_node_id": rt.get("current_node_id"),
        "completed_nodes": rt.get("completed_nodes", []),
        "available_edges": rt.get("available_edges", []),
        "variables": rt.get("variables", {}),
        "unlocked_nodes": rt.get("unlocked_nodes", []),
        "status": rt.get("status"),
        "started_at": rt.get("started_at"),
        "updated_at": rt.get("updated_at"),
        "last_tick_at": rt.get("last_tick_at"),
    }


# ---------------------------------------------------------------------------
# Public API / 公共接口
# ---------------------------------------------------------------------------


def create_plot_runtime(
    plot_id: str,
    world_id: str,
    *,
    work_dir: str,
    initial_variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """创建并启动一个剧情运行时实例。

    从 devkit 工作目录加载剧情数据，验证结构，初始化运行时状态，
    并写入 ``state.plot_runtime_states`` 桶。

    Raises
    ------
    PlotError
        世界不存在、剧情不存在/加载失败、结构非法或缺少起始节点时抛出。
    """
    if worlds_stub.get(world_id) is None:
        raise PlotError(f"世界不存在: {world_id}")

    plot_data = _load_plot_from_devkit(work_dir, plot_id)
    if not plot_data:
        raise PlotError(f"剧情不存在或加载失败: {plot_id}")

    meta = plot_data["meta"]
    nodes = plot_data["nodes"]
    edges = plot_data["edges"]

    for node in nodes:
        _validate_node(node)
    for edge in edges:
        _validate_edge(edge)

    start_node = _find_start_node(nodes)
    if not start_node:
        raise PlotError("剧情缺少起始节点")

    runtime_id = _gen_runtime_id()
    now = now_ts()
    nodes_map = {n["id"]: n for n in nodes}

    runtime_state: dict[str, Any] = {
        "id": runtime_id,
        "plot_id": plot_id,
        "world_id": world_id,
        "current_node_id": start_node["id"],
        "completed_nodes": [],
        "available_edges": [
            e["id"] for e in _get_outgoing_edges(edges, start_node["id"])
        ],
        "variables": dict(initial_variables or {}),
        "unlocked_nodes": [start_node["id"]],
        "status": PLOT_RUNTIME_STATUS_RUNNING,
        "started_at": now,
        "updated_at": now,
        "last_tick_at": now,
        # 冗余存储剧情结构，避免重复读盘（内部字段，序列化时剔除）
        "_nodes": nodes_map,
        "_edges": edges,
    }

    bucket = _get_bucket()
    bucket[runtime_id] = runtime_state

    _LOGGER.info(
        "剧情运行时创建: runtime_id=%s plot_id=%s world_id=%s start_node=%s",
        runtime_id, plot_id, world_id, start_node["id"],
    )

    return _serialize_runtime(runtime_state)


def get_plot_runtime(runtime_id: str) -> dict[str, Any] | None:
    """获取剧情运行时状态。"""
    bucket = _get_bucket()
    rt = bucket.get(runtime_id)
    if not rt:
        return None
    return _serialize_runtime(rt)


def list_plot_runtimes(
    *,
    world_id: str | None = None,
    plot_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """列出剧情运行时，支持按 world_id / plot_id / status 过滤。"""
    bucket = _get_bucket()
    results: list[dict[str, Any]] = []
    for rt in bucket.values():
        if world_id and rt.get("world_id") != world_id:
            continue
        if plot_id and rt.get("plot_id") != plot_id:
            continue
        if status and rt.get("status") != status:
            continue
        results.append(_serialize_runtime(rt))
    return results


def advance_plot_runtime(
    runtime_id: str,
    *,
    choose_edge_id: str | None = None,
) -> dict[str, Any]:
    """推进剧情运行时：执行当前节点的奖励/效果，沿边流转到下一节点。

    对于 choice 类型节点，必须提供 ``choose_edge_id`` 指定选择的边；
    对于其他类型，自动选择第一条满足条件的边。到达 end 节点时
    运行时标记为完成。

    Raises
    ------
    PlotError
        运行时不存在、状态不可推进、choice 节点未指定边、
        边条件不满足时抛出。
    """
    bucket = _get_bucket()
    rt = bucket.get(runtime_id)
    if not rt:
        raise PlotError(f"剧情运行时不存在: {runtime_id}")

    if rt.get("status") != PLOT_RUNTIME_STATUS_RUNNING:
        raise PlotError(f"剧情运行时状态不可推进: {rt.get('status')}")

    nodes = rt.get("_nodes", {})
    edges = rt.get("_edges", [])

    current_node_id = rt["current_node_id"]
    current_node = nodes.get(current_node_id)
    if not current_node:
        raise PlotError(f"当前节点不存在: {current_node_id}")

    world_id = rt["world_id"]

    # choice 节点必须先给出选择，再执行奖励/效果。
    if current_node.get("type") == "choice" and not choose_edge_id:
        raise PlotError("choice 节点必须指定 choose_edge_id")

    # 执行当前节点的奖励和效果。
    rewards_results = _execute_rewards(current_node.get("rewards", []), world_id, rt)
    effects_results = _execute_effects(current_node.get("effects", []), world_id, rt)

    # 标记当前节点完成。
    if current_node_id not in rt["completed_nodes"]:
        rt["completed_nodes"].append(current_node_id)

    outgoing = _get_outgoing_edges(edges, current_node_id)
    next_node_id: str | None = None

    if current_node.get("type") == "choice":
        chosen_edge = next(
            (e for e in outgoing if e.get("id") == choose_edge_id), None
        )
        if not chosen_edge:
            raise PlotError(f"未找到指定的边: {choose_edge_id}")
        if chosen_edge.get("condition"):
            context = _runtime_context(world_id, rt)
            if not _evaluate_condition(chosen_edge["condition"], context):
                raise PlotError("边条件不满足")
        next_node_id = chosen_edge["target"]
    elif current_node.get("type") == "end":
        rt["status"] = PLOT_RUNTIME_STATUS_COMPLETED
        next_node_id = None
    else:
        # 自动选择第一条满足条件的边。
        context = _runtime_context(world_id, rt)
        for edge in outgoing:
            if edge.get("condition"):
                if _evaluate_condition(edge["condition"], context):
                    next_node_id = edge["target"]
                    break
            else:
                next_node_id = edge["target"]
                break

    if next_node_id:
        rt["current_node_id"] = next_node_id
        rt["status"] = PLOT_RUNTIME_STATUS_RUNNING
        next_node = nodes.get(next_node_id)
        if next_node and next_node.get("type") == "end":
            rt["status"] = PLOT_RUNTIME_STATUS_COMPLETED
    else:
        # 无出边且非 end 类型，视为结束。
        if current_node.get("type") != "end":
            rt["status"] = PLOT_RUNTIME_STATUS_COMPLETED
        rt["current_node_id"] = None

    rt["updated_at"] = now_ts()
    rt["last_tick_at"] = now_ts()
    bucket[runtime_id] = rt

    result = _serialize_runtime(rt)
    result["executed_rewards"] = rewards_results
    result["executed_effects"] = effects_results
    result["next_node_id"] = next_node_id

    _LOGGER.info(
        "剧情推进: runtime_id=%s from_node=%s to_node=%s status=%s",
        runtime_id, current_node_id, next_node_id, rt["status"],
    )

    return result


def evaluate_plot_triggers(world_id: str) -> list[dict[str, Any]]:
    """评估指定世界下所有运行中剧情的节点触发条件。

    由 events scheduler 在每次 tick 时调用。返回已激活的节点列表，
    每个条目包含 ``runtime_id`` / ``plot_id`` / ``node_id``（触发节点）/
    ``new_node_id``（推进目标）与推进后的 ``status``。
    """
    bucket = _get_bucket()
    activated: list[dict[str, Any]] = []

    for rt in bucket.values():
        if rt.get("world_id") != world_id:
            continue
        if rt.get("status") != PLOT_RUNTIME_STATUS_RUNNING:
            continue

        current_node_id = rt.get("current_node_id")
        if not current_node_id:
            continue

        nodes = rt.get("_nodes", {})
        current_node = nodes.get(current_node_id)
        if not current_node:
            continue

        # 已完成节点不再重复触发。
        if current_node_id in rt.get("completed_nodes", []):
            continue

        trigger = current_node.get("trigger")
        if not trigger:
            continue

        if not _evaluate_node_trigger(trigger, world_id, rt):
            continue

        try:
            result = advance_plot_runtime(rt["id"])
        except PlotError as exc:
            _LOGGER.warning(
                "自动推进剧情失败: runtime_id=%s error=%s", rt["id"], exc
            )
            rt["status"] = PLOT_RUNTIME_STATUS_FAILED
            rt["updated_at"] = now_ts()
            bucket[rt["id"]] = rt
            continue

        activated.append({
            "runtime_id": rt["id"],
            "plot_id": rt.get("plot_id"),
            "node_id": current_node_id,
            "new_node_id": result.get("next_node_id"),
            "status": result.get("status"),
        })

    return activated


def pause_plot_runtime(runtime_id: str) -> dict[str, Any]:
    """暂停剧情运行时。

    Raises
    ------
    PlotError
        运行时不存在或当前状态不是 running 时抛出。
    """
    bucket = _get_bucket()
    rt = bucket.get(runtime_id)
    if not rt:
        raise PlotError(f"剧情运行时不存在: {runtime_id}")
    if rt["status"] != PLOT_RUNTIME_STATUS_RUNNING:
        raise PlotError(f"只能暂停运行中的剧情，当前状态: {rt['status']}")
    rt["status"] = PLOT_RUNTIME_STATUS_PAUSED
    rt["updated_at"] = now_ts()
    bucket[runtime_id] = rt
    return _serialize_runtime(rt)


def resume_plot_runtime(runtime_id: str) -> dict[str, Any]:
    """恢复暂停的剧情运行时。

    Raises
    ------
    PlotError
        运行时不存在或当前状态不是 paused 时抛出。
    """
    bucket = _get_bucket()
    rt = bucket.get(runtime_id)
    if not rt:
        raise PlotError(f"剧情运行时不存在: {runtime_id}")
    if rt["status"] != PLOT_RUNTIME_STATUS_PAUSED:
        raise PlotError(f"只能恢复暂停的剧情，当前状态: {rt['status']}")
    rt["status"] = PLOT_RUNTIME_STATUS_RUNNING
    rt["updated_at"] = now_ts()
    rt["last_tick_at"] = now_ts()
    bucket[runtime_id] = rt
    return _serialize_runtime(rt)


def delete_plot_runtime(runtime_id: str) -> bool:
    """删除剧情运行时实例。"""
    bucket = _get_bucket()
    if runtime_id in bucket:
        del bucket[runtime_id]
        return True
    return False


def get_plot_node(runtime_id: str, node_id: str) -> dict[str, Any] | None:
    """获取剧情运行时中的节点详情（含运行时上下文标记）。"""
    bucket = _get_bucket()
    rt = bucket.get(runtime_id)
    if not rt:
        return None
    nodes = rt.get("_nodes", {})
    node = nodes.get(node_id)
    if not node:
        return None
    return {
        **node,
        "is_current": node_id == rt.get("current_node_id"),
        "is_completed": node_id in rt.get("completed_nodes", []),
        "is_unlocked": node_id in rt.get("unlocked_nodes", []),
    }


def get_plot_edges(
    runtime_id: str,
    node_id: str | None = None,
) -> list[dict[str, Any]]:
    """获取剧情运行时的边列表，可选按 source 节点过滤。"""
    bucket = _get_bucket()
    rt = bucket.get(runtime_id)
    if not rt:
        return []
    edges = rt.get("_edges", [])
    if node_id:
        edges = [e for e in edges if e.get("source") == node_id]
    return list(edges)


# ---------------------------------------------------------------------------
# Devkit 剧情设计读取接口（供 routes 使用）
# ---------------------------------------------------------------------------


def list_available_plots(work_dir: str) -> list[dict[str, Any]]:
    """列出 devkit 工作目录下所有可用的剧情设计。"""
    base = os.path.join(work_dir, "plots")
    if not os.path.isdir(base):
        return []
    results: list[dict[str, Any]] = []
    for name in sorted(os.listdir(base)):
        plot_dir = os.path.join(base, name)
        if not os.path.isdir(plot_dir):
            continue
        meta_path = os.path.join(plot_dir, "plot.json")
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(meta, dict):
            results.append(meta)
    return results


def get_plot_design(work_dir: str, plot_id: str) -> dict[str, Any] | None:
    """获取剧情设计元数据。"""
    meta_path = os.path.join(work_dir, "plots", plot_id, "plot.json")
    if not os.path.isfile(meta_path):
        return None
    try:
        with open(meta_path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def get_plot_design_nodes(work_dir: str, plot_id: str) -> list[dict[str, Any]]:
    """获取剧情设计的节点列表。"""
    nodes_path = os.path.join(work_dir, "plots", plot_id, "nodes.json")
    if not os.path.isfile(nodes_path):
        return []
    try:
        with open(nodes_path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def get_plot_design_edges(work_dir: str, plot_id: str) -> list[dict[str, Any]]:
    """获取剧情设计的边列表。"""
    edges_path = os.path.join(work_dir, "plots", plot_id, "edges.json")
    if not os.path.isfile(edges_path):
        return []
    try:
        with open(edges_path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


# ---------------------------------------------------------------------------
# Lifecycle / 生命周期
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """幂等默认种子。

    剧情运行时没有默认记录 —— 运行时由路由层按需创建
    （如同钱包/交易：由编排方触发）。此钩子存在以便
    :func:`xijian_api.stubs.seed_all` 可以统一调用。
    """
    return None


def reset_for_testing() -> None:
    """清空剧情运行时状态桶。"""
    state.plot_runtime_states.clear()


__all__ = [
    # Constants
    "PLOT_RUNTIME_BUCKET",
    "NODE_TYPES",
    "EDGE_CONDITION_OPS",
    "PLOT_RUNTIME_STATUS_RUNNING",
    "PLOT_RUNTIME_STATUS_COMPLETED",
    "PLOT_RUNTIME_STATUS_PAUSED",
    "PLOT_RUNTIME_STATUS_FAILED",
    "TRIGGER_TIME",
    "TRIGGER_INTERVAL",
    "TRIGGER_PROBABILITY",
    "TRIGGER_CONDITION",
    # Errors
    "PlotError",
    # Public API
    "create_plot_runtime",
    "get_plot_runtime",
    "list_plot_runtimes",
    "advance_plot_runtime",
    "evaluate_plot_triggers",
    "pause_plot_runtime",
    "resume_plot_runtime",
    "delete_plot_runtime",
    "get_plot_node",
    "get_plot_edges",
    # Devkit design readers
    "list_available_plots",
    "get_plot_design",
    "get_plot_design_nodes",
    "get_plot_design_edges",
    # Lifecycle
    "seed_default",
    "reset_for_testing",
]
