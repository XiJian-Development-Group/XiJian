"""Stub file storage — kept in-memory plus a temp-dir byte dump.
文件存根存储 — 保存在内存中加上临时目录字节转储。
"""

from __future__ import annotations

from pathlib import Path
import tempfile

from xijian_api.stubs import state


_FILE_DIR = Path(tempfile.gettempdir()) / "xijian_files"
_FILE_DIR.mkdir(parents=True, exist_ok=True)


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
    target = _FILE_DIR / file_id
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