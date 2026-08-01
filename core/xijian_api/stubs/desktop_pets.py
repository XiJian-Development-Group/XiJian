"""Stub desktop-pet / dynamic-wallpaper service — A8 in the function list v2.

A8 桌宠/动态壁纸（仅支持 macOS）：1~N 个角色可以放到桌面自由活动
（部分角色可飞行，US-A8-01），在允许下可操作电脑"在合理范围内捣乱"
（US-A8-02）；单个角色可作为动态壁纸，壁纸内是模拟世界场景且只能
改桌面布局、不能做任何其他操作（US-A8-03 / US-A8-04）。

数据模型镜像 §A8 的 SQL 建表语句：

* ``desktop_pets``      — (id / character_id / can_fly / can_interact /
                          spawn_x / spawn_y / is_active) + name/timestamps。
* ``dynamic_wallpapers``— (id / character_id / world_id / env_settings /
                          can_layout / is_active) + timestamps。
* ``pet_action_log``    — (id / pet_id / action_kind / payload /
                          created_at) — AC-2 要求的可审计日志，追加式。

验收标准落地：

* AC-1（桌宠 FPS 不影响系统，默认 30 可调）—— 渲染层在桌面客户端；
  本模块在 ``pet_action_log`` 与 WS 事件里带 ``fps_cap`` 建议字段。
* AC-2（"捣乱"必须有可审计日志）—— :func:`log_pet_action` 追加式落库
  + WS 广播 ``desktop_pet.action``。
* AC-3（动态壁纸 CPU < 10%）—— 客户端渲染责任；服务器侧在壁纸记录上
  暴露 ``env_settings``（时间变化 + 环境模拟参数）供客户端节制渲染。
* AC-4（动态壁纸模式下桌宠写操作能力完全禁用）—— :func:`write_ops_allowed`
  在角色有活动壁纸时返回 ``False``；pending 执行端（
  :func:`report_result` / 路由层）用它 gate 写类动作。

执行端闭环（A5.2 审计点名的缺口，本模块补齐）：

* ``state.mcp_pending_actions`` — :mod:`xijian_api.mcp.tools.desktop`
  把桌面操作（app_launch / browser_open / mouse_click / ...）写入的
  待办队列。本模块提供 :func:`list_pending` / :func:`claim_action` /
  :func:`get_pending` / :func:`report_result`，路由层暴露
  ``GET /v1/xijian/mcp/pending`` + 结果回写端点，桌面客户端据此
  轮询 → 认领 → 执行 → 回写，形成闭环。

WS 推送：``desktop_pet.event`` / ``wallpaper.event`` /
``desktop_pet.action`` / ``desktop_pet.pending``，全部经
:func:`xijian_api.routes.ws_routes.publish_event` 尽力而为广播。
"""

from __future__ import annotations

import logging
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import (
    gen_desktop_pet_id,
    gen_pet_action_log_id,
    gen_wallpaper_id,
)
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.desktop_pets")

#: Monotonic sequence for stable log ordering (same pattern as
#: safety.py's ``_seq``).  ``created_at`` is second-resolution, so two
#: entries in the same second need a tiebreaker.
#: 单调序列，用于稳定的日志排序 (与 safety.py 的 ``_seq`` 同模式)。
#: ``created_at`` 是秒级分辨率，同一秒内的两条记录需要决胜键。
_seq = 0


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Pet action kinds (spec §A8: mouse_click / key_input / window_move / ...).
ACTION_MOUSE_CLICK = "mouse_click"
ACTION_KEY_INPUT = "key_input"
ACTION_KEYBOARD_KEY = "keyboard_key"
ACTION_WINDOW_MOVE = "window_move"
ACTION_APP_LAUNCH = "app_launch"
ACTION_BROWSER_OPEN = "browser_open"

#: Kinds that count as "写操作"（AC-4 在壁纸模式下禁用的那类）。
WRITE_ACTION_KINDS: frozenset[str] = frozenset({
    ACTION_MOUSE_CLICK,
    ACTION_KEY_INPUT,
    ACTION_KEYBOARD_KEY,
    ACTION_WINDOW_MOVE,
})

