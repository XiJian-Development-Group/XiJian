"""模型管理路由 — ``/v1/models`` 系列。

实现 OAI 兼容的模型端点，外加隙间的 ``load`` 进度 URL。

模型填充
--------

启动时，路由注册器调用 :func:`init_app`，从
``app.config["XIJIAN_CONFIG"].models``（``config.toml`` 中的 ``[[models]]`` 块）
填充 :data:`xijian_api.stubs.state.models`。没有任何硬编码：
若配置中没有模型，则桶保持为空，操作员可在检查点落盘后通过
``POST /v1/models/<id>/load`` 注册模型。

加载语义
--------

``POST /v1/models/<id>/load`` 立即返回 ``202`` 和进度 URL；
实际加载在后台线程中运行，调用 :func:`xijian_api.ai.get_registry().load`。
成功后操作转为 ``status="loaded"``，出错时转为 ``status="failed"``；
失败情况下 ``error`` 字段填充来自 AI 层
:class:`xijian_api.ai.base.BackendError` / :class:`xijian_api.ai.base.ModelNotFound`
的底层 ``message`` 和 ``code``。

当桶已非空时，``seed_default_models`` 辅助函数保持空操作——
测试中手动清空桶后，可调用它以从当前 Flask 应用的配置重新填充，
而无需依赖路由模块导入时的副作用。
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
    """将 :class:`ModelEntry` 渲染为 OAI 兼容的记录结构。"""
    return {
        "id": entry.id,
        "object": "model",
        "created": now_ts(),
        "owned_by": "xijian",
        "xijian": entry.to_oai_metadata(),
    }


def _seed_models_from_config(config: Config) -> None:
    """从 ``config.models`` �����充 :data:`state.models`。

    当��已非空时为空操作，以免����手动注册（或运行时��加）的模型。
    当配置没有 ``[[models]]`` 条目时��保持为空 — 不��加任何演示数据。
    """
    if state.models:
        return
    for entry in config.models:
        state.models[entry.id] = _entry_to_oai_record(entry)


def seed_default_models() -> None:
    """从当前 Flask 应用的配置重新填充模型桶。

    公共辅助函数，使测试重置路径（会清空 ``state.models``）可以重新填充，
    而无需依赖路由模块导入时的副作用。
    """
    try:
        config = current_app.config.get("XIJIAN_CONFIG")
    except RuntimeError:
        # 无应用上下文（例如从脚本导入）。跳过。
        return
    if config is None:
        return
    _seed_models_from_config(config)


def init_app(app) -> None:
    """从应用的 :class:`Config` ��������充模型��。"""
    config = app.config.get("XIJIAN_CONFIG")
    if config is not None:
        _seed_models_from_config(config)


@bp.get("/v1/models")
def list_models():
    """列出所有已知模型。"""
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
    """为 ``model_id`` 启动后台加载，返回 202 与进度 URL。

    实际加载在守护线程中运行，委托给 :func:`xijian_api.ai.get_registry`。
    成功时操作转为 ``status="loaded"``，任何 AI 层错误时转为
    ``status="failed"``；失败情况会用 ``message`` 和 ``code`` 填充
    ``error``，使客户端能展示有用的诊断信息。``record["xijian"]["loaded"]``
    在公开 OAI 列表中跟踪注册表的状态。
    """
    record = state.models.get(model_id)
    if record is None:
        raise ApiError(404, f"model not found: {model_id}", "not_found_error", code="model_not_found")
    config: Config | None = current_app.config.get("XIJIAN_CONFIG")
    if config is None:
        # ``XIJIAN_CONFIG`` 始终由应用工厂设置；此处加防护，
        # 使未来去掉配置的重构不会让加载线程以不透明的回溯崩溃。
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
        # 注册表是进程级单例；此处调用 ``load`` 即使多个请求
        # 竞争同一 ``model_id`` 也是安全的 — :meth:`ModelRegistry._lock_for`
        # 串行化实际工作，第二个调用会廉价地返回缓存的 ``LoadedModel``。
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
    # 为响应快照操作，使后台线程无法在序列化中途修改它。
    # 模拟模型微秒级加载，曾与 jsonify 竞争，在测试观察到排队状态前
    # 就把 ``status`` 从 ``"loading"`` 翻转为 ``"loaded"``。
    snapshot = dict(op)
    response = jsonify(snapshot)
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