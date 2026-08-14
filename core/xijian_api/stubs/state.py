"""SQLite-backed state containers for the API stubs.
SQLite 支持的 API 存根状态容器。

Every attribute below is a :class:`xijian_api.store.DictDB` instance
mapping resource id → record, persisted to ``~/.xijian/xijian.db``.
The API is identical to the previous in-memory dict interface, so
existing stubs work unchanged.

下面的每个属性都是一个 :class:`xijian_api.store.DictDB` 实例，
将资源 id → 记录映射，持久化到 ``~/.xijian/xijian.db``。
API 与之前的内存字典接口相同，因此现有存根无需修改即可工作。

The shared in-memory buckets (``safety_state`` / ``overload`` /
``audits`` / ``packs_index``) are wrapped in lock-guarded containers
(E3) so concurrent stub writes (e.g. the overload monitor thread
mutating ``overload`` while a request reads it) cannot corrupt the
structures; the DictDB buckets are already internally locked in
:mod:`xijian_api.store`.

共享内存桶（``safety_state`` / ``overload`` / ``audits`` /
``packs_index``）被包裹在带锁容器中 (E3)，使并发存根写入
（例如过载监控线程写入 ``overload`` 时请求正在读取）不会破坏
结构；DictDB 桶在 :mod:`xijian_api.store` 中已有内部锁。
"""

from __future__ import annotations

import threading
from typing import Any

from xijian_api.store import DictDB, bucket


# ---------------------------------------------------------------------------
# Lock-guarded container wrappers (E3)
# 带锁容器包装 (E3)
# ---------------------------------------------------------------------------


class _ThreadSafeDict(dict):
    """A dict whose mutating operations are serialised by an RLock.

    所有变更操作由 RLock 串行化的字典。

    Reads stay lock-free for hot paths; every write goes through the
    same lock so compound read-modify-write sequences (``d["k"] += 1``)
    at least never interleave two writers mid-update, and iterating
    while another thread writes cannot raise ``RuntimeError``.

    读保持无锁以支持热路径；所有写走同一把锁，使复合
    读-改-写序列（``d["k"] += 1``）至少不会让两个写者中途交错，
    且遍历时其他线程写入不会抛 ``RuntimeError``。
    """

    def __init__(self, *args, **kwargs) -> None:
        self._lock = threading.RLock()
        super().__init__(*args, **kwargs)

    def __setitem__(self, key, value) -> None:
        with self._lock:
            super().__setitem__(key, value)

    def __delitem__(self, key) -> None:
        with self._lock:
            super().__delitem__(key)

    def clear(self) -> None:
        with self._lock:
            super().clear()

    def update(self, *args, **kwargs) -> None:
        with self._lock:
            super().update(*args, **kwargs)

    def setdefault(self, key, default=None):
        with self._lock:
            return super().setdefault(key, default)

    def pop(self, key, *args):
        with self._lock:
            return super().pop(key, *args)

    def popitem(self):
        with self._lock:
            return super().popitem()


class _ThreadSafeList(list):
    """A list whose mutating operations are serialised by an RLock.

    所有变更操作由 RLock 串行化的列表。
    """

    def __init__(self, *args, **kwargs) -> None:
        self._lock = threading.RLock()
        super().__init__(*args, **kwargs)

    def append(self, x) -> None:
        with self._lock:
            super().append(x)

    def extend(self, iterable) -> None:
        with self._lock:
            super().extend(iterable)

    def insert(self, index, x) -> None:
        with self._lock:
            super().insert(index, x)

    def remove(self, x) -> None:
        with self._lock:
            super().remove(x)

    def pop(self, *args):
        with self._lock:
            return super().pop(*args)

    def clear(self) -> None:
        with self._lock:
            super().clear()

    def __setitem__(self, index, value) -> None:
        with self._lock:
            super().__setitem__(index, value)

    def __iadd__(self, iterable):
        with self._lock:
            return super().__iadd__(iterable)

    def sort(self, *args, **kwargs) -> None:
        with self._lock:
            super().sort(*args, **kwargs)


