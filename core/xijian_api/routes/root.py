"""根路由 — ``GET /`` 与 ``GET /v1``。

两个端点都返回一个小的 JSON 信封，描述服务器身份、
API 版本和能力列表（DESIGN §12）。

在此基础交付件中，能力列表刻意保持最小；
其他任务（``oai-routes``、``xijian-routes``、``websocket``）会扩展它。
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
    """返回 ``/v1`` 通告的静态能力列表。"""
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


# 单一蓝图使 ``register_routes`` 保持简单。
root_bp = Blueprint("root", __name__)


@root_bp.get("/")
def root_index():
    """返回基本服务器身份信息。"""
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
    """返回 API 版本和能力列表（DESIGN §12）。"""
    return jsonify(
        {
            "api_version": API_VERSION,
            "server_version": SERVER_VERSION,
            "capabilities": _capabilities(),
        }
    )


__all__ = ["root_bp"]