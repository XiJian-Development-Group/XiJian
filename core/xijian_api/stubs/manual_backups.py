"""A1.1 manual backup system — protected modules + versioned per-character backups.

A1.1 手动备份系统 — 受保护模块 + 按角色的版本化备份。

This module implements the A1.1 half of the memory system in
``docs/Dev. Function List功能清单v2.md``:

* ``protected_modules`` — the hot-updatable registry of protected
  modules (AC-1: at least ``memory_entries / character_documents /
  world_documents / safety_snapshots``).
  ``protected_modules`` — 可热更新的受保护模块注册表
  (AC-1：至少包含 ``memory_entries / character_documents /
  world_documents / safety_snapshots``)。
* ``character_protected_module`` — per-character association with
  ``auto_backup`` flag + ``last_backup_at`` (US-A1.1-01).
  ``character_protected_module`` — 每角色关联，带 ``auto_backup``
  标志 + ``last_backup_at`` (US-A1.1-01)。
* ``manual_backups`` — the versioned backup records.  File naming
  follows AC-3: ``{character_id}_{ISO8601}_v{n}.bak`` with at most
  ``MAX_VERSIONS_PER_CHARACTER`` kept per character (M1 default 10).
  ``manual_backups`` — 版本化备份记录。文件命名遵循 AC-3：
  ``{character_id}_{ISO8601}_v{n}.bak``，每角色最多保留
  ``MAX_VERSIONS_PER_CHARACTER`` 个版本（M1 默认 10）。

Compression
-----------

Per spec A1.1 §技术视角 the backup file is "SQLite Dump + JSON 元信息；
压缩采用 zstd".  The stub serialises the character's protected-module
data as JSON and compresses it with **zstd** (``zstandard``), falling
back to ``zlib`` only when the optional dependency is unavailable.

Auto-backup triggers (spec §自动备份策略)
-----------------------------------------

* 定时（每日凌晨） — the background scheduler thread
  (:func:`start_scheduler`, env ``XIJIAN_BACKUP_SCHEDULER``).
* 手动修改 50 条以上 — :func:`notify_memory_modified` counts memory
  mutations per character; crossing the 50-edit threshold triggers an
  automatic backup.
* 角色首次加载 — :func:`notify_first_load` backs up a character the
  first time it transitions into the loaded state.
* 安全终止之后 — :func:`notify_safe_termination` backs up every
  character (or a specific one) after a safety-stop restore.  The
  A5.2 ``confirm_safety_stop`` flow (``stubs/mcp.py``) is the natural
  call site; it lives in the A5 chapter so this module exposes the
  hook and the A5.2 agent wires it (see ``docs/notes.md``).

Everything here is a stub: records live in the process-level
``state`` buckets (same DictDB persistence as the other stubs), and
the "file" is modelled by ``file_path`` + ``size_bytes`` on the
record with the JSON payload retained on the record for restore.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_manual_backup_id
from xijian_api.utils.time import iso_now, now_ts


_LOGGER = logging.getLogger("xijian_api.manual_backups")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: AC-1 — the four protected modules every deployment must expose.
#: AC-1 — 每个部署都必须暴露的四个受保护模块。
DEFAULT_PROTECTED_MODULES: tuple[dict[str, Any], ...] = (
    {
        "module_name": "memory_entries",
        "description": "角色的记忆条目（长期/短期，含软删除条目）",
    },
    {
        "module_name": "character_documents",
        "description": "角色档案：人设文档、语言风格、模型/动作/声音资源",
    },
    {
        "module_name": "world_documents",
        "description": "角色绑定世界的世界观文档与环境状态",
    },
    {
        "module_name": "safety_snapshots",
        "description": "A5.3 安全快照归档（按角色维度引用）",
    },
)

#: Valid backup scopes.  ``'all'`` covers everything; the others
#: restore only the named slice (US-A1.1-03).
#: 有效备份范围。``'all'`` 覆盖全部；其余仅恢复指定切片。
SCOPE_ALL = "all"
SCOPE_MEMORY_ONLY = "memory_only"
SCOPE_STATE_ONLY = "state_only"
SCOPE_DOC_ONLY = "doc_only"
VALID_SCOPES: frozenset[str] = frozenset({
    SCOPE_ALL, SCOPE_MEMORY_ONLY, SCOPE_STATE_ONLY, SCOPE_DOC_ONLY,
})

#: AC-3 — M1 default for the number of versions kept per character.
#: AC-3 — M1 默认每角色保留的版本数。
MAX_VERSIONS_PER_CHARACTER = 10

#: Memory-mutation threshold that triggers an automatic backup
#: (spec §自动备份策略 "手动修改 50 条以上").
#: 触发自动备份的记忆修改阈值（规范 §自动备份策略 "手动修改 50 条以上"）。
AUTO_BACKUP_EDIT_THRESHOLD = 50

#: Scheduler wake interval — how often the background thread checks
#: whether the daily-dawn backup is due.
#: 调度器唤醒间隔 — 后台线程多久检查一次每日凌晨备份是否到期。
DEFAULT_SCHEDULER_INTERVAL_SECONDS = 60.0

#: Dawn hour (local time) for the daily automatic backup.
#: 每日自动备份的凌晨时刻（本地时间）。
DAILY_BACKUP_HOUR = 4
DAILY_BACKUP_MINUTE = 0

#: Env flag to disable the background scheduler in tests / CI.
#: 用于在测试 / CI 中禁用后台调度器的环境变量。
_SCHEDULER_ENV_FLAG = "XIJIAN_BACKUP_SCHEDULER"

#: Lock guarding the scheduler thread + the modification counters.
_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# Module-level scheduler state
# ---------------------------------------------------------------------------

_SCHED_THREAD: threading.Thread | None = None
_SCHED_STOP = threading.Event()
_SCHED_GENERATION = 0

#: Per-character memory-edit counters (spec trigger: ≥ 50 edits).
#: 每角色记忆修改计数器（规范触发条件：≥ 50 次修改）。
_edit_counters: dict[str, int] = {}

#: Per-character "ever loaded" tracking — the first load of a
#: character triggers an automatic backup.
#: 每角色"曾经加载"跟踪 — 角色首次加载触发自动备份。
_ever_loaded: set[str] = set()


# ---------------------------------------------------------------------------
# Compression helpers (zstd per spec; zlib fallback)
# ---------------------------------------------------------------------------


def _zstd_available() -> bool:
    try:
        import zstandard  # noqa: F401
        return True
    except ImportError:
        return False


def compress_json(data: dict) -> tuple[bytes, str]:
    """Serialise ``data`` as JSON and compress it.

    Returns ``(compressed_bytes, codec_name)`` where ``codec_name`` is
    ``"zstd"`` when ``zstandard`` is installed (the spec's codec) and
    ``"zlib"`` otherwise (defensive fallback).
    """
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        import zstandard
        cctx = zstandard.ZstdCompressor(level=3)
        return cctx.compress(raw), "zstd"
    except ImportError:  # pragma: no cover - optional dep  # 可选依赖
        import zlib
        return zlib.compress(raw, level=6), "zlib"


def decompress_json(payload: bytes, codec: str = "zstd") -> dict:
    """Decompress ``payload`` back into a dict."""
    if codec == "zstd":
        try:
            import zstandard
            dctx = zstandard.ZstdDecompressor()
            raw = dctx.decompress(payload)
            return json.loads(raw.decode("utf-8"))
        except ImportError:  # pragma: no cover - optional dep  # 可选依赖
            import zlib
            raw = zlib.decompress(payload)
            return json.loads(raw.decode("utf-8"))
    import zlib
    raw = zlib.decompress(payload)
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# Protected-module registry (AC-1)
# ---------------------------------------------------------------------------


def _module_key(module_name: str) -> str:
    return f"module:{module_name}"


def seed_default() -> None:
    """Seed the four default protected modules (idempotent)."""
    for template in DEFAULT_PROTECTED_MODULES:
        module_name = template["module_name"]
        key = _module_key(module_name)
        if key in state.protected_modules:
            continue
        record = {
            "id": key,
            "module_name": module_name,
            "description": template["description"],
            "enabled": 1,
            "updated_at": now_ts(),
        }
        state.protected_modules[key] = record


def list_protected_modules(*, character_id: str | None = None) -> list[dict]:
    """List the protected-module registry.

    When ``character_id`` is given, each module record gains the
    character's association (``auto_backup`` / ``last_backup_at``).
    """
    seed_default()
    out: list[dict] = []
    for key in sorted(state.protected_modules.keys()):
        record = dict(state.protected_modules[key])
        if character_id:
            assoc = _get_association(character_id, record["module_name"])
            record["auto_backup"] = assoc.get("auto_backup", 1) if assoc else 1
            record["last_backup_at"] = assoc.get("last_backup_at") if assoc else None
        out.append(record)
    return out


def get_protected_module(module_name: str) -> dict | None:
    seed_default()
    record = state.protected_modules.get(_module_key(module_name))
    return dict(record) if record is not None else None


def set_module_enabled(module_name: str, enabled: bool) -> dict | None:
    """Hot-update a module's ``enabled`` flag (spec: 可热更新)."""
    seed_default()
    record = state.protected_modules.get(_module_key(module_name))
    if record is None:
        return None
    record["enabled"] = 1 if enabled else 0
    record["updated_at"] = now_ts()
    return dict(record)


# ---------------------------------------------------------------------------
# Per-character association (US-A1.1-01)
# ---------------------------------------------------------------------------


def _assoc_key(character_id: str, module_name: str) -> str:
    return f"{character_id}:{_module_key(module_name)}"


def _get_association(character_id: str, module_name: str) -> dict | None:
    record = state.character_protected_module.get(
        _assoc_key(character_id, module_name)
    )
    return record


def get_character_protection(character_id: str) -> dict:
    """Return the character's protected-module association list."""
    seed_default()
    modules = list_protected_modules(character_id=character_id)
    return {
        "character_id": character_id,
        "modules": modules,
        "auto_backup_enabled": any(m.get("auto_backup") for m in modules),
    }


def set_auto_backup(character_id: str, module_name: str, enabled: bool) -> dict:
    """Toggle automatic backup for one character's module."""
    seed_default()
    if get_protected_module(module_name) is None:
        raise ValueError(f"unknown protected module: {module_name!r}")
    key = _assoc_key(character_id, module_name)
    existing = state.character_protected_module.get(key) or {
        "character_id": character_id,
        "module_name": module_name,
    }
    existing["auto_backup"] = 1 if enabled else 0
    state.character_protected_module[key] = existing
    return dict(existing)


def touch_backup(character_id: str, module_name: str, ts: int | None = None) -> None:
    """Record ``last_backup_at`` on a character's module association."""
    key = _assoc_key(character_id, module_name)
    existing = state.character_protected_module.get(key) or {
        "character_id": character_id,
        "module_name": module_name,
        "auto_backup": 1,
    }
    existing["last_backup_at"] = ts if ts is not None else now_ts()
    state.character_protected_module[key] = existing


# ---------------------------------------------------------------------------
# Snapshot assembly — which data belongs to which protected module
# ---------------------------------------------------------------------------


def _memory_snapshot(character_id: str) -> list[dict]:
    """Deep-copy the character's memory entries (incl. soft-deleted)."""
    import copy
    return [
        copy.deepcopy(entry)
        for entry in state.memory.values()
        if entry.get("character_id") == character_id
    ]


def _character_doc_snapshot(character_id: str) -> dict:
    """Deep-copy the character's document-ish records."""
    import copy
    char = state.characters.get(character_id)
    return {
        "characters": copy.deepcopy(char) if char else None,
        "character_styles": copy.deepcopy(
            state.character_styles.get(character_id, {})
        ),
        "character_models": copy.deepcopy(
            state.character_models.get(character_id, {})
        ),
        "character_motions": copy.deepcopy(
            state.character_motions.get(character_id, {})
        ),
        "character_voices": copy.deepcopy(
            state.character_voices.get(character_id, {})
        ),
    }


def _world_doc_snapshot(character_id: str) -> dict:
    """Deep-copy the character's bound-world docs (if any)."""
    import copy
    char = state.characters.get(character_id) or {}
    world_id = char.get("assigned_world") or char.get("world_id")
    if not world_id:
        return {"worlds": None, "world_environment": None}
    return {
        "worlds": copy.deepcopy(state.worlds.get(world_id)),
        "world_environment": copy.deepcopy(
            state.world_environment.get(world_id)
        ),
    }


def _safety_snapshot_refs(character_id: str) -> list[dict]:
    """Metadata-only references to A5.3 snapshots targeting the character."""
    refs: list[dict] = []
    for record in state.safety_snapshots.values():
        if record.get("target_id") != character_id:
            continue
        refs.append(
            {
                "id": record.get("id"),
                "scope": record.get("scope"),
                "reason": record.get("reason"),
                "created_at": record.get("created_at"),
                "size_bytes": record.get("size_bytes"),
            }
        )
    refs.sort(key=lambda r: r.get("created_at", 0.0), reverse=True)
    return refs


def _assemble_payload(character_id: str, scope: str) -> dict:
    """Assemble the backup payload for ``character_id`` at ``scope``."""
    payload: dict[str, Any] = {
        "character_id": character_id,
        "scope": scope,
        "created_at": now_ts(),
    }
    if scope in (SCOPE_ALL, SCOPE_MEMORY_ONLY):
        payload["memory_entries"] = _memory_snapshot(character_id)
    if scope in (SCOPE_ALL, SCOPE_DOC_ONLY):
        payload["character_documents"] = _character_doc_snapshot(character_id)
        payload["world_documents"] = _world_doc_snapshot(character_id)
    if scope in (SCOPE_ALL, SCOPE_STATE_ONLY):
        from xijian_api.stubs import character_state as cs_stub
        payload["character_state"] = _deepcopy_or_none(
            cs_stub.get_state(character_id)
        )
        payload["character_state_config"] = _deepcopy_or_none(
            cs_stub.get_config(character_id)
        )
        payload["character_state_log"] = _deepcopy_or_none(
            cs_stub.list_log(character_id, limit=10_000)
        )
    if scope == SCOPE_ALL:
        payload["safety_snapshot_refs"] = _safety_snapshot_refs(character_id)
    return payload


def _deepcopy_or_none(value: Any) -> Any:
    if value is None:
        return None
    import copy
    return copy.deepcopy(value)


# ---------------------------------------------------------------------------
# Backup CRUD
# ---------------------------------------------------------------------------


def _next_version(character_id: str) -> int:
    versions = [
        int(b.get("version", 0) or 0)
        for b in state.manual_backups.values()
        if b.get("character_id") == character_id
    ]
    return max(versions, default=0) + 1


def _file_name(character_id: str, version: int, now: int | None = None) -> str:
    stamp = iso_now()
    return f"{character_id}_{stamp}_v{version}.bak"


def _prune_versions(character_id: str, keep: int = MAX_VERSIONS_PER_CHARACTER) -> int:
    """Drop the oldest backups for ``character_id`` beyond ``keep``."""
    records = [
        b for b in state.manual_backups.values()
        if b.get("character_id") == character_id
    ]
    if len(records) <= keep:
        return 0
    records.sort(key=lambda r: (r.get("created_at", 0), r.get("_seq", 0)))
    dropped = 0
    for record in records[: len(records) - keep]:
        state.manual_backups.pop(record["id"], None)
        dropped += 1
    return dropped


_SEQ = 0


def _seq_next() -> int:
    global _SEQ
    _SEQ += 1
    return _SEQ


def create_backup(
    character_id: str,
    scope: str = SCOPE_ALL,
    created_by: str = "user",
    *,
    now: int | None = None,
) -> dict:
    """Create a manual backup for a character.

    * ``scope`` — one of :data:`VALID_SCOPES`.
    * ``created_by`` — ``"user"`` or ``"system"``.
    * Naming follows AC-3: ``{character_id}_{ISO8601}_v{n}.bak``.
    * Retention: at most :data:`MAX_VERSIONS_PER_CHARACTER` versions
      are kept per character; the oldest are pruned first.

    Returns the backup record.  Raises ``ValueError`` for invalid
    scope or unknown character.
    """
    if scope not in VALID_SCOPES:
        raise ValueError(
            "scope must be one of %s, got %r" % (sorted(VALID_SCOPES), scope)
        )
    if state.characters.get(character_id) is None:
        raise ValueError(f"unknown character: {character_id!r}")

    moment = now if now is not None else now_ts()
    version = _next_version(character_id)
    payload = _assemble_payload(character_id, scope)
    compressed, codec = compress_json(payload)
    backup_id = gen_manual_backup_id()
    record = {
        "id": backup_id,
        "object": "manual_backup",
        "character_id": character_id,
        "scope": scope,
        "file_path": f"manual_backups/{_file_name(character_id, version, moment)}",
        "file_name": _file_name(character_id, version, moment),
        "version": version,
        "size_bytes": len(compressed),
        "codec": codec,
        "created_at": moment,
        "created_by": created_by,
        "payload_bytes": compressed,
        "_seq": _seq_next(),
    }
    state.manual_backups[backup_id] = record

    # Update last_backup_at on the character's protected modules.
    for module in DEFAULT_PROTECTED_MODULES:
        touch_backup(character_id, module["module_name"], moment)

    # AC-3 retention.
    _prune_versions(character_id)
    # The route layer serialises records to JSON — strip the raw bytes.
    slim = {k: v for k, v in record.items() if k != "payload_bytes"}
    return slim


def list_backups(
    *,
    character_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List backup records, newest first, optionally per character."""
    out: list[dict] = []
    for record in state.manual_backups.values():
        if character_id is not None and record.get("character_id") != character_id:
            continue
        slim = {k: v for k, v in record.items() if k != "payload_bytes"}
        out.append(slim)
    out.sort(key=lambda r: (r.get("created_at", 0), r.get("_seq", 0)), reverse=True)
    if limit < 1:
        limit = 1
    return out[:limit]


def get_backup(backup_id: str) -> dict | None:
    """Return a backup record **without** the raw payload bytes."""
    record = state.manual_backups.get(backup_id)
    if record is None:
        return None
    slim = {k: v for k, v in record.items() if k != "payload_bytes"}
    return slim


def get_backup_payload(backup_id: str) -> dict | None:
    """Decompress and return the backup's payload dict (test / tooling)."""
    record = state.manual_backups.get(backup_id)
    if record is None:
        return None
    return decompress_json(record["payload_bytes"], record.get("codec", "zstd"))


def _get_backup_with_payload(backup_id: str) -> dict | None:
    return state.manual_backups.get(backup_id)


def delete_backup(backup_id: str) -> bool:
    return state.manual_backups.pop(backup_id, None) is not None


# ---------------------------------------------------------------------------
# Restore (US-A1.1-03) — optional scope
# ---------------------------------------------------------------------------


def restore_backup(
    backup_id: str,
    scope: str | None = None,
    *,
    target_character_id: str | None = None,
) -> dict:
    """Restore a backup.

    * ``scope`` — overrides the backup's stored scope.  ``None`` uses
      the backup's own scope.
    * ``target_character_id`` — restore into a different character
      than the one the backup was created for (US-A1.1-03: "恢复某一次
      备份到任意一个角色").

    Memory entries are restored by replacing the target character's
    current entries with the backup's snapshot (soft-deleted state is
    preserved verbatim).  State / docs are restored similarly.

    Returns a summary dict with the per-slice restore counts.
    """
    record = _get_backup_with_payload(backup_id)
    if record is None:
        raise KeyError(f"backup not found: {backup_id!r}")

    payload = decompress_json(record["payload_bytes"], record.get("codec", "zstd"))
    source_character = payload.get("character_id") or record["character_id"]
    target = target_character_id or source_character

    effective_scope = scope or record.get("scope") or SCOPE_ALL
    if effective_scope not in VALID_SCOPES:
        raise ValueError(
            "scope must be one of %s, got %r" % (sorted(VALID_SCOPES), effective_scope)
        )

    summary: dict[str, Any] = {
        "backup_id": backup_id,
        "source_character": source_character,
        "target_character": target,
        "scope": effective_scope,
        "restored": {},
    }

    if effective_scope in (SCOPE_ALL, SCOPE_MEMORY_ONLY):
        restored = _restore_memory(target, payload.get("memory_entries") or [])
        summary["restored"]["memory_entries"] = restored

    if effective_scope in (SCOPE_ALL, SCOPE_STATE_ONLY):
        restored = _restore_state(
            target, payload.get("character_state"),
            payload.get("character_state_config"),
            payload.get("character_state_log") or [],
        )
        summary["restored"]["character_state"] = restored

    if effective_scope in (SCOPE_ALL, SCOPE_DOC_ONLY):
        restored = _restore_docs(target, payload)
        summary["restored"]["character_documents"] = restored

    return summary


def _restore_memory(target: str, entries: list[dict]) -> dict:
    """Replace the target character's memory entries with the snapshot."""
    import copy

    removed = 0
    for entry_id in [
        eid for eid, entry in state.memory.items()
        if entry.get("character_id") == target
    ]:
        state.memory.pop(entry_id, None)
        removed += 1
    inserted = 0
    for entry in entries:
        fresh = copy.deepcopy(entry)
        fresh["character_id"] = target
        state.memory[fresh["id"]] = fresh
        inserted += 1
    return {"removed": removed, "inserted": inserted}


def _restore_state(
    target: str,
    state_record: dict | None,
    config: dict | None,
    log_entries: list[dict],
) -> dict:
    import copy
    from xijian_api.stubs import character_state as cs_stub

    restored: dict[str, int] = {}
    if state_record is not None:
        fresh = copy.deepcopy(state_record)
        fresh["character_id"] = target
        state.character_states[target] = fresh
        restored["state"] = 1
    if config is not None:
        fresh_cfg = copy.deepcopy(config)
        fresh_cfg["character_id"] = target
        state.character_state_configs[target] = fresh_cfg
        restored["config"] = 1
    log_ids = [e["id"] for e in state.character_state_log.values()
               if e.get("character_id") == target]
    for log_id in log_ids:
        state.character_state_log.pop(log_id, None)
    for entry in log_entries:
        fresh = copy.deepcopy(entry)
        fresh["character_id"] = target
        state.character_state_log[fresh["id"]] = fresh
    restored["log"] = len(log_entries)
    return restored


def _restore_docs(target: str, payload: dict) -> dict:
    import copy

    restored: dict[str, int] = {}

    char_docs = payload.get("character_documents") or {}
    char = char_docs.get("characters")
    if isinstance(char, dict) and char.get("id"):
        fresh = copy.deepcopy(char)
        fresh["id"] = target
        state.characters[target] = fresh
        restored["characters"] = 1
    for bucket_name, bucket in (
        ("character_styles", state.character_styles),
        ("character_models", state.character_models),
        ("character_motions", state.character_motions),
        ("character_voices", state.character_voices),
    ):
        snapshot = char_docs.get(bucket_name)
        if isinstance(snapshot, dict):
            bucket[target] = copy.deepcopy(snapshot)
            restored[bucket_name] = len(snapshot)

    world_docs = payload.get("world_documents") or {}
    world = world_docs.get("worlds")
    if isinstance(world, dict) and world.get("id"):
        state.worlds[world["id"]] = copy.deepcopy(world)
        restored["worlds"] = 1
    env = world_docs.get("world_environment")
    if isinstance(env, dict):
        state.world_environment[env["id"]] = copy.deepcopy(env)
        restored["world_environment"] = 1

    return restored


# ---------------------------------------------------------------------------
# Auto-backup triggers
# ---------------------------------------------------------------------------


def notify_memory_modified(character_id: str, count: int = 1) -> dict | None:
    """Count a memory mutation; cross the 50-edit threshold → auto backup.

    Returns the created backup record when the threshold was crossed,
    ``None`` otherwise.  The counter resets after the backup so the
    next 50 edits trigger again.
    """
    with _LOCK:
        current = _edit_counters.get(character_id, 0) + int(count)
        _edit_counters[character_id] = current
        if current < AUTO_BACKUP_EDIT_THRESHOLD:
            return None
        _edit_counters[character_id] = 0
    try:
        return create_backup(character_id, scope=SCOPE_ALL, created_by="system")
    except Exception as exc:  # noqa: BLE001 — trigger must never crash the caller
        _LOGGER.warning("auto backup after %d edits failed: %s", current, exc)
        return None


def notify_first_load(character_id: str) -> dict | None:
    """Trigger an automatic backup the first time a character loads."""
    with _LOCK:
        if character_id in _ever_loaded:
            return None
        _ever_loaded.add(character_id)
    try:
        return create_backup(character_id, scope=SCOPE_ALL, created_by="system")
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("auto backup on first load failed: %s", exc)
        return None


def notify_safe_termination(character_id: str | None = None) -> dict:
    """Back up after a safety-termination (A5.2 confirm_safety_stop).

    ``character_id=None`` backs up every character that has auto
    backup enabled on any protected module.  This is the hook the
    A5.2 safety-stop confirm path should call.
    """
    targets: list[str] = []
    if character_id is not None:
        targets = [character_id]
    else:
        seen: set[str] = set()
        for assoc in state.character_protected_module.values():
            if assoc.get("auto_backup") and assoc.get("character_id") not in seen:
                seen.add(assoc["character_id"])
                targets.append(assoc["character_id"])
        # Characters with a memory record but no explicit association
        # still get backed up (auto_backup defaults to on).
        for entry in state.memory.values():
            cid = entry.get("character_id")
            if cid and cid not in seen and state.characters.get(cid):
                seen.add(cid)
                targets.append(cid)

    created: list[str] = []
    for cid in targets:
        try:
            record = create_backup(cid, scope=SCOPE_ALL, created_by="system")
            created.append(record["id"])
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("safe-termination backup for %s failed: %s", cid, exc)
    return {"created": created, "count": len(created)}


# ---------------------------------------------------------------------------
# Daily scheduler (spec: 定时（每日凌晨）)
# ---------------------------------------------------------------------------


def _is_daily_backup_due(now: float | None = None) -> bool:
    """True once per day when the local clock crosses the dawn hour.

    Uses a module-level ``_last_daily_run`` date so the check is
    true only on the first tick after dawn on a new calendar day.
    """
    moment = time.localtime(now if now is not None else time.time())
    if (moment.tm_hour, moment.tm_min) < (DAILY_BACKUP_HOUR, DAILY_BACKUP_MINUTE):
        return False
    with _LOCK:
        last = getattr(_scheduler_loop, "_last_daily_run", None)  # type: ignore[attr-defined]
        today = moment.tm_yday
        if last == today:
            return False
        setattr(_scheduler_loop, "_last_daily_run", today)  # type: ignore[attr-defined]
        return True


def _run_daily_backups() -> dict:
    """Back up every character with auto-backup enabled (system)."""
    targets: list[str] = []
    seen: set[str] = set()
    for assoc in state.character_protected_module.values():
        if assoc.get("auto_backup") and assoc.get("character_id") not in seen:
            seen.add(assoc["character_id"])
            targets.append(assoc["character_id"])
    for entry in state.memory.values():
        cid = entry.get("character_id")
        if cid and cid not in seen and state.characters.get(cid):
            seen.add(cid)
            targets.append(cid)
    created: list[str] = []
    for cid in targets:
        try:
            record = create_backup(cid, scope=SCOPE_ALL, created_by="system")
            created.append(record["id"])
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("daily backup for %s failed: %s", cid, exc)
    return {"created": created, "count": len(created)}


def _scheduler_loop(stop_event: threading.Event, generation: int) -> None:
    while not stop_event.is_set():
        with _LOCK:
            if _SCHED_GENERATION != generation:
                return
        try:
            if _is_daily_backup_due():
                _run_daily_backups()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("backup scheduler tick failed: %s", exc)
        if stop_event.wait(DEFAULT_SCHEDULER_INTERVAL_SECONDS):
            break


def start_scheduler() -> dict:
    """Start the daily-backup scheduler thread (idempotent)."""
    global _SCHED_THREAD, _SCHED_GENERATION
    with _LOCK:
        if _SCHED_THREAD is not None and _SCHED_THREAD.is_alive():
            return {"started": False, "reason": "already_running"}
        if os.environ.get(_SCHEDULER_ENV_FLAG) == "0":
            return {"started": False, "reason": "disabled_by_env"}
        _SCHED_STOP.clear()
        _SCHED_GENERATION += 1
        generation = _SCHED_GENERATION
        thread = threading.Thread(
            target=_scheduler_loop,
            args=(_SCHED_STOP, generation),
            name="xijian-manual-backup-scheduler",
            daemon=True,
        )
        _SCHED_THREAD = thread
        thread.start()
    return {"started": True, "interval_s": DEFAULT_SCHEDULER_INTERVAL_SECONDS}


def stop_scheduler() -> dict:
    """Stop the daily-backup scheduler thread."""
    global _SCHED_THREAD
    with _LOCK:
        thread = _SCHED_THREAD
        if thread is None or not thread.is_alive():
            return {"stopped": False, "reason": "not_running"}
        _SCHED_STOP.set()
    thread.join(timeout=DEFAULT_SCHEDULER_INTERVAL_SECONDS * 3)
    with _LOCK:
        _SCHED_THREAD = None
    return {"stopped": True}


def scheduler_status() -> dict:
    with _LOCK:
        running = _SCHED_THREAD is not None and _SCHED_THREAD.is_alive()
    return {
        "running": running,
        "interval_s": DEFAULT_SCHEDULER_INTERVAL_SECONDS,
        "enabled_by_env": os.environ.get(_SCHEDULER_ENV_FLAG) != "0",
    }


# ---------------------------------------------------------------------------
# Seed / reset
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.  Seeds protected modules and starts
    the scheduler if the env allows (mirrors A3.2 / A4.1 / A5.4)."""
    for template in DEFAULT_PROTECTED_MODULES:
        key = _module_key(template["module_name"])
        if key not in state.protected_modules:
            record = {
                "id": key,
                "module_name": template["module_name"],
                "description": template["description"],
                "enabled": 1,
                "updated_at": now_ts(),
            }
            state.protected_modules[key] = record
    if os.environ.get(_SCHEDULER_ENV_FLAG) == "0":
        return
    start_scheduler()


def reset_for_testing() -> None:
    """Wipe manual-backup state + stop the scheduler (test hook)."""
    global _SEQ, _SCHED_GENERATION
    stop_scheduler()
    with _LOCK:
        _SEQ = 0
        _SCHED_GENERATION += 1
        _edit_counters.clear()
        _ever_loaded.clear()
    state.protected_modules.clear()
    state.character_protected_module.clear()
    state.manual_backups.clear()


__all__ = [
    # constants
    "DEFAULT_PROTECTED_MODULES", "MAX_VERSIONS_PER_CHARACTER",
    "AUTO_BACKUP_EDIT_THRESHOLD", "VALID_SCOPES",
    "SCOPE_ALL", "SCOPE_MEMORY_ONLY", "SCOPE_STATE_ONLY", "SCOPE_DOC_ONLY",
    "DAILY_BACKUP_HOUR", "DAILY_BACKUP_MINUTE",
    "DEFAULT_SCHEDULER_INTERVAL_SECONDS",
    # compression
    "compress_json", "decompress_json", "_zstd_available",
    # protected modules
    "seed_default", "list_protected_modules", "get_protected_module",
    "set_module_enabled", "get_character_protection", "set_auto_backup",
    "touch_backup",
    # backup CRUD
    "create_backup", "list_backups", "get_backup", "get_backup_payload",
    "delete_backup",
    "restore_backup",
    # triggers
    "notify_memory_modified", "notify_first_load", "notify_safe_termination",
    # scheduler
    "start_scheduler", "stop_scheduler", "scheduler_status",
    "_is_daily_backup_due", "_run_daily_backups",
    # lifecycle
    "reset_for_testing",
]
