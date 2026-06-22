"""Model management routes — ``/v1/models`` family.

Implements the OAI-compatible model endpoints plus a XiJian ``load``
progress URL.

Model population
----------------

At start-up :func:`init_app` is called by the route registrar and
seeds :data:`xijian_api.stubs.state.models` from
``app.config["XIJIAN_CONFIG"].models`` (the ``[[models]]`` block in
``config.toml``).  Nothing is hardcoded: if the config has no models
the bucket starts empty and operators register them with
``POST /v1/models/<id>/load`` once the checkpoint is on disk.

The ``seed_default_models`` helper remains a no-op when the bucket is
already populated — tests that manually clear the bucket can call it
to re-populate from the active Flask app's config without depending on
the route module's import-time side effects.
"""

from __future__ import annotations

import threading
import time

from flask import Blueprint, current_app, jsonify, request

from xijian_api.config import Config, ModelEntry
from xijian_api.errors import ApiError
from xijian_api.stubs import state
from xijian_api.utils.ids import gen_load_op_id, gen_unload_op_id
from xijian_api.utils.time import now_ts


bp = Blueprint("models", __name__)


def _entry_to_oai_record(entry: ModelEntry) -> dict:
    """Render a :class:`ModelEntry` into the OAI-compatible record shape."""
    return {
        "id": entry.id,
        "object": "model",
        "created": now_ts(),
        "owned_by": "xijian",
        "xijian": entry.to_oai_metadata(),
    }


def _seed_models_from_config(config: Config) -> None:
    """Populate :data:`state.models` from ``config.models``.

    No-op when the bucket is already non-empty so manual registrations
    (or models added at runtime) aren't overwritten.  When the config
    has no ``[[models]]`` entries the bucket stays empty — no demo data
    is added.
    """
    if state.models:
        return
    for entry in config.models:
        state.models[entry.id] = _entry_to_oai_record(entry)


def seed_default_models() -> None:
    """Re-seed the models bucket from the active Flask app's config.

    Public helper so the test reset path (which clears ``state.models``)
    can re-populate it without depending on the route module's import
    side effects.
    """
    try:
        config = current_app.config.get("XIJIAN_CONFIG")
    except RuntimeError:
        # No application context (e.g. imported from a script).  Skip.
        return
    if config is None:
        return
    _seed_models_from_config(config)


def init_app(app) -> None:
    """Populate the model bucket from the app's :class:`Config`."""
    config = app.config.get("XIJIAN_CONFIG")
    if config is not None:
        _seed_models_from_config(config)


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


__all__ = ["bp", "seed_default_models", "init_app"]