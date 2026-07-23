"""MCP resources — read-only views of XiJian state.

Resources are URIs that the model can read to inspect the current
state of the system without side effects.  They complement tools:
use a resource when you want to *look* at something, use a tool
when you want to *do* something.

URI scheme
==========

* ``xijian://characters``               — list all characters
* ``xijian://characters/{id}``          — single character
* ``xijian://characters/{id}/state``    — character state (A3.2)
* ``xijian://worlds``                   — list all worlds
* ``xijian://worlds/{id}``              — single world
* ``xijian://worlds/{id}/summary``      — world summary
* ``xijian://memory?character_id=X``    — memory entries (filtered)
* ``xijian://sessions``                 — list sessions
* ``xijian://sessions/{id}``            — single session
* ``xijian://mcp/rules``                — MCP protection rules
* ``xijian://mcp/audit``                — MCP audit log
* ``xijian://mcp/policy/{world_id}``    — world MCP policy
* ``xijian://server/info``              — server info
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

from xijian_api.stubs import characters as characters_stub
from xijian_api.stubs import character_state as char_state_stub
from xijian_api.stubs import mcp as mcp_stub
from xijian_api.stubs import mcp_rules as rules_stub
from xijian_api.stubs import memory as memory_stub
from xijian_api.stubs import sessions as sessions_stub
from xijian_api.stubs import state
from xijian_api.stubs import worlds as worlds_stub


# ---------------------------------------------------------------------------
# Resource registry
# ---------------------------------------------------------------------------

_RESOURCES: list[dict[str, Any]] = []


def _register(uri: str, name: str, description: str, mime_type: str = "application/json") -> None:
    _RESOURCES.append({
        "uri": uri,
        "name": name,
        "description": description,
        "mimeType": mime_type,
    })


def list_resources() -> list[dict[str, Any]]:
    """Return every registered resource spec."""
    if not _RESOURCES:
        _seed_resources()
    return list(_RESOURCES)


def read_resource(uri: str) -> dict[str, Any]:
    """Read a resource by URI and return the MCP contents envelope.

    Returns ``{"contents": [{"uri": ..., "mimeType": ..., "text": ...}]}``.
    Raises ``ValueError`` if the URI is unknown.
    """
    if not _RESOURCES:
        _seed_resources()
    parsed = urlparse(uri)
    scheme = parsed.scheme
    if scheme != "xijian":
        raise ValueError("unsupported URI scheme: %r" % scheme)
    # ``urlparse`` treats the host portion of ``xijian://server/info``
    # as ``netloc`` (``"server"``) and only ``/info`` as ``path``.  Our
    # resource URIs use the ``xijian://<segment>/<...>`` form, so we
    # re-join ``netloc`` + ``path`` to recover the full logical path.
    full_path = (parsed.netloc + parsed.path) if parsed.netloc else parsed.path
    path = full_path.rstrip("/") or "/"
    query = parse_qs(parsed.query)
    text, mime = _dispatch_read(path, query)
    return {
        "contents": [
            {"uri": uri, "mimeType": mime, "text": text},
        ]
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _dispatch_read(path: str, query: dict[str, list[str]]) -> tuple[str, str]:
    parts = [p for p in path.split("/") if p]
    if not parts:
        return _server_info()
    top = parts[0]
    if top == "characters":
        return _read_characters(parts[1:], query)
    if top == "worlds":
        return _read_worlds(parts[1:], query)
    if top == "memory":
        return _read_memory(query)
    if top == "sessions":
        return _read_sessions(parts[1:])
    if top == "mcp":
        return _read_mcp(parts[1:], query)
    if top == "server":
        return _server_info()
    raise ValueError("unknown resource path: %s" % path)


def _json_text(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str, indent=2)


def _server_info() -> tuple[str, str]:
    from xijian_api.mcp.protocol import PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION
    info = {
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "protocol": PROTOCOL_VERSION,
        "characters": len(characters_stub.list_all()),
        "worlds": len(worlds_stub.list_all()),
        "sessions": len(state.sessions),
    }
    return _json_text(info), "application/json"


def _read_characters(parts: list[str], _query: dict[str, list[str]]) -> tuple[str, str]:
    if not parts:
        return _json_text(characters_stub.list_all()), "application/json"
    char_id = parts[0]
    record = characters_stub.get(char_id)
    if record is None:
        raise ValueError("character not found: %s" % char_id)
    if len(parts) >= 2 and parts[1] == "state":
        state = char_state_stub.get_state(char_id)
        return _json_text(state or {"character_id": char_id, "note": "no state"}), "application/json"
    return _json_text(record), "application/json"


def _read_worlds(parts: list[str], _query: dict[str, list[str]]) -> tuple[str, str]:
    if not parts:
        return _json_text(worlds_stub.list_all()), "application/json"
    world_id = parts[0]
    record = worlds_stub.get(world_id)
    if record is None:
        raise ValueError("world not found: %s" % world_id)
    if len(parts) >= 2 and parts[1] == "summary":
        return _json_text(worlds_stub.summary()), "application/json"
    return _json_text(record), "application/json"


def _read_memory(query: dict[str, list[str]]) -> tuple[str, str]:
    character_id = query.get("character_id", [None])[0]
    entries = memory_stub.list_all(character_id=character_id)
    return _json_text(entries), "application/json"


def _read_sessions(parts: list[str]) -> tuple[str, str]:
    if not parts:
        return _json_text(list(state.sessions.values())), "application/json"
    record = sessions_stub.get(parts[0])
    if record is None:
        raise ValueError("session not found: %s" % parts[0])
    return _json_text(record), "application/json"


def _read_mcp(parts: list[str], query: dict[str, list[str]]) -> tuple[str, str]:
    if not parts:
        raise ValueError("unknown MCP resource path")
    sub = parts[0]
    if sub == "rules":
        return _json_text(rules_stub.list_all()), "application/json"
    if sub == "audit":
        limit = int(query.get("limit", ["50"])[0])
        return _json_text(mcp_stub.list_audit(limit=limit)), "application/json"
    if sub == "policy":
        if len(parts) < 2:
            raise ValueError("policy resource requires a world_id")
        return _json_text(mcp_stub.get_world_policy(parts[1])), "application/json"
    raise ValueError("unknown MCP resource path: mcp/%s" % sub)


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def _seed_resources() -> None:
    _register("xijian://server/info", "Server Info", "Server version, protocol, and state counts")
    _register("xijian://characters", "Characters", "List all characters")
    _register("xijian://worlds", "Worlds", "List all worlds")
    _register("xijian://memory", "Memory", "Memory entries (filter with ?character_id=)")
    _register("xijian://sessions", "Sessions", "List all sessions")
    _register("xijian://mcp/rules", "MCP Rules", "MCP protection rules (A5.2)")
    _register("xijian://mcp/audit", "MCP Audit", "MCP audit log")
    _register("xijian://mcp/policy", "MCP Policy", "World MCP policy (use /mcp/policy/{world_id})")


__all__ = ["list_resources", "read_resource"]
