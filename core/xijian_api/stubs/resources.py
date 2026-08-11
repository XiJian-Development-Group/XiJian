"""Resource import — real implementation built on the packs engine.
资源导入 — 基于包引擎的真实实现。

Replaces the old placeholder that generated a fake ``{"stub": true}``
zip.  The import pipeline is now:

1. Resolve the archive bytes (server-side ``path`` or uploaded ``file_id``).
2. Persist the bytes to files storage (the job's ``file_id``), so
   ``GET /v1/files/<id>/content`` returns the real archive.
3. Install via :func:`xijian_api.stubs.packs.install_archive`.
4. Mark the job ``completed`` with ``package_id`` + a result summary,
   or ``failed`` with a non-empty ``error``.

All work happens in a daemon thread so the HTTP request returns
immediately with a ``queued`` job.

旧版占位实现会生成伪造的 ``{"stub": true}`` zip，现已替换。导入流水线：

1. 解析归档字节（服务端 ``path`` 或已上传的 ``file_id``）。
2. 将字节持久化到文件存储（关联任务的 ``file_id``），使
   ``GET /v1/files/<id>/content`` 返回真实归档。
3. 通过 :func:`xijian_api.stubs.packs.install_archive` 安装。
4. 将任务标记为 ``completed``（带 ``package_id`` 与结果摘要），
   或 ``failed``（带非空 ``error``）。

所有工作在守护线程中完成，HTTP 请求立即返回 ``queued`` 任务。
"""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from xijian_api.runtime import default_storage_dir
from xijian_api.stubs import packs as packs_stub
from xijian_api.stubs import state
from xijian_api.stubs.files import content as files_content
from xijian_api.stubs.files import persist as files_persist
from xijian_api.utils.ids import gen_file_id
from xijian_api.utils.time import now_ts

#: 7z magic bytes — ``7z¼¯'`` (7-Zip signature).
#: 7z 魔数 — ``7z¼¯'`` (7-Zip 签名)。
_7Z_MAGIC = b"7z\xbc\xaf\x27\x1c"

#: Hard cap for a single import archive (S5).  Anything larger is
#: rejected before bytes are read into memory.
#: 单个导入归档的硬上限 (S5)。更大的文件在读取进内存前就被拒绝。
_MAX_IMPORT_BYTES = 512 * 1024 * 1024  # 512 MiB


def _validate_import_path(src_path: Path) -> Path:
    """Validate a server-side import path against the S5 whitelist.

    按 S5 白名单校验服务端导入路径。

    The resolved path must live inside the user data directory
    (``~/Library/Application Support/XiJian/Core`` by default, or the
    ``XIJIAN_DATA_DIR`` override).  ``Path.resolve()`` collapses ``..``
    segments and follows symlinks, so both ``..`` escapes and symlink
    escapes land outside the root and are rejected.  Raises
    :class:`ValueError` (turned into a job error by the caller) with
    an explicit message.

    解析后的路径必须位于用户数据目录内（默认
    ``~/Library/Application Support/XiJian/Core``，或 ``XIJIAN_DATA_DIR``
    覆盖值）。``Path.resolve()`` 会折叠 ``..`` 段并跟随符号链接，
    因此 ``..`` 逃逸与符号链接逃逸都会落到根目录之外并被拒绝。
    抛出 :class:`ValueError`（调用方转为任务错误）并携带明确消息。
    """
    data_root = default_storage_dir().resolve()
    resolved = src_path.resolve()
    if not resolved.is_relative_to(data_root):
        raise ValueError(
            f"archive path {src_path} is outside the user data directory {data_root}"
        )
    size = resolved.stat().st_size
    if size > _MAX_IMPORT_BYTES:
        raise ValueError(
            f"archive too large: {size} bytes exceeds the {_MAX_IMPORT_BYTES} byte limit"
        )
    return resolved


def _detect_archive_ext(data: bytes) -> str:
    """Detect the archive extension from magic bytes.

    根据魔数检测归档扩展名。

    Falls back to ``.zip`` for anything unrecognised — the packs
    engine validates the real format during extraction anyway.
    无法识别时回退到 ``.zip`` —— 包引擎在解压时会校验真实格式。
    """
    if data[:6] == _7Z_MAGIC:
        return ".7z"
    if data[:2] == b"PK":
        return ".zip"
    return ".zip"


