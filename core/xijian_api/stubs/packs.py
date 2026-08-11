"""Resource pack engine — install, scan, load, and manage resource packs.
资源包引擎 — 安装、扫描、加载与管理资源包。

Pack format (archive root layout / 归档根级布局):
    manifest.json
    characters/<id>/character.json          # kind=character 时必需
    characters/<id>/persona.md              # 可选
    memories/<id>/entries.json              # 可选，记忆条目列表
    worlds/<id>/world.json                  # kind=world 时必需
    worlds/<id>/world_doc.md                # 可选
    worlds/<id>/world_config.json           # 可选
    audio/** model/** motion/**             # 可选资源，保留不解析

An archive may also wrap that layout inside a single top-level directory
(DevKit submissions do this); both shapes are accepted.

归档也可将该布局包在一个单层顶层目录内（DevKit 提交即如此）；两种形态均接受。

manifest.json contract / manifest.json 契约:
{
  "schema": "xijian.pack/v1",
  "package_id": "char-yuki",       // 可选；缺省时由 name 派生（slug 化）
  "name": "Yuki",                  // 必需
  "version": "1.0.0",              // 必需
  "kind": "character"|"world"|"mixed",  // 可选；缺省时按目录内容推导
  "author": "", "description": "",      // 可选
  "dependencies": [],                   // 可选
  "created_at": "",                     // 可选 ISO8601
  "files": ["characters/char-yuki/character.json"]  // 可选
}

Compatibility / 兼容: schema 为 "xijian.devkit.submission/v1" 时也接受
（DevKit 提交归档向后兼容）；manifest 里额外的 DevKit 提交字段
（developer_id/submitted_at/ai_ratio/notes）一律忽略。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any

try:
    import py7zr as _py7zr  # noqa: E402 - guarded optional dependency

    _HAS_PY7ZR = True
except ImportError:  # pragma: no cover - depends on the install env
    _py7zr = None  # type: ignore[assignment]
    _HAS_PY7ZR = False

py7zr = _py7zr  # type: ignore[assignment]

#: Exception types treated as "corrupt archive" by :func:`extract_archive`.
#: ``py7zr.exceptions.Bad7zFile`` is only available when py7zr is installed.
#: :func:`extract_archive` 视为“归档损坏”的异常类型。仅当 py7zr
#: 已安装时 ``py7zr.exceptions.Bad7zFile`` 才可用。
if _HAS_PY7ZR:
    _ARCHIVE_ERRORS: tuple[type[Exception], ...] = (
        zipfile.BadZipFile,
        py7zr.exceptions.Bad7zFile,  # type: ignore[union-attr]
        OSError,
    )
else:
    _ARCHIVE_ERRORS = (zipfile.BadZipFile, OSError)

from xijian_api.stubs import state  # noqa: E402 - after optional dep guard
from xijian_api.utils.time import now_ts  # noqa: E402

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PackError(Exception):
    """Base exception for pack operations.

    包操作的基础异常。
    """

    def __init__(self, message: str, code: str = "pack_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class PackValidationError(PackError):
    """Raised when a pack manifest or archive structure is invalid.

    当包 manifest 或归档结构无效时抛出。
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code="pack_validation_error")


# ---------------------------------------------------------------------------
# Path resolution (lazy — mirrors files.py _file_dir)
# 路径解析（惰性 — 镜像 files.py 的 _file_dir）
# ---------------------------------------------------------------------------

_PACKS_ROOT_OVERRIDE: Path | None = None


def packs_path() -> Path:
    """Resolve the on-disk packs directory from config storage.

    从配置存储解析磁盘资源包目录。

    Priority: test override → ``current_app`` config → ``Config.from_env()``.
    优先级：测试覆盖 → ``current_app`` 配置 → ``Config.from_env()``。
    """
    if _PACKS_ROOT_OVERRIDE is not None:
        return _PACKS_ROOT_OVERRIDE
    try:
        from flask import current_app

        cfg = current_app.config.get("XIJIAN_CONFIG")
        if cfg is not None:
            return cfg.storage.packs_path
    except Exception:
        pass
    from xijian_api.config import Config

    return Config.from_env().storage.packs_path


def _set_paths_for_test(packs_root: Path | None) -> None:
    """Override the packs directory for testing.  Not for production use.

    为测试覆盖资源包目录。不用于生产环境。
    """
    global _PACKS_ROOT_OVERRIDE  # noqa: PLW0603
    _PACKS_ROOT_OVERRIDE = Path(packs_root) if packs_root is not None else None


