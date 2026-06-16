"""``/v1/xijian/resources/*`` routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.stubs import resources as resources_stub
from xijian_api.utils.ids import gen_import_job_id


bp = Blueprint("xijian_resources", __name__)


@bp.post("/v1/xijian/resources/import")
def import_resource():
    payload = request.get_json(silent=True) or {}
    if "name" not in payload:
        raise ApiError(400, "`name` is required", "invalid_request_error", code="missing_name", param="name")
    job_id = gen_import_job_id()
    resources_stub.start_import(payload, job_id)
    return jsonify({"job_id": job_id, "status": "queued"}), 202


@bp.get("/v1/xijian/resources/imports/<job_id>")
def get_import(job_id: str):
    record = resources_stub.get(job_id)
    if record is None:
        raise ApiError(404, "import job not found", "not_found_error", code="import_not_found")
    return jsonify(record)


__all__ = ["bp"]