def start_import(payload: dict, job_id: str) -> None:
    """Schedule an import job for ``job_id``.

    为 ``job_id`` 调度一个导入任务。

    Payload keys / 负载键:
    - ``name`` (required): display name for the job.
    - ``kind`` (optional): "character" | "world" | "mixed" — hint only;
      the pack manifest decides the actual kind.
    - ``path`` (optional): server-side path to a .7z/.zip archive.
    - ``file_id`` (optional): file id from ``/v1/files`` upload.

    Exactly one of ``path`` / ``file_id`` must be provided.
    """
    file_id = gen_file_id()
    state.import_jobs[job_id] = {
        "id": job_id,
        "object": "resource.import",
        "status": "queued",
        "kind": payload.get("kind", "character"),
        "name": payload.get("name", ""),
        "file_id": file_id,
        "created_at": now_ts(),
    }

    def _run() -> None:
        record = state.import_jobs.get(job_id)
        if record is None:
            return
        tmp_name: str | None = None
        try:
            # 1. Resolve the archive bytes.
            # 1. 解析归档字节。
            if payload.get("file_id"):
                stored = state.files.get(payload["file_id"]) or {}
                # S5 — check the recorded byte count (when known) before
                # pulling the whole payload into memory.
                # S5 — 在把整个负载读入内存前检查记录的字节数（若已知）。
                recorded_size = stored.get("bytes_count")
                if recorded_size is not None and recorded_size > _MAX_IMPORT_BYTES:
                    raise ValueError(
                        f"file {payload['file_id']!r} too large: {recorded_size} bytes "
                        f"exceeds the {_MAX_IMPORT_BYTES} byte limit"
                    )
                src_bytes = files_content(payload["file_id"])
                if src_bytes is None:
                    raise ValueError(f"file {payload['file_id']!r} not found in storage")
                filename = stored.get("filename") or f"import_{job_id}.bin"
            elif payload.get("path"):
                src_path = _validate_import_path(Path(payload["path"]))
                src_bytes = src_path.read_bytes()
                filename = src_path.name
            else:
                raise ValueError("either 'path' or 'file_id' must be provided")

            # 2. Persist the archive bytes to files storage.
            # 2. 将归档字节持久化到文件存储。
            files_persist(
                file_id,
                src_bytes,
                purpose="user_data",
                filename=filename,
            )

            # 3. Write to a temp file with the detected extension and install.
            # 3. 以检测到的扩展名写入临时文件并安装。
            ext = _detect_archive_ext(src_bytes)
            fd, tmp_name = tempfile.mkstemp(suffix=ext)
            os.close(fd)
            with open(tmp_name, "wb") as fh:
                fh.write(src_bytes)

            pack_record = packs_stub.install_archive(tmp_name)

            # 4. Mark completed.
            # 4. 标记完成。
            record["status"] = "completed"
            record["completed_at"] = now_ts()
            record["package_id"] = pack_record["package_id"]
            record["result"] = {
                "kind": pack_record["kind"],
                "loaded_characters": len(pack_record["loaded"].get("characters", [])),
                "loaded_worlds": len(pack_record["loaded"].get("worlds", [])),
                "loaded_memories": len(pack_record["loaded"].get("memories", [])),
            }
        except packs_stub.PackValidationError as exc:
            record["status"] = "failed"
            record["completed_at"] = now_ts()
            record["error"] = f"pack validation failed: {exc}"
        except Exception as exc:  # noqa: BLE001 - job failure is recorded, not raised
            record["status"] = "failed"
            record["completed_at"] = now_ts()
            record["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            # DictDB write-through only happens on ``__setitem__``; the
            # in-place mutations above must be re-assigned so the status
            # change survives a restart.  (Matches fine_tuning/batches.)
            # DictDB 仅在 ``__setitem__`` 时写透；上面的原地修改必须重新
            # 赋值才能在重启后保留。（与 fine_tuning/batches 一致。）
            state.import_jobs[job_id] = record

    threading.Thread(target=_run, daemon=True).start()


def get(job_id: str) -> dict | None:
    """Return the import job record or ``None``.

    返回导入任务记录或 ``None``。
    """
    return state.import_jobs.get(job_id)


__all__ = ["start_import", "get"]