"""Port probing and automatic fallback for the Core API server.

Core API 服务器的端口探测与自动更换。

Behaviour / 行为:
* On startup the server first *probes* the configured port (a plain
  ``bind`` attempt, no listener is kept alive).
  启动时先*探测*配置端口（仅尝试 ``bind``，不保持监听）。
* If the port is occupied, we report the occupying process (via
  ``psutil``, best-effort) and scan upwards (``port+1``, ``port+2``, …)
  for the first free port, bounded by ``DEFAULT_MAX_ATTEMPTS``.
  若端口被占用，报告占用进程（通过 ``psutil``，尽力而为）并向上
  扫描（``port+1``、``port+2``…）寻找第一个空闲端口，上限为
  ``DEFAULT_MAX_ATTEMPTS``。
* ``--port-strict`` keeps the old behaviour: an occupied port aborts
  startup with an error (no fallback).
  ``--port-strict`` 保留旧行为：端口被占用直接报错退出（不更换）。

The probing uses a short-lived socket with ``SO_REUSEADDR`` so a
recently-closed server port does not false-positive.  There is a small
TOCTOU window between probing and the real bind in ``_serve``; for a
local single-user server this is acceptable — if the race actually
happens, ``_serve``'s existing EADDRINUSE handling aborts cleanly.

探测使用设置了 ``SO_REUSEADDR`` 的短生命周期 socket，避免刚关闭的
服务端口误报。探测与实际 ``_serve`` 绑定之间存在极小的 TOCTOU 窗口；
对本地单用户服务器可接受——若竞态真的发生，``_serve`` 已有的
EADDRINUSE 处理会干净地中止。
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

#: How many consecutive ports to scan before giving up.
#: 放弃前连续扫描的端口数量上限。
DEFAULT_MAX_ATTEMPTS = 100


class PortExhaustedError(RuntimeError):
    """Raised when no free port is found within ``max_attempts``.

    在 ``max_attempts`` 范围内找不到空闲端口时抛出。
    """

    def __init__(
        self,
        preferred: int,
        occupied_by: str | None,
        max_attempts: int,
    ) -> None:
        super().__init__(
            f"端口 {preferred} 起连续 {max_attempts} 个端口均被占用，无法启动"
            + (f"（{preferred} 被 {occupied_by} 占用）" if occupied_by else "")
        )
        self.preferred = preferred
        self.occupied_by = occupied_by
        self.max_attempts = max_attempts


@dataclass(frozen=True)
class PortResolution:
    """Result of port probing.

    端口探测结果。
    """

    #: The port that will actually be used.
    #: 实际将使用的端口。
    port: int
    #: Occupant description of the *preferred* port (None if it was free).
    #: 首选端口的占用进程描述（未占用时为 None）。
    occupied_by: str | None
    #: True when the preferred port was occupied and we fell back.
    #: 首选端口被占用且已回退时返回 True。
    changed: bool


def is_port_in_use(host: str, port: int) -> bool:
    """Probe whether ``port`` is already bound on ``host``.

    探测 ``host`` 上的 ``port`` 是否已被绑定。

    Uses a short-lived ``SO_REUSEADDR`` socket and immediately closes it
    — the probe never keeps the port reserved.

    使用短生命周期 ``SO_REUSEADDR`` socket 并立即关闭——探测不会占用端口。
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            return False
    except OSError:
        return True


def find_port_occupant(port: int) -> str | None:
    """Best-effort description of the process listening on ``port``.

    尽力描述监听 ``port`` 的进程。

    Uses ``psutil.net_connections`` filtered to ``LISTEN`` state; returns
    ``None`` when psutil is unavailable, lacks permission, or no listener
    matches.  Never raises.

    使用 ``psutil.net_connections`` 过滤 ``LISTEN`` 状态；psutil 不可用、
    权限不足或没有匹配的监听者时返回 ``None``。绝不抛出异常。
    """
    try:
        import psutil
    except ImportError:
        _LOGGER.debug("psutil 未安装，无法报告占用进程")
        return None

    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr.port == port:
                try:
                    proc = psutil.Process(conn.pid)
                    name = proc.name()
                    cmdline = " ".join(proc.cmdline()[:3])
                    return f"PID {conn.pid}（{name}）{cmdline}"
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    return f"PID {conn.pid}（无法读取进程信息）"
    except (psutil.AccessDenied, OSError) as exc:  # noqa: BLE001 - best-effort
        _LOGGER.debug("查询端口占用进程失败: %s", exc)
    return None


def resolve_available_port(
    host: str,
    preferred: int,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> PortResolution:
    """Return the first free port at or above ``preferred``.

    返回从 ``preferred`` 起第一个空闲端口。

    Args:
        host: Interface to probe on (``127.0.0.1`` / ``0.0.0.0`` …).
            探测使用的网卡（``127.0.0.1`` / ``0.0.0.0`` …）。
        preferred: The configured port to start scanning from.
            配置的起始扫描端口。
        max_attempts: How many consecutive ports to try (default 100).
            连续尝试的端口数量（默认 100）。

    Returns:
        :class:`PortResolution` with the chosen port.

    Raises:
        PortExhaustedError: No free port within ``max_attempts``.
            在 ``max_attempts`` 内没有空闲端口。
    """
    occupied_by: str | None = None
    for i in range(max_attempts):
        candidate = preferred + i
        if candidate > 65535:
            # Port range exhausted before max_attempts — stop early.
            # 端口范围提前耗尽——提前停止。
            break
        if is_port_in_use(host, candidate):
            if i == 0:
                # Only report the occupant of the *preferred* port; the
                # intermediate ports are just stepping stones.
                # 只报告*首选*端口的占用者；中间端口只是踏脚石。
                occupied_by = find_port_occupant(candidate)
            continue
        return PortResolution(
            port=candidate,
            occupied_by=occupied_by,
            changed=(candidate != preferred),
        )
    raise PortExhaustedError(preferred, occupied_by, max_attempts)


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "PortExhaustedError",
    "PortResolution",
    "is_port_in_use",
    "find_port_occupant",
    "resolve_available_port",
]