# Persisted key-value buckets (one SQLite table per bucket)
# 持久化键值桶（每个桶一个 SQLite 表）
characters: DictDB = bucket("characters")
interactions: DictDB = bucket("interactions")
worlds: DictDB = bucket("worlds")
memory: DictDB = bucket("memory")
# C3 plot runtime — one record per running plot instance.
# C3 剧情运行时 — 每个运行中的剧情实例一条记录。
plot_runtime_states: DictDB = bucket("plot_runtime_states")
memory_configs: DictDB = bucket("memory_configs")
sessions: DictDB = bucket("sessions")
snapshots: DictDB = bucket("snapshots")
import_jobs: DictDB = bucket("import_jobs")
character_states: DictDB = bucket("character_states")
character_state_configs: DictDB = bucket("character_state_configs")
character_state_log: DictDB = bucket("character_state_log")
character_models: DictDB = bucket("character_models")
character_motions: DictDB = bucket("character_motions")
character_voices: DictDB = bucket("character_voices")
character_handwritings: DictDB = bucket("character_handwritings")
character_styles: DictDB = bucket("character_styles")
character_asset_cache: DictDB = bucket("character_asset_cache")
world_events: DictDB = bucket("world_events")
world_event_instances: DictDB = bucket("world_event_instances")
npcs: DictDB = bucket("npcs")
npc_scheduling_log: DictDB = bucket("npc_scheduling_log")
world_compute_config: DictDB = bucket("world_compute_config")
world_environment: DictDB = bucket("world_environment")
world_audit_log: DictDB = bucket("world_audit_log")
pois: DictDB = bucket("pois")
travel_modes: DictDB = bucket("travel_modes")
scene_interactions: DictDB = bucket("scene_interactions")
world_currencies: DictDB = bucket("world_currencies")
wallets: DictDB = bucket("wallets")
transactions: DictDB = bucket("transactions")
world_economy_state: DictDB = bucket("world_economy_state")
safety_audit_log: DictDB = bucket("safety_audit_log")
safety_rules: DictDB = bucket("safety_rules")
mcp_rules: DictDB = bucket("mcp_rules")
mcp_audit: DictDB = bucket("mcp_audit")
mcp_freezes: DictDB = bucket("mcp_freezes")
mcp_snapshots: DictDB = bucket("mcp_snapshots")
safety_snapshots: DictDB = bucket("safety_snapshots")
# A5.3 backup policy — single-row record persisted via DictDB
# (SQLite write-through).  Carries the configurable compression
# backend (``compression_backend``: zstd|zlib|auto) and the
# per-snapshot cap (``max_single_snapshot_bytes``) so both survive
# restarts without extra wiring.
# A5.3 备份策略 — 通过 DictDB（SQLite 写透）持久化的单行记录。
# 携带可配置压缩后端（``compression_backend``: zstd|zlib|auto）
# 与单快照上限（``max_single_snapshot_bytes``），两者重启后保留。
backup_policies: DictDB = bucket("backup_policies")
# A1.1 manual backup system — protected-module registry, per-character
# module associations and the manual backup records.
# A1.1 手动备份系统 — 受保护模块注册表、每角色模块关联和手动备份记录。
protected_modules: DictDB = bucket("protected_modules")
character_protected_module: DictDB = bucket("character_protected_module")
manual_backups: DictDB = bucket("manual_backups")
files: DictDB = bucket("files")
batches: DictDB = bucket("batches")
fine_tuning_jobs: DictDB = bucket("fine_tuning_jobs")
assistants: DictDB = bucket("assistants")
threads: DictDB = bucket("threads")
runs: DictDB = bucket("runs")
messages: DictDB = bucket("messages")
videos: DictDB = bucket("videos")
models: DictDB = bucket("models")
# A6 realtime call — call sessions + per-call event stream.
# A6 实时通话 — 通话会话 + ����通话事件流。
voice_calls: DictDB = bucket("voice_calls")
call_events: DictDB = bucket("call_events")
# A7 proactive contact — character-initiated actions + per-character
# / global notification policy.
# A7 主动联系 — 角色主动发起动作 + ����角色/全局通知策略。
character_initiated_actions: DictDB = bucket("character_initiated_actions")
character_initiated_configs: DictDB = bucket("character_initiated_configs")
# A8 desktop pets — pet placements, dynamic wallpapers, auditable
# pet action log, and the desktop-client pending-action queue that
# :mod:`xijian_api.mcp.tools.desktop` fills.
# A8 ������ — ������放置、动态����、可��计����动作日志，以及��面客户端
# 待办动作队列 (由 :mod:`xijian_api.mcp.tools.desktop` �����充)。
desktop_pets: DictDB = bucket("desktop_pets")
dynamic_wallpapers: DictDB = bucket("dynamic_wallpapers")
pet_action_log: DictDB = bucket("pet_action_log")
mcp_pending_actions: DictDB = bucket("mcp_pending_actions")
# AI Backend & Model management (dynamic configuration)
# AI 后端与模型管理（动态配置）
ai_backends: DictDB = bucket("ai_backends")
ai_models: DictDB = bucket("ai_models")


# In-memory special buckets (not suited for key-value SQL)
# 内存特殊桶（不适合键值 SQL）
#
# E3 — wrapped in lock-guarded containers: the overload monitor,
# safety state handlers and citations audit all mutate these from
# background threads while request handlers read them.
# E3 — 包裹在带锁容器中：过载监控、安全状态处理器和引文审计
# 都会在后台线程中修改它们，而请求处理器在读它们。
safety_state: dict[str, Any] = _ThreadSafeDict()
overload: dict[str, Any] = _ThreadSafeDict()
audits: list[dict[str, Any]] = _ThreadSafeList()

