"""Stub character-initiated action service — A7 in the function list v2.

A7 主动发起聊天/通话：角色在后台运行时按条件**主动**给用户发消息或
发起语音通话（US-A7-01），用户通过系统通知接听/拒绝（US-A7-02）。

数据模型镜像 §A7 的 SQL 建表语句：

* ``character_initiated_actions`` — 每次主动发起一条记录：
  (id / character_id / kind / payload / triggered_at / user_response /
  responded_at) + 状态扩展 (status: pending→sent→accepted/declined/
  ignored / created_at / updated_at)。§A7 的 ``user_response`` 字段
  与我们的 ``status`` 字段等价，两者都保留。
* ``character_initiated_configs`` — 通知权限管理（AC-3：必须在系统允许
  的通知权限下工作）：全局开关（``"__global__"`` 键）+ 每角色配置
  （是否允许、触发类型、冷却、每小时上限、情绪阈值）。

触发机制（AC-1：通知送达延迟 < 3s 由 WS 推送保证）：

* :func:`scan_for_actions` 扫描满足条件的角色（全局开关 + 角色开关 +
  冷却已过 + 每小时上限未超 + 可选情绪阈值），为每个合格角色创建
  ``pending`` 动作并立即 WS 广播 ``character.initiated_action``。
* :func:`start_tick` 启动后台定时扫描线程（参考 character_state 的
  tick 线程模式），``XIJIAN_INITIATED_TICK=0`` 可禁用（CI/测试），
  ``XIJIAN_INITIATED_TICK_SECONDS`` 覆盖间隔（默认 60）。

用户拒绝 → 角色"理解"（AC-2）：:func:`respond(..., 'declined')` 会经
:mod:`xijian_api.stubs.memory` 的创建接口写一条短期记忆（内容形如
「角色主动联系被拒绝，角色表现出理解」），实现规格 AC-2 的"写回记忆"
语义；同时 WS 广播 ``character.initiated_response``。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_initiated_action_id
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.character_initiated_actions")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Action kinds (spec §A7).
KIND_MESSAGE = "message"
KIND_VOICE_CALL = "voice_call"

ALL_KINDS: tuple[str, ...] = (KIND_MESSAGE, KIND_VOICE_CALL)

#: Action status machine (spec §A7 user_response mirrors this).
STATUS_PENDING = "pending"
STATUS_SENT = "sent"
STATUS_ACCEPTED = "accepted"
STATUS_DECLINED = "declined"
STATUS_IGNORED = "ignored"

ALL_STATUSES: tuple[str, ...] = (
    STATUS_PENDING,
    STATUS_SENT,
    STATUS_ACCEPTED,
    STATUS_DECLINED,
    STATUS_IGNORED,
)

#: Valid user responses (spec §A7).
RESPONSE_ACCEPTED = "accepted"
RESPONSE_DECLINED = "declined"
RESPONSE_IGNORED = "ignored"

ALL_RESPONSES: tuple[str, ...] = (
    RESPONSE_ACCEPTED,
    RESPONSE_DECLINED,
    RESPONSE_IGNORED,
)

#: Global-settings key inside the configs bucket.
GLOBAL_KEY = "__global__"

#: Defaults for the notification policy.
DEFAULT_GLOBAL_ENABLED = True
DEFAULT_MAX_PER_HOUR = 2
DEFAULT_COOLDOWN_SECONDS = 3600
DEFAULT_TRIGGER_KIND = KIND_MESSAGE

#: Default mood threshold — ``None`` means "no mood condition".
DEFAULT_MOOD_THRESHOLD = 70.0

#: Tick loop env flags (same posture as character_state / npcs).
_TICK_ENV_FLAG = "XIJIAN_INITIATED_TICK"
_TICK_INTERVAL_ENV_FLAG = "XIJIAN_INITIATED_TICK_SECONDS"
DEFAULT_TICK_INTERVAL_SECONDS = 60.0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InitiatedActionError(ValueError):
    """Raised on action validation / lifecycle errors."""


# ---------------------------------------------------------------------------
# WS broadcast (best-effort)
# ---------------------------------------------------------------------------


def _publish(event_type: str, data: dict[str, Any]) -> None:
    try:
        from xijian_api.routes.ws_routes import publish_event
        publish_event(event_type, data)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("initiated_actions WS publish failed: %s", event_type)


# ---------------------------------------------------------------------------
# Notification policy (AC-3)
# ---------------------------------------------------------------------------


def _default_global_settings() -> dict:
    return {
        "key": GLOBAL_KEY,
        "enabled": DEFAULT_GLOBAL_ENABLED,
        "default_max_per_hour": DEFAULT_MAX_PER_HOUR,
        "default_cooldown_seconds": DEFAULT_COOLDOWN_SECONDS,
    }


def get_global_settings() -> dict:
    """Return the global notification policy (materialised on demand)."""
    settings = state.character_initiated_configs.get(GLOBAL_KEY)
    if settings is None:
        settings = _default_global_settings()
        state.character_initiated_configs[GLOBAL_KEY] = settings
    return settings


def set_global_settings(patch: dict) -> dict:
    """Patch the global notification policy.

    ``enabled`` 是 A7 的"可全局开关"。``default_max_per_hour`` /
    ``default_cooldown_seconds`` 是未单独配置角色的默认值。
    """
    settings = get_global_settings()
    for key in ("enabled", "default_max_per_hour", "default_cooldown_seconds"):
        if key in patch:
            if key == "enabled":
                settings[key] = bool(patch[key])
            else:
                settings[key] = max(0, int(patch[key]))
    return settings


def _default_character_config(character_id: str) -> dict:
    return {
        "character_id": character_id,
        "enabled": True,
        "kind": DEFAULT_TRIGGER_KIND,
        "cooldown_seconds": DEFAULT_COOLDOWN_SECONDS,
        "max_per_hour": DEFAULT_MAX_PER_HOUR,
        "mood_threshold": DEFAULT_MOOD_THRESHOLD,  # None → 不按情绪触发
        "last_triggered_at": None,
    }


def get_character_config(character_id: str) -> dict:
    """Return a character's notification config (materialised on demand)."""
    cfg = state.character_initiated_configs.get(character_id)
    if cfg is None:
        cfg = _default_character_config(character_id)
        state.character_initiated_configs[character_id] = cfg
    return cfg


