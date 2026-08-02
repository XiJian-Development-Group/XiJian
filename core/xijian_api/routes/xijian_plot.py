"""``/v1/xijian/plots/*`` 和 ``/v1/xijian/worlds/<wid>/plots/*`` 路由。

C3 剧情运行时 REST 端点。

端点
======

剧情运行时实例管理::

    POST   /v1/xijian/plots/runtime              — 创建并启动剧情运行时
    GET    /v1/xijian/plots/runtime              — 列表（过滤：world_id, plot_id, status）
    GET    /v1/xijian/plots/runtime/<rt_id>      — 读取运行时状态
    POST   /v1/xijian/plots/runtime/<rt_id>/advance — 推进剧情（执行节点、流转边）
    POST   /v1/xijian/plots/runtime/<rt_id>/pause — 暂停剧情
    POST   /v1/xijian/plots/runtime/<rt_id>/resume — 恢复剧情
    DELETE /v1/xijian/plots/runtime/<rt_id>      — 删除运行时实例

节点/边查询::

    GET    /v1/xijian/plots/runtime/<rt_id>/nodes          — 列出所有节点（含运行时状态）
    GET    /v1/xijian/plots/runtime/<rt_id>/nodes/<nid>    — 读取单节点详情
    GET    /v1/xijian/plots/runtime/<rt_id>/edges          — 列出所有边
    GET    /v1/xijian/plots/runtime/<rt_id>/edges?node_id= — 列出指定节点的出边

剧情设计元数据（从 devkit 读取）::

    GET    /v1/xijian/plots/designs              — 列出可用剧情设计
    GET    /v1/xijian/plots/designs/<plot_id>    — 读取剧情设计详情
    GET    /v1/xijian/plots/designs/<plot_id>/nodes — 列出剧情节点
    GET    /v1/xijian/plots/designs/<plot_id>/edges — 列出剧情边

调度器集成::

    POST   /v1/xijian/plots/scheduler/tick       — 手动触发一次触发器评估（dev only）
"""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import plot_runtime as plot_stub
from xijian_api.stubs import worlds as worlds_stub


bp = Blueprint("xijian_plot", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_work_dir() -> str:
    """获取 devkit 工作目录（从环境变量或配置）。"""
    work_dir = os.environ.get("XIJIAN_DEVKIT_WORK_DIR")
    if not work_dir:
        # 尝试从配置读取
        try:
            from xijian_api.config import get_config
            work_dir = get_config().devkit_work_dir
        except Exception:
            pass
    if not work_dir:
        # 默认回退到 core 目录下的 devkit_data
        work_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "devkit_data")
        work_dir = os.path.abspath(work_dir)
    os.makedirs(work_dir, exist_ok=True)
    return work_dir


# ---------------------------------------------------------------------------
# 剧情设计元数据（只读，从 devkit 文件系统加载）
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/plots/designs")
def list_plot_designs():
    """列出所有可用的剧情设计。"""
    work_dir = _require_work_dir()
    designs = plot_stub.list_available_plots(work_dir)
    return jsonify(paginate(designs).to_dict())


@bp.get("/v1/xijian/plots/designs/<plot_id>")
def get_plot_design(plot_id: str):
    """获取剧情设计详情。"""
    work_dir = _require_work_dir()
    design = plot_stub.get_plot_design(work_dir, plot_id)
    if not design:
        raise ApiError(404, "剧情设计不存在", "not_found_error", code="plot_not_found")
    return jsonify(design)


@bp.get("/v1/xijian/plots/designs/<plot_id>/nodes")
def list_plot_design_nodes(plot_id: str):
    """列出剧情设计的节点。"""
    work_dir = _require_work_dir()
    design = plot_stub.get_plot_design(work_dir, plot_id)
    if not design:
        raise ApiError(404, "剧情设计不存在", "not_found_error", code="plot_not_found")
    nodes = plot_stub.get_plot_design_nodes(work_dir, plot_id)
    return jsonify({"data": nodes, "object": "list"})


@bp.get("/v1/xijian/plots/designs/<plot_id>/edges")
def list_plot_design_edges(plot_id: str):
    """列出剧情设计的边。"""
    work_dir = _require_work_dir()
    design = plot_stub.get_plot_design(work_dir, plot_id)
    if not design:
        raise ApiError(404, "剧情设计不存在", "not_found_error", code="plot_not_found")
    edges = plot_stub.get_plot_design_edges(work_dir, plot_id)
    return jsonify({"data": edges, "object": "list"})


# ---------------------------------------------------------------------------
# 剧情运行时实例管理
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/plots/runtime")
def create_plot_runtime():
    """创建并启动一个剧情运行时实例。"""
    payload = request.get_json(silent=True) or {}
    required = ("plot_id", "world_id")
    missing = [key for key in required if key not in payload]
    if missing:
        raise ApiError(
            400,
            f"缺少必填字段: {', '.join(missing)}",
            "invalid_request_error",
            code="missing_fields",
            param=",".join(missing),
        )

    plot_id = payload["plot_id"]
    world_id = payload["world_id"]
    initial_variables = payload.get("initial_variables", {})

    if worlds_stub.get(world_id) is None:
        raise ApiError(404, "世界不存在", "not_found_error", code="world_not_found")

    work_dir = _require_work_dir()
    try:
        runtime = plot_stub.create_plot_runtime(
            plot_id=plot_id,
            world_id=world_id,
            work_dir=work_dir,
            initial_variables=initial_variables,
        )
    except plot_stub.PlotError as exc:
        raise ApiError(400, str(exc), "invalid_request_error", code="plot_error") from exc

    return jsonify(runtime), 201


