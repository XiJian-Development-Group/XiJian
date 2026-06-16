"""OAI fine-tuning routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import fine_tuning as ft_stub
from xijian_api.stubs import state
from xijian_api.utils.ids import gen_fine_tuning_job_id
from xijian_api.utils.time import now_ts


bp = Blueprint("fine_tuning", __name__)


@bp.post("/v1/fine_tuning/jobs")
def create_job():
    payload = request.get_json(silent=True) or {}
    if "model" not in payload:
        raise ApiError(
            400,
            "`model` is required",
            "invalid_request_error",
            code="missing_model",
            param="model",
        )
    if "training_file" not in payload:
        raise ApiError(
            400,
            "`training_file` is required",
            "invalid_request_error",
            code="missing_training_file",
            param="training_file",
        )
    job_id = gen_fine_tuning_job_id()
    record = {
        "id": job_id,
        "object": "fine_tuning.job",
        "created_at": now_ts(),
        "fine_tuned_model": None,
        "finished_at": None,
        "model": payload["model"],
        "organization_id": "xijian-local",
        "result_files": [],
        "status": "queued",
        "training_file": payload["training_file"],
        "hyperparameters": payload.get("hyperparameters", {}),
    }
    state.fine_tuning_jobs[job_id] = record
    state.fine_tuning_jobs.setdefault("__events__", {})[job_id] = [ft_stub.initial_event(job_id)]
    return jsonify(record)


@bp.get("/v1/fine_tuning/jobs")
def list_jobs():
    items = [it for it in state.fine_tuning_jobs.values() if isinstance(it, dict) and it.get("object") == "fine_tuning.job"]
    return jsonify(paginate(items).to_dict())


@bp.get("/v1/fine_tuning/jobs/<job_id>")
def get_job(job_id: str):
    record = state.fine_tuning_jobs.get(job_id)
    if record is None or not isinstance(record, dict) or record.get("object") != "fine_tuning.job":
        raise ApiError(404, f"job not found: {job_id}", "not_found_error", code="job_not_found")
    return jsonify(record)


@bp.post("/v1/fine_tuning/jobs/<job_id>/cancel")
def cancel_job(job_id: str):
    record = state.fine_tuning_jobs.get(job_id)
    if record is None or record.get("object") != "fine_tuning.job":
        raise ApiError(404, f"job not found: {job_id}", "not_found_error", code="job_not_found")
    record["status"] = "cancelled"
    record["cancelled_at"] = now_ts()
    return jsonify(record)


@bp.get("/v1/fine_tuning/jobs/<job_id>/events")
def list_events(job_id: str):
    if job_id not in state.fine_tuning_jobs:
        raise ApiError(404, f"job not found: {job_id}", "not_found_error", code="job_not_found")
    events = state.fine_tuning_jobs.setdefault("__events__", {}).setdefault(job_id, [])
    return jsonify({"object": "list", "data": events, "has_more": False})


@bp.get("/v1/fine_tuning/jobs/<job_id>/checkpoints")
def list_checkpoints(job_id: str):
    if job_id not in state.fine_tuning_jobs:
        raise ApiError(404, f"job not found: {job_id}", "not_found_error", code="job_not_found")
    return jsonify({"object": "list", "data": [], "has_more": False})


@bp.post("/v1/fine_tuning/jobs/<job_id>/checkpoints/permissions")
def checkpoint_permissions(job_id: str):
    if job_id not in state.fine_tuning_jobs:
        raise ApiError(404, f"job not found: {job_id}", "not_found_error", code="job_not_found")
    return jsonify({"object": "list", "data": [], "has_more": False})


__all__ = ["bp"]