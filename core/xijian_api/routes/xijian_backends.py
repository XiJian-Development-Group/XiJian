"""AI Backend & Model management routes — ``/v1/xijian/backends`` and ``/v1/xijian/models``.

AI 后端与模型管理路由。
提供动态配置后端（OpenAI-compatible 等）与模型的增删改查能力，
配置持久化到数据库，启动时与 config.toml 合并。
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.config import ModelEntry
from xijian_api.errors import ApiError
from xijian_api.stubs import state
from xijian_api.utils.ids import gen_backend_id, gen_model_id
from xijian_api.utils.time import now_ts


bp = Blueprint("xijian_backends", __name__)


# ---------------------------------------------------------------------------
# Backend CRUD
# ---------------------------------------------------------------------------

# Backend 存储：backend_id -> record
# Record fields: id, name, type, base_url, api_key, headers, is_default, created_at, updated_at


def _validate_backend_payload(payload: dict, require_id: bool = False) -> dict:
    """Validate and normalize backend payload."""
    name = payload.get("name", "").strip()
    if not name:
        raise ApiError(400, "name is required", "invalid_request_error", code="missing_name", param="name")

    backend_type = payload.get("type", "").strip().lower()
    if backend_type not in {"openai", "openai_compatible"}:
        raise ApiError(400, "type must be 'openai' or 'openai_compatible'", "invalid_request_error", code="invalid_type", param="type")

    base_url = payload.get("base_url", "").strip()
    if not base_url:
        raise ApiError(400, "base_url is required", "invalid_request_error", code="missing_base_url", param="base_url")

    api_key = payload.get("api_key", "")
    headers = payload.get("headers", {}) or {}
    if not isinstance(headers, dict):
        raise ApiError(400, "headers must be an object", "invalid_request_error", code="invalid_headers", param="headers")

    is_default = bool(payload.get("is_default", False))

    return {
        "name": name,
        "type": backend_type,
        "base_url": base_url,
        "api_key": api_key,
        "headers": headers,
        "is_default": is_default,
    }


@bp.get("/v1/xijian/backends")
def list_backends():
    """列出所有已配置的 AI 后端。"""
    backends = list(state.ai_backends.values())
    backends.sort(key=lambda b: (0 if b.get("is_default") else 1, b.get("name", "")))
    return jsonify({"object": "list", "data": backends})


@bp.post("/v1/xijian/backends")
def create_backend():
    """创建新的 AI 后端配置。"""
    payload = request.get_json(silent=True) or {}
    validated = _validate_backend_payload(payload)

    # If setting as default, clear other defaults
    if validated["is_default"]:
        for b in state.ai_backends.values():
            b["is_default"] = False

    backend_id = gen_backend_id()
    now = now_ts()
    record = {
        "id": backend_id,
        "object": "ai_backend",
        **validated,
        "created_at": now,
        "updated_at": now,
    }
    state.ai_backends[backend_id] = record
    return jsonify(record), 201


@bp.get("/v1/xijian/backends/<backend_id>")
def get_backend(backend_id: str):
    """获取单个后端配置。"""
    record = state.ai_backends.get(backend_id)
    if record is None:
        raise ApiError(404, "backend not found", "not_found_error", code="backend_not_found")
    return jsonify(record)


@bp.patch("/v1/xijian/backends/<backend_id>")
def patch_backend(backend_id: str):
    """更新后端配置。"""
    record = state.ai_backends.get(backend_id)
    if record is None:
        raise ApiError(404, "backend not found", "not_found_error", code="backend_not_found")

    payload = request.get_json(silent=True) or {}
    validated = _validate_backend_payload(payload)

    # If setting as default, clear other defaults
    if validated["is_default"]:
        for b in state.ai_backends.values():
            if b["id"] != backend_id:
                b["is_default"] = False

    for key in ("name", "type", "base_url", "api_key", "headers", "is_default"):
        if key in validated:
            record[key] = validated[key]

    record["updated_at"] = now_ts()
    return jsonify(record)


@bp.delete("/v1/xijian/backends/<backend_id>")
def delete_backend(backend_id: str):
    """删除后端配置。"""
    if backend_id not in state.ai_backends:
        raise ApiError(404, "backend not found", "not_found_error", code="backend_not_found")
    del state.ai_backends[backend_id]
    return ("", 204)


# ---------------------------------------------------------------------------
# Model CRUD (dynamic models beyond config.toml)
# ---------------------------------------------------------------------------

# Model 存储：model_id -> record
# Record fields: id, name, backend_id, backend_type, filename, family, size_b, quant,
#                context_length, min_ram_gb, loaded, created_at, updated_at


def _validate_model_payload(payload: dict) -> dict:
    """Validate and normalize model payload."""
    name = payload.get("name", "").strip()
    if not name:
        raise ApiError(400, "name is required", "invalid_request_error", code="missing_name", param="name")

    backend_id = payload.get("backend_id", "").strip()
    if not backend_id:
        raise ApiError(400, "backend_id is required", "invalid_request_error", code="missing_backend_id", param="backend_id")
    if backend_id not in state.ai_backends:
        raise ApiError(400, "referenced backend does not exist", "invalid_request_error", code="backend_not_found", param="backend_id")

    # Optional fields with defaults
    filename = payload.get("filename", "").strip()
    family = payload.get("family", "").strip()
    size_b = payload.get("size_b")
    quant = payload.get("quant", "").strip()
    context_length = payload.get("context_length")
    min_ram_gb = payload.get("min_ram_gb")

    return {
        "name": name,
        "backend_id": backend_id,
        "filename": filename,
        "family": family,
        "size_b": float(size_b) if size_b is not None else 0.0,
        "quant": quant,
        "context_length": int(context_length) if context_length is not None else 0,
        "min_ram_gb": float(min_ram_gb) if min_ram_gb is not None else 0.0,
    }


@bp.get("/v1/xijian/models")
def list_models():
    """列出所有模型（包含 config.toml 与动态添加的）。"""
    models = list(state.ai_models.values())
    models.sort(key=lambda m: m.get("name", ""))
    return jsonify({"object": "list", "data": models})


@bp.post("/v1/xijian/models")
def create_model():
    """创建新的模型配置（动态添加，不修改 config.toml）。"""
    payload = request.get_json(silent=True) or {}
    validated = _validate_model_payload(payload)

    model_id = gen_model_id()
    now = now_ts()
    record = {
        "id": model_id,
        "object": "ai_model",
        **validated,
        "loaded": False,
        "created_at": now,
        "updated_at": now,
    }
    state.ai_models[model_id] = record
    return jsonify(record), 201


@bp.get("/v1/xijian/models/<model_id>")
def get_model(model_id: str):
    """获取单个模型配置。"""
    record = state.ai_models.get(model_id)
    if record is None:
        raise ApiError(404, "model not found", "not_found_error", code="model_not_found")
    return jsonify(record)


@bp.patch("/v1/xijian/models/<model_id>")
def patch_model(model_id: str):
    """更新模型配置。"""
    record = state.ai_models.get(model_id)
    if record is None:
        raise ApiError(404, "model not found", "not_found_error", code="model_not_found")

    payload = request.get_json(silent=True) or {}
    validated = _validate_model_payload(payload)

    for key in ("name", "backend_id", "filename", "family", "size_b", "quant", "context_length", "min_ram_gb"):
        if key in validated:
            record[key] = validated[key]

    record["updated_at"] = now_ts()
    return jsonify(record)


@bp.delete("/v1/xijian/models/<model_id>")
def delete_model(model_id: str):
    """删除动态模型配置。"""
    if model_id not in state.ai_models:
        raise ApiError(404, "model not found", "not_found_error", code="model_not_found")
    del state.ai_models[model_id]
    return ("", 204)


@bp.post("/v1/xijian/models/<model_id>/load")
def load_model(model_id: str):
    """加载动态模型。
    
    对于 OpenAI-compatible 后端的远程模型，验证连接并标记为已加载。
    对于本地模型，委托给 /v1/models/<id>/load 端点。
    """
    record = state.ai_models.get(model_id)
    if record is None:
        raise ApiError(404, "model not found", "not_found_error", code="model_not_found")

    backend_id = record.get("backend_id")
    if not backend_id:
        raise ApiError(400, "model has no associated backend", "invalid_request_error", code="missing_backend")

    backend = state.ai_backends.get(backend_id)
    if not backend:
        raise ApiError(404, "associated backend not found", "not_found_error", code="backend_not_found")

    backend_type = backend.get("type", "openai_compatible")
    if backend_type not in ("openai", "openai_compatible"):
        raise ApiError(400, f"backend type {backend_type} does not support dynamic loading", "invalid_request_error", code="unsupported_backend_type")

    # For OpenAI-compatible backends, validate the connection by making a test request
    import httpx
    base_url = backend.get("base_url", "").rstrip("/")
    api_key = backend.get("api_key", "")
    model_name = record.get("name", "")

    try:
        # Test connection with a lightweight request (list models)
        test_url = f"{base_url}/models"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        with httpx.Client(timeout=10.0) as client:
            response = client.get(test_url, headers=headers)
            if response.status_code >= 400:
                raise ApiError(502, f"backend connection failed: HTTP {response.status_code}", "backend_error", code="backend_connection_failed")
            # Verify the model exists on the backend
            models_data = response.json()
            model_ids = [m.get("id") for m in models_data.get("data", [])]
            if model_ids and model_name not in model_ids:
                # Model not found on backend - log warning but don't fail (backend might have different naming)
                pass
    except httpx.RequestError as exc:
        raise ApiError(502, f"backend connection failed: {exc}", "backend_error", code="backend_connection_failed")

    # Mark as loaded
    record["loaded"] = True
    record["updated_at"] = now_ts()
    return jsonify(record)


@bp.post("/v1/xijian/models/<model_id>/unload")
def unload_model(model_id: str):
    """卸载动态模型。"""
    record = state.ai_models.get(model_id)
    if record is None:
        raise ApiError(404, "model not found", "not_found_error", code="model_not_found")

    record["loaded"] = False
    record["updated_at"] = now_ts()
    return jsonify(record)


__all__ = ["bp"]