"""File routes — upload, list, get, content, delete. / 文件路由 — 上传、列表、获取、内容、删除。"""

from __future__ import annotations

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
        payload = uploaded.read()
        filename = uploaded.filename or filename
        purpose = request.form.get("purpose", purpose)
    else:
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