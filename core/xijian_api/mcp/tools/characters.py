"""MCP tools for the character domain.
MCP 字符域工具。

Wraps the in-memory character CRUD stub (:mod:`xijian_api.stubs.characters`)
and the A3.2 character-state stub (:mod:`xijian_api.stubs.character_state`)
as MCP tools registered with :mod:`xijian_api.mcp.registry`.
将内存角色 CRUD 桩层 (:mod:`xijian_api.stubs.characters`) 和 A3.2 角色状态桩层
(:mod:`xijian_api.stubs.character_state`) 封装为 MCP 工具，注册到 :mod:`xijian_api.mcp.registry`。

These are internal domain tools (``action_kind=None``): they only touch
in-memory state, so they skip the A5.2 gate and rely on the stubs' own
input validation.
这些是内部领域工具 (``action_kind=None``)：仅操作内存状态，因此绕过 A5.2 门禁，依赖桩层自身的输入验证。

Tools registered / 已注册工具
----------------

Character CRUD / 角色增删改查:

* ``character_create``      — create a character / 创建角色
* ``character_list``        — list every character / 列出所有角色
* ``character_get``         — fetch a character by id / 按 ID 获取角色
* ``character_update``      — patch mutable character fields / 修补可变角色字段
* ``character_delete``      — delete a character / 删除角色
* ``character_set_loaded``  — toggle the character's ``loaded`` flag / 切换角色的 ``loaded`` 标志

Character state (A3.2) / 角色状态 (A3.2):

* ``character_state_get``     — read the raw state record / 读取原始状态记录
* ``character_state_update``  — apply a numeric state patch / 应用数字状态修补
* ``character_state_summary`` — read the JSON-friendly state summary / 读取 JSON 友好状态摘要
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import character_state as character_state_stub
from xijian_api.stubs import characters as characters_stub


# ---------------------------------------------------------------------------
# Character CRUD handlers / 角色增删改查处理器
# ---------------------------------------------------------------------------


def _character_create(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    name = args.get("name")
    if not name:
        raise ToolError("name is required")
    payload: dict[str, Any] = {"name": name}
    for key in (
        "display_name", "persona_doc", "voice_profile",
        "default_emotion", "tags",
    ):
        if key in args:
            payload[key] = args[key]
    return characters_stub.create(payload)


def _character_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    return characters_stub.list_all()


def _character_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    character_id = args.get("character_id")
    if not character_id:
        raise ToolError("character_id is required")
    record = characters_stub.get(character_id)
    if record is None:
        raise ToolError(f"character {character_id!r} not found")
    return record


_CHARACTER_PATCH_FIELDS = (
    "name", "display_name", "persona_doc", "voice_profile",
    "default_emotion", "tags",
)


def _character_update(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    character_id = args.get("character_id")
    if not character_id:
        raise ToolError("character_id is required")
    patch = {key: args[key] for key in _CHARACTER_PATCH_FIELDS if key in args}
    record = characters_stub.update(character_id, patch)
    if record is None:
        raise ToolError(f"character {character_id!r} not found")
    return record


def _character_delete(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    character_id = args.get("character_id")
    if not character_id:
        raise ToolError("character_id is required")
    if not characters_stub.delete(character_id):
        raise ToolError(f"character {character_id!r} not found")
    return {"deleted": True, "character_id": character_id}


def _character_set_loaded(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    character_id = args.get("character_id")
    if not character_id:
        raise ToolError("character_id is required")
    if "loaded" not in args:
        raise ToolError("loaded is required")
    record = characters_stub.set_loaded(character_id, bool(args.get("loaded")))
    if record is None:
        raise ToolError(f"character {character_id!r} not found")
    return record


# ---------------------------------------------------------------------------
# Character state (A3.2) handlers / 角色状态处理器
# ---------------------------------------------------------------------------


def _character_state_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    character_id = args.get("character_id")
    if not character_id:
        raise ToolError("character_id is required")
    record = character_state_stub.get_state(character_id)
    if record is None:
        raise ToolError(f"no state record for character {character_id!r}")
    return record


_STATE_PATCH_FIELDS = (
    "hunger", "thirst", "health", "mood",
    "max_hunger", "max_thirst", "max_health", "max_mood",
)


def _character_state_update(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    character_id = args.get("character_id")
    if not character_id:
        raise ToolError("character_id is required")
    patch = {key: args[key] for key in _STATE_PATCH_FIELDS if key in args}
    if not patch:
        raise ToolError("at least one state field is required")
    reason = args.get("reason", "manual")
    ref_id = args.get("ref_id")
    return character_state_stub.apply_patch(
        character_id, patch, reason=reason, ref_id=ref_id,
    )


def _character_state_summary(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    character_id = args.get("character_id")
    if not character_id:
        raise ToolError("character_id is required")
    result = character_state_stub.summary(character_id)
    if result is None:
        raise ToolError(f"no state record for character {character_id!r}")
    return result


# ---------------------------------------------------------------------------
# Registration / 注册
# ---------------------------------------------------------------------------


register_tool(
    name="character_create",
    description="Create a new character with persona, voice, and emotion settings. / 创建带人设、语音和情绪设置的新角色。",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Internal character name. / 内部角色名称。"},
            "display_name": {"type": "string", "description": "Display name shown to users. / 向用户展示的显示名称。"},
            "persona_doc": {"type": "string", "description": "Persona / background document text. / 人设/背景文档文本。"},
            "voice_profile": {"type": "string", "description": "Voice profile identifier. / 语音配置文件标识符。"},
            "default_emotion": {"type": "string", "description": "Default emotion label. / 默认情绪标签。"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Free-form tags. / 自由格式标签。"},
        },
        "required": ["name"],
    },
    handler=_character_create,
    action_kind=None,
)


register_tool(
    name="character_list",
    description="List every character record. / 列出所有角色记录。",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_character_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="character_get",
    description="Fetch a single character by id. / 按 ID 获取单个角色。",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "The character id to fetch. / 要获取的角色 ID。"},
        },
        "required": ["character_id"],
    },
    handler=_character_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="character_update",
    description="Patch mutable character fields (name, persona, voice, emotion, tags, ...). / 修补可变角色字段（名称、人设、语音、情绪、标签等）。",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "The character id to update. / 要更新的角色 ID。"},
            "name": {"type": "string"},
            "display_name": {"type": "string"},
            "persona_doc": {"type": "string"},
            "voice_profile": {"type": "string"},
            "default_emotion": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["character_id"],
    },
    handler=_character_update,
    action_kind=None,
)


register_tool(
    name="character_delete",
    description="Delete a character by id. / 按 ID 删除角色。",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "The character id to delete. / 要删除的角色 ID。"},
        },
        "required": ["character_id"],
    },
    handler=_character_delete,
    action_kind=None,
    annotations={"destructiveHint": True},
)


register_tool(
    name="character_set_loaded",
    description="Set a character's loaded (active) flag. / 设置角色的加载（活跃）标志。",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "The character id to update. / 要更新的角色 ID。"},
            "loaded": {"type": "boolean", "description": "Whether the character is loaded/active. / 角色是否已加载/活跃。"},
        },
        "required": ["character_id", "loaded"],
    },
    handler=_character_set_loaded,
    action_kind=None,
)


register_tool(
    name="character_state_get",
    description="Read a character's raw A3.2 state record (hunger/thirst/health/mood/status). / 读取角色原始 A3.2 状态记录（饥饿/口渴/健康/心情/状态）。",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "The character id to read state for. / 要读取状态的角色 ID。"},
        },
        "required": ["character_id"],
    },
    handler=_character_state_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="character_state_update",
    description="Apply a numeric state patch (hunger/thirst/health/mood and max values) with clamping and logging. / 应用数字状态修补（饥饿/口渴/健康/心情及最大值），带钳制和日志记录。",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "The character id to update state for. / 要更新状态的角色 ID。"},
            "hunger": {"type": "number"},
            "thirst": {"type": "number"},
            "health": {"type": "number"},
            "mood": {"type": "number"},
            "max_hunger": {"type": "number"},
            "max_thirst": {"type": "number"},
            "max_health": {"type": "number"},
            "max_mood": {"type": "number"},
            "reason": {"type": "string", "description": "Reason tag written to the state log (default 'manual'). / 写入状态日志的原因标签（默认 'manual'）。"},
            "ref_id": {"type": "string", "description": "Optional traceability ref id. / 可选的可追溯性引用 ID。"},
        },
        "required": ["character_id"],
    },
    handler=_character_state_update,
    action_kind=None,
)


register_tool(
    name="character_state_summary",
    description="Read a character's JSON-friendly state summary (values, status, active behavior, modifiers). / 读取角色 JSON 友好的状态摘要（数值、状态、活跃行为、修正器）。",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "The character id to summarize. / 要摘要的角色 ID。"},
        },
        "required": ["character_id"],
    },
    handler=_character_state_summary,
    action_kind=None,
    annotations={"readOnlyHint": True},
)