@bp.get("/v1/xijian/plots/runtime")
def list_plot_runtimes():
    """列出剧情运行时实例。"""
    args = request.args
    world_id = args.get("world_id")
    plot_id = args.get("plot_id")
    status = args.get("status")
    runtimes = plot_stub.list_plot_runtimes(
        world_id=world_id,
        plot_id=plot_id,
        status=status,
    )
    return jsonify(paginate(runtimes).to_dict())


@bp.get("/v1/xijian/plots/runtime/<runtime_id>")
def get_plot_runtime(runtime_id: str):
    """获取剧情运行时状态。"""
    runtime = plot_stub.get_plot_runtime(runtime_id)
    if not runtime:
        raise ApiError(404, "剧情运行时不存在", "not_found_error", code="runtime_not_found")
    return jsonify(runtime)


@bp.post("/v1/xijian/plots/runtime/<runtime_id>/advance")
def advance_plot_runtime(runtime_id: str):
    """推进剧情运行时。"""
    payload = request.get_json(silent=True) or {}
    choose_edge_id = payload.get("choose_edge_id")

    try:
        result = plot_stub.advance_plot_runtime(runtime_id, choose_edge_id=choose_edge_id)
    except plot_stub.PlotError as exc:
        raise ApiError(400, str(exc), "invalid_request_error", code="plot_error") from exc

    return jsonify(result)


@bp.post("/v1/xijian/plots/runtime/<runtime_id>/pause")
def pause_plot_runtime(runtime_id: str):
    """暂停剧情运行时。"""
    try:
        result = plot_stub.pause_plot_runtime(runtime_id)
    except plot_stub.PlotError as exc:
        raise ApiError(400, str(exc), "invalid_request_error", code="plot_error") from exc
    return jsonify(result)


@bp.post("/v1/xijian/plots/runtime/<runtime_id>/resume")
def resume_plot_runtime(runtime_id: str):
    """恢复剧情运行时。"""
    try:
        result = plot_stub.resume_plot_runtime(runtime_id)
    except plot_stub.PlotError as exc:
        raise ApiError(400, str(exc), "invalid_request_error", code="plot_error") from exc
    return jsonify(result)


@bp.delete("/v1/xijian/plots/runtime/<runtime_id>")
def delete_plot_runtime(runtime_id: str):
    """删除剧情运行时实例。"""
    ok = plot_stub.delete_plot_runtime(runtime_id)
    if not ok:
        raise ApiError(404, "剧情运行时不存在", "not_found_error", code="runtime_not_found")
    return ("", 204)


# ---------------------------------------------------------------------------
# 节点/边查询
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/plots/runtime/<runtime_id>/nodes")
def list_plot_runtime_nodes(runtime_id: str):
    """列出剧情运行时的所有节点（含运行时状态标记）。"""
    runtime = plot_stub.get_plot_runtime(runtime_id)
    if not runtime:
        raise ApiError(404, "剧情运行时不存在", "not_found_error", code="runtime_not_found")

    # 从内部存储获取完整节点列表
    bucket = plot_stub._get_bucket()
    rt = bucket.get(runtime_id)
    if not rt:
        raise ApiError(404, "剧情运行时不存在", "not_found_error", code="runtime_not_found")

    nodes = rt.get("_nodes", {})
    result = []
    for node_id, node in nodes.items():
        result.append({
            **node,
            "is_current": node_id == rt.get("current_node_id"),
            "is_completed": node_id in rt.get("completed_nodes", []),
            "is_unlocked": node_id in rt.get("unlocked_nodes", []),
        })
    return jsonify({"data": result, "object": "list"})


@bp.get("/v1/xijian/plots/runtime/<runtime_id>/nodes/<node_id>")
def get_plot_runtime_node(runtime_id: str, node_id: str):
    """获取剧情运行时中单个节点的详情。"""
    node = plot_stub.get_plot_node(runtime_id, node_id)
    if not node:
        raise ApiError(404, "节点不存在", "not_found_error", code="node_not_found")
    return jsonify(node)


@bp.get("/v1/xijian/plots/runtime/<runtime_id>/edges")
def list_plot_runtime_edges(runtime_id: str):
    """列出剧情运行时的边。可选按 node_id 过滤出边。"""
    runtime = plot_stub.get_plot_runtime(runtime_id)
    if not runtime:
        raise ApiError(404, "剧情运行时不存在", "not_found_error", code="runtime_not_found")

    bucket = plot_stub._get_bucket()
    rt = bucket.get(runtime_id)
    if not rt:
        raise ApiError(404, "剧情运行时不存在", "not_found_error", code="runtime_not_found")

    args = request.args
    node_id = args.get("node_id")

    edges = rt.get("_edges", [])
    if node_id:
        edges = [e for e in edges if e.get("source") == node_id]

    return jsonify({"data": edges, "object": "list"})


# ---------------------------------------------------------------------------
# 调度器集成
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/plots/scheduler/tick")
def plot_scheduler_tick():
    """手动触发一次剧情触发器评估（仅开发环境）。"""
    if os.environ.get("XIJIAN_DEV") != "1":
        raise ApiError(404, "not found", "not_found_error", code="route_not_found")

    payload = request.get_json(silent=True) or {}
    world_id = payload.get("world_id")

    if world_id:
        if worlds_stub.get(world_id) is None:
            raise ApiError(404, "世界不存在", "not_found_error", code="world_not_found")
        activated = plot_stub.evaluate_plot_triggers(world_id)
        return jsonify({"world_id": world_id, "activated": activated})

    # 全世界评估
    all_worlds = worlds_stub.list_all()
    all_activated = []
    for w in all_worlds:
        activated = plot_stub.evaluate_plot_triggers(w["id"])
        if activated:
            all_activated.extend(activated)
    return jsonify({"activated": all_activated})


__all__ = ["bp"]