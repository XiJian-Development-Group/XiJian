"""Core API discovery — bridge between the standalone DevKit and the
running Core API server.

The Core API writes a small JSON file to a well-known location when it
starts.  The DevKit (and any other local tooling) can read this file to
discover the port, PID, and auth token of the running Core instance
without port-scanning or process enumeration.

This is the reverse of the handshake/healthz approach — instead of the
UI process polling for a response, the API pushes its coordinates to a
stable file path that any local process can read.

File location
-------------
``~/.xijian/xijian_core.json``

Format
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
-from DevKit::
    from devkit.discovery import discover_core
    info = discover_core()  # {"port": 18500, ...} or None
    if info:
        resp = requests.get(f"http://127.0.0.1:{info['port']}/v1/xijian/devkit/status")
-from Core::
    from xijian_api.discovery import write_discovery, remove_discovery
    write_discovery(port=18500, token="...")  # on startup
    remove_discovery()                         # on shutdown
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
DISCOVERY_DIR = Path.home() / ".xijian"
DISCOVERY_FILE = DISCOVERY_DIR / "xijian_core.json"

#: The version string returned in the discovery file.
CORE_VERSION = "v1"


def write_discovery(
    port: int,
    auth_token: str,
    *,
    host: str = "127.0.0.1",
    pid: int | None = None,
) -> None:
    """Write the Core API's coordinates to the well-known discovery file.

    Creates ``~/.xijian/`` with ``0700`` perms if it doesn't exist.
    The file is written atomically via a temp file + rename to avoid
    partial reads from other processes.

    Parameters
    ----------
    port:
        The port the Core API is listening on.
    auth_token:
        The current Bearer token (as returned by
        :func:`xijian_api.auth.get_token()`).
    host:
        The host the Core API is bound to (default ``127.0.0.1``).
    pid:
        The PID of the Core API process.  Falls back to ``os.getpid()``.
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

    Safe to call multiple times; silently no-ops if the file is gone
    or the directory doesn't exist.
    """
    try:
        if DISCOVERY_FILE.is_file():
            DISCOVERY_FILE.unlink()
            _LOGGER.info("Core discovery file removed: %s", DISCOVERY_FILE)
    except OSError:
        pass


def read_discovery() -> dict | None:
    """Read the Core API's coordinates from the discovery file.

    Returns the payload dict (``port``, ``host``, ``auth_token``, …)
    or ``None`` if the file doesn't exist or is unreadable.

    The caller should verify the file is still current by calling
    :func:`verify_discovery`.
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

    Makes a single ``GET /healthz`` request and checks for the
    expected ``XIJIAN_OK_v1`` response body.  Returns ``True`` if
    the Core is reachable and responds correctly.

    Parameters
    ----------
    info:
        The payload returned by :func:`read_discovery`.  ``None``
        is accepted and returns ``False``.
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
