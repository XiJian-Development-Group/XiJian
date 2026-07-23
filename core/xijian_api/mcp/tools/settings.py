"""MCP tools for the global settings domain.

Wraps the in-memory settings stub (:mod:`xijian_api.stubs.settings`) as
MCP tools registered with :mod:`xijian_api.mcp.registry`.  The settings
store is a lazily-created dict inside ``state.protection`` that holds
operator-tunable preferences; the stub ships with no pre-populated
defaults (operators configure them via ``settings_update``).

These are internal domain tools (``action_kind=None``): they only touch
in-memory state, so they skip the A5.2 gate and rely on the stub's own
input validation.

The stub exposes ``get_settings`` / ``patch_settings`` but no explicit
reset, so ``settings_reset`` clears the lazy container in place (a full
clear when no key is given, a single-key drop otherwise) — the stub's
``seed_default`` is a no-op, so "defaults" is the empty state.

Tools registered
----------------

* ``settings_get``    — read all settings or a single key
* ``settings_update`` — patch settings via (key, value) or a patch dict
* ``settings_reset``  — reset settings (all or a single key)
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import settings as settings_stub
from xijian_api.stubs import state


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _settings_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    settings = settings_stub.get_settings()
    key = args.get("key")
    if key is None:
        return settings
    return {"key": key, "value": settings.get(key)}


def _settings_update(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    patch = args.get("patch")
    if patch is not None:
        if not isinstance(patch, dict):
            raise ToolError("patch must be an object")
    else:
        key = args.get("key")
        if not key:
            raise ToolError("either patch or key is required")
        if "value" not in args:
            raise ToolError("value is required when key is given")
        patch = {key: args["value"]}
    return settings_stub.patch_settings(patch)


def _settings_reset(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    key = args.get("key")
    bucket = state.protection.get("settings")
    if bucket is None:
        # Lazy container not yet created — nothing to reset.
        return {"reset": True, "key": key, "settings": {}}
    if key is not None:
        bucket.pop(key, None)
    else:
        bucket.clear()
    return {"reset": True, "key": key, "settings": dict(bucket)}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


register_tool(
    name="settings_get",
    description="Read all settings, or a single key's value when 'key' is supplied.",
    input_schema={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Optional setting key; omit to read all settings."},
        },
        "required": [],
    },
    handler=_settings_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="settings_update",
    description=(
        "Update settings. Pass 'patch' (an object) for a multi-key merge, "
        "or 'key' + 'value' for a single-key set."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Single key to set (used with 'value')."},
            "value": {"description": "Value to set for 'key' (any JSON type)."},
            "patch": {"type": "object", "description": "Multi-key patch object merged into settings."},
        },
        "required": [],
    },
    handler=_settings_update,
    action_kind=None,
)


register_tool(
    name="settings_reset",
    description=(
        "Reset settings to defaults. Omit 'key' to clear all settings; "
        "pass 'key' to clear a single entry."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Optional setting key to clear; omit to clear all."},
        },
        "required": [],
    },
    handler=_settings_reset,
    action_kind=None,
    annotations={"destructiveHint": True},
)
