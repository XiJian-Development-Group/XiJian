"""Root routes — ``GET /`` and ``GET /v1``.

Both endpoints return a small JSON envelope describing the server
identity, API version and capabilities list (DESIGN §12).

The capabilities list is intentionally minimal in this foundation
deliverable; other tasks (``oai-routes``, ``xijian-routes``,
``websocket``) extend it.
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from xijian_api.config import API_VERSION
from xijian_api._version import CORE_VERSION_NORMALIZED

# Server version follows the generated ``_version`` module (sourced from
# ``Config/Config.json`` → ``Version.CoreApi`` via scripts/sync-versions.py).
# 服务版本跟随生成的 ``_version`` 模块（由 scripts/sync-versions.py 从
# ``Config/Config.json`` → ``Version.CoreApi`` 同步而来）。
SERVER_VERSION = CORE_VERSION_NORMALIZED


def _capabilities() -> list[str]:
    """Return the static capabilities list advertised by ``/v1``."""
    return [
        "chat.completions",
        "chat.streaming",
        "chat.abort",
        "embeddings",
        "audio.speech",
        "audio.transcriptions",
        "audio.translations",
        "images.generations",
        "images.edits",
        "images.variations",
        "videos.generations",
        "files",
        "batches",
        "fine_tuning",
        "assistants",
        "threads",
        "runs",
        "messages",
        "xijian.characters",
        "xijian.interactions",
        "xijian.worlds",
        "xijian.memory",
        "xijian.protection",
        "xijian.sessions",
        "xijian.settings",
        "xijian.resources",
        "websocket",
    ]


# Single blueprint keeps ``register_routes`` simple.
root_bp = Blueprint("root", __name__)


@root_bp.get("/")
def root_index():
    """Return basic server identity."""
    return jsonify(
        {
            "name": "xijian-api",
            "server_version": SERVER_VERSION,
            "api_version": API_VERSION,
            "status": "ok",
        }
    )


@root_bp.get("/v1")
def v1_index():
    """Return API version and capabilities (DESIGN §12)."""
    return jsonify(
        {
            "api_version": API_VERSION,
            "server_version": SERVER_VERSION,
            "capabilities": _capabilities(),
        }
    )


__all__ = ["root_bp"]