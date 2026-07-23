"""MCP tools for the character domain.

Wraps the in-memory character CRUD stub (:mod:`xijian_api.stubs.characters`)
and the A3.2 character-state stub (:mod:`xijian_api.stubs.character_state`)
as MCP tools registered with :mod:`xijian_api.mcp.registry`.

These are internal domain tools (``action_kind=None``): they only touch
in-memory state, so they skip the A5.2 gate and rely on the stubs' own
input validation.

Tools registered
----------------

Character CRUD:

* ``character_create``      — create a character
* ``character_list``        — list every character
* ``character_get``         — fetch a character by id
* ``character_update``      — patch mutable character fields
* ``character_delete``      — delete a character
* ``character_set_loaded``  — toggle the character's ``loaded`` flag

Character state (A3.2):

* ``character_state_get``     — read the raw state record
* ``character_state_update``  — apply a numeric state patch
* ``character_state_summary`` — read the JSON-friendly state summary
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import character_state as character_state_stub
from xijian_api.stubs import characters as characters_stub


# ---------------------------------------------------------------------------
# Character CRUD handlers
# ---------------------------------------------------------------------------


def _character_create(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    name = args.get("name")
    if not name:
        raise ToolError("name is required")
    payload: dict[str, Any] = {"name": name}
    for key in (
        "display_name", "persona_doc", "voice_profile",
        "live2d_model", "default_emotion", "tags",
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
    "live2d_model", "default_emotion", "tags",
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
# Character state (A3.2) handlers
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
# Registration
# ---------------------------------------------------------------------------


register_tool(
    name="character_create",
    description="Create a new character with persona, voice, and Live2D settings.",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Internal character name."},
            "display_name": {"type": "string", "description": "Display name shown to users."},
            "persona_doc": {"type": "string", "description": "Persona / background document text."},
            "voice_profile": {"type": "string", "description": "Voice profile identifier."},
            "live2d_model": {"type": "string", "description": "Live2D model identifier."},
            "default_emotion": {"type": "string", "description": "Default emotion label."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Free-form tags."},
        },
        "required": ["name"],
    },
    handler=_character_create,
    action_kind=None,
)


register_tool(
    name="character_list",
    description="List every character record.",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_character_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="character_get",
    description="Fetch a single character by id.",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "The character id to fetch."},
        },
        "required": ["character_id"],
    },
    handler=_character_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="character_update",
    description="Patch mutable character fields (name, persona, voice, Live2D, tags, ...).",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "The character id to update."},
            "name": {"type": "string"},
            "display_name": {"type": "string"},
            "persona_doc": {"type": "string"},
            "voice_profile": {"type": "string"},
            "live2d_model": {"type": "string"},
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
    description="Delete a character by id.",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "The character id to delete."},
        },
        "required": ["character_id"],
    },
    handler=_character_delete,
    action_kind=None,
    annotations={"destructiveHint": True},
)


register_tool(
    name="character_set_loaded",
    description="Set a character's loaded (active) flag.",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "The character id to update."},
            "loaded": {"type": "boolean", "description": "Whether the character is loaded/active."},
        },
        "required": ["character_id", "loaded"],
    },
    handler=_character_set_loaded,
    action_kind=None,
)


register_tool(
    name="character_state_get",
    description="Read a character's raw A3.2 state record (hunger/thirst/health/mood/status).",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "The character id to read state for."},
        },
        "required": ["character_id"],
    },
    handler=_character_state_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="character_state_update",
    description="Apply a numeric state patch (hunger/thirst/health/mood and max values) with clamping and logging.",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "The character id to update state for."},
            "hunger": {"type": "number"},
            "thirst": {"type": "number"},
            "health": {"type": "number"},
            "mood": {"type": "number"},
            "max_hunger": {"type": "number"},
            "max_thirst": {"type": "number"},
            "max_health": {"type": "number"},
            "max_mood": {"type": "number"},
            "reason": {"type": "string", "description": "Reason tag written to the state log (default 'manual')."},
            "ref_id": {"type": "string", "description": "Optional traceability ref id."},
        },
        "required": ["character_id"],
    },
    handler=_character_state_update,
    action_kind=None,
)


register_tool(
    name="character_state_summary",
    description="Read a character's JSON-friendly state summary (values, status, active behavior, modifiers).",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "The character id to summarize."},
        },
        "required": ["character_id"],
    },
    handler=_character_state_summary,
    action_kind=None,
    annotations={"readOnlyHint": True},
)
