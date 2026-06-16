"""Video routes — async generations, status, list, remix, delete."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import state, video as video_stub
from xijian_api.utils.ids import gen_video_id
from xijian_api.utils.time import now_ts


bp = Blueprint("videos", __name__)


@bp.post("/v1/videos/generations")
def submit_generation():
    payload = request.get_json(silent=True) or {}
    if "prompt" not in payload:
        raise ApiError(
            400,
            "`prompt` is required",
            "invalid_request_error",
            code="missing_prompt",
            param="prompt",
        )
    video_id = gen_video_id()
    record = {
        "id": video_id,
        "object": "video.generation",
        "status": "queued",
        "created_at": now_ts(),
        "completed_at": None,
        "expires_at": None,
        "error": None,
        "remixed_from_video_id": None,
        "prompt": payload["prompt"],
        "model": payload.get("model", "stub-video"),
        "size": payload.get("size", "1280x720"),
        "seconds": int(payload.get("seconds", 4)),
        "fps": int(payload.get("fps", 24)),
        "xijian": payload.get("xijian", {}),
    }
    state.videos[video_id] = record
    video_stub.submit(
        payload["prompt"],
        model=record["model"],
        seconds=record["seconds"],
        size=record["size"],
        fps=record["fps"],
        video_id=video_id,
    )
    response = jsonify(record)
    response.status_code = 202
    return response


@bp.get("/v1/videos/<video_id>")
def get_video(video_id: str):
    record = state.videos.get(video_id)
    if record is None:
        raise ApiError(404, f"video not found: {video_id}", "not_found_error", code="video_not_found")
    return jsonify(record)


@bp.get("/v1/videos")
def list_videos():
    return jsonify(paginate(list(state.videos.values())).to_dict())


@bp.post("/v1/videos/<video_id>/remix")
def remix_video(video_id: str):
    parent = state.videos.get(video_id)
    if parent is None:
        raise ApiError(404, f"video not found: {video_id}", "not_found_error", code="video_not_found")
    payload = request.get_json(silent=True) or {}
    new_id = gen_video_id()
    record = {
        "id": new_id,
        "object": "video.generation",
        "status": "queued",
        "created_at": now_ts(),
        "completed_at": None,
        "expires_at": None,
        "error": None,
        "remixed_from_video_id": video_id,
        "prompt": payload.get("prompt", parent.get("prompt", "")),
        "model": payload.get("model", parent.get("model")),
        "size": payload.get("size", parent.get("size")),
        "seconds": int(payload.get("seconds", parent.get("seconds", 4))),
        "fps": int(payload.get("fps", parent.get("fps", 24))),
    }
    state.videos[new_id] = record
    video_stub.submit(record["prompt"], video_id=new_id)
    response = jsonify(record)
    response.status_code = 202
    return response


@bp.delete("/v1/videos/<video_id>")
def delete_video(video_id: str):
    record = state.videos.pop(video_id, None)
    if record is None:
        raise ApiError(404, f"video not found: {video_id}", "not_found_error", code="video_not_found")
    return ("", 204)


__all__ = ["bp"]