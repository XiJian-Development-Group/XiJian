"""``/v1/xijian/scenes/*`` 路由 — A4.3。

三个逻辑组共享 ``/v1/xijian/scenes`` 命名空间，使 URL 表面易于浏览：

* **POI**（``/v1/xijian/scenes/pois/*``）— 三级地图 / 区域 / POI 树。
* **旅行**（``/v1/xijian/scenes/travel-modes/*``）— 按世界的
  交通选项。
* **场景互动**（``/v1/xijian/scenes/interactions/*``）—
  操作员策划的“此动作可在该 POI 对该目标执行”定义，
  带遵守按角色冷却的 ``POST .../trigger`` 端点。

POI 端点
=============

* ``GET    /v1/xijian/scenes/pois``                 — 列表 (可选 ?world_id)
* ``POST   /v1/xijian/scenes/pois``                 — 创建
* ``GET    /v1/xijian/scenes/pois/<poi_id>``        — 获取
* ``PATCH  /v1/xijian/scenes/pois/<poi_id>``        — 修改
* ``DELETE /v1/xijian/scenes/pois/<poi_id>``        — 删除（不留孤儿）
* ``GET    /v1/xijian/scenes/pois/tree``            — 嵌套树 (?world_id)
* ``GET    /v1/xijian/scenes/pois/<poi_id>/chain``  — 祖先链
* ``GET    /v1/xijian/scenes/pois/<poi_id>/children``  — 直接子节点
* ``GET    /v1/xijian/scenes/pois/<poi_id>/descendants`` — 扁平 DFS

旅行端点
================

* ``GET    /v1/xijian/scenes/travel-modes``         — 列表 (?world_id)
* ``POST   /v1/xijian/scenes/travel-modes``         — 创建
* ``GET    /v1/xijian/scenes/travel-modes/<id>``    — 获取
* ``PATCH  /v1/xijian/scenes/travel-modes/<id>``    — 修改
* ``DELETE /v1/xijian/scenes/travel-modes/<id>``    — 删除
* ``POST   /v1/xijian/scenes/travel-modes/<id>/estimate`` — 成本预览
* ``POST   /v1/xijian/scenes/travel-modes/<id>/execute`` — 真实行程
  (AC-3: 实际从行动角色的体力中扣除)

场景互动端点
===========================

* ``GET    /v1/xijian/scenes/interactions``         — 列表 (?world_id / ?poi_id)
* ``POST   /v1/xijian/scenes/interactions``         — 创建
* ``GET    /v1/xijian/scenes/interactions/<id>``    — 获取
* ``PATCH  /v1/xijian/scenes/interactions/<id>``    — 修改
* ``DELETE /v1/xijian/scenes/interactions/<id>``    — 删除
* ``POST   /v1/xijian/scenes/interactions/<id>/trigger`` — 触发（感知冷却）
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import pois as pois_stub
from xijian_api.stubs import scene_interactions as si_stub
from xijian_api.stubs import travel_modes as tm_stub
from xijian_api.utils.params import parse_float


bp = Blueprint("xijian_scenes", __name__)
_LOGGER = logging.getLogger("xijian_api.routes.xijian_scenes")


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _require_json(*, optional: bool = False) -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        if optional:
            return {}
        raise ApiError(
            400, "request body must be a JSON object",
            "invalid_request_error", code="invalid_body",
        )
    return body


def _err_from_stub(exc: Exception, *, default_code: str) -> "ApiError":
    """将存根异常映射为 4xx ApiError。

    存根异常基于 :class:`ValueError` 并带字符串消息；我们不检查
    消息内容（可能包含用户输入），但始终在响应中原样保留。
    """
    return ApiError(
        400, str(exc), "invalid_request_error", code=default_code,
    )


# ===========================================================================
# POI
# ===========================================================================


@bp.get("/v1/xijian/scenes/pois")
def list_pois():
    world_id = request.args.get("world_id")
    if world_id is not None:
        return jsonify(paginate(pois_stub.list_for_world(world_id)).to_dict())
    return jsonify(paginate(pois_stub.list_all()).to_dict())


@bp.post("/v1/xijian/scenes/pois")
def create_poi():
    body = _require_json()
    try:
        record = pois_stub.create(
            world_id=body.get("world_id"),
            name=body.get("name"),
            kind=body.get("kind"),
            parent_id=body.get("parent_id"),
            coords=body.get("coords"),
            description=body.get("description", ""),
            poi_id=body.get("poi_id"),
        )
    except pois_stub.POIError as exc:
        raise _err_from_stub(exc, default_code="poi_error")
    return jsonify(record), 201


@bp.get("/v1/xijian/scenes/pois/tree")
def tree_pois():
    world_id = request.args.get("world_id")
    if not isinstance(world_id, str) or not world_id:
        raise ApiError(400, "`world_id` query param is required",
                       "invalid_request_error", code="missing_world_id")
    root_id = request.args.get("root_id")
    try:
        tree = pois_stub.get_tree(world_id, root_id=root_id)
    except pois_stub.POIError as exc:
        raise _err_from_stub(exc, default_code="poi_error")
    return jsonify({"world_id": world_id, "tree": tree})


@bp.get("/v1/xijian/scenes/pois/<poi_id>")
def get_poi(poi_id: str):
    record = pois_stub.get(poi_id)
    if record is None:
        raise ApiError(404, f"poi {poi_id!r} not found",
                       "not_found_error", code="poi_not_found")
    return jsonify(record)


@bp.patch("/v1/xijian/scenes/pois/<poi_id>")
def patch_poi(poi_id: str):
    body = _require_json()
    try:
        record = pois_stub.update(poi_id, body)
    except pois_stub.POIError as exc:
        raise _err_from_stub(exc, default_code="poi_error")
    if record is None:
        raise ApiError(404, f"poi {poi_id!r} not found",
                       "not_found_error", code="poi_not_found")
    return jsonify(record)


@bp.delete("/v1/xijian/scenes/pois/<poi_id>")
def delete_poi(poi_id: str):
    try:
        removed = pois_stub.delete(poi_id)
    except pois_stub.POIError as exc:
        raise _err_from_stub(exc, default_code="poi_error")
    if not removed:
        raise ApiError(404, f"poi {poi_id!r} not found",
                       "not_found_error", code="poi_not_found")
    return jsonify({"deleted": poi_id})


@bp.get("/v1/xijian/scenes/pois/<poi_id>/chain")
def poi_chain(poi_id: str):
    chain = pois_stub.get_ancestor_chain(poi_id)
    if not chain:
        raise ApiError(404, f"poi {poi_id!r} not found",
                       "not_found_error", code="poi_not_found")
    return jsonify({"poi_id": poi_id, "chain": chain})


@bp.get("/v1/xijian/scenes/pois/<poi_id>/children")
def poi_children(poi_id: str):
    if pois_stub.get(poi_id) is None:
        raise ApiError(404, f"poi {poi_id!r} not found",
                       "not_found_error", code="poi_not_found")
    return jsonify({"poi_id": poi_id, "children": pois_stub.list_children(poi_id)})


@bp.get("/v1/xijian/scenes/pois/<poi_id>/descendants")
def poi_descendants(poi_id: str):
    if pois_stub.get(poi_id) is None:
        raise ApiError(404, f"poi {poi_id!r} not found",
                       "not_found_error", code="poi_not_found")
    return jsonify({
        "poi_id": poi_id,
        "descendants": pois_stub.get_descendants(poi_id),
    })


# ===========================================================================
# 旅行模式
# ===========================================================================


@bp.get("/v1/xijian/scenes/travel-modes")
def list_travel_modes():
    world_id = request.args.get("world_id")
    if world_id is not None:
        return jsonify(paginate(tm_stub.list_for_world(world_id)).to_dict())
    return jsonify(paginate(tm_stub.list_all()).to_dict())


@bp.post("/v1/xijian/scenes/travel-modes")
def create_travel_mode():
    body = _require_json()
    try:
        record = tm_stub.create(
            world_id=body.get("world_id"),
            name=body.get("name"),
            speed_factor=body.get("speed_factor", 1.0),
            stamina_cost=body.get("stamina_cost", 0.0),
            event_chance=body.get("event_chance", 0.0),
            mode_id=body.get("mode_id"),
        )
    except tm_stub.TravelModeError as exc:
        raise _err_from_stub(exc, default_code="travel_mode_error")
    return jsonify(record), 201


@bp.get("/v1/xijian/scenes/travel-modes/<mode_id>")
def get_travel_mode(mode_id: str):
    record = tm_stub.get(mode_id)
    if record is None:
        raise ApiError(404, f"travel mode {mode_id!r} not found",
                       "not_found_error", code="travel_mode_not_found")
    return jsonify(record)


@bp.patch("/v1/xijian/scenes/travel-modes/<mode_id>")
def patch_travel_mode(mode_id: str):
    body = _require_json()
    try:
        record = tm_stub.update(mode_id, body)
    except tm_stub.TravelModeError as exc:
        raise _err_from_stub(exc, default_code="travel_mode_error")
    if record is None:
        raise ApiError(404, f"travel mode {mode_id!r} not found",
                       "not_found_error", code="travel_mode_not_found")
    return jsonify(record)


@bp.delete("/v1/xijian/scenes/travel-modes/<mode_id>")
def delete_travel_mode(mode_id: str):
    if not tm_stub.delete(mode_id):
        raise ApiError(404, f"travel mode {mode_id!r} not found",
                       "not_found_error", code="travel_mode_not_found")
    return jsonify({"deleted": mode_id})


@bp.post("/v1/xijian/scenes/travel-modes/<mode_id>/estimate")
def estimate_travel_mode(mode_id: str):
    body = _require_json(optional=True)
    record = tm_stub.get(mode_id)
    if record is None:
        raise ApiError(404, f"travel mode {mode_id!r} not found",
                       "not_found_error", code="travel_mode_not_found")
    try:
        preview = tm_stub.estimate_trip(
            record,
            base_seconds=parse_float(body.get("base_seconds"), "base_seconds", tm_stub.DEFAULT_BASE_TRAVEL_SECONDS),
            random_roll=body.get("random_roll"),
        )
    except tm_stub.TravelModeError as exc:
        raise _err_from_stub(exc, default_code="travel_mode_error")
    return jsonify({"mode_id": mode_id, "preview": preview})


@bp.post("/v1/xijian/scenes/travel-modes/<mode_id>/execute")
def execute_travel_mode(mode_id: str):
    """使用此旅行模式执行行程 — A4.3 AC-3 真实扣减。

    Body: ``{"character_id": ..., "from_poi_id"?, "to_poi_id"?,
    "base_seconds"?, "random_roll"?, "fire_event_id"?}``。角色
    的体力会被实际扣除（reason ``travel``）并写入一条
    ``travel.execute`` 审计记录；响应携带扣减后的
    ``stamina_remaining``。
    """
    body = _require_json(optional=True)
    if tm_stub.get(mode_id) is None:
        raise ApiError(404, f"travel mode {mode_id!r} not found",
                       "not_found_error", code="travel_mode_not_found")
    try:
        result = tm_stub.execute_trip(
            mode_id,
            character_id=body.get("character_id"),
            from_poi_id=body.get("from_poi_id"),
            to_poi_id=body.get("to_poi_id"),
            base_seconds=parse_float(body.get("base_seconds"), "base_seconds", tm_stub.DEFAULT_BASE_TRAVEL_SECONDS),
            random_roll=body.get("random_roll"),
            fire_event_id=body.get("fire_event_id"),
        )
    except tm_stub.TravelModeError as exc:
        raise _err_from_stub(exc, default_code="travel_mode_error")
    return jsonify(result)


# ===========================================================================
# 场景互动
# ===========================================================================


@bp.get("/v1/xijian/scenes/interactions")
def list_scene_interactions():
    world_id = request.args.get("world_id")
    poi_id = request.args.get("poi_id")
    if poi_id is not None:
        return jsonify(paginate(si_stub.list_for_poi(poi_id)).to_dict())
    if world_id is not None:
        return jsonify(paginate(si_stub.list_for_world(world_id)).to_dict())
    return jsonify(paginate(si_stub.list_all()).to_dict())


@bp.post("/v1/xijian/scenes/interactions")
def create_scene_interaction():
    body = _require_json()
    try:
        record = si_stub.create(
            world_id=body.get("world_id"),
            poi_id=body.get("poi_id"),
            target_type=body.get("target_type"),
            target_id=body.get("target_id"),
            action=body.get("action"),
            effects=body.get("effects"),
            cooldown_sec=body.get("cooldown_sec"),
            interaction_id=body.get("interaction_id"),
        )
    except si_stub.SceneInteractionError as exc:
        raise _err_from_stub(exc, default_code="scene_interaction_error")
    return jsonify(record), 201


@bp.get("/v1/xijian/scenes/interactions/<interaction_id>")
def get_scene_interaction(interaction_id: str):
    record = si_stub.get(interaction_id)
    if record is None:
        raise ApiError(404, f"scene interaction {interaction_id!r} not found",
                       "not_found_error", code="scene_interaction_not_found")
    return jsonify(record)


@bp.patch("/v1/xijian/scenes/interactions/<interaction_id>")
def patch_scene_interaction(interaction_id: str):
    body = _require_json()
    try:
        record = si_stub.update(interaction_id, body)
    except si_stub.SceneInteractionError as exc:
        raise _err_from_stub(exc, default_code="scene_interaction_error")
    if record is None:
        raise ApiError(404, f"scene interaction {interaction_id!r} not found",
                       "not_found_error", code="scene_interaction_not_found")
    return jsonify(record)


@bp.delete("/v1/xijian/scenes/interactions/<interaction_id>")
def delete_scene_interaction(interaction_id: str):
    if not si_stub.delete(interaction_id):
        raise ApiError(404, f"scene interaction {interaction_id!r} not found",
                       "not_found_error", code="scene_interaction_not_found")
    return jsonify({"deleted": interaction_id})


@bp.post("/v1/xijian/scenes/interactions/<interaction_id>/trigger")
def trigger_scene_interaction(interaction_id: str):
    body = _require_json(optional=True)
    result = si_stub.trigger(
        interaction_id,
        character_id=body.get("character_id"),
        payload=body.get("payload"),
    )
    if not result.get("accepted"):
        reason = result.get("reason")
        # 区分“未找到”(404) 与语义拒绝 (409)。
        if reason == "interaction_not_found":
            raise ApiError(404, "scene interaction not found",
                           "not_found_error", code="scene_interaction_not_found")
        raise ApiError(409, reason or "rejected",
                       "invalid_request_error", code=reason or "rejected")
    return jsonify(result)


# ---------------------------------------------------------------------------
# 种子钩子
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """空种子。真实世界由操作员策划。

    该钩子存在是为了让 ``xijian_api.stubs.seed_all`` 对 A4.3 的桶
    有一个稳定的调用点。
    """
