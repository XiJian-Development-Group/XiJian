"""File routes — upload, list, get, content, delete."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import files as files_stub
from xijian_api.stubs import state
from xijian_api.utils.ids import gen_file_id
from xijian_api.utils.time import now_ts


bp = Blueprint("files", __name__)


@bp.post("/v1/files")
def upload_file():
    """Upload via multipart or raw body.

    Multipart form expects ``file`` and ``purpose``.  Raw bodies use
    the ``filename`` query parameter and default ``purpose="user_data"``.
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
    return jsonify(paginate(files_stub.list_public()).to_dict())


@bp.get("/v1/files/<file_id>")
def get_file(file_id: str):
    record = files_stub.public_view(file_id)
    if record is None:
        raise ApiError(404, f"file not found: {file_id}", "not_found_error", code="file_not_found")
    return jsonify(record)


@bp.get("/v1/files/<file_id>/content")
def get_file_content(file_id: str):
    payload = files_stub.content(file_id)
    if payload is None:
        raise ApiError(404, f"file not found: {file_id}", "not_found_error", code="file_not_found")
    record = state.files.get(file_id, {})
    filename = record.get("filename", f"{file_id}.bin")
    content_type = record.get("content_type", "application/octet-stream")
    response = Response(payload, mimetype=content_type)
    response.headers["Content-Disposition"] = f'attachment; filename="{Path(filename).name}"'
    return response


@bp.delete("/v1/files/<file_id>")
def delete_file(file_id: str):
    if not files_stub.delete(file_id):
        raise ApiError(404, f"file not found: {file_id}", "not_found_error", code="file_not_found")
    return ("", 204)


__all__ = ["bp"]