# ---------------------------------------------------------------------------
# Archive extraction with path-traversal protection
# ---------------------------------------------------------------------------


def extract_archive(archive_path: str | Path, dest_dir: Path) -> None:
    """Extract a .7z or .zip archive to ``dest_dir``.

    将 .7z 或 .zip 归档解压到 ``dest_dir``。

    Path-traversal protection: any entry that is an absolute path,
    contains ``..``, or looks like a Windows-style path (drive letter
    or backslash) causes the entire extraction to fail; already
    extracted files are cleaned up before the error propagates.

    路径穿越防护：任何绝对路径、包含 ``..`` 或呈现 Windows 风格
    （盘符/反斜杠）的条目会使整个解压失败；错误抛出前会清理已解出的文件。

    Raises:
        PackValidationError: unsupported format, corrupt archive, or traversal.
    """
    archive_path = Path(archive_path)
    suffix = archive_path.suffix.lower()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []

    def _cleanup() -> None:
        for p in reversed(created):
            try:
                if p.is_file() or p.is_symlink():
                    p.unlink(missing_ok=True)
                elif p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass

    def _check_traversal(name: str) -> None:
        # Absolute path.
        if os.path.isabs(name):
            raise PackValidationError(f"archive entry is an absolute path: {name}")
        # Windows-style drive letter, e.g. "C:foo" / "C:/foo".
        if re.match(r"^[A-Za-z]:[\\/]", name):
            raise PackValidationError(f"archive entry has a drive letter: {name}")
        # Backslash path traversal (Windows separators).
        if "\\" in name:
            raise PackValidationError(f"archive entry uses backslash separators: {name}")
        # Path traversal via '..'.
        parts = Path(name).parts
        if any(p == ".." for p in parts):
            raise PackValidationError(f"archive entry contains '..': {name}")

    try:
        if suffix == ".7z":
            if not _HAS_PY7ZR:
                raise PackError(
                    "处理 7z 包需要安装 py7zr：pip install py7zr",
                    code="pack_py7zr_missing",
                )
            with py7zr.SevenZipFile(archive_path, mode="r") as z:
                for info in z.list():
                    name = info.filename
                    _check_traversal(name)
                    # Reject symlink entries outright — a later entry could
                    # otherwise write *through* the symlink outside dest_dir.
                    # 直接拒绝符号链接条目 — 否则后续条目可能借由符号链接写到 dest_dir 之外。
                    if getattr(info, "is_symlink", False):
                        raise PackValidationError(f"archive entry is a symlink: {name}")
                z.extractall(dest_dir)
            created.append(dest_dir)
        elif suffix == ".zip":
            with zipfile.ZipFile(archive_path, "r") as z:
                for info in z.infolist():
                    name = info.filename
                    _check_traversal(name)
                    # Reject symlink entries (S_IFLNK in the Unix mode bits).
                    # 拒绝符号链接条目（Unix 模式位中的 S_IFLNK）。
                    if stat.S_ISLNK(info.external_attr >> 16):
                        raise PackValidationError(f"archive entry is a symlink: {name}")
                z.extractall(dest_dir)
            created.append(dest_dir)
        else:
            raise PackValidationError(
                f"unsupported archive format: {suffix!r} (only .7z/.zip are supported)"
            )
    except PackValidationError:
        _cleanup()
        raise
    except _ARCHIVE_ERRORS as exc:
        _cleanup()
        raise PackValidationError(f"corrupt archive: {exc}") from exc


def _locate_pack_dir(extracted: Path) -> Path:
    """Locate the pack root inside an extraction.

    在解压结果中定位包根目录。

    Either the extraction root itself (manifest.json at root) or the
    single wrapping top-level directory.  Anything else is rejected.
    要么是解压根本身（manifest.json 在根部），要么是唯一的顶层包装目录。其他一律拒绝。
    """
    if (extracted / "manifest.json").is_file():
        return extracted
    # macOS Finder/Archive Utility zips carry a ``__MACOSX`` sidecar dir —
    # ignore it when locating the single wrapper dir.
    # macOS 压缩工具产生的 zip 常带 ``__MACOSX`` 附带目录 — 定位单层包装目录时忽略它。
    subdirs = [
        d for d in extracted.iterdir()
        if d.is_dir() and d.name != "__MACOSX"
    ]
    if len(subdirs) == 1 and (subdirs[0] / "manifest.json").is_file():
        return subdirs[0]
    raise PackValidationError(
        "archive must contain manifest.json at its root or inside a single top-level directory"
    )


