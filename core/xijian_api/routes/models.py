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

Load semantics
--------------

``POST /v1/models/<id>/load`` returns immediately with ``202`` and a
progress URL; the actual load runs in a background thread that calls
:func:`xijian_api.ai.get_registry().load`.  The op transitions to
``status="loaded"`` on success or ``status="failed"`` on error; in
the failure case the ``error`` field is populated with the underlying
``message`` and ``code`` from the AI layer's
:class:`xijian_api.ai.base.BackendError` /
:class:`xijian_api.ai.base.ModelNotFound`.

The ``seed_default_models`` helper remains a no-op when the bucket is
already populated — tests that manually clear the bucket can call it
to re-populate from the active Flask app's config without depending on
the route module's import-time side effects.
"""

from __future__ import annotations

import threading

from flask import Blueprint, current_app, jsonify, request

from xijian_api.ai import get_registry
from xijian_api.ai.base import BackendError, ModelNotFound
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
    """Kick off a background load for ``model_id`` and return 202 with a progress URL.

    The actual load runs in a daemon thread that delegates to
    :func:`xijian_api.ai.get_registry`.  The op transitions to
    ``status="loaded"`` on success or ``status="failed"`` on any
    AI-layer error; the failure case populates ``error`` with
    ``message`` and ``code`` so clients can surface a useful
    diagnostic.  ``record["xijian"]["loaded"]`` tracks the registry's
    state in the public OAI listing.
    """
    record = state.models.get(model_id)
    if record is None:
        raise ApiError(404, f"model not found: {model_id}", "not_found_error", code="model_not_found")
    config: Config | None = current_app.config.get("XIJIAN_CONFIG")
    if config is None:
        # ``XIJIAN_CONFIG`` is always set by the app factory; guard
        # here so a future refactor that drops the config doesn't
        # crash the load thread with an opaque traceback.
        raise ApiError(500, "server config not initialised", "server_error", code="config_missing")
    payload = request.get_json(silent=True) or {}
    op_id = gen_load_op_id()
    op = {
        "id": op_id,
        "object": "model.load",
        "status": "loading",
        "progress_url": f"/v1/models/operations/{op_id}",
        "model_id": model_id,
        "kwargs": payload,
        "created_at": now_ts(),
    }
    state.models[op_id] = op

    def _run() -> None:
        # The registry is a process-wide singleton; calling ``load``
        # here is safe even when many requests race for the same
        # ``model_id`` — :meth:`ModelRegistry._lock_for` serialises
        # the actual work and the second call returns the cached
        # ``LoadedModel`` cheaply.
        registry = get_registry()
        try:
            registry.load(model_id, config=config, **payload)
        except ModelNotFound as exc:
            op["status"] = "failed"
            op["error"] = {"message": str(exc), "code": exc.code}
            op["finished_at"] = now_ts()
            record["xijian"]["loaded"] = False
            return
        except BackendError as exc:
            op["status"] = "failed"
            op["error"] = {
                "message": str(exc),
                "code": getattr(exc, "code", "backend_error"),
            }
            op["finished_at"] = now_ts()
            record["xijian"]["loaded"] = False
            return
        except Exception as exc:  # pragma: no cover - defensive
            op["status"] = "failed"
            op["error"] = {
                "message": f"unexpected error: {exc}",
                "code": "internal_error",
            }
            op["finished_at"] = now_ts()
            record["xijian"]["loaded"] = False
            return
        op["status"] = "loaded"
        op["finished_at"] = now_ts()
        record["xijian"]["loaded"] = True

    threading.Thread(target=_run, daemon=True).start()
    response = jsonify(op)
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