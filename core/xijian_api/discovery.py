"""Core API discovery — bridge between the standalone DevKit and the
running Core API server.

Core API 发现 — 独立 DevKit 与运行中的 Core API 服务器之间的桥梁。

The Core API writes a small JSON file to a well-known location when it
starts.  The DevKit (and any other local tooling) can read this file to
discover the port, PID, and auth token of the running Core instance
without port-scanning or process enumeration.

Core API 在启动时将一个小型 JSON 文件写入众所周知的位置。
DevKit（及任何其他本地工具）可以读取此文件来发现运行中 Core 实例的端口、
PID 和认证令牌，无需端口扫描或进程枚举。

This is the reverse of the handshake/healthz approach — instead of the
UI process polling for a response, the API pushes its coordinates to a
stable file path that any local process can read.

这与 handshake/healthz 方法相反——不是 UI 进程轮询响应，
而是 API 将其坐标推送到任何本地进程都能读取的稳定文件路径。

File location
-------------
文件位置
-------------

``~/.xijian/xijian_core.json``

Format
------
格式
------

.. code-block:: json

    {
        "pid": 12345,
        "port": 18500,
        "host": "127.0.0.1",
        "auth_token": "a1b2c3d4e5f6...",
        "version": "v1",
        "healthy": true,
        "started_at": 1785390000
    }

Usage
-----
用法
-----

-From DevKit::
-从 DevKit 使用::
    from devkit.discovery import discover_core
    info = discover_core()  # {"port": 18500, ...} or None / 或 None
    if info:
        resp = requests.get(f"http://127.0.0.1:{info['port']}/v1/xijian/devkit/status")
-From Core::
-从 Core 使用::
    from xijian_api.discovery import write_discovery, remove_discovery
    write_discovery(port=18500, token="...")  # on startup  启动时
    remove_discovery()                         # on shutdown 关闭时
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from xijian_api.handshake import HEALTHZ_BODY

_LOGGER = logging.getLogger(__name__)

#: Well-known file path where the Core API writes its coordinates.
#: The ``~/.xijian/`` directory is used as a general-purpose local
#: IPC directory (not user data, not cache — just cross-process info).
#: Core API 写入其坐标的已知文件路径。
#: ``~/.xijian/`` 目录用作通用的本地 IPC 目录（不是用户数据，不是缓存——仅用于跨进程信息）。
DISCOVERY_DIR = Path.home() / ".xijian"
DISCOVERY_FILE = DISCOVERY_DIR / "xijian_core.json"

#: The version string returned in the discovery file.
#: 发现文件中返回的版本字符串。
CORE_VERSION = "v1"


def write_discovery(
    port: int,
    auth_token: str,
    *,
    host: str = "127.0.0.1",
    pid: int | None = None,
) -> None:
    """Write the Core API's coordinates to the well-known discovery file.

    将 Core API 的坐标写入已知的发现文件。

    Creates ``~/.xijian/`` with ``0700`` perms if it doesn't exist.
    The file is written atomically via a temp file + rename to avoid
    partial reads from other processes.

    如果 ``~/.xijian/`` 不存在则以 ``0700`` 权限创建。
    通过临时文件 + 重命名原子性地写入文件，避免其他进程读取到不完整的内容。

    Parameters
    ----------
    port:
        The port the Core API is listening on.
        Core API 正在监听的端口。
    auth_token:
        The current Bearer token (as returned by
        :func:`xijian_api.auth.get_token()`).
        当前的 Bearer 令牌（由 :func:`xijian_api.auth.get_token()` 返回）。
    host:
        The host the Core API is bound to (default ``127.0.0.1``).
        Core API 绑定的主机（默认 ``127.0.0.1``）。
    pid:
        The PID of the Core API process.  Falls back to ``os.getpid()``.
        Core API 进程的 PID。回退到 ``os.getpid()``。
    """
    DISCOVERY_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)

    payload = {
        "pid": pid or os.getpid(),
        "port": port,
        "host": host,
        "auth_token": auth_token,
        "version": CORE_VERSION,
        "healthy": True,
        "started_at": int(time.time()),
    }

    # Atomic write: temp file → rename.
    # 原子写入：临时文件 → 重命名。
    tmp = DISCOVERY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.rename(DISCOVERY_FILE)

    _LOGGER.info(
        "Core discovery written: %s (port=%d, pid=%d)",
        DISCOVERY_FILE, port, payload["pid"],
    )


def remove_discovery() -> None:
    """Remove the discovery file on shutdown.

    在关闭时移除发现文件。

    Safe to call multiple times; silently no-ops if the file is gone
    or the directory doesn't exist.

    可安全多次调用；如果文件已不存在或目录不存在则静默无操作。
    """
    try:
        if DISCOVERY_FILE.is_file():
            DISCOVERY_FILE.unlink()
            _LOGGER.info("Core discovery file removed: %s", DISCOVERY_FILE)
    except OSError:
        pass


def read_discovery() -> dict | None:
    """Read the Core API's coordinates from the discovery file.

    从发现文件读取 Core API 的坐标。

    Returns the payload dict (``port``, ``host``, ``auth_token``, …)
    or ``None`` if the file doesn't exist or is unreadable.

    返回载荷字典（``port``、``host``、``auth_token`` 等），
    如果文件不存在或无法读取则返回 ``None``。

    The caller should verify the file is still current by calling
    :func:`verify_discovery`.

    调用者应通过 :func:`verify_discovery` 验证文件仍然是最新的。
    """
    try:
        if not DISCOVERY_FILE.is_file():
            return None
        data = json.loads(DISCOVERY_FILE.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, OSError):
        return None


def verify_discovery(info: dict | None) -> bool:
    """Quick health-check: does the discovery info point to a live
    Core API?

    快速健康检查：发现信息是否指向一个运行中的 Core API？

    Makes a single ``GET /healthz`` request and checks for the
    expected ``XIJIAN_OK_v1`` response body.  Returns ``True`` if
    the Core is reachable and responds correctly.

    发起一次 ``GET /healthz`` 请求并检查预期的 ``XIJIAN_OK_v1`` 响应体。
    如果 Core 可达且响应正确则返回 ``True``。

    Parameters
    ----------
    info:
        The payload returned by :func:`read_discovery`.  ``None``
        is accepted and returns ``False``.
        :func:`read_discovery` 返回的载荷。接受 ``None`` 并返回 ``False``。
    """
    if info is None:
        return False
    port = info.get("port")
    if not isinstance(port, int):
        return False
    try:
        import urllib.request
        url = f"http://127.0.0.1:{port}/healthz"
        with urllib.request.urlopen(url, timeout=2) as resp:
            body = resp.read().decode("utf-8")
            return body.startswith("XIJIAN_OK_")
    except Exception:
        return False


__all__ = [
    "write_discovery",
    "remove_discovery",
    "read_discovery",
    "verify_discovery",
    "DISCOVERY_FILE",
    "CORE_VERSION",
]