# ---------------------------------------------------------------------------
# Manifest reading & validation
# ---------------------------------------------------------------------------


def read_manifest(pack_dir: Path) -> dict:
    """Read and parse manifest.json from a pack directory.

    从包目录读取并解析 manifest.json。

    Raises:
        PackValidationError: manifest missing or invalid JSON.
    """
    manifest_path = Path(pack_dir) / "manifest.json"
    if not manifest_path.is_file():
        raise PackValidationError(f"manifest.json not found in {pack_dir}")
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackValidationError(f"invalid JSON in manifest: {exc}") from exc


def _derive_kind(pack_dir: Path) -> str:
    """Derive the pack kind from the directory layout.

    根据目录布局推导包类型。

    Only ``characters/`` → ``character``; only ``worlds/`` → ``world``;
    both (or neither) → ``mixed``.
    只有 ``characters/`` → ``character``；只有 ``worlds/`` → ``world``；
    两者都有（或都没有）→ ``mixed``。
    """
    has_chars = (pack_dir / "characters").is_dir()
    has_worlds = (pack_dir / "worlds").is_dir()
    if has_chars and not has_worlds:
        return "character"
    if has_worlds and not has_chars:
        return "world"
    return "mixed"


def validate_manifest(manifest: dict, pack_dir: Path | None = None) -> dict:
    """Validate and normalise a manifest dict.

    校验并规范化 manifest 字典。

    * schema must be ``xijian.pack/v1`` or ``xijian.devkit.submission/v1``.
    * ``name`` / ``version`` are required; ``kind`` is required unless it
      can be derived from ``pack_dir`` layout.
    * ``package_id`` is derived from ``name`` when missing; must match
      ``^[a-z0-9][a-z0-9._-]*$`` (slugified from name otherwise).
    * DevKit submission extra fields are stripped (ignored silently).

    Returns the normalised manifest with derived/fallback fields filled in.
    返回填充了派生/回退字段的规范化 manifest。

    Raises:
        PackValidationError: schema/required fields/kind/package_id invalid.
    """
    if not isinstance(manifest, dict):
        raise PackValidationError("manifest must be a JSON object")

    schema = manifest.get("schema")
    if schema not in ("xijian.pack/v1", "xijian.devkit.submission/v1"):
        raise PackValidationError(
            f"unsupported schema: {schema!r} (expected xijian.pack/v1 or xijian.devkit.submission/v1)"
        )

    name = manifest.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        raise PackValidationError("manifest.name is required and must be a non-empty string")

    version = manifest.get("version")
    if not version or not isinstance(version, str) or not version.strip():
        raise PackValidationError("manifest.version is required and must be a string")

    kind = manifest.get("kind")
    if kind in (None, ""):
        if pack_dir is not None:
            kind = _derive_kind(pack_dir)
        else:
            raise PackValidationError(
                "manifest.kind is required (and cannot be derived without the pack directory)"
            )
    if kind not in ("character", "world", "mixed"):
        raise PackValidationError(
            f"manifest.kind must be 'character', 'world', or 'mixed', got {kind!r}"
        )

    # package_id: explicit → validate → slugify-from-name on failure.
    package_id = manifest.get("package_id")
    if not package_id:
        package_id = _slugify(name)
    if not re.match(r"^[a-z0-9][a-z0-9._-]*$", package_id):
        package_id = _slugify(name)
    if not re.match(r"^[a-z0-9][a-z0-9._-]*$", package_id):
        raise PackValidationError(
            f"package_id {package_id!r} is invalid; must match ^[a-z0-9][a-z0-9._-]*$"
        )

    norm = dict(manifest)
    norm["package_id"] = package_id
    norm["name"] = name.strip()
    norm["version"] = version.strip()
    norm["kind"] = kind
    # Strip DevKit submission extra fields (ignored silently).
    for key in ("developer_id", "submitted_at", "ai_ratio", "notes"):
        norm.pop(key, None)
    return norm


def _slugify(text: str) -> str:
    """Convert a string to a valid package_id slug.

    将字符串转换为合法的 package_id slug。
    """
    s = str(text).strip().lower()
    s = re.sub(r"[^a-z0-9._-]+", "-", s)
    s = re.sub(r"-+", "-", s)
    s = s.strip("-._")
    if not s:
        s = "pack"
    if not re.match(r"^[a-z0-9]", s):
        s = "p-" + s
    return s


