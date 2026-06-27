"""``/v1/xijian/memory/*`` routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import memory as memory_stub
from xijian_api.stubs import memory_config as memory_config_stub
from xijian_api.utils.ids import gen_audit_id


bp = Blueprint("xijian_memory", __name__)


@bp.post("/v1/xijian/memory/entries")
def create_entry():
    payload = request.get_json(silent=True) or {}
    if "content" not in payload:
        raise ApiError(400, "`content` is required", "invalid_request_error", code="missing_content", param="content")
    record = memory_stub.create(payload)
    return jsonify(record), 201


@bp.get("/v1/xijian/memory/entries")
def list_entries():
    character_id = request.args.get("character_id")
    importance = request.args.get("importance")
    tags = request.args.getlist("tag") or None
    items = memory_stub.list_all(character_id=character_id, importance=importance, tags=tags)
    return jsonify(paginate(items).to_dict())


@bp.get("/v1/xijian/memory/entries/<entry_id>")
def get_entry(entry_id: str):
    record = memory_stub.get(entry_id)
    if record is None:
        raise ApiError(404, "memory entry not found", "not_found_error", code="memory_not_found")
    return jsonify(record)


@bp.patch("/v1/xijian/memory/entries/<entry_id>")
def patch_entry(entry_id: str):
    record = memory_stub.update(entry_id, request.get_json(silent=True) or {})
    if record is None:
        raise ApiError(404, "memory entry not found", "not_found_error", code="memory_not_found")
    return jsonify(record)


@bp.delete("/v1/xijian/memory/entries/<entry_id>")
def delete_entry(entry_id: str):
    if not memory_stub.delete(entry_id):
        raise ApiError(404, "memory entry not found", "not_found_error", code="memory_not_found")
    return ("", 204)


@bp.post("/v1/xijian/memory/search")
def search_entries():
    payload = request.get_json(silent=True) or {}
    if "query" not in payload:
        raise ApiError(400, "`query` is required", "invalid_request_error", code="missing_query", param="query")
    hits = memory_stub.search(
        query=payload["query"],
        character_id=payload.get("character_id"),
        top_k=int(payload.get("top_k", 5)),
        min_score=float(payload.get("min_score", 0.0)),
    )
    return jsonify(
        {
            "object": "list",
            "data": hits,
            "has_more": False,
            "first_id": None,
            "last_id": None,
        }
    )


@bp.post("/v1/xijian/memory/consolidate")
def consolidate():
    payload = request.get_json(silent=True) or {}
    job_id = gen_audit_id()  # reuse generator to get a 12-hex id
    memory_stub.schedule_consolidate(job_id, character_id=payload.get("character_id"))
    return jsonify({"job_id": job_id, "status": "queued"}), 202


@bp.post("/v1/xijian/memory/forget")
def forget():
    payload = request.get_json(silent=True) or {}
    result = memory_stub.forget(
        entry_ids=payload.get("entry_ids"),
        decay=payload.get("decay"),
    )
    return jsonify(result)


# --- per-character config (A1.2 §character_memory_config) --------------------


@bp.get("/v1/xijian/memory/config/<character_id>")
def get_config(character_id: str):
    return jsonify(memory_config_stub.get(character_id))


@bp.put("/v1/xijian/memory/config/<character_id>")
def upsert_config(character_id: str):
    payload = request.get_json(silent=True) or {}
    record = memory_config_stub.upsert(character_id, payload)
    return jsonify(record)


@bp.delete("/v1/xijian/memory/config/<character_id>")
def delete_config(character_id: str):
    if not memory_config_stub.delete(character_id):
        # Idempotent — deleting a config that doesn't exist returns
        # the default in :func:`get`, so we mirror that here too.
        return jsonify(memory_config_stub.get(character_id))
    return ("", 204)


__all__ = ["bp"]
