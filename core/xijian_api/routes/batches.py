"""OAI batches routes."""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import batches as batches_stub
from xijian_api.stubs import files as files_stub
from xijian_api.stubs import state
from xijian_api.utils.ids import gen_batch_id
from xijian_api.utils.time import now_ts


bp = Blueprint("batches", __name__)


@bp.post("/v1/batches")
def create_batch():
    payload = request.get_json(silent=True) or {}
    if "input_file_id" not in payload:
        raise ApiError(
            400,
            "`input_file_id` is required",
            "invalid_request_error",
            code="missing_input_file_id",
            param="input_file_id",
        )
    batch_id = gen_batch_id()
    record = {
        "id": batch_id,
        "object": "batch",
        "endpoint": payload.get("endpoint", "/v1/chat/completions"),
        "input_file_id": payload["input_file_id"],
        "completion_window": payload.get("completion_window", "24h"),
        "status": "validating",
        "created_at": now_ts(),
        "metadata": payload.get("metadata", {}),
        "request_counts": {"total": 0, "completed": 0, "failed": 0},
    }
    state.batches[batch_id] = record
    batches_stub.schedule_completion(batch_id)
    return jsonify(record)


@bp.get("/v1/batches/<batch_id>")
def get_batch(batch_id: str):
    record = state.batches.get(batch_id)
    if record is None:
        raise ApiError(404, f"batch not found: {batch_id}", "not_found_error", code="batch_not_found")
    return jsonify(record)


@bp.get("/v1/batches")
def list_batches():
    return jsonify(paginate(list(state.batches.values())).to_dict())


@bp.get("/v1/batches/<batch_id>/results")
def batch_results(batch_id: str):
    record = state.batches.get(batch_id)
    if record is None:
        raise ApiError(404, f"batch not found: {batch_id}", "not_found_error", code="batch_not_found")
    file_id = record.get("output_file_id")
    if not file_id:
        return Response(b"", mimetype="application/x-ndjson")
    payload = files_stub.content(file_id) or b""
    return Response(payload, mimetype="application/x-ndjson")


@bp.post("/v1/batches/<batch_id>/cancel")
def cancel_batch(batch_id: str):
    record = state.batches.get(batch_id)
    if record is None:
        raise ApiError(404, f"batch not found: {batch_id}", "not_found_error", code="batch_not_found")
    record["status"] = "cancelled"
    record["cancelled_at"] = now_ts()
    return jsonify(record)


__all__ = ["bp"]