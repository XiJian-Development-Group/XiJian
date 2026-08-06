"""``/v1/xijian/generation/*`` — A4.1 的生成作用域辅助。

此蓝图包含两件事：

* ``POST /v1/xijian/generation/abort`` — 广域中止（原有）。
* ``GET  /v1/xijian/generation/scene/<instance_id>`` — 读取已触发世界事件
  实例所附的场景记录（A4.1 US-A4.1-03）。
* ``POST /v1/xijian/generation/scene/<instance_id>/generate`` —
  （重新）生成实例场景；当核心后端不可用时降级为占位场景（A4.1 AC-2）。

实际生成位于 :mod:`xijian_api.stubs.events`
（:func:`~xijian_api.stubs.events._ensure_scene_generated`）—
路由层只是薄薄的 HTTP 外壳。
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api import abort as abort_registry
from xijian_api.errors import ApiError
from xijian_api.stubs import events as events_stub


bp = Blueprint("xijian_generation", __name__)


@bp.post("/v1/xijian/generation/abort")
def generation_abort():
    payload = request.get_json(silent=True) or {}
    request_id = payload.get("request_id", "")
    if not request_id:
        raise ApiError(400, "`request_id` is required", "invalid_request_error", code="missing_request_id", param="request_id")
    scope = payload.get("scope", "all")
    signalled = abort_registry.abort(request_id)
    return jsonify(
        {
            "aborted": signalled,
            "request_id": request_id,
            "scope": scope,
        }
    )


@bp.get("/v1/xijian/generation/scene/<instance_id>")
def get_event_scene(instance_id: str):
    """返回已触发事件实例所附的场景记录。"""
    scene = events_stub.get_instance_scene(instance_id)
    if scene is None:
        raise ApiError(
            404,
            "event instance not found or it doesn't need a scene",
            "not_found_error",
            code="event_scene_not_found",
        )
    return jsonify({"instance_id": instance_id, "scene": scene})


@bp.post("/v1/xijian/generation/scene/<instance_id>/generate")
def generate_event_scene(instance_id: str):
    """（重新）为已触发事件实例生成场景。

    尽力而为：当核心图像后端不可用时，存根降级为占位场景
    （A4.1 AC-2）而不是失败。
    """
    scene = events_stub.ensure_scene_for_instance(instance_id)
    if scene is None:
        raise ApiError(
            404,
            "event instance not found or it doesn't need a scene",
            "not_found_error",
            code="event_scene_not_found",
        )
    return jsonify({"instance_id": instance_id, "scene": scene})


__all__ = ["bp"]
