"""Model management routes — ``/v1/models`` family.

Implements the OAI-compatible model endpoints plus a XiJian ``load``
progress URL.  In this stub build, ``load`` flips the record to
``loaded=true`` after a short delay, and the matching
``/v1/models/operations/<op_id>`` endpoint surfaces the state.
"""

from __future__ import annotations

import threading
import time

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.stubs import state
from xijian_api.utils.ids import gen_load_op_id, gen_unload_op_id
from xijian_api.utils.time import now_ts


bp = Blueprint("models", __name__)


def _seed_models() -> None:
    if state.models:
        return
    state.models["qwen2.5-7b-mlx-4bit"] = {
        "id": "qwen2.5-7b-mlx-4bit",
        "object": "model",
        "created": 1718000000,
        "owned_by": "xijian",
        "xijian": {
            "backend": "mlx",
            "family": "qwen2.5",
            "size_b": 7.0,
            "quant": "4bit",
            "context_length": 32768,
            "min_ram_gb": 8,
            "loaded": True,
        },
    }
    state.models["qwen2.5-14b-mlx-4bit"] = {
        "id": "qwen2.5-14b-mlx-4bit",
        "object": "model",
        "created": 1718000000,
        "owned_by": "xijian",
        "xijian": {
            "backend": "mlx",
            "family": "qwen2.5",
            "size_b": 14.0,
            "quant": "4bit",
            "context_length": 32768,
            "min_ram_gb": 16,
            "loaded": False,
        },
    }
    state.models["qwen2.5-7b-gguf-q4km"] = {
        "id": "qwen2.5-7b-gguf-q4km",
        "object": "model",
        "created": 1718000000,
        "owned_by": "xijian",
        "xijian": {
            "backend": "gguf",
            "family": "qwen2.5",
            "size_b": 7.0,
            "quant": "q4_k_m",
            "context_length": 8192,
            "min_ram_gb": 8,
            "loaded": False,
        },
    }


def seed_default_models() -> None:
    """Re-seed the models bucket.

    Public helper so the test reset path (which clears ``state.models``)
    can re-populate it without depending on the route module's import
    side effects.
    """
    _seed_models()


# Seed at import-time so the routes are always ready.
_seed_models()


@bp.get("/v1/models")
def list_models():
    """List every known model."""
    return jsonify(
        {
            "object": "list",
            "data": list(state.models.values()),
        }
    )


@bp.get("/v1/models/<model_id>")
def get_model(model_id: str):
    record = state.models.get(model_id)
    if record is None:
        raise ApiError(404, f"model not found: {model_id}", "not_found_error", code="model_not_found")
    return jsonify(record)


@bp.post("/v1/models/<model_id>/load")
def load_model(model_id: str):
    record = state.models.get(model_id)
    if record is None:
        raise ApiError(404, f"model not found: {model_id}", "not_found_error", code="model_not_found")
    payload = request.get_json(silent=True) or {}
    op_id = gen_load_op_id()
    state.models[op_id] = {
        "id": op_id,
        "object": "model.load",
        "status": "loading",
        "progress_url": f"/v1/models/operations/{op_id}",
        "model_id": model_id,
        "kwargs": payload,
        "created_at": now_ts(),
    }

    def _flip():
        time.sleep(0.05)
        op = state.models.get(op_id)
        if op is None:
            return
        op["status"] = "loaded"
        op["finished_at"] = now_ts()
        record["xijian"]["loaded"] = True

    threading.Thread(target=_flip, daemon=True).start()
    response = jsonify(state.models[op_id])
    response.status_code = 202
    return response


@bp.post("/v1/models/<model_id>/unload")
def unload_model(model_id: str):
    record = state.models.get(model_id)
    if record is None:
        raise ApiError(404, f"model not found: {model_id}", "not_found_error", code="model_not_found")
    op_id = gen_unload_op_id()
    state.models[op_id] = {
        "id": op_id,
        "object": "model.unload",
        "status": "unloaded",
        "model_id": model_id,
        "created_at": now_ts(),
        "finished_at": now_ts(),
    }
    record["xijian"]["loaded"] = False
    return jsonify(state.models[op_id])


@bp.get("/v1/models/operations/<op_id>")
def get_operation(op_id: str):
    record = state.models.get(op_id)
    if record is None or record.get("object") not in {"model.load", "model.unload"}:
        raise ApiError(404, f"operation not found: {op_id}", "not_found_error", code="operation_not_found")
    return jsonify(record)


__all__ = ["bp", "seed_default_models"]