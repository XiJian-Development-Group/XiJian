"""Per-world environment state — weather / time / light / ambient.
每世界环境状态 — 天气 / 时间 / 光照 / 氛围。

A4.2 spec defines a `world_environment` table that holds *visual /
audio ambient state* which the renderer reads to draw the scene, the
audio backend reads to mix the BGM, and the simulator reads to drive
tick-based transitions (time_of_day advances, weather drifts).

A4.2 规格定义了 `world_environment` 表，持有*视觉/音频环境状态*，
渲染器读取它来绘制场景，音频后端读取它来混合 BGM，
模拟器读取它来驱动基于 tick 的转换（time_of_day 推进、天气漂移）。

The module is intentionally simple:

本模块有意保持简单：

* One row per world.  Created lazily on first read.
  每世界一行。首次读取时惰性创建。
* Updateable via ``patch_environment`` (any subset of fields).
  可通过 ``patch_environment`` 更新（任意字段子集）。
* Time-of-day defaults to ``12:00`` (midday) for new worlds.
  新世界的时间默认到 ``12:00``（正午）。
* Light level = deterministic from time_of_day (no-op, here
  computed for callers that want it).
  光照水平由 time_of_day 确定性得出（空操作，此处为需要它的调用者计算）。
"""

from __future__ import annotations

import logging
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.world_environment")

#: Default weather on world creation.
#: 创建世界时的默认天气。
DEFAULT_WEATHER = "sunny"

#: Default time of day, in minutes-from-midnight (noon = 720).
#: 默认时间，自午夜起的分钟数（正午 = 720）。
DEFAULT_TIME_OF_DAY = 720

#: Default light level, 0..1.
#: 默认光照水平，0..1。
DEFAULT_LIGHT_LEVEL = 0.6


def _default_record(world_id: str) -> dict:
    """Return a default environment record.
    返回默认环境记录。
    """
    return {
        "world_id": world_id,
        "weather": DEFAULT_WEATHER,
        "time_of_day": DEFAULT_TIME_OF_DAY,
        "light_level": DEFAULT_LIGHT_LEVEL,
        "ambient_audio": None,
        "env_meta": {},
        "updated_at": now_ts(),
    }


def get(world_id: str) -> dict | None:
    """Return the environment record for ``world_id`` (creating one if absent).

    ``None`` is returned *only* for the explicit "world doesn't exist"
    case (the caller already supplies world_id from a known id, so this
    rarely matters — but we honour the contract).
    返回 ``world_id`` 的环境记录（缺失时创建）。

    仅当"世界不存在"时返回 ``None``（调用者已从已知 id 提供 world_id，
    所以这很少有关系——但我们遵守约定）。
    """
    record = state.world_environment.get(world_id)
    if record is None:
        # Treat as "no world" so callers like the scheduler can early-exit.
        # The audit / route layer is responsible for materializing an
        # initial record via :func:`ensure_environment`.
        # 视为"无世界"，以便调度器等调用者可提前退出。
        # 审计/路由层负责通过 :func:`ensure_environment` 实现初始记录。
        return None
    return record


def ensure_environment(world_id: str) -> dict:
    """Materialize a default environment record if absent; return it.

    Idempotent.  Called by the world-creation route and any test fixture
    that wants a deterministic environment shape.
    若不存在则物化默认环境记录；然后返回它。

    幂等。由世界创建路由和任何需要确定环境形状的测试装置调用。
    """
    record = state.world_environment.get(world_id)
    if record is not None:
        return record
    record = _default_record(world_id)
    state.world_environment[world_id] = record
    return record


def patch_environment(
    world_id: str, patch: dict[str, Any], *, now: float | None = None
) -> dict:
    """Update a subset of the environment fields; create on first call.

    Recognised keys: ``weather``, ``time_of_day`` (0..1439 minutes),
    ``light_level`` (0..1), ``ambient_audio`` (path string or ``None``),
    ``env_meta`` (merge dict).  Values outside recognised keys are
    silently accepted into ``env_meta`` for forward-compat — explicit
    operators may extend the schema.
    更新环境字段的子集；首次调用时创建。

    识别的键：``weather``、``time_of_day`` (0..1439 分钟)、
    ``light_level`` (0..1)、``ambient_audio`` (路径字符串或 ``None``)、
    ``env_meta``（合并字典）。识别的键之外的值被静默接受到
    ``env_meta`` 中以保持前向兼容——显式操作者可能扩展模式。
    """
    record = ensure_environment(world_id)
    timestamp = float(now) if now is not None else now_ts()
    meta = dict(record.get("env_meta") or {})
    for key, value in patch.items():
        if key in {"weather", "ambient_audio"}:
            record[key] = value
        elif key == "time_of_day":
            try:
                minutes = int(value) % 1440
            except (TypeError, ValueError):
                _LOGGER.debug("invalid time_of_day %r for world %s", value, world_id)
                continue
            record["time_of_day"] = minutes
            record["light_level"] = _light_from_time(minutes)
        elif key == "light_level":
            try:
                lvl = float(value)
            except (TypeError, ValueError):
                continue
            record["light_level"] = max(0.0, min(1.0, lvl))
        else:
            meta[key] = value
    record["env_meta"] = meta
    record["updated_at"] = timestamp
    return record


def _light_from_time(minutes: int) -> float:
    """Return a deterministic light level from time-of-day minutes.

    A simple sinusoidal model — noon=1.0, midnight=0.0.  Callers can
    override via ``light_level`` if they have a richer model.
    从时间分钟数返回确定性的光照水平。

    简单的正弦模型——正午=1.0，午夜=0.0。调用者若有更丰富的模型
    可通过 ``light_level`` 覆盖。
    """
    import math
    # Phase shift so peak lands at 12:00.
    # 相移使峰值落在 12:00。
    radians = ((minutes - 720) / 1440.0) * 2 * math.pi
    return 0.5 + 0.5 * math.cos(radians)


def delete(world_id: str) -> bool:
    """Drop the environment record for a world.
    删除世界的环境记录。
    """
    return state.world_environment.pop(world_id, None) is not None


def reset_for_testing() -> None:
    """Clear all environment records for testing.
    清空所有环境记录用于测试。
    """
    state.world_environment.clear()


__all__ = [
    "DEFAULT_WEATHER",
    "DEFAULT_TIME_OF_DAY",
    "DEFAULT_LIGHT_LEVEL",
    "get",
    "ensure_environment",
    "patch_environment",
    "delete",
    "reset_for_testing",
]
