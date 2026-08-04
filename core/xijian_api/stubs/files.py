"""Stub file storage — kept in-memory plus a config-based byte dump.
文件存根存储 — 保存在内存中加上基于配置的字节转储。

The on-disk directory is no longer hardcoded to ``/tmp/xijian_files``;
it is resolved from the storage config (``storage.files_path``), which
defaults to ``<CORE_ROOT>/files``.  Kept lazy so module import never
touches the filesystem and the path follows the active config
(app context → ``Config.from_env()``).

磁盘目录不再硬编码为 ``/tmp/xijian_files``，而是从存储配置
(``storage.files_path``) 解析，默认 ``<CORE_ROOT>/files``。
保持惰性解析，模块导入不触碰文件系统，路径跟随当前配置
(app 上下文 → ``Config.from_env()``)。
"""

from __future__ import annotations

from pathlib import Path

from xijian_api.stubs import state


def _file_dir() -> Path:
    """Resolve the on-disk file directory from config storage.

    从配置存储解析磁盘文件目录。
    """
    try:
        from flask import current_app

        cfg = current_app.config.get("XIJIAN_CONFIG")
        if cfg is not None:
            return cfg.storage.files_path
    except Exception:
        pass
    from xijian_api.config import Config

    return Config.from_env().storage.files_path


def _public_record(record: dict) -> dict:
    """Return a JSON-safe view of ``record`` (no raw bytes, no path).
    返回 ``record`` 的 JSON 安全视图 (无原始字节，无路径)。
    """
    return {
        "id": record.get("id"),
        "object": "file",
        "bytes": record.get("bytes_count", len(record.get("bytes") or b"")),
        "created_at": record.get("created_at"),
        "filename": record.get("filename"),
        "purpose": record.get("purpose"),
    }


def persist(file_id: str, payload: bytes, *, purpose: str, filename: str) -> dict:
    """Write ``payload`` to disk and create a state record.
    将 ``payload`` 写入磁盘并创建状态记录。
    """
    target = _file_dir() / file_id
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    record = {
        "id": file_id,
        "bytes": payload,
        "path": str(target),
        "purpose": purpose,
        "filename": filename,
        "bytes_count": len(payload),
    }
    state.files[file_id] = record
    return record


def delete(file_id: str) -> bool:
    """Delete a file record and its on-disk bytes. 删除文件记录及其磁盘字节。"""
    record = state.files.pop(file_id, None)
    if record is None:
        return False
    path = record.get("path")
    if path:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
    return True


def content(file_id: str) -> bytes | None:
    """Read the raw bytes of a stored file. 读取存储文件的原始字节。"""
    record = state.files.get(file_id)
    if record is None:
        return None
    # Prefer the bytes cached in memory; fall back to disk.
    # 优先使用内存中缓存的字节；回退到磁盘。
    payload = record.get("bytes")
    if payload is not None:
        return payload
    path = record.get("path")
    if path:
        return Path(path).read_bytes()
    return None


def public_view(file_id: str) -> dict | None:
    """Return a JSON-safe dict for ``file_id`` or ``None``.
    返回 ``file_id`` 的 JSON 安全字典或 ``None``。
    """
    record = state.files.get(file_id)
    if record is None:
        return None
    return _public_record(record)


def list_public() -> list[dict]:
    """Return a JSON-safe list of every file record.
    返回每个文件记录的 JSON 安全列表。
    """
    return [_public_record(r) for r in state.files.values()]


__all__ = ["persist", "delete", "content", "public_view", "list_public"]
