"""MCP tools for the global settings domain.
MCP 全局设置域工具。

Wraps the in-memory settings stub (:mod:`xijian_api.stubs.settings`) as
MCP tools registered with :mod:`xijian_api.mcp.registry`.  The settings
store is a lazily-created dict inside ``state.safety_state`` that holds
operator-tunable preferences; the stub ships with no pre-populated
defaults (operators configure them via ``settings_update``).
将内存设置桩层封装为 MCP 工具。设置存储是 ``state.safety_state`` 中的延迟创建字典，
保存操作者可调整的偏好；桩层未预填充默认值。

These are internal domain tools (``action_kind=None``): they only touch
in-memory state, so they skip the A5.2 gate.
内部领域工具，仅操作内存状态，绕过 A5.2 门禁。

Tools registered / 已注册工具
----------------

* ``settings_get``    — read all settings or a single key / 读取所有设置或单个键
* ``settings_update`` — patch settings via (key, value) or a patch dict / 通过键值对或修补字典更新设置
* ``settings_reset``  — reset settings (all or a single key) / 重置设置
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import settings as settings_stub
from xijian_api.stubs import state


# ---------------------------------------------------------------------------
# Handlers / 处理器
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
    bucket = state.safety_state.get("settings")
    if bucket is None:
        return {"reset": True, "key": key, "settings": {}}
    if key is not None:
        bucket.pop(key, None)
    else:
        bucket.clear()
    return {"reset": True, "key": key, "settings": dict(bucket)}


# ---------------------------------------------------------------------------
# Registration / 注册
# ---------------------------------------------------------------------------


register_tool(
    name="settings_get",
    description="Read all settings, or a single key's value when 'key' is supplied. / 读取所有设置，或提供 'key' 时读取单个键的值。",
    input_schema={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Optional setting key / 可选的设置键"},
        },
        "required": [],
    },
    handler=_settings_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="settings_update",
    description="Update settings. Pass 'patch' for a multi-key merge, or 'key' + 'value' for a single-key set. / 更新设置。传递 'patch' 进行多键合并，或 'key' + 'value' 设置单个键。",
    input_schema={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Single key to set (used with 'value'). / 要设置的单个键。"},
            "value": {"description": "Value to set for 'key' (any JSON type). / 要为 'key' 设置的值。"},
            "patch": {"type": "object", "description": "Multi-key patch object / 多键修补对象"},
        },
        "required": [],
    },
    handler=_settings_update,
    action_kind=None,
)


register_tool(
    name="settings_reset",
    description="Reset settings to defaults. Omit 'key' to clear all settings; pass 'key' to clear a single entry. / 将设置重置为默认值。省略 'key' 清除所有设置；传递 'key' 清除单个条目。",
    input_schema={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Optional setting key to clear / 可选的要清除的设置键"},
        },
        "required": [],
    },
    handler=_settings_reset,
    action_kind=None,
    annotations={"destructiveHint": True},
)
