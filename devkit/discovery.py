"""用于 DevKit 的 Core API 发现。

读取 Core API 写入的发现文件（位于
``~/.xijian/xijian_core.json``），并提供辅助函数以验证
连接并推送数据用于预览/测试。
"""

from __future__ import annotations

import json
import logging
import os
import socket
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger("devkit.discovery")

DISCOVERY_FILE = Path.home() / ".xijian" / "xijian_core.json"


def discover_core() -> dict[str, Any] | None:
    """定位正在运行的 Core API 实例。

    读取众所周知的发现文件，通过 ``/healthz`` 验证实例
    仍然存活，并返回连接信息。

    返回
    -------
    包含 ``port``、``host``、``auth_token``、``pid``、
    ``version``、``healthy``、``started_at`` 的 dict；
    如果 Core API 不可用则返回 ``None``。
    """
    if not DISCOVERY_FILE.is_file():
        _LOGGER.debug("Core discovery file not found at %s", DISCOVERY_FILE)
        return None

    try:
        data = json.loads(DISCOVERY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _LOGGER.warning("Core discovery file corrupted, ignoring")
        return None

    # 快速验证 Core 仍然在线。
    if not _check_health(data):
        _LOGGER.debug("Core at %s:%s is not responding", data.get("host"), data.get("port"))
        return None

    return data


def _check_health(info: dict) -> bool:
    """检查 ``info`` 标识的 Core API 是否存活。"""
    host = info.get("host", "127.0.0.1")
    port = info.get("port")
    if not isinstance(port, int):
        return False
    try:
        import urllib.request
        url = f"http://{host}:{port}/healthz"
        with urllib.request.urlopen(url, timeout=2) as resp:
            body = resp.read().decode("utf-8")
            return body.startswith("XIJIAN_OK_")
    except Exception:
        return False


def push_character_for_preview(character_id: str) -> dict[str, Any]:
    """将 DevKit 角色推送到正在运行的 Core API 以供预览。

    将角色加载到 Core 运行时中，使用户可以立即与之交互。

    返回 Core API 的响应 dict。
    """
    core = discover_core()
    if core is None:
        return {"ok": False, "error": "Core API not available; make sure 隙间 is running"}

    port = core["port"]
    token = core["auth_token"]
    import urllib.request

    url = f"http://127.0.0.1:{port}/v1/xijian/devkit/characters/{character_id}/load"
    req = urllib.request.Request(url, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "data": body}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"Core API returned {e.code}: {e.reason}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def push_world_for_preview(world_id: str) -> dict[str, Any]:
    """将 DevKit 世界推送到正在运行的 Core API 以供预览。"""
    core = discover_core()
    if core is None:
        return {"ok": False, "error": "Core API not available; make sure 隙间 is running"}

    port = core["port"]
    token = core["auth_token"]
    import urllib.request

    url = f"http://127.0.0.1:{port}/v1/xijian/devkit/worlds/{world_id}/load"
    req = urllib.request.Request(url, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "data": body}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"Core API returned {e.code}: {e.reason}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def reload_all() -> dict[str, Any]:
    """告诉 Core API 重新扫描并重新加载所有 DevKit 条目。"""
    core = discover_core()
    if core is None:
        return {"ok": False, "error": "Core API not available"}
    port = core["port"]
    token = core["auth_token"]
    import urllib.request
    url = f"http://127.0.0.1:{port}/v1/xijian/devkit/reload"
    req = urllib.request.Request(url, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "data": body}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def core_status() -> dict[str, Any]:
    """返回 Core API 连接的状态。"""
    core = discover_core()
    if core is None:
        return {"available": False, "error": "Core API not available"}
    return {
        "available": True,
        "port": core["port"],
        "pid": core["pid"],
        "version": core.get("version", "unknown"),
        "started_at": core.get("started_at"),
    }


__all__ = [
    "discover_core",
    "push_character_for_preview",
    "push_world_for_preview",
    "reload_all",
    "core_status",
]