# ---------------------------------------------------------------------------
# Runtime loading (mirrors devkit's _load_character_impl / _load_world_impl)
# ---------------------------------------------------------------------------

_SOURCE_TAG = "_pack_source"
_ORIGINAL_ID_TAG = "_pack_id"


def _load_pack_into_runtime(pack_dir: Path, manifest: dict) -> dict:
    """Load a validated pack's contents into the core runtime state.

    将已校验包的内容加载到核心运行时状态中。

    Mirrors devkit's ``_load_character_impl`` / ``_load_world_impl`` but
    sources data from the pack directory and tags every record with
    ``_pack_source=True`` / ``_pack_id=<package_id>``.  Records from the
    same pack with the same core id are replaced (not duplicated).

    镜像 devkit 的 ``_load_character_impl`` / ``_load_world_impl``，但数据来自
    包目录，且每条记录标记 ``_pack_source=True`` / ``_pack_id=<package_id>``。
    同包同核心 id 的旧记录会被替换（不重复）。

    Returns a record summarising what was loaded.
    返回汇总已加载内容的记录。
    """
    pack_id = manifest["package_id"]
    kind = manifest["kind"]
    now = now_ts()
    loaded: dict[str, list[str]] = {"characters": [], "worlds": [], "memories": []}

    # --- Characters --------------------------------------------------------
    if kind in ("character", "mixed"):
        chars_dir = Path(pack_dir) / "characters"
        if chars_dir.is_dir():
            for char_dir in sorted(chars_dir.iterdir()):
                if not char_dir.is_dir():
                    continue
                char_json_path = char_dir / "character.json"
                if not char_json_path.is_file():
                    continue
                try:
                    raw = json.loads(char_json_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as exc:
                    _LOGGER.warning(
                        "pack %s: skip unreadable character.json in %s: %s",
                        pack_id, char_dir, exc,
                    )
                    continue

                # Optional persona.md.
                persona_path = char_dir / "persona.md"
                persona_doc = (
                    persona_path.read_text(encoding="utf-8")
                    if persona_path.is_file()
                    else raw.get("persona_doc", "")
                )

                rid = str(raw.get("id") or char_dir.name)
                # Replace any previously loaded record from the same pack.
                _remove_pack_records("character", pack_id, item_id=rid)

                record: dict[str, Any] = {
                    "id": rid,
                    "object": "character",
                    "name": raw.get("name", "Unnamed"),
                    "display_name": raw.get("display_name") or raw.get("name", "Unnamed"),
                    "description": raw.get("description", ""),
                    "persona_doc": persona_doc,
                    "voice_profile": raw.get("voice_profile"),
                    "default_emotion": raw.get("default_emotion", "neutral"),
                    "language_style": raw.get("language_style", ""),
                    "tags": list(raw.get("tags", [])),
                    "models": list(raw.get("models", [])),
                    "assigned_memory_pack": raw.get("assigned_memory_pack", ""),
                    "assigned_voice_pack": raw.get("assigned_voice_pack", ""),
                    "assigned_model": raw.get("assigned_model", ""),
                    "assigned_world": raw.get("assigned_world", ""),
                    "character_config": raw.get("character_config", {}),
                    "loaded": True,
                    "created_at": raw.get("created_at", now),
                    "updated_at": now,
                    _SOURCE_TAG: True,
                    _ORIGINAL_ID_TAG: pack_id,
                }
                state.characters[rid] = record
                loaded["characters"].append(rid)

                # Memory config.
                mem_cfg = raw.get("memory_config")
                if mem_cfg and isinstance(mem_cfg, dict):
                    cfg = dict(mem_cfg)
                    cfg["character_id"] = rid
                    cfg["updated_at"] = now
                    cfg[_SOURCE_TAG] = True
                    cfg[_ORIGINAL_ID_TAG] = pack_id
                    state.memory_configs[rid] = cfg

                # Character state config (inside character_config.state_config).
                state_cfg = raw.get("character_config", {}).get("state_config", {})
                if state_cfg and isinstance(state_cfg, dict):
                    sc = dict(state_cfg)
                    sc["character_id"] = rid
                    sc.setdefault("hunger_decay_per_hour", 2.0)
                    sc.setdefault("thirst_decay_per_hour", 3.0)
                    sc.setdefault("health_decay_per_hour", 0.1)
                    sc.setdefault("mood_decay_per_hour", 1.0)
                    sc.setdefault("low_hunger_threshold", 30.0)
                    sc.setdefault("low_mood_threshold", 20.0)
                    sc[_SOURCE_TAG] = True
                    sc[_ORIGINAL_ID_TAG] = pack_id
                    state.character_state_configs[rid] = sc

                # Memories from memories/<id>/entries.json.
                mem_path = Path(pack_dir) / "memories" / char_dir.name / "entries.json"
                if mem_path.is_file():
                    try:
                        mem_entries = json.loads(mem_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError) as exc:
                        _LOGGER.warning(
                            "pack %s: unreadable entries.json at %s: %s",
                            pack_id, mem_path, exc,
                        )
                        mem_entries = None
                    if isinstance(mem_entries, list):
                        for entry in mem_entries:
                            if not isinstance(entry, dict):
                                continue
                            entry["character_id"] = rid
                            eid = entry.get("id") or f"mem_{char_dir.name}_{len(state.memory)}"
                            entry["id"] = eid
                            entry.setdefault("type", "long")
                            entry.setdefault("source", "pack_initial")
                            entry.setdefault("importance", 0.6)
                            entry[_SOURCE_TAG] = True
                            entry[_ORIGINAL_ID_TAG] = pack_id
                            state.memory[eid] = entry
                            loaded["memories"].append(eid)

    # --- Worlds ------------------------------------------------------------
    if kind in ("world", "mixed"):
        worlds_dir = Path(pack_dir) / "worlds"
        if worlds_dir.is_dir():
            for world_dir in sorted(worlds_dir.iterdir()):
                if not world_dir.is_dir():
                    continue
                world_json_path = world_dir / "world.json"
                if not world_json_path.is_file():
                    continue
                try:
                    raw = json.loads(world_json_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as exc:
                    _LOGGER.warning(
                        "pack %s: skip unreadable world.json in %s: %s",
                        pack_id, world_dir, exc,
                    )
                    continue

                # Optional world_doc.md.
                doc_path = world_dir / "world_doc.md"
                world_doc = (
                    doc_path.read_text(encoding="utf-8")
                    if doc_path.is_file()
                    else raw.get("world_doc", "")
                )

                # Optional world_config.json.
                wc_path = world_dir / "world_config.json"
                world_config = {}
                if wc_path.is_file():
                    try:
                        world_config = json.loads(wc_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError) as exc:
                        _LOGGER.warning(
                            "pack %s: unreadable world_config.json at %s: %s",
                            pack_id, wc_path, exc,
                        )
                else:
                    world_config = raw.get("config", {}) or {}

                wid = str(raw.get("id") or world_dir.name)
                # Replace any previously loaded record from the same pack.
                _remove_pack_records("world", pack_id, item_id=wid)

                record = {
                    "id": wid,
                    "name": raw.get("name", "Unnamed World"),
                    "world_doc": world_doc,
                    "world_doc_path": str(doc_path) if doc_path.is_file() else None,
                    "config": world_config,
                    "config_path": str(wc_path) if wc_path.is_file() else None,
                    "state_doc_path": None,
                    "is_active": False,
                    "last_active_at": None,
                    "created_at": raw.get("created_at", now),
                    "updated_at": now,
                    _SOURCE_TAG: True,
                    _ORIGINAL_ID_TAG: pack_id,
                }
                state.worlds[wid] = record
                loaded["worlds"].append(wid)

                # Init world_environment with defaults overlaid from config.
                env = {
                    "weather": "sunny",
                    "time_of_day": 360,  # 06:00 default
                    "light_level": 0.8,
                    "ambient_audio": None,
                    "env_meta": json.dumps(world_config) if world_config else "{}",
                    _SOURCE_TAG: True,
                    _ORIGINAL_ID_TAG: pack_id,
                }
                state.world_environment[wid] = env

                # Init compute config.
                state.world_compute_config[wid] = {
                    "world_id": wid,
                    "total_token_budget": 50_000,
                    "active_tier": "low_active",
                    "max_npcs": 50,
                    "max_active_npcs": 3,
                    "updated_at": now,
                    _SOURCE_TAG: True,
                    _ORIGINAL_ID_TAG: pack_id,
                }

                # Auto-generate basic NPCs for the world (mirrors devkit).
                try:
                    from xijian_api.stubs import npcs as npcs_stub
                    npcs_stub.auto_generate_npcs(wid, count=5)
                except Exception as exc:  # noqa: BLE001 - best-effort
                    _LOGGER.warning("auto_generate_npcs failed for world %s: %s", wid, exc)

    _LOGGER.info("Loaded pack %s (%s) into runtime: %s", pack_id, kind, loaded)
    return {
        "package_id": pack_id,
        "kind": kind,
        "name": manifest["name"],
        "version": manifest["version"],
        "loaded": loaded,
        "path": str(pack_dir),
        "manifest": manifest,
    }


def _remove_pack_records(kind: str, pack_id: str, item_id: str | None = None) -> None:
    """Remove existing runtime records from ``pack_id`` (optionally scoped
    to a single core ``item_id``).

    移除 ``pack_id`` 的既有运行时记录（可限定单个核心 ``item_id``）。
    """
    if kind == "character":
        bucket, sidecars = state.characters, (
            state.memory_configs, state.character_state_configs,
        )
    elif kind == "world":
        bucket, sidecars = state.worlds, (
            state.world_environment, state.world_compute_config,
        )
    else:
        return
    for rid, rec in list(bucket.items()):
        if not (rec.get(_SOURCE_TAG) and rec.get(_ORIGINAL_ID_TAG) == pack_id):
            continue
        if item_id is not None and rid != item_id:
            continue
        del bucket[rid]
        for sidecar in sidecars:
            sidecar.pop(rid, None)


def unload_pack(package_id: str) -> None:
    """Unload all runtime records belonging to a pack (files untouched).

    卸载属于某个包的所有运行时标记记录（不删除文件）。

    Clears every pack-tagged record from characters / worlds / memories /
    memory_configs / character_state_configs / world_environment /
    world_compute_config.
    清空 characters / worlds / memories / memory_configs /
    character_state_configs / world_environment / world_compute_config 中
    所有带该包标记的记录。
    """
    buckets = [
        state.characters, state.worlds, state.memory,
        state.memory_configs, state.character_state_configs,
        state.world_environment, state.world_compute_config,
    ]
    removed = 0
    for bucket in buckets:
        for key, rec in list(bucket.items()):
            if isinstance(rec, dict) and rec.get(_SOURCE_TAG) and rec.get(_ORIGINAL_ID_TAG) == package_id:
                del bucket[key]
                removed += 1
    if removed:
        _LOGGER.info("Unloaded pack %s from runtime (%d records)", package_id, removed)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _index_record(pack_dir: Path, manifest: dict) -> dict:
    """Build the packs_index entry for an installed pack.

    为已安装包构建 packs_index 条目。
    """
    return {
        "kind": manifest["kind"],
        "name": manifest["name"],
        "version": manifest["version"],
        "path": str(pack_dir),
        "manifest": manifest,
        "loaded": True,
    }


def install_archive(archive_path: str | Path) -> dict:
    """Install a pack from an archive file.

    从归档文件安装资源包。

    Steps / 步骤:
    1. Extract to a temp directory under packs_path.
    2. Locate the pack root (root-level manifest or single wrapper dir),
       read & validate the manifest (kind derived from layout if absent).
    3. If ``<packs_path>/<package_id>/`` already exists, unload + overwrite
       (idempotent replace).
    4. Move the pack root into its final location.
    5. Load into the runtime and update ``state.packs_index``.

    Any failure cleans up the half-extracted / half-moved directory.

    Returns the pack record.
    返回包记录。
    """
    archive_path = Path(archive_path)
    if not archive_path.is_file():
        raise PackValidationError(f"archive not found: {archive_path}")

    packs_root = packs_path()
    packs_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="pack_install_", dir=packs_root) as tmp:
        tmp_path = Path(tmp)
        extract_archive(archive_path, tmp_path)
        pack_dir = _locate_pack_dir(tmp_path)

        manifest = read_manifest(pack_dir)
        manifest = validate_manifest(manifest, pack_dir=pack_dir)
        package_id = manifest["package_id"]

        final_dir = packs_root / package_id
        if final_dir.exists():
            unload_pack(package_id)
            shutil.rmtree(final_dir, ignore_errors=True)
        final_dir.mkdir(parents=True, exist_ok=True)

        # Move the pack root's *contents* into the final dir.  Handles both
        # root-level layouts (``pack_dir == tmp_path``) and single-wrapper-dir
        # layouts (DevKit submissions) without leaving a nested wrapper layer:
        # ``<packs_root>/<package_id>/manifest.json`` in both cases.
        # 将包根目录的*内容*移入最终目录；兼容根级布局（``pack_dir == tmp_path``）
        # 与单层包装目录布局（DevKit 提交），不会留下嵌套包装层：
        # 两种情形下都是 ``<packs_root>/<package_id>/manifest.json``。
        for child in list(pack_dir.iterdir()):
            shutil.move(str(child), str(final_dir))

    loaded_record = _load_pack_into_runtime(final_dir, manifest)
    state.packs_index[package_id] = _index_record(final_dir, manifest)
    return loaded_record


def install_pack_dir(pack_dir: Path) -> dict:
    """Install a pack from an already-extracted directory.

    从已解压的目录安装资源包（供预置与目录投放使用）。

    The directory is copied into ``<packs_path>/<package_id>/`` and
    loaded into the runtime.
    目录被复制到 ``<packs_path>/<package_id>/`` 并加载进运行时。
    """
    pack_dir = Path(pack_dir)
    if not pack_dir.is_dir():
        raise PackValidationError(f"pack directory not found: {pack_dir}")

    manifest = read_manifest(pack_dir)
    manifest = validate_manifest(manifest, pack_dir=pack_dir)
    package_id = manifest["package_id"]

    packs_root = packs_path()
    packs_root.mkdir(parents=True, exist_ok=True)

    final_dir = packs_root / package_id
    if final_dir.exists():
        unload_pack(package_id)
        shutil.rmtree(final_dir, ignore_errors=True)
    shutil.copytree(pack_dir, final_dir)

    loaded_record = _load_pack_into_runtime(final_dir, manifest)
    state.packs_index[package_id] = _index_record(final_dir, manifest)
    return loaded_record


def scan_packs() -> dict:
    """Scan ``<packs_path>/*/`` and rebuild ``state.packs_index``.

    扫描 ``<packs_path>/*/`` 并重建 ``state.packs_index``。

    Every installed pack is loaded into the runtime; records belonging
    to the same ``package_id`` are unloaded first (idempotent reload).
    Packs whose directory name differs from the manifest package_id are
    rejected (the directory name is the canonical key).

    Returns ``{"installed": [records...], "errors": [...]}``.
    """
    packs_root = packs_path()
    if not packs_root.is_dir():
        return {"installed": [], "errors": []}

    # Drop index entries whose directory no longer exists.
    for pid in list(state.packs_index):
        rec = state.packs_index[pid]
        if not Path(rec.get("path", "")).is_dir():
            del state.packs_index[pid]

    installed: list[dict] = []
    errors: list[dict] = []

    for entry in sorted(packs_root.iterdir()):
        if not entry.is_dir():
            continue
        # Skip leftover install temp dirs (``pack_install_*``) — a crashed
        # or concurrent install must not surface as a scan error.
        # 跳过残留的安装临时目录（``pack_install_*``）— 崩溃或并发安装
        # 不应作为扫描错误出现。
        if entry.name.startswith("pack_install_"):
            continue
        package_id = entry.name
        try:
            manifest = read_manifest(entry)
            manifest = validate_manifest(manifest, pack_dir=entry)
            if manifest["package_id"] != package_id:
                raise PackValidationError(
                    f"directory name {package_id!r} does not match manifest package_id {manifest['package_id']!r}"
                )
            # Idempotent reload: drop old runtime records first.
            unload_pack(package_id)
            loaded_record = _load_pack_into_runtime(entry, manifest)
            state.packs_index[package_id] = _index_record(entry, manifest)
            installed.append(loaded_record)
        except PackValidationError as exc:
            _LOGGER.warning("scan_packs: skipping %s: %s", package_id, exc)
            errors.append({"package_id": package_id, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("scan_packs: failed on %s: %s", package_id, exc)
            errors.append({"package_id": package_id, "error": f"{type(exc).__name__}: {exc}"})

    return {"installed": installed, "errors": errors}


def uninstall_pack(package_id: str) -> dict:
    """Uninstall a pack: unload runtime records, delete the directory, and
    drop the index entry.

    卸载资源包：卸载运行时记录、删除包目录、移除索引条目。

    Returns the removed pack record (with ``package_id`` merged in).
    返回被删除的包记录（已合并 ``package_id``）。

    Raises:
        PackValidationError: pack is not installed.
    """
    record = state.packs_index.get(package_id)
    if record is None:
        raise PackValidationError(f"pack {package_id!r} is not installed")
    record = dict(record)
    record["package_id"] = package_id

    unload_pack(package_id)

    pack_dir = Path(record["path"])
    if pack_dir.exists():
        shutil.rmtree(pack_dir, ignore_errors=True)

    del state.packs_index[package_id]
    return record


def list_packs() -> list[dict]:
    """Return a JSON-safe list of every installed pack record.

    返回每个已安装包记录的 JSON 安全列表。
    """
    return [
        {"package_id": pid, **dict(rec)}
        for pid, rec in sorted(state.packs_index.items())
    ]


def get_pack(package_id: str) -> dict | None:
    """Return a single pack record (with ``package_id``) or ``None``.

    返回单个包记录（含 ``package_id``）或 ``None``。
    """
    rec = state.packs_index.get(package_id)
    if rec is None:
        return None
    return {"package_id": package_id, **dict(rec)}


def ensure_preload_packs() -> dict:
    """Install preload packs from the preload directory (idempotent).

    从预置目录安装预置包（幂等）。

    Preload directory resolution order / 预置目录解析顺序:
    1. env ``XIJIAN_PRELOAD_PACKS_DIR``
    2. frozen mode ``<exe_dir>/preload_packs``
    3. dev mode ``core/preload_packs/`` (located via ``xijian_api.__file__``)

    Every ``.7z`` / ``.zip`` archive or already-extracted directory is
    attempted; packs whose ``package_id`` + ``version`` are already
    installed are skipped.

    Returns ``{"installed": [...], "skipped": [...], "errors": [...]}``.
    """
    preload_dir = _resolve_preload_dir()
    if not preload_dir or not preload_dir.is_dir():
        return {"installed": [], "skipped": [], "errors": []}

    installed: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []

    for entry in sorted(preload_dir.iterdir()):
        try:
            if entry.is_file() and entry.suffix.lower() in (".7z", ".zip"):
                with tempfile.TemporaryDirectory(prefix="preload_probe_") as tmp:
                    tmp_path = Path(tmp)
                    extract_archive(entry, tmp_path)
                    pack_dir = _locate_pack_dir(tmp_path)
                    manifest = read_manifest(pack_dir)
                    manifest = validate_manifest(manifest, pack_dir=pack_dir)
                    pid = manifest["package_id"]
                    version = manifest["version"]

                    existing = state.packs_index.get(pid)
                    if existing and existing.get("version") == version:
                        skipped.append({"package_id": pid, "version": version, "reason": "already installed"})
                        continue

                    record = install_archive(entry)
                    installed.append(record)
            elif entry.is_dir():
                manifest = read_manifest(entry)
                manifest = validate_manifest(manifest, pack_dir=entry)
                pid = manifest["package_id"]
                version = manifest["version"]

                existing = state.packs_index.get(pid)
                if existing and existing.get("version") == version:
                    skipped.append({"package_id": pid, "version": version, "reason": "already installed"})
                    continue

                record = install_pack_dir(entry)
                installed.append(record)
        except PackValidationError as exc:
            errors.append({"item": entry.name, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            errors.append({"item": entry.name, "error": f"{type(exc).__name__}: {exc}"})

    return {"installed": installed, "skipped": skipped, "errors": errors}


def _resolve_preload_dir() -> Path | None:
    """Resolve the preload packs directory per the priority order.

    按优先级顺序解析预置包目录。
    """
    # 1. Environment variable.
    env_dir = os.environ.get("XIJIAN_PRELOAD_PACKS_DIR")
    if env_dir:
        p = Path(env_dir).expanduser()
        if p.is_dir():
            return p

    # 2. Frozen mode: <exe_dir>/preload_packs.
    try:
        from xijian_api.runtime import is_frozen, executable_dir

        if is_frozen():
            p = executable_dir() / "preload_packs"
            if p.is_dir():
                return p
    except Exception:  # noqa: BLE001
        pass

    # 3. Dev mode: core/preload_packs/ via xijian_api.__file__.
    try:
        import xijian_api

        api_root = Path(xijian_api.__file__).resolve().parent.parent
        p = api_root / "preload_packs"
        if p.is_dir():
            return p
    except Exception:  # noqa: BLE001
        pass

    return None


__all__ = [
    "PackError",
    "PackValidationError",
    "packs_path",
    "_set_paths_for_test",
    "extract_archive",
    "read_manifest",
    "validate_manifest",
    "install_archive",
    "install_pack_dir",
    "scan_packs",
    "uninstall_pack",
    "list_packs",
    "get_pack",
    "ensure_preload_packs",
    "unload_pack",
    "_load_pack_into_runtime",
    "_remove_pack_records",
    "_SOURCE_TAG",
    "_ORIGINAL_ID_TAG",
]