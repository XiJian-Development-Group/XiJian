"""``/v1/xijian/devkit/*`` 路由 — DevKit 预览/测试环境。

为主程序提供端点，用于发现、预览和加载独立 DevKit 进程中的创作。
通过将 DevKit 的保存目录桥接到核心运行时，实现
“本地预览与测试 → 通过? → 回炉修改”循环 (C0)。

所有端点都以读为主（列表 / 预览），仅有三个变更动词 — 加载、卸载和重新扫描。
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.stubs import devkit as devkit_stub
from xijian_api.pagination import paginate

bp = Blueprint("xijian_devkit", __name__)


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/devkit/status")
def devkit_status():
    """检查 DevKit 目录可用性并返回简要摘要。

    响应示例::

        {
            "available": true,
            "directory": "/Users/.../DevKit",
            "character_count": 3,
            "world_count": 1,
            "loaded_characters": 2,
            "loaded_worlds": 0,
        }
    """
    available = devkit_stub.is_available()
    if not available:
        return jsonify({
            "available": False,
            "directory": devkit_stub.get_devkit_dir(),
            "character_count": 0,
            "world_count": 0,
            "loaded_characters": 0,
            "loaded_worlds": 0,
            "error": "DevKit directory not found",
        })

    chars = devkit_stub.scan_characters()
    worlds = devkit_stub.scan_worlds()
    loaded = devkit_stub.list_loaded()

    return jsonify({
        "available": True,
        "directory": devkit_stub.get_devkit_dir(),
        "character_count": len(chars),
        "world_count": len(worlds),
        "loaded_characters": len(loaded["characters"]),
        "loaded_worlds": len(loaded["worlds"]),
    })


# ---------------------------------------------------------------------------
# 角色列表、预览、加载、卸载
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/devkit/characters")
def devkit_characters():
    """列出 DevKit 目录中保存的所有角色。

    查询参数
    ------------
    ``loaded_only`` — 为 ``"true"`` 时，仅返回当前已加载到核心运行时的角色。
    """
    chars = devkit_stub.scan_characters()

    loaded_only = request.args.get("loaded_only", "").lower() == "true"
    if loaded_only:
        loaded_ids = {
            r.get("devkit_original_id")
            for r in devkit_stub.list_loaded()["characters"]
        }
        chars = [c for c in chars if c.get("id") in loaded_ids]

    # 为每个条目补充预览元数据。
    for c in chars:
        preview = devkit_stub.get_character_preview(c.get("id", ""))
        if preview:
            c["_loaded"] = preview.get("_preview", {}).get("is_loaded", False)
            c["_persona_exists"] = preview.get("_preview", {}).get("persona_exists", False)
            c["_memories_count"] = preview.get("_preview", {}).get("memories_count", 0)

    return jsonify(paginate(chars).to_dict())


@bp.get("/v1/xijian/devkit/characters/<id>")
def devkit_character_detail(id: str):
    """返回单个 DevKit 角色的完整预览数据。"""
    preview = devkit_stub.get_character_preview(id)
    if preview is None:
        raise ApiError(404, f"DevKit character {id} not found",
                       "not_found_error", code="not_found")
    return jsonify(preview)


@bp.post("/v1/xijian/devkit/characters/<id>/load")
def devkit_character_load(id: str):
    """将 DevKit 角色加载到核心运行时。

    若该角色此前已加载，旧记录会被替换。
    """
    record = devkit_stub.load_character(id)
    if record is None:
        raise ApiError(404, f"DevKit character {id} not found or unreadable",
                       "not_found_error", code="not_found")
    return jsonify({"ok": True, "data": record}), 200


@bp.delete("/v1/xijian/devkit/characters/<id>")
@bp.post("/v1/xijian/devkit/characters/<id>/unload")
def devkit_character_unload(id: str):
    """从核心运行时卸载 DevKit 角色。

    同时接受 ``DELETE`` 和 ``POST /unload``（某些客户端在限制性代理
    后面偏好对变更端点使用 ``POST``）。
    """
    ok = devkit_stub.unload("character", id)
    if not ok:
        raise ApiError(404, f"No devkit-loaded character {id} found",
                       "not_found_error", code="not_found")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# 世界列表、预览、加载、卸载
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/devkit/worlds")
def devkit_worlds():
    """列出 DevKit 目录中保存的所有世界。"""
    worlds = devkit_stub.scan_worlds()
    for w in worlds:
        preview = devkit_stub.get_world_preview(w.get("id", ""))
        if preview:
            w["_loaded"] = preview.get("_preview", {}).get("is_loaded", False)
            w["_doc_exists"] = preview.get("_preview", {}).get("doc_exists", False)
            w["_config_exists"] = preview.get("_preview", {}).get("config_exists", False)
    return jsonify(paginate(worlds).to_dict())


@bp.get("/v1/xijian/devkit/worlds/<id>")
def devkit_world_detail(id: str):
    """返回单个 DevKit 世界的完整预览数据。"""
    preview = devkit_stub.get_world_preview(id)
    if preview is None:
        raise ApiError(404, f"DevKit world {id} not found",
                       "not_found_error", code="not_found")
    return jsonify(preview)


@bp.post("/v1/xijian/devkit/worlds/<id>/load")
def devkit_world_load(id: str):
    """将 DevKit 世界加载到核心运行时。"""
    record = devkit_stub.load_world(id)
    if record is None:
        raise ApiError(404, f"DevKit world {id} not found or unreadable",
                       "not_found_error", code="not_found")
    return jsonify({"ok": True, "data": record}), 200


@bp.delete("/v1/xijian/devkit/worlds/<id>")
@bp.post("/v1/xijian/devkit/worlds/<id>/unload")
def devkit_world_unload(id: str):
    """从核心运行时卸载 DevKit 世界。"""
    ok = devkit_stub.unload("world", id)
    if not ok:
        raise ApiError(404, f"No devkit-loaded world {id} found",
                       "not_found_error", code="not_found")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# 已加载条目与重新加载
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/devkit/loaded")
def devkit_loaded():
    """返回所有当前已加载的 DevKit 条目，按类型分组。"""
    loaded = devkit_stub.list_loaded()
    return jsonify(loaded)


@bp.post("/v1/xijian/devkit/reload")
def devkit_reload():
    """重新扫描 DevKit 目录并重新加载所有内容。

    这是“重新加载”按钮端点 — 在 DevKit 中编辑角色或世界后调用它，
    无需重启 API 服务器即可刷新核心运行时。

    查询参数
    ------------
    ``kind`` — 可选过滤（``"character"`` 或 ``"world"``）。
    省略时重新加载两种类型。
    """
    kind = request.args.get("kind", "").strip().lower()

    result: dict[str, int] = {}
    if not kind or kind == "character":
        chars = devkit_stub.reload_characters()
        result["characters"] = len(chars)
    if not kind or kind == "world":
        worlds = devkit_stub.reload_worlds()
        result["worlds"] = len(worlds)

    if not result:
        raise ApiError(400, f"Invalid kind: {kind!r}",
                       "invalid_request_error", code="invalid_kind")

    return jsonify({"ok": True, "reloaded": result})


# ---------------------------------------------------------------------------
# 通用 kind 端点（替代访问方式）
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/devkit/<kind>")
def devkit_list(kind: str):
    """通用列表 — 根据 ``kind`` 分发到角色或世界。"""
    if kind == "characters":
        return devkit_characters()
    elif kind == "worlds":
        return devkit_worlds()
    raise ApiError(400, f"Unknown devkit resource kind: {kind!r}",
                   "invalid_request_error", code="invalid_kind")


@bp.get("/v1/xijian/devkit/<kind>/<id>")
def devkit_detail(kind: str, id: str):
    """通用详情 — 分发到角色或世界的预览。"""
    if kind == "characters":
        return devkit_character_detail(id)
    elif kind == "worlds":
        return devkit_world_detail(id)
    raise ApiError(400, f"Unknown devkit resource kind: {kind!r}",
                   "invalid_request_error", code="invalid_kind")


__all__ = ["bp"]
