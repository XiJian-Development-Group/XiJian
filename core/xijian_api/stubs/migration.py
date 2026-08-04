"""Legacy data migration — ``~/.xijian`` → unified CORE_ROOT.

旧数据迁移 — ``~/.xijian`` → 统一 CORE_ROOT。

Moves the legacy storage layout (``~/.xijian``) into the unified root
``~/Library/Application Support/XiJian/Core`` (overridable via
``XIJIAN_DATA_DIR``).  The migration is:

* **Idempotent** — a mark file (``.migrated_from_xijian``) is written
  once all items have been processed; a second run short-circuits.
* **Non-destructive** — the legacy directory is never deleted.
* **Conflict-aware** — existing target files with different content
  (size + mtime) are recorded as conflicts and never overwritten.
* **Best-effort** — failures are recorded in the status dict and never
  block server startup.

迁移是：
* **幂等** — 全部处理完成后写入标记文件（``.migrated_from_xijian``），
  再次运行直接短路。
* **非破坏性** — 旧目录永远不会被删除。
* **冲突感知** — 目标已存在且内容不同（size + mtime）的文件会记入冲突清单，
  绝不覆盖。
* **尽力而为** — 失败记录在状态字典中，不阻塞服务启动。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any

from xijian_api.utils.time import now_ts

_LOGGER = logging.getLogger(__name__)

#: Legacy storage root.
#: 旧存储根目录。
LEGACY_ROOT = Path(os.path.expanduser("~/.xijian"))

#: Unified storage root (CORE_ROOT) — same resolution as store.py.
#: 统一存储根目录 (CORE_ROOT) — 与 store.py 解析方式一致。
CORE_ROOT = Path(
    os.environ.get("XIJIAN_DATA_DIR")
    or "~/Library/Application Support/XiJian/Core"
).expanduser()

#: Mark file written after a successful migration.
#: 迁移成功后写入的标记文件。
MIGRATION_MARK = CORE_ROOT / ".migrated_from_xijian"

#: Items to migrate: (legacy_name, kind) where kind is "file" or "dir".
#: 待迁移项：(旧名称, 类型)，类型为 "file" 或 "dir"。
_MIGRATION_ITEMS: tuple[tuple[str, str], ...] = (
    ("xijian.db", "file"),
    ("files", "dir"),
    ("models", "dir"),
    ("snapshots", "dir"),
    ("audit", "dir"),
    ("xijian_core.json", "file"),
)

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Content comparison
# ---------------------------------------------------------------------------


def _same_content(src: Path, dst: Path) -> bool:
    """Return True when ``src`` and ``dst`` look identical (size + mtime).

    当 ``src`` 与 ``dst`` 看起来相同（size + mtime）时返回 True。
    """
    try:
        s = src.stat()
        d = dst.stat()
    except OSError:
        return False
    if s.st_size != d.st_size:
        return False
    # mtime granularity varies by filesystem; a stable byte comparison is
    # done on the rare equal-size / different-mtime case via optional sha256.
    # mtime 粒度因文件系统而异；在少见的 size 相同 / mtime 不同时可选做 sha256。
    if s.st_mtime == d.st_mtime:
        return True
    try:
        import hashlib

        return (
            hashlib.sha256(src.read_bytes()).digest()
            == hashlib.sha256(dst.read_bytes()).digest()
        )
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def migrate_legacy_data() -> dict:
    """Migrate legacy ``~/.xijian`` data into CORE_ROOT (thread-safe).

    将旧 ``~/.xijian`` 数据迁移到 CORE_ROOT（线程安全）。

    Returns a status dict — see :func:`get_migration_status`.
    返回状态字典 — 见 :func:`get_migration_status`。
    """
    with _lock:
        return _migrate_locked()


def _migrate_locked() -> dict:
    if MIGRATION_MARK.exists():
        return get_migration_status()
    if not LEGACY_ROOT.exists():
        return get_migration_status()

    items: list[dict] = []
    conflicts: list[dict] = []
    error: str | None = None

    try:
        CORE_ROOT.mkdir(parents=True, exist_ok=True)
        for name, kind in _MIGRATION_ITEMS:
            src = LEGACY_ROOT / name
            if not src.exists():
                continue
            dst = CORE_ROOT / name
            if kind == "dir":
                _migrate_dir(src, dst, items, conflicts)
            else:
                _migrate_file(src, dst, items, conflicts)
    except OSError as exc:
        error = f"{type(exc).__name__}: {exc}"
        _LOGGER.error("迁移失败: %s", error)
        # 失败不写标记 — 下次启动重试
        # Failure → do not write the mark; retried on next startup
        return _status(legacy_exists=True, migrated=False, items=items, conflicts=conflicts, error=error)

    mark = {
        "migrated_at": now_ts(),
        "items": items,
        "conflicts": conflicts,
    }
    try:
        MIGRATION_MARK.write_text(
            json.dumps(mark, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        error = f"{type(exc).__name__}: {exc}"
        _LOGGER.error("写入迁移标记失败: %s", error)
        return _status(legacy_exists=True, migrated=False, items=items, conflicts=conflicts, error=error)

    _LOGGER.info(
        "迁移完成: %d 项, %d 个冲突",
        len(items),
        len(conflicts),
    )
    return _status(legacy_exists=True, migrated=True, items=items, conflicts=conflicts, error=None)


def _migrate_file(src: Path, dst: Path, items: list[dict], conflicts: list[dict]) -> None:
    if dst.exists():
        if _same_content(src, dst):
            items.append({"item": src.name, "status": "identical"})
            return
        conflicts.append(_conflict_record(src.name, src, dst))
        items.append({"item": src.name, "status": "conflict"})
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)  # 保留 mtime / preserve mtime
    items.append({"item": src.name, "status": "copied"})


def _migrate_dir(src: Path, dst: Path, items: list[dict], conflicts: list[dict]) -> None:
    """Recursively copy a directory; never overwrite differing files.

    递归复制目录；绝不覆盖内容不同的文件。
    """
    dst.mkdir(parents=True, exist_ok=True)
    for sfile in sorted(src.rglob("*")):
        if not sfile.is_file():
            continue
        rel = sfile.relative_to(src)
        tfile = dst / rel
        item = str(rel)
        if tfile.exists():
            if _same_content(sfile, tfile):
                items.append({"item": item, "status": "identical"})
                continue
            conflicts.append(_conflict_record(item, sfile, tfile))
            items.append({"item": item, "status": "conflict"})
            continue
        tfile.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sfile, tfile)
        items.append({"item": item, "status": "copied"})


def _conflict_record(item: str, src: Path, dst: Path) -> dict:
    return {
        "conflict_id": f"conflict_{now_ts()}_{_slug_id(item)}",
        "item": item,
        "source_path": str(src),
        "target_path": str(dst),
        "source_size": src.stat().st_size,
        "source_mtime": int(src.stat().st_mtime),
        "target_size": dst.stat().st_size,
        "target_mtime": int(dst.stat().st_mtime),
        "reason": "目标已存在且内容不同 (target exists with different content)",
        "status": "pending",
    }


def _slug_id(text: str) -> str:
    """Turn a relative path into a URL/JSON-safe conflict-id fragment.

    将相对路径转换为适合 conflict_id 的安全片段。
    """
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")
    return s or "item"


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def _status(*, legacy_exists: bool, migrated: bool, items: list[dict], conflicts: list[dict], error: str | None) -> dict:
    return {
        "legacy_exists": legacy_exists,
        "migrated": migrated,
        "items": items,
        "conflicts": conflicts,
        "error": error,
    }


def get_migration_status() -> dict:
    """Return the current migration status.

    返回当前迁移状态。

    ``{legacy_exists, migrated, items, conflicts, error}``
    """
    legacy_exists = LEGACY_ROOT.exists()
    if not MIGRATION_MARK.exists():
        return _status(
            legacy_exists=legacy_exists,
            migrated=False,
            items=[],
            conflicts=[],
            error=None,
        )
    try:
        mark = json.loads(MIGRATION_MARK.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _status(
            legacy_exists=legacy_exists,
            migrated=True,
            items=[],
            conflicts=[],
            error=f"mark unreadable: {exc}",
        )
    return _status(
        legacy_exists=legacy_exists,
        migrated=True,
        items=list(mark.get("items", [])),
        conflicts=list(mark.get("conflicts", [])),
        error=None,
    )


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------


def resolve_conflict(conflict_id: str, keep: str) -> dict:
    """Resolve a recorded migration conflict.

    解决一条已记录的迁移冲突。

    * ``keep="legacy"`` — rename the target file to ``<target>.conflict-<ts>``
      then copy the legacy source over it.
      — 将目标文件改名为 ``<target>.conflict-<ts>``，再把旧源文件复制过去。
    * ``keep="new"`` — rename the legacy source file to
      ``<source>.conflict-<ts>`` (kept in the legacy dir); target untouched.
      — 将旧源文件改名为 ``<source>.conflict-<ts>``（保留在旧目录）；目标不动。

    Updates the MIGRATION_MARK conflict record.  Returns the operation
    result; missing conflicts / invalid ``keep`` return ``ok=False``.
    """
    with _lock:
        if not MIGRATION_MARK.exists():
            return {"ok": False, "error": "migration_mark_missing"}
        try:
            mark = json.loads(MIGRATION_MARK.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": f"mark unreadable: {exc}"}

        conflicts = list(mark.get("conflicts", []))
        target = None
        for c in conflicts:
            if c.get("conflict_id") == conflict_id:
                target = c
                break
        if target is None:
            return {"ok": False, "error": "conflict_not_found", "conflict_id": conflict_id}

        src = Path(target["source_path"])
        dst = Path(target["target_path"])
        ts = now_ts()

        if keep == "legacy":
            if dst.exists():
                backup = dst.with_name(f"{dst.name}.conflict-{ts}")
                dst.rename(backup)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            target["status"] = "resolved"
            target["resolved_keep"] = "legacy"
            target["resolved_at"] = ts
            target["backup_path"] = str(dst.with_name(f"{dst.name}.conflict-{ts}"))
        elif keep == "new":
            if src.exists():
                backup = src.with_name(f"{src.name}.conflict-{ts}")
                src.rename(backup)
            target["status"] = "resolved"
            target["resolved_keep"] = "new"
            target["resolved_at"] = ts
            target["backup_path"] = str(src.with_name(f"{src.name}.conflict-{ts}"))
        else:
            return {"ok": False, "error": "invalid_keep", "conflict_id": conflict_id}

        mark["conflicts"] = conflicts
        MIGRATION_MARK.write_text(
            json.dumps(mark, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"ok": True, "conflict_id": conflict_id, "keep": keep, "conflict": target}


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _set_paths_for_test(legacy_root: Path, core_root: Path) -> None:
    """Override the migration paths for testing.  Not for production use.

    为测试覆盖迁移路径。不用于生产环境。
    """
    global LEGACY_ROOT, CORE_ROOT, MIGRATION_MARK  # noqa: PLW0603
    LEGACY_ROOT = Path(legacy_root)
    CORE_ROOT = Path(core_root)
    MIGRATION_MARK = CORE_ROOT / ".migrated_from_xijian"


__all__ = [
    "LEGACY_ROOT",
    "CORE_ROOT",
    "MIGRATION_MARK",
    "migrate_legacy_data",
    "get_migration_status",
    "resolve_conflict",
    # Test helper (exported but not public API)
    "_set_paths_for_test",
]