# Resource pack index — package_id → {kind, target_ids, path, manifest}.
# Rebuilt at startup from scan_packs(); package directories are the
# source of truth for what is installed.
# 资源包索引 — package_id → {kind, target_ids, path, manifest}。
# 启动时由 scan_packs() 重建；包目录是已安装状态的唯一事实源。
packs_index: dict[str, Any] = _ThreadSafeDict()

# world_event_categories_disabled uses set values — stored via a
# dedicated DictDB bucket with list↔set conversion.
# world_event_categories_disabled 使用 set 值 — 通过专用 DictDB 桶
# 存储，带 list↔set 转换。
_world_event_categories_db: DictDB = bucket("world_event_categories_disabled")


def _load_categories() -> dict[str, set[str]]:
    """Load disabled world event categories from DB as sets.
    从数据库加载禁用的世界事件分类为集合。
    """
    d = {}
    for key in list(_world_event_categories_db.keys()):
        raw = _world_event_categories_db[key]
        d[key] = set(raw) if isinstance(raw, list) else set()
    return d


def _save_categories(d: dict[str, set[str]]) -> None:
    """Save disabled world event categories to DB as lists.
    将禁用的世界事件分类保存到数据库为列表。
    """
    _world_event_categories_db.clear()
    for key, val in d.items():
        _world_event_categories_db[key] = list(val)


world_event_categories_disabled: dict[str, set[str]] = _load_categories()


# ---------------------------------------------------------------------------
# Reset & seed — called between tests
# ---------------------------------------------------------------------------

# 重置和种子 — 在测试之间调用
# ---------------------------------------------------------------------------


def reset_for_testing(seed_demo_data: bool = True) -> None:
    """Wipe every bucket and re-seed defaults.

    DictDB buckets are cleared (cache + SQLite table truncated).
    In-memory buckets are cleared directly.
    ��空每个��并重新播种默认值。

    DictDB ��被清空（��存 + SQLite 表截断）。
    ��存��直接被清空。

    Parameters
    ----------
    seed_demo_data:
        When True (default), seed demo records (Yuki, Modern Tokyo, etc.).
        When False, only system-level defaults are seeded.
        为 True (默认) 时播种演示记录 (Yuki、Modern Tokyo 等)。
        为 False 时仅播种系统级默认值。
    """
    _all_dictdb = [
        characters, interactions, worlds, memory, memory_configs,
        sessions, snapshots, import_jobs,
        character_states, character_state_configs, character_state_log,
        character_models, character_motions, character_voices,
        character_handwritings, character_styles, character_asset_cache,
        world_events, world_event_instances,
        npcs, npc_scheduling_log, world_compute_config, world_environment,
        world_audit_log, plot_runtime_states,
        pois, travel_modes, scene_interactions,
        world_currencies, wallets, transactions, world_economy_state,
        safety_audit_log, safety_rules,
        mcp_rules, mcp_audit, mcp_freezes, mcp_snapshots,
        safety_snapshots, backup_policies,
        protected_modules, character_protected_module, manual_backups,
        files, batches, fine_tuning_jobs,
        assistants, threads, runs, messages, videos, models,
        # A6 / A7 / A8 buckets (added 2026-08-01).
        voice_calls, call_events,
        character_initiated_actions, character_initiated_configs,
        desktop_pets, dynamic_wallpapers, pet_action_log,
        mcp_pending_actions,
        # AI Backend & Model management (added 2026-08-14).
        ai_backends, ai_models,
    ]
    for db in _all_dictdb:
        db.clear()

    safety_state.clear()
    overload.clear()
    audits.clear()
    packs_index.clear()
    _world_event_categories_db.clear()
    world_event_categories_disabled.clear()

    from xijian_api.stubs import seed_all
    seed_all(seed_demo_data=seed_demo_data)
# Re-seed models from config (models are not part of seed_all).
    # ��������型不属于 seed_all，单独从配置重新播种。
    try:
        from flask import current_app
        from xijian_api.routes.models import seed_default_models
        from xijian_api.stubs import state as stubs_state
        if current_app:
            seed_default_models()
    except RuntimeError:
        # No app context (e.g. called from non-Flask script).
        # 无应用上下文（例如从非 Flask 脚本调用）。
        pass


# Reload categories after seed_all in case seed_all populated them
# 在 seed_all 之后重新加载分类，以防 seed_all 填充了它们
world_event_categories_disabled.update(_load_categories())


__all__ = [k for k, v in list(locals().items())
           if isinstance(v, (DictDB, dict, list)) and not k.startswith("_")] + [
    "reset_for_testing",
]
