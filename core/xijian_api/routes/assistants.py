"""OAI Assistants / Threads / Messages / Runs routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import assistants as asst_stub
from xijian_api.stubs import state
from xijian_api.utils.ids import (
    gen_assistant_id,
    gen_message_id,
    gen_run_id,
    gen_thread_id,
)
from xijian_api.utils.time import now_ts


bp = Blueprint("assistants", __name__)


# --- assistants --------------------------------------------------------------


@bp.post("/v1/assistants")
def create_assistant():
    payload = request.get_json(silent=True) or {}
    asst_id = gen_assistant_id()
    record = {
        "id": asst_id,
        "object": "assistant",
        "created_at": now_ts(),
        "model": payload.get("model", "stub-model"),
        "name": payload.get("name"),
        "description": payload.get("description"),
        "instructions": payload.get("instructions", ""),
        "tools": payload.get("tools", []),
        "metadata": payload.get("metadata", {}),
    }
    state.assistants[asst_id] = record
    return jsonify(record)


@bp.get("/v1/assistants")
def list_assistants():
    items = list(state.assistants.values())
    return jsonify(paginate(items).to_dict())


@bp.get("/v1/assistants/<assistant_id>")
def get_assistant(assistant_id: str):
    record = state.assistants.get(assistant_id)
    if record is None:
        raise ApiError(404, "assistant not found", "not_found_error", code="assistant_not_found")
    return jsonify(record)


@bp.post("/v1/assistants/<assistant_id>")
def modify_assistant(assistant_id: str):
    record = state.assistants.get(assistant_id)
    if record is None:
        raise ApiError(404, "assistant not found", "not_found_error", code="assistant_not_found")
    patch = request.get_json(silent=True) or {}
    for key in ("model", "name", "description", "instructions", "tools", "metadata"):
        if key in patch:
            record[key] = patch[key]
    return jsonify(record)


@bp.delete("/v1/assistants/<assistant_id>")
def delete_assistant(assistant_id: str):
    if state.assistants.pop(assistant_id, None) is None:
        raise ApiError(404, "assistant not found", "not_found_error", code="assistant_not_found")
    return ("", 204)


# --- threads -----------------------------------------------------------------


@bp.post("/v1/threads")
def create_thread():
    payload = request.get_json(silent=True) or {}
    thread_id = gen_thread_id()
    record = {
        "id": thread_id,
        "object": "thread",
        "created_at": now_ts(),
        "metadata": payload.get("metadata", {}),
    }
    state.threads[thread_id] = record
    return jsonify(record)


@bp.get("/v1/threads/<thread_id>")
def get_thread(thread_id: str):
    record = state.threads.get(thread_id)
    if record is None:
        raise ApiError(404, "thread not found", "not_found_error", code="thread_not_found")
    return jsonify(record)


@bp.post("/v1/threads/<thread_id>")
def modify_thread(thread_id: str):
    record = state.threads.get(thread_id)
    if record is None:
        raise ApiError(404, "thread not found", "not_found_error", code="thread_not_found")
    patch = request.get_json(silent=True) or {}
    if "metadata" in patch:
        record["metadata"] = patch["metadata"]
    return jsonify(record)


@bp.delete("/v1/threads/<thread_id>")
def delete_thread(thread_id: str):
    if state.threads.pop(thread_id, None) is None:
        raise ApiError(404, "thread not found", "not_found_error", code="thread_not_found")
    return ("", 204)


# --- messages ----------------------------------------------------------------


@bp.post("/v1/threads/<thread_id>/messages")
def create_message(thread_id: str):
    thread = state.threads.get(thread_id)
    if thread is None:
        raise ApiError(404, "thread not found", "not_found_error", code="thread_not_found")
    payload = request.get_json(silent=True) or {}
    if "content" not in payload:
        raise ApiError(400, "`content` is required", "invalid_request_error", code="missing_content", param="content")
    msg_id = gen_message_id()
    record = asst_stub.initial_message(thread_id, str(payload["content"]), role=payload.get("role", "user"), message_id=msg_id)
    state.messages[msg_id] = record
    return jsonify(record)


@bp.get("/v1/threads/<thread_id>/messages")
def list_messages(thread_id: str):
    items = [m for m in state.messages.values() if m.get("thread_id") == thread_id]
    return jsonify(paginate(items).to_dict())


@bp.get("/v1/threads/<thread_id>/messages/<message_id>")
def get_message(thread_id: str, message_id: str):
    record = state.messages.get(message_id)
    if record is None or record.get("thread_id") != thread_id:
        raise ApiError(404, "message not found", "not_found_error", code="message_not_found")
    return jsonify(record)


# --- runs --------------------------------------------------------------------


@bp.post("/v1/threads/<thread_id>/runs")
def create_run(thread_id: str):
    thread = state.threads.get(thread_id)
    if thread is None:
        raise ApiError(404, "thread not found", "not_found_error", code="thread_not_found")
    payload = request.get_json(silent=True) or {}
    assistant_id = payload.get("assistant_id", "")
    if assistant_id and assistant_id not in state.assistants:
        raise ApiError(404, "assistant not found", "not_found_error", code="assistant_not_found")
    run_id = gen_run_id()
    record = asst_stub.initial_run(thread_id, assistant_id, run_id)
    if "instructions" in payload:
        record["instructions"] = payload["instructions"]
    record["status"] = "completed"  # stub: complete immediately
    state.runs[run_id] = record
    return jsonify(record)


@bp.get("/v1/threads/<thread_id>/runs")
def list_runs(thread_id: str):
    items = [r for r in state.runs.values() if r.get("thread_id") == thread_id]
    return jsonify(paginate(items).to_dict())


@bp.get("/v1/threads/<thread_id>/runs/<run_id>")
def get_run(thread_id: str, run_id: str):
    record = state.runs.get(run_id)
    if record is None or record.get("thread_id") != thread_id:
        raise ApiError(404, "run not found", "not_found_error", code="run_not_found")
    return jsonify(record)


@bp.post("/v1/threads/<thread_id>/runs/<run_id>")
def modify_run(thread_id: str, run_id: str):
    record = state.runs.get(run_id)
    if record is None or record.get("thread_id") != thread_id:
        raise ApiError(404, "run not found", "not_found_error", code="run_not_found")
    patch = request.get_json(silent=True) or {}
    if "metadata" in patch:
        record["metadata"] = patch["metadata"]
    return jsonify(record)


@bp.post("/v1/threads/<thread_id>/runs/<run_id>/cancel")
def cancel_run(thread_id: str, run_id: str):
    record = state.runs.get(run_id)
    if record is None or record.get("thread_id") != thread_id:
        raise ApiError(404, "run not found", "not_found_error", code="run_not_found")
    record["status"] = "cancelled"
    record["cancelled_at"] = now_ts()
    return jsonify(record)


@bp.post("/v1/threads/<thread_id>/runs/<run_id>/steps")
def create_run_step(thread_id: str, run_id: str):
    record = state.runs.get(run_id)
    if record is None or record.get("thread_id") != thread_id:
        raise ApiError(404, "run not found", "not_found_error", code="run_not_found")
    payload = request.get_json(silent=True) or {}
    step_id = f"step_{gen_run_id()[4:]}"
    step = {
        "id": step_id,
        "object": "thread.run.step",
        "created_at": now_ts(),
        "run_id": run_id,
        "thread_id": thread_id,
        "type": "tool_calls",
        "status": "completed",
        "step_details": payload.get("step_details", {"type": "tool_calls", "tool_calls": []}),
    }
    return jsonify(step)


@bp.get("/v1/threads/<thread_id>/runs/<run_id>/steps/<step_id>")
def get_run_step(thread_id: str, run_id: str, step_id: str):
    record = state.runs.get(run_id)
    if record is None or record.get("thread_id") != thread_id:
        raise ApiError(404, "run not found", "not_found_error", code="run_not_found")
    return jsonify(
        {
            "id": step_id,
            "object": "thread.run.step",
            "created_at": now_ts(),
            "run_id": run_id,
            "thread_id": thread_id,
            "type": "tool_calls",
            "status": "completed",
        }
    )


@bp.post("/v1/threads/<thread_id>/runs/<run_id>/submit_tool_outputs")
def submit_tool_outputs(thread_id: str, run_id: str):
    record = state.runs.get(run_id)
    if record is None or record.get("thread_id") != thread_id:
        raise ApiError(404, "run not found", "not_found_error", code="run_not_found")
    record["status"] = "completed"
    return jsonify(record)


__all__ = ["bp"]