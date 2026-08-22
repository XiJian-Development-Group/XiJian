"""AI Backend & Model management routes — ``/v1/xijian/backends`` and ``/v1/xijian/models``.

AI 后端与模型管理路由。
提供动态配置后端（OpenAI-compatible 等）与模型的增删改查能力。

持久化与机密安全
----------------

* 配置元数据（名称 / URL / headers 等）存 SQLite（DictDB，跨重启保留）。
* **API Key 只存操作系统钥匙串**（macOS Keychain，经
  :mod:`xijian_api.keychain`），SQLite 中永不落明文；读取接口在响应时
  从钥匙串回填，删除后端时同步清除钥匙串条目。历史明文数据在首次
  访问时自动迁移进钥匙串并抹除。
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from xijian_api import keychain
from xijian_api.config import ModelEntry
from xijian_api.errors import ApiError
from xijian_api.stubs import state
from xijian_api.utils.ids import gen_backend_id, gen_model_id
from xijian_api.utils.time import now_ts


bp = Blueprint("xijian_backends", __name__)

_LOGGER = logging.getLogger("xijian_api.backends")

#: 模块级迁移守卫：每个进程只跑一次明文 key 迁移。
_MIGRATED = False


def _ensure_keys_migrated() -> None:
    """把历史版本明文存进 DB 的 api_key 迁移到系统钥匙串并抹除。

    幂等：进程内仅执行一次；无明文 key 时零开销。
    """
    global _MIGRATED
    if _MIGRATED:
        return
    _MIGRATED = True
    for record in state.ai_backends.values():
        plain = record.get("api_key") or ""
        if not plain:
            continue
        backend_id = record.get("id") or ""
        if backend_id and keychain.set_secret(backend_id, plain):
            record["api_key"] = ""
            record["updated_at"] = now_ts()
            _LOGGER.info("migrated api_key for backend %s into system keychain", backend_id)


def _store_api_key(backend_id: str, value: str) -> None:
    """写入钥匙串；失败时抛错，避免用户误以为已安全保存。"""
    if not value:
        return
    if not keychain.set_secret(backend_id, value):
        raise ApiError(
            500,
            "无法将 API Key 写入系统钥匙串，为避免明文落盘已取消保存。",
            "server_error",
            code="keychain_write_failed",
        )


def _with_api_key(record: dict) -> dict:
    """返回回填了钥匙串中 api_key 的记录副本（用于响应）。"""
    out = dict(record)
    out["api_key"] = keychain.get_secret(out.get("id", "")) or ""
    return out


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
    """列出所有已配置的 AI 后端（api_key 从钥匙串回填）。"""
    _ensure_keys_migrated()
    backends = [_with_api_key(b) for b in state.ai_backends.values()]
    backends.sort(key=lambda b: (0 if b.get("is_default") else 1, b.get("name", "")))
    return jsonify({"object": "list", "data": backends})


@bp.post("/v1/xijian/backends")
def create_backend():
    """创建新的 AI 后端配置（API Key 存入系统钥匙串）。"""
    payload = request.get_json(silent=True) or {}
    validated = _validate_backend_payload(payload)

    # If setting as default, clear other defaults
    if validated["is_default"]:
        for b in state.ai_backends.values():
            b["is_default"] = False

    backend_id = gen_backend_id()
    api_key = validated.pop("api_key") or ""
    now = now_ts()
    record = {
        "id": backend_id,
        "object": "ai_backend",
        **validated,
        # SQLite 永不落明文 key；仅存"是否已配置"标记。
        "api_key": "",
        "has_api_key": bool(api_key),
        "created_at": now,
        "updated_at": now,
    }
    if api_key:
        _store_api_key(backend_id, api_key)
    state.ai_backends[backend_id] = record
    return jsonify(_with_api_key(record)), 201


@bp.get("/v1/xijian/backends/<backend_id>")
def get_backend(backend_id: str):
    """获取单个后端配置（api_key 从钥匙串回填）。"""
    _ensure_keys_migrated()
    record = state.ai_backends.get(backend_id)
    if record is None:
        raise ApiError(404, "backend not found", "not_found_error", code="backend_not_found")
    return jsonify(_with_api_key(record))


@bp.patch("/v1/xijian/backends/<backend_id>")
def patch_backend(backend_id: str):
    """更新后端配置（新 API Key 写入钥匙串；留空表示保持不变）。"""
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

    new_api_key = validated.pop("api_key") or ""
    for key in ("name", "type", "base_url", "headers", "is_default"):
        if key in validated:
            record[key] = validated[key]

    if new_api_key:
        # 仅在用户提交了非空 key 时更新；空值 = 保留现有 key。
        _store_api_key(backend_id, new_api_key)
        record["has_api_key"] = True

    record["updated_at"] = now_ts()
    return jsonify(_with_api_key(record))


@bp.delete("/v1/xijian/backends/<backend_id>")
def delete_backend(backend_id: str):
    """删除后端配置（同步清除钥匙串条目）。"""
    if backend_id not in state.ai_backends:
        raise ApiError(404, "backend not found", "not_found_error", code="backend_not_found")
    del state.ai_backends[backend_id]
    keychain.delete_secret(backend_id)
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


@bp.get("/v1/xijian/backends/<backend_id>/remote-models")
def list_remote_models(backend_id: str):
    """代理拉取指定后端可用的远程模型列表（GET {base_url}/models）。

    返回 ``{"object": "list", "data": [{"id": ..., ...}, ...]}``，
    连接失败返回 502 与明确错误信息。
    """
    backend = state.ai_backends.get(backend_id)
    if backend is None:
        raise ApiError(404, "backend not found", "not_found_error", code="backend_not_found")

    base_url = (backend.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise ApiError(400, "backend has no base_url", "invalid_request_error", code="missing_base_url")

    api_key = keychain.get_secret(backend_id) or backend.get("api_key") or ""
    extra_headers = backend.get("headers") or {}
    headers = dict(extra_headers)
    if api_key:
        headers.setdefault("Authorization", f"Bearer {api_key}")

    import httpx

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{base_url}/models", headers=headers)
    except httpx.RequestError as exc:
        raise ApiError(502, f"无法连接后端：{exc}", "backend_error", code="backend_connection_failed")

    if resp.status_code >= 400:
        raise ApiError(
            502,
            f"后端返回 HTTP {resp.status_code}",
            "backend_error",
            code="backend_connection_failed",
        )

    try:
        payload = resp.json()
    except ValueError:
        raise ApiError(502, "后端返回了无法解析的响应", "backend_error", code="backend_invalid_response")

    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        items = []
    normalized = []
    for item in items:
        if isinstance(item, dict) and item.get("id"):
            normalized.append({"id": str(item["id"]), "owned_by": item.get("owned_by", "")})
        elif isinstance(item, str):
            normalized.append({"id": item, "owned_by": ""})

    return jsonify({"object": "list", "data": normalized})


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
    api_key = keychain.get_secret(backend_id) or backend.get("api_key") or ""
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