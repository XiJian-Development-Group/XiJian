"""``/v1/xijian/devkit/*`` routes — DevKit preview/test environment.

Provides endpoints for the main program to discover, preview, and load
creations from the standalone DevKit process.  Implements the "本地预览
与测试 → 通过? → 回炉修改" loop (C0) by bridging the DevKit's save
directory into the core runtime.

All endpoints are read-heavy (listing / previewing) with only three
mutating verbs — load, unload, and rescan.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.stubs import devkit as devkit_stub
from xijian_api.pagination import paginate

bp = Blueprint("xijian_devkit", __name__)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/devkit/status")
def devkit_status():
    """Check DevKit directory availability and return a brief summary.

    Response::

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
# Character list, preview, load, unload
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/devkit/characters")
def devkit_characters():
    """List all characters saved in the DevKit directory.

    Query params
    ------------
    ``loaded_only`` — if ``"true"``, only return characters that are
    currently loaded into the core runtime.
    """
    chars = devkit_stub.scan_characters()

    loaded_only = request.args.get("loaded_only", "").lower() == "true"
    if loaded_only:
        loaded_ids = {
            r.get("devkit_original_id")
            for r in devkit_stub.list_loaded()["characters"]
        }
        chars = [c for c in chars if c.get("id") in loaded_ids]

    # Enrich each entry with preview metadata.
    for c in chars:
        preview = devkit_stub.get_character_preview(c.get("id", ""))
        if preview:
            c["_loaded"] = preview.get("_preview", {}).get("is_loaded", False)
            c["_persona_exists"] = preview.get("_preview", {}).get("persona_exists", False)
            c["_memories_count"] = preview.get("_preview", {}).get("memories_count", 0)

    return jsonify(paginate(chars).to_dict())


@bp.get("/v1/xijian/devkit/characters/<id>")
def devkit_character_detail(id: str):
    """Return full preview data for a single DevKit character."""
    preview = devkit_stub.get_character_preview(id)
    if preview is None:
        raise ApiError(404, f"DevKit character {id} not found",
                       "not_found_error", code="not_found")
    return jsonify(preview)


@bp.post("/v1/xijian/devkit/characters/<id>/load")
def devkit_character_load(id: str):
    """Load a DevKit character into the core runtime.

    If the character was previously loaded, the old record is replaced.
    """
    record = devkit_stub.load_character(id)
    if record is None:
        raise ApiError(404, f"DevKit character {id} not found or unreadable",
                       "not_found_error", code="not_found")
    return jsonify({"ok": True, "data": record}), 200


@bp.delete("/v1/xijian/devkit/characters/<id>")
@bp.post("/v1/xijian/devkit/characters/<id>/unload")
def devkit_character_unload(id: str):
    """Unload a DevKit character from the core runtime.

    Accepts both ``DELETE`` and ``POST /unload`` (some clients prefer
    ``POST`` for mutating endpoints behind restrictive proxies).
    """
    ok = devkit_stub.unload("character", id)
    if not ok:
        raise ApiError(404, f"No devkit-loaded character {id} found",
                       "not_found_error", code="not_found")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# World list, preview, load, unload
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/devkit/worlds")
def devkit_worlds():
    """List all worlds saved in the DevKit directory."""
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
    """Return full preview data for a single DevKit world."""
    preview = devkit_stub.get_world_preview(id)
    if preview is None:
        raise ApiError(404, f"DevKit world {id} not found",
                       "not_found_error", code="not_found")
    return jsonify(preview)


@bp.post("/v1/xijian/devkit/worlds/<id>/load")
def devkit_world_load(id: str):
    """Load a DevKit world into the core runtime."""
    record = devkit_stub.load_world(id)
    if record is None:
        raise ApiError(404, f"DevKit world {id} not found or unreadable",
                       "not_found_error", code="not_found")
    return jsonify({"ok": True, "data": record}), 200


@bp.delete("/v1/xijian/devkit/worlds/<id>")
@bp.post("/v1/xijian/devkit/worlds/<id>/unload")
def devkit_world_unload(id: str):
    """Unload a DevKit world from the core runtime."""
    ok = devkit_stub.unload("world", id)
    if not ok:
        raise ApiError(404, f"No devkit-loaded world {id} found",
                       "not_found_error", code="not_found")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Loaded items & reload
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/devkit/loaded")
def devkit_loaded():
    """Return all currently-loaded DevKit items, grouped by kind."""
    loaded = devkit_stub.list_loaded()
    return jsonify(loaded)


@bp.post("/v1/xijian/devkit/reload")
def devkit_reload():
    """Rescan the DevKit directory and reload everything.

    This is the "重新加载" button endpoint — after editing a character
    or world in the DevKit, call this to refresh the core runtime without
    restarting the API server.

    Query params
    ------------
    ``kind`` — optional filter (``"character"`` or ``"world"``).
    If omitted, both kinds are reloaded.
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
# Generic kind-based endpoints (alternative access)
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/devkit/<kind>")
def devkit_list(kind: str):
    """Generic list — dispatches to characters or worlds based on ``kind``."""
    if kind == "characters":
        return devkit_characters()
    elif kind == "worlds":
        return devkit_worlds()
    raise ApiError(400, f"Unknown devkit resource kind: {kind!r}",
                   "invalid_request_error", code="invalid_kind")


@bp.get("/v1/xijian/devkit/<kind>/<id>")
def devkit_detail(kind: str, id: str):
    """Generic detail — dispatches to character or world preview."""
    if kind == "characters":
        return devkit_character_detail(id)
    elif kind == "worlds":
        return devkit_world_detail(id)
    raise ApiError(400, f"Unknown devkit resource kind: {kind!r}",
                   "invalid_request_error", code="invalid_kind")


__all__ = ["bp"]
