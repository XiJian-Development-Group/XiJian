"""Core API discovery for the DevKit.

Reads the discovery file written by the Core API at
``~/.xijian/xijian_core.json`` and provides helpers to verify
the connection and push data for preview/testing.
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
    """Locate a running Core API instance.

    Reads the well-known discovery file, verifies the instance is
    still alive via ``/healthz``, and returns the connection info.

    Returns
    -------
    A dict with ``port``, ``host``, ``auth_token``, ``pid``,
    ``version``, ``healthy``, ``started_at``, or ``None`` if the
    Core API is not available.
    """
    if not DISCOVERY_FILE.is_file():
        _LOGGER.debug("Core discovery file not found at %s", DISCOVERY_FILE)
        return None

    try:
        data = json.loads(DISCOVERY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _LOGGER.warning("Core discovery file corrupted, ignoring")
        return None

    # Quickly verify the Core is still there.
    if not _check_health(data):
        _LOGGER.debug("Core at %s:%s is not responding", data.get("host"), data.get("port"))
        return None

    return data


def _check_health(info: dict) -> bool:
    """Check that the Core API identified by ``info`` is alive."""
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
    """Push a DevKit character to the running Core API for preview.

    Loads the character into the Core runtime so the user can
    immediately interact with it.

    Returns the Core API's response dict.
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
    """Push a DevKit world to the running Core API for preview."""
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
    """Tell the Core API to rescan and reload all DevKit items."""
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
    """Return status of the Core API connection."""
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
