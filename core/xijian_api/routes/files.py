"""File routes — upload, list, get, content, delete. / 文件路由 — 上传、列表、获取、内容、删除。"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import files as files_stub
from xijian_api.stubs import state
from xijian_api.utils.ids import gen_file_id
from xijian_api.utils.params import safe_header_value
from xijian_api.utils.time import now_ts


bp = Blueprint("files", __name__)


def _stream_to_bytes(source) -> bytes:
    """Stream ``source`` into a temp file, then read it back (S6).

    将 ``source`` 流式写入临时文件，再读回内存 (S6)。

    Avoids buffering the whole upload in memory: the bytes are copied
    to a temp file with :func:`shutil.copyfileobj` (bounded chunks),
    then read back.  The temp file is always removed, even on error.

    避免把整个上传缓冲在内存中：字节通过 :func:`shutil.copyfileobj`
    （有界块）拷贝到临时文件后再读回。临时文件无论是否出错都会删除。
    """
    fd, tmp_path = tempfile.mkstemp(prefix="xijian_upload_")
    os.close(fd)
    try:
        with open(tmp_path, "wb") as out:
            shutil.copyfileobj(source, out)
        return Path(tmp_path).read_bytes()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@bp.post("/v1/files")
def upload_file():
    """Upload via multipart or raw body.

    Multipart form expects ``file`` and ``purpose``. Raw bodies use
    the ``filename`` query parameter and default ``purpose="user_data"``.

    通过 multipart 或原始 body 上传。

    Multipart 表单期望 ``file`` 和 ``purpose``。原始 body 使用
    ``filename`` 查询参数并默认 ``purpose="user_data"``。
    """
    purpose = "user_data"
    filename = "upload.bin"
    payload = b""

    if request.files:
        uploaded = request.files.get("file")
        if uploaded is None:
            raise ApiError(
                400,
                "multipart `file` is required",
                "invalid_request_error",
                code="missing_file",
            )
        # S6 — stream the multipart file through a temp file instead of
        # ``uploaded.read()`` so a large upload never fully lands in
        # memory at once.  (``uploaded.stream`` is the underlying
        # file-like object of the FileStorage.)
        # S6 — 将 multipart 文件经临时文件流式落盘，而不是
        # ``uploaded.read()`` 一次性读入内存。
        # （``uploaded.stream`` 是 FileStorage 底层的文件对象。）
        payload = _stream_to_bytes(uploaded.stream)
        filename = uploaded.filename or filename
        purpose = request.form.get("purpose", purpose)
    else:
        # Raw-body upload: ``get_data`` enforces MAX_CONTENT_LENGTH
        # (413 on overflow) and caches the bytes for reuse.
        # 原始 body 上传：``get_data`` 会执行 MAX_CONTENT_LENGTH
        # （超限 413）并缓存字节供复用。
        payload = request.get_data(cache=True) or b""
        if not payload:
            raise ApiError(
                400,
                "upload body is required",
                "invalid_request_error",
                code="missing_body",
            )
        filename = request.args.get("filename", filename)
        purpose = request.args.get("purpose", purpose)

    if purpose not in {"assistants", "vision", "evals", "fine-tune", "user_data"}:
        raise ApiError(
            400,
            f"unsupported purpose: {purpose}",
            "invalid_request_error",
            code="invalid_purpose",
            param="purpose",
        )

    file_id = gen_file_id()
    record = files_stub.persist(file_id, payload, purpose=purpose, filename=filename)
    record["created_at"] = now_ts()
    response = jsonify(
        {
            "id": record["id"],
            "object": "file",
            "bytes": record["bytes_count"],
            "created_at": record["created_at"],
            "filename": record["filename"],
            "purpose": record["purpose"],
        }
    )
    response.status_code = 201
    return response


@bp.get("/v1/files")
def list_files():
    """List uploaded files. / 列出已上传的文件。"""
    return jsonify(paginate(files_stub.list_public()).to_dict())


@bp.get("/v1/files/<file_id>")
def get_file(file_id: str):
    """Retrieve file metadata by ID. / 根据 ID 检索文件元数据。"""
    record = files_stub.public_view(file_id)
    if record is None:
        raise ApiError(404, f"file not found: {file_id}", "not_found_error", code="file_not_found")
    return jsonify(record)


@bp.get("/v1/files/<file_id>/content")
def get_file_content(file_id: str):
    """Download file content. / 下载文件内容。"""
    payload = files_stub.content(file_id)
    if payload is None:
        raise ApiError(404, f"file not found: {file_id}", "not_found_error", code="file_not_found")
    record = state.files.get(file_id, {})
    # Scrub CR/LF + control chars from the filename before embedding it
    # in a response header — a hostile filename would otherwise raise
    # ValueError in Werkzeug (500) or inject extra headers.
    # 在把文件名嵌入响应头之前清除 CR/LF 和控制字符——否则恶意的
    # 文件名会在 Werkzeug 中触发 ValueError (500) 或注入额外响应头。
    filename = safe_header_value(record.get("filename", f"{file_id}.bin"))
    content_type = record.get("content_type", "application/octet-stream")
    response = Response(payload, mimetype=content_type)
    response.headers["Content-Disposition"] = f'attachment; filename="{Path(filename).name}"'
    return response


@bp.delete("/v1/files/<file_id>")
def delete_file(file_id: str):
    """Delete a file. / 删除文件。"""
    if not files_stub.delete(file_id):
        raise ApiError(404, f"file not found: {file_id}", "not_found_error", code="file_not_found")
    return ("", 204)


__all__ = ["bp"]