def set_character_config(character_id: str, patch: dict) -> dict:
    """Patch a character's notification config (按角色关闭 / 开启).

    ``enabled`` 是 A7 的"可按角色关闭"。``mood_threshold`` 传 ``None``
    表示不按情绪触发；``kind`` 限定 message / voice_call。
    """
    cfg = get_character_config(character_id)
    for key, value in patch.items():
        if key == "enabled":
            cfg["enabled"] = bool(value)
        elif key == "kind":
            if value not in ALL_KINDS:
                raise InitiatedActionError(
                    f"kind must be one of {ALL_KINDS}, got {value!r}"
                )
            cfg["kind"] = value
        elif key == "cooldown_seconds":
            cfg["cooldown_seconds"] = max(0, int(value))
        elif key == "max_per_hour":
            cfg["max_per_hour"] = max(0, int(value))
        elif key == "mood_threshold":
            cfg["mood_threshold"] = (
                None if value is None else float(value)
            )
    return cfg


def notifications_summary() -> dict:
    """JSON-friendly view of the whole notification policy."""
    return {
        "global": get_global_settings(),
        "characters": [
            cfg for key, cfg in state.character_initiated_configs.items()
            if key != GLOBAL_KEY
        ],
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_action(
    *,
    character_id: str,
    kind: str = KIND_MESSAGE,
    payload: dict[str, Any] | None = None,
    action_id: str | None = None,
    now: float | None = None,
    publish: bool = True,
) -> dict:
    """Create a ``pending`` initiated action and broadcast it.

    创建后立即 WS 广播 ``character.initiated_action``（AC-1 通知送达
    延迟 < 3s 由推送保证）。``publish=False`` 供测试/内部调用避免
    干扰其他连接的 WS 断言。
    """
    if not character_id:
        raise InitiatedActionError("character_id is required")
    if kind not in ALL_KINDS:
        raise InitiatedActionError(
            f"kind must be one of {ALL_KINDS}, got {kind!r}"
        )
    timestamp = _now_or(now)
    new_id = action_id or gen_initiated_action_id()
    record = {
        "id": new_id,
        "character_id": character_id,
        "kind": kind,
        "payload": payload or {},
        "status": STATUS_PENDING,
        "triggered_at": timestamp,
        "user_response": None,
        "responded_at": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    state.character_initiated_actions[new_id] = record
    cfg = get_character_config(character_id)
    cfg["last_triggered_at"] = timestamp
    if publish:
        _publish("character.initiated_action", {
            "action_id": new_id,
            "character_id": character_id,
            "kind": kind,
            "status": STATUS_PENDING,
            "triggered_at": timestamp,
            "payload": record["payload"],
        })
    return record


def get_action(action_id: str) -> dict | None:
    """Return an action record or ``None``."""
    return state.character_initiated_actions.get(action_id)


def list_actions(
    *,
    character_id: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    user_response: str | None = None,
) -> list[dict]:
    """List actions, newest first.  Optional filters."""
    items = list(state.character_initiated_actions.values())
    if character_id:
        items = [it for it in items if it.get("character_id") == character_id]
    if kind:
        items = [it for it in items if it.get("kind") == kind]
    if status:
        items = [it for it in items if it.get("status") == status]
    if user_response:
        items = [it for it in items if it.get("user_response") == user_response]
    items.sort(key=lambda r: r.get("triggered_at", 0), reverse=True)
    return items


def _require_action(action_id: str) -> dict:
    record = state.character_initiated_actions.get(action_id)
    if record is None:
        raise InitiatedActionError("action not found")
    return record


# ---------------------------------------------------------------------------
# Respond (AC-2: declined → "理解" 记忆回写)
# ---------------------------------------------------------------------------


def _write_decline_memory(record: dict) -> dict | None:
    """Write the "character understands" memory entry (AC-2).

    用户拒绝后角色必须表现出"理解"——把这次拒绝沉淀为角色记忆。
    经 :mod:`xijian_api.stubs.memory` 的创建接口写入一条短期记忆
    （低重要性，source=``character_initiated``）。
    """
    try:
        from xijian_api.stubs import memory as memory_stub
        kind_label = "消息" if record.get("kind") == KIND_MESSAGE else "来电"
        entry = memory_stub.create({
            "character_id": record.get("character_id"),
            "content": (
                f"角色主动发起的{kind_label}被用户婉拒了；"
                "角色表现出理解，尊重用户的意愿，不再追问。"
            ),
            "importance": 0.3,
            "type": "short",
            "source": "character_initiated",
            "source_ref_id": record.get("id"),
            "tags": ["proactive", "declined", "understanding"],
        })
        return entry
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("decline memory write failed: %s", exc)
        return None


def respond(action_id: str, user_response: str) -> dict:
    """Record the user's response to an initiated action.

    * ``accepted`` — 状态 → ``accepted``（外部调用方可据此接起通话/
      回复消息）。
    * ``declined`` — 状态 → ``declined`` 并写回"理解"记忆（AC-2）。
    * ``ignored``  — 状态 → ``ignored``（通知超时未处理）。

    广播 ``character.initiated_response``。已应答的动作幂等返回。
    """
    record = _require_action(action_id)
    if user_response not in ALL_RESPONSES:
        raise InitiatedActionError(
            f"user_response must be one of {ALL_RESPONSES}, got {user_response!r}"
        )
    if record.get("user_response") is not None:
        return record  # idempotent — 不能二次应答
    timestamp = now_ts()
    record["user_response"] = user_response
    record["responded_at"] = timestamp
    record["status"] = user_response  # accepted/declined/ignored
    record["updated_at"] = timestamp
    if user_response == RESPONSE_DECLINED:
        _write_decline_memory(record)
    _publish("character.initiated_response", {
        "action_id": action_id,
        "character_id": record.get("character_id"),
        "kind": record.get("kind"),
        "user_response": user_response,
        "responded_at": timestamp,
    })
    return record


# ---------------------------------------------------------------------------
# Trigger scan — 角色按条件主动发起
# ---------------------------------------------------------------------------


def _character_mood(character_id: str) -> float | None:
    """Best-effort read of the character's current mood (A3.2)."""
    try:
        from xijian_api.stubs import character_state as cs_stub
        state_record = cs_stub.get_state(character_id)
        if state_record is None:
            return None
        return state_record.get("mood")
    except Exception:  # noqa: BLE001
        return None


def _character_eligible(character_id: str, cfg: dict, now: float) -> tuple[bool, str]:
    """Decide whether ``character_id`` may initiate an action right now.

    条件（全部满足才触发）：

    1. 全局开关开启（AC-3 通知权限）。
    2. 角色开关开启（A7 可全局开关 + 可按角色关闭）。
    3. 冷却已过（``now - last_triggered_at >= cooldown_seconds``）。
    4. 每小时上限未超（``max_per_hour``，统计过去 3600s 内动作数）。
    5. 可选情绪阈值（``mood_threshold`` 配置时：mood <= 阈值，例如
       情绪低落的角色想找人倾诉）。
    """
    if not get_global_settings().get("enabled", True):
        return False, "global_disabled"
    if not cfg.get("enabled", True):
        return False, "character_disabled"
    last = cfg.get("last_triggered_at")
    cooldown = float(cfg.get("cooldown_seconds") or 0)
    if last is not None and (now - float(last)) < cooldown:
        return False, "cooldown"
    max_per_hour = int(cfg.get("max_per_hour") or 0)
    if max_per_hour > 0:
        window_start = now - 3600.0
        recent = [
            it for it in state.character_initiated_actions.values()
            if it.get("character_id") == character_id
            and float(it.get("triggered_at") or 0) >= window_start
        ]
        if len(recent) >= max_per_hour:
            return False, "rate_limited"
    threshold = cfg.get("mood_threshold")
    if threshold is not None:
        mood = _character_mood(character_id)
        if mood is not None and float(mood) > float(threshold):
            return False, "mood_too_high"
    return True, "eligible"


def scan_for_actions(now: float | None = None) -> list[dict]:
    """Scan every character and create actions for eligible ones.

    参考 stubs/character_state.py 的 tick 线程模式 —— 本函数是
    定时扫描的"一轮"；:func:`start_tick` 按固定间隔调用它。

    返回本轮创建的动作记录列表（空列表 = 本轮无合格角色）。
    """
    timestamp = _now_or(now)
    created: list[dict] = []
    try:
        from xijian_api.stubs import characters as characters_stub
        character_ids = [c.get("id") for c in characters_stub.list_all()]
    except Exception:  # noqa: BLE001
        character_ids = []
    for character_id in character_ids:
        cfg = get_character_config(character_id)
        eligible, _reason = _character_eligible(character_id, cfg, timestamp)
        if not eligible:
            continue
        payload: dict[str, Any] = {}
        if cfg.get("kind") == KIND_VOICE_CALL:
            payload = {"offer": "voice_call", "message": "想和你说说话"}
        else:
            payload = {"message": "在吗？想和你聊聊。"}
        record = create_action(
            character_id=character_id,
            kind=cfg.get("kind", KIND_MESSAGE),
            payload=payload,
            now=timestamp,
        )
        created.append(record)
    return created


# ---------------------------------------------------------------------------
# Tick thread (same pattern as character_state / npcs)
# ---------------------------------------------------------------------------

_TICK_LOCK = threading.Lock()
_TICK_STOP = threading.Event()
_TICK_THREAD: threading.Thread | None = None
_TICK_GENERATION = 0


def _current_interval() -> float:
    raw = os.environ.get(_TICK_INTERVAL_ENV_FLAG)
    try:
        value = float(raw) if raw else DEFAULT_TICK_INTERVAL_SECONDS
    except ValueError:
        value = DEFAULT_TICK_INTERVAL_SECONDS
    return max(1.0, value)


def _tick_loop(stop_event: threading.Event, generation: int) -> None:
    while not stop_event.is_set():
        with _TICK_LOCK:
            if _TICK_GENERATION != generation:
                return
        try:
            scan_for_actions()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("initiated_actions tick failed: %s", exc)
        if stop_event.wait(_current_interval()):
            break


def start_tick() -> dict:
    """Start the background scan thread (idempotent, env-gated)."""
    global _TICK_THREAD, _TICK_GENERATION
    with _TICK_LOCK:
        if _TICK_THREAD is not None and _TICK_THREAD.is_alive():
            return {"started": False, "reason": "already_running"}
        if os.environ.get(_TICK_ENV_FLAG) == "0":
            return {"started": False, "reason": "disabled_by_env"}
        _TICK_STOP.clear()
        _TICK_GENERATION += 1
        generation = _TICK_GENERATION
        thread = threading.Thread(
            target=_tick_loop,
            args=(_TICK_STOP, generation),
            name="xijian-initiated-actions-tick",
            daemon=True,
        )
        _TICK_THREAD = thread
        thread.start()
    return {"started": True, "interval_s": _current_interval()}


def stop_tick() -> dict:
    """Stop the background scan thread.  No-op if not running."""
    global _TICK_THREAD
    with _TICK_LOCK:
        thread = _TICK_THREAD
        if thread is None or not thread.is_alive():
            return {"stopped": False, "reason": "not_running"}
        _TICK_STOP.set()
    thread.join(timeout=_current_interval() * 3)
    with _TICK_LOCK:
        _TICK_THREAD = None
    return {"stopped": True}


def tick_status() -> dict:
    """Debug-friendly snapshot of the tick lifecycle."""
    with _TICK_LOCK:
        running = _TICK_THREAD is not None and _TICK_THREAD.is_alive()
    return {
        "running": running,
        "interval_s": _current_interval(),
        "env_disabled": os.environ.get(_TICK_ENV_FLAG) == "0",
        "generation": _TICK_GENERATION,
    }


# ---------------------------------------------------------------------------
# Pure helpers (for tests)
# ---------------------------------------------------------------------------


def _now_or(value: float | None) -> float:
    return float(value) if value is not None else now_ts()


# ---------------------------------------------------------------------------
# Seed / reset
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.

    Materialises the global policy and starts the background scan
    thread if the env allows it (same posture as npcs.seed_default).
    """
    get_global_settings()
    if os.environ.get(_TICK_ENV_FLAG) == "0":
        return
    start_tick()


def reset_for_testing() -> None:
    """Wipe action/config buckets and stop the tick thread."""
    global _TICK_GENERATION
    stop_tick()
    state.character_initiated_actions.clear()
    state.character_initiated_configs.clear()
    with _TICK_LOCK:
        _TICK_GENERATION += 1  # invalidate any lingering loop


__all__ = [
    # Constants
    "KIND_MESSAGE", "KIND_VOICE_CALL", "ALL_KINDS",
    "STATUS_PENDING", "STATUS_SENT", "STATUS_ACCEPTED", "STATUS_DECLINED",
    "STATUS_IGNORED", "ALL_STATUSES",
    "RESPONSE_ACCEPTED", "RESPONSE_DECLINED", "RESPONSE_IGNORED",
    "ALL_RESPONSES",
    "GLOBAL_KEY",
    "DEFAULT_GLOBAL_ENABLED", "DEFAULT_MAX_PER_HOUR",
    "DEFAULT_COOLDOWN_SECONDS", "DEFAULT_TRIGGER_KIND",
    "DEFAULT_MOOD_THRESHOLD", "DEFAULT_TICK_INTERVAL_SECONDS",
    # Errors
    "InitiatedActionError",
    # Notification policy
    "get_global_settings", "set_global_settings",
    "get_character_config", "set_character_config",
    "notifications_summary",
    # CRUD
    "create_action", "get_action", "list_actions", "respond",
    # Trigger
    "_character_eligible", "scan_for_actions",
    # Tick
    "start_tick", "stop_tick", "tick_status",
    # Lifecycle
    "seed_default", "reset_for_testing",
]