#: Default desktop-pet FPS cap suggestion (AC-1: 默认 30，可调).
DEFAULT_FPS_CAP = 30

#: Pending-action lifecycle.
PENDING_STATUS_PENDING = "pending"
PENDING_STATUS_CLAIMED = "claimed"
PENDING_STATUS_EXECUTED = "executed"
PENDING_STATUS_FAILED = "failed"

ALL_PENDING_STATUSES: tuple[str, ...] = (
    PENDING_STATUS_PENDING,
    PENDING_STATUS_CLAIMED,
    PENDING_STATUS_EXECUTED,
    PENDING_STATUS_FAILED,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DesktopPetError(ValueError):
    """Raised on pet / wallpaper / pending-action validation errors."""


# ---------------------------------------------------------------------------
# WS broadcast (best-effort)
# ---------------------------------------------------------------------------


def _publish(event_type: str, data: dict[str, Any]) -> None:
    try:
        from xijian_api.routes.ws_routes import publish_event
        publish_event(event_type, data)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("desktop_pets WS publish failed: %s", event_type)


# ---------------------------------------------------------------------------
# Desktop pets — CRUD
# ---------------------------------------------------------------------------


def create_pet(
    *,
    character_id: str,
    can_fly: bool = False,
    can_interact: bool = False,
    spawn_x: float = 0.0,
    spawn_y: float = 0.0,
    is_active: bool = True,
    pet_id: str | None = None,
    name: str | None = None,
) -> dict:
    """Place a character on the desktop as a pet (US-A8-01)."""
    if not character_id:
        raise DesktopPetError("character_id is required")
    timestamp = now_ts()
    new_id = pet_id or gen_desktop_pet_id()
    record = {
        "id": new_id,
        "character_id": character_id,
        "name": name or f"pet-{new_id[-6:]}",
        "can_fly": bool(can_fly),
        "can_interact": bool(can_interact),
        "spawn_x": float(spawn_x),
        "spawn_y": float(spawn_y),
        "is_active": bool(is_active),
        "fps_cap": DEFAULT_FPS_CAP,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    state.desktop_pets[new_id] = record
    _publish("desktop_pet.event", {
        "event": "created", "pet_id": new_id,
        "character_id": character_id, "is_active": record["is_active"],
    })
    return record


def get_pet(pet_id: str) -> dict | None:
    return state.desktop_pets.get(pet_id)


def list_pets(*, character_id: str | None = None, is_active: bool | None = None) -> list[dict]:
    items = list(state.desktop_pets.values())
    if character_id:
        items = [it for it in items if it.get("character_id") == character_id]
    if is_active is not None:
        items = [it for it in items if bool(it.get("is_active")) == is_active]
    items.sort(key=lambda r: r.get("created_at", 0))
    return items


def _require_pet(pet_id: str) -> dict:
    record = state.desktop_pets.get(pet_id)
    if record is None:
        raise DesktopPetError("desktop pet not found")
    return record


def update_pet(pet_id: str, patch: dict) -> dict:
    """Patch mutable pet fields (``id``/``character_id``/``created_at`` immutable)."""
    record = _require_pet(pet_id)
    if any(k in patch for k in ("id", "character_id", "created_at")):
        raise DesktopPetError("id, character_id and created_at are immutable")
    for key in ("name", "can_fly", "can_interact", "spawn_x", "spawn_y",
                "is_active", "fps_cap"):
        if key in patch:
            if key in ("can_fly", "can_interact", "is_active"):
                record[key] = bool(patch[key])
            elif key == "fps_cap":
                record[key] = max(1, min(120, int(patch[key])))
            elif key == "name":
                record[key] = str(patch[key])
            else:
                record[key] = float(patch[key])
    record["updated_at"] = now_ts()
    _publish("desktop_pet.event", {"event": "updated", "pet_id": pet_id})
    return record


def delete_pet(pet_id: str) -> bool:
    """Delete a pet placement.  The audit log is kept (AC-2)."""
    record = _require_pet(pet_id)
    _publish("desktop_pet.event", {
        "event": "deleted", "pet_id": pet_id,
        "character_id": record.get("character_id"),
    })
    state.desktop_pets.pop(pet_id, None)
    return True


def set_pet_active(pet_id: str, active: bool) -> dict:
    """Activate / deactivate a pet on the desktop."""
    record = _require_pet(pet_id)
    record["is_active"] = bool(active)
    record["updated_at"] = now_ts()
    _publish("desktop_pet.event", {
        "event": "activated" if active else "deactivated",
        "pet_id": pet_id,
        "character_id": record.get("character_id"),
    })
    return record


# ---------------------------------------------------------------------------
# Dynamic wallpapers — CRUD (AC-3 / AC-4)
# ---------------------------------------------------------------------------


def create_wallpaper(
    *,
    character_id: str,
    world_id: str | None = None,
    env_settings: dict[str, Any] | None = None,
    can_layout: bool = True,
    is_active: bool = False,
    wallpaper_id: str | None = None,
) -> dict:
    """Create a dynamic-wallpaper record (US-A8-03)."""
    if not character_id:
        raise DesktopPetError("character_id is required")
    timestamp = now_ts()
    new_id = wallpaper_id or gen_wallpaper_id()
    record = {
        "id": new_id,
        "character_id": character_id,
        "world_id": world_id,
        "env_settings": env_settings or {},
        "can_layout": bool(can_layout),
        "is_active": bool(is_active),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    state.dynamic_wallpapers[new_id] = record
    if record["is_active"]:
        _enforce_wallpaper_exclusivity(record)
    _publish("wallpaper.event", {
        "event": "created", "wallpaper_id": new_id,
        "character_id": character_id, "is_active": record["is_active"],
    })
    return record


def get_wallpaper(wallpaper_id: str) -> dict | None:
    return state.dynamic_wallpapers.get(wallpaper_id)


def list_wallpapers(
    *,
    character_id: str | None = None,
    is_active: bool | None = None,
) -> list[dict]:
    items = list(state.dynamic_wallpapers.values())
    if character_id:
        items = [it for it in items if it.get("character_id") == character_id]
    if is_active is not None:
        items = [it for it in items if bool(it.get("is_active")) == is_active]
    items.sort(key=lambda r: r.get("created_at", 0))
    return items


def _require_wallpaper(wallpaper_id: str) -> dict:
    record = state.dynamic_wallpapers.get(wallpaper_id)
    if record is None:
        raise DesktopPetError("dynamic wallpaper not found")
    return record


def update_wallpaper(wallpaper_id: str, patch: dict) -> dict:
    record = _require_wallpaper(wallpaper_id)
    if any(k in patch for k in ("id", "character_id", "created_at")):
        raise DesktopPetError("id, character_id and created_at are immutable")
    for key in ("world_id", "env_settings", "can_layout", "is_active"):
        if key in patch:
            record[key] = patch[key]
    record["updated_at"] = now_ts()
    if record["is_active"]:
        _enforce_wallpaper_exclusivity(record)
    _publish("wallpaper.event", {"event": "updated", "wallpaper_id": wallpaper_id})
    return record


def delete_wallpaper(wallpaper_id: str) -> bool:
    _require_wallpaper(wallpaper_id)
    _publish("wallpaper.event", {"event": "deleted", "wallpaper_id": wallpaper_id})
    state.dynamic_wallpapers.pop(wallpaper_id, None)
    return True


def _enforce_wallpaper_exclusivity(active: dict) -> None:
    """At most one active wallpaper per character (spec: 单个角色作为动态壁纸).

    同时把该角色的桌宠停用 —— 壁纸模式下角色渲染在壁纸里，不再是桌宠。
    """
    character_id = active.get("character_id")
    for wp in state.dynamic_wallpapers.values():
        if (
            wp.get("character_id") == character_id
            and wp.get("id") != active.get("id")
            and wp.get("is_active")
        ):
            wp["is_active"] = False
            wp["updated_at"] = now_ts()
    for pet in state.desktop_pets.values():
        if pet.get("character_id") == character_id and pet.get("is_active"):
            pet["is_active"] = False
            pet["updated_at"] = now_ts()


def set_wallpaper_active(wallpaper_id: str, active: bool) -> dict:
    """Activate / deactivate a wallpaper (US-A8-03).

    激活时强制该角色壁纸唯一 + 停用其桌宠（AC-4 语义：壁纸模式接管）。
    """
    record = _require_wallpaper(wallpaper_id)
    record["is_active"] = bool(active)
    record["updated_at"] = now_ts()
    if record["is_active"]:
        _enforce_wallpaper_exclusivity(record)
    _publish("wallpaper.event", {
        "event": "activated" if active else "deactivated",
        "wallpaper_id": wallpaper_id,
        "character_id": record.get("character_id"),
    })
    return record


def write_ops_allowed(character_id: str) -> bool:
    """AC-4: 动态壁纸模式下，桌宠的写操作能力被完全禁用.

    角色存在**活动壁纸**时返回 ``False``（该角色的桌宠写操作——
    mouse_click / key_input / window_move —— 一律拒绝）。
    """
    for wp in state.dynamic_wallpapers.values():
        if wp.get("character_id") == character_id and wp.get("is_active"):
            return False
    return True


# ---------------------------------------------------------------------------
# Pet action log — AC-2 可审计日志
# ---------------------------------------------------------------------------


def log_pet_action(
    pet_id: str,
    action_kind: str,
    payload: dict[str, Any] | None = None,
    *,
    entry_id: str | None = None,
    publish: bool = True,
) -> dict:
    """Append one auditable pet action (AC-2).  Append-only."""
    global _seq
    pet = _require_pet(pet_id)
    timestamp = now_ts()
    _seq += 1
    entry = {
        "id": entry_id or gen_pet_action_log_id(),
        "pet_id": pet_id,
        "character_id": pet.get("character_id"),
        "action_kind": action_kind,
        "payload": payload or {},
        "created_at": timestamp,
        "seq": _seq,
    }
    state.pet_action_log[entry["id"]] = entry
    if publish:
        _publish("desktop_pet.action", {
            "log_id": entry["id"],
            "pet_id": pet_id,
            "action_kind": action_kind,
            "payload": entry["payload"],
        })
    return entry


def list_pet_actions(
    pet_id: str | None = None,
    *,
    action_kind: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Query the audit log (AC-2), newest first."""
    items = list(state.pet_action_log.values())
    if pet_id:
        items = [it for it in items if it.get("pet_id") == pet_id]
    if action_kind:
        items = [it for it in items if it.get("action_kind") == action_kind]
    items.sort(key=lambda e: (e.get("created_at", 0), e.get("seq", 0)), reverse=True)
    return items[:limit] if limit and limit > 0 else items


# ---------------------------------------------------------------------------
# Desktop-client execution loop — the A5.2-flagged gap
# ---------------------------------------------------------------------------


def _pending_bucket() -> dict[str, Any]:
    # ``state.mcp_pending_actions`` is now a proper DictDB bucket;
    # kept via ``hasattr`` guard for safety.
    return state.mcp_pending_actions  # type: ignore[attr-defined]


def list_pending(*, status: str | None = None, limit: int = 50) -> list[dict]:
    """List pending desktop actions (GET /v1/xijian/mcp/pending).

    供桌面客户端轮询待执行动作。按创建时间倒序。
    """
    items = list(_pending_bucket().values())
    if status:
        items = [it for it in items if it.get("status") == status]
    items.sort(key=lambda e: e.get("created_at", 0), reverse=True)
    return items[:limit] if limit and limit > 0 else items


def get_pending(action_id: str) -> dict | None:
    return _pending_bucket().get(action_id)


def claim_action(action_id: str) -> dict:
    """Claim a pending action: ``pending`` → ``claimed``.

    桌面客户端取走动作开始执行。重复认领幂等返回当前记录。
    """
    record = _pending_bucket().get(action_id)
    if record is None:
        raise DesktopPetError("pending action not found")
    if record.get("status") == PENDING_STATUS_PENDING:
        record["status"] = PENDING_STATUS_CLAIMED
        record["claimed_at"] = now_ts()
        _publish("desktop_pet.pending", {
            "event": "claimed", "action_id": action_id,
            "kind": record.get("kind"),
        })
    return record


def report_result(
    action_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    *,
    pet_id: str | None = None,
) -> dict:
    """Write back the execution result (POST result endpoint).

    * ``status`` ∈ {``executed``, ``failed``}；记录 ``result``。
    * AC-4 gate：写类动作（鼠标/键盘/移动窗口）在角色有活动壁纸时
      一律标记为 ``failed``（reason=wallpaper_mode_read_only），
      即使客户端报告成功 —— 服务器侧强制"完全禁用"。
    * 携带 ``pet_id`` 且执行成功时，追加一条 pet_action_log（AC-2
      审计闭环：MCP 待办 → 桌宠动作日志）。
    """
    record = _pending_bucket().get(action_id)
    if record is None:
        raise DesktopPetError("pending action not found")
    if status not in (PENDING_STATUS_EXECUTED, PENDING_STATUS_FAILED):
        raise DesktopPetError(
            f"result status must be one of "
            f"({PENDING_STATUS_EXECUTED!r}, {PENDING_STATUS_FAILED!r})"
        )
    kind = record.get("kind", "")
    blocked = False
    if status == PENDING_STATUS_EXECUTED and kind in WRITE_ACTION_KINDS:
        # 找到该动作关联的角色（via pet 或直接写在 action 上）。
        character_id = (result or {}).get("character_id") or (
            _require_pet(pet_id).get("character_id") if pet_id else None
        )
        if character_id and not write_ops_allowed(character_id):
            blocked = True

    if blocked:
        record["status"] = PENDING_STATUS_FAILED
        record["result"] = {
            **(result or {}),
            "blocked_by": "wallpaper_mode_read_only",
            "message": "动态壁纸模式下桌宠写操作被完全禁用 (AC-4)",
        }
    else:
        record["status"] = status
        record["result"] = result or {}

    if pet_id and record["status"] == PENDING_STATUS_EXECUTED:
        log_pet_action(
            pet_id,
            kind,
            {"action_id": action_id, "result": record["result"]},
        )
    _publish("desktop_pet.pending", {
        "event": "result", "action_id": action_id,
        "kind": kind, "status": record["status"],
    })
    return record


# ---------------------------------------------------------------------------
# Seed / reset
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.  A8 keeps no default pets / wallpapers —
    placements are user-driven.  Hook kept for the package seed point."""
    return None


def reset_for_testing() -> None:
    """Wipe the A8 buckets and the pending-action queue."""
    global _seq
    state.desktop_pets.clear()
    state.dynamic_wallpapers.clear()
    state.pet_action_log.clear()
    state.mcp_pending_actions.clear()  # type: ignore[attr-defined]
    _seq = 0


__all__ = [
    # Constants
    "ACTION_MOUSE_CLICK", "ACTION_KEY_INPUT", "ACTION_KEYBOARD_KEY",
    "ACTION_WINDOW_MOVE", "ACTION_APP_LAUNCH", "ACTION_BROWSER_OPEN",
    "WRITE_ACTION_KINDS",
    "DEFAULT_FPS_CAP",
    "PENDING_STATUS_PENDING", "PENDING_STATUS_CLAIMED",
    "PENDING_STATUS_EXECUTED", "PENDING_STATUS_FAILED",
    "ALL_PENDING_STATUSES",
    # Errors
    "DesktopPetError",
    # Pets
    "create_pet", "get_pet", "list_pets", "update_pet", "delete_pet",
    "set_pet_active",
    # Wallpapers
    "create_wallpaper", "get_wallpaper", "list_wallpapers",
    "update_wallpaper", "delete_wallpaper", "set_wallpaper_active",
    "write_ops_allowed",
    # Pet action log
    "log_pet_action", "list_pet_actions",
    # Pending queue (execution loop)
    "list_pending", "get_pending", "claim_action", "report_result",
    # Lifecycle
    "seed_default", "reset_for_testing",
]
