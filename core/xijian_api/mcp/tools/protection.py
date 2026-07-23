"""MCP tools for the A5.2 MCP protection management surface.

Wraps the rulebook stub (:mod:`xijian_api.stubs.mcp_rules`) and the
protection orchestrator stub (:mod:`xijian_api.stubs.mcp`) as MCP tools
registered with :mod:`xijian_api.mcp.registry`.

These are *management* tools — rule CRUD, world policy, safety-stop
lifecycle, audit queries, and snapshot dump/sanitize/restore.  They are
NOT the gate itself: the gate is :func:`xijian_api.stubs.mcp.check`,
which the registry runs automatically for any tool that declares an
``action_kind``.  Every tool here uses ``action_kind=None`` so the
management surface stays operable even while the gate is denying
desktop-control calls.

Tools registered
----------------

Rules:

* ``mcp_rule_list``    — list rules (active or all)
* ``mcp_rule_create``  — create a rule
* ``mcp_rule_get``     — fetch a rule by id
* ``mcp_rule_update``  — patch mutable rule fields
* ``mcp_rule_delete``  — delete a rule

World policy:

* ``mcp_policy_get``    — read the per-world MCP policy
* ``mcp_policy_set``    — mutate the per-world policy
* ``mcp_policy_reset``  — drop the per-world policy entry

Audit:

* ``mcp_audit_list``   — list audit entries (filtered)
* ``mcp_audit_count``  — count audit entries (filtered)

Safety stop:

* ``mcp_safety_stop_initiate`` — initiate a safety stop
* ``mcp_safety_stop_list``     — list freeze records
* ``mcp_safety_stop_get``      — fetch a freeze by id
* ``mcp_safety_stop_confirm``  — confirm (sanitize + restore)
* ``mcp_safety_stop_cancel``   — cancel a pending freeze

Snapshots:

* ``mcp_snapshot_list``     — list snapshot summaries
* ``mcp_snapshot_get``      — fetch a snapshot by id
* ``mcp_snapshot_create``   — dump a new snapshot
* ``mcp_snapshot_sanitize`` — sanitize a snapshot in place
* ``mcp_snapshot_restore``  — restore live state from a snapshot
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import mcp as mcp_stub
from xijian_api.stubs import mcp_rules as rules_stub


# ---------------------------------------------------------------------------
# Rule handlers
# ---------------------------------------------------------------------------


def _mcp_rule_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    active_only = bool(args.get("active_only", False))
    action_kind = args.get("action_kind")
    mode = args.get("mode")
    if active_only:
        return rules_stub.list_active(action_kind=action_kind, mode=mode)
    return rules_stub.list_all(action_kind=action_kind, mode=mode)


def _mcp_rule_create(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    action_kind = args.get("action_kind")
    if not action_kind:
        raise ToolError("action_kind is required")
    pattern = args.get("pattern")
    if not pattern:
        raise ToolError("pattern is required")
    mode = args.get("mode")
    if not mode:
        raise ToolError("mode is required")
    kwargs: dict[str, Any] = {
        "action_kind": action_kind,
        "pattern": pattern,
        "mode": mode,
    }
    if "severity" in args and args["severity"] is not None:
        kwargs["severity"] = args["severity"]
    if "is_active" in args and args["is_active"] is not None:
        kwargs["is_active"] = bool(args["is_active"])
    return rules_stub.create(**kwargs)


def _mcp_rule_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    rule_id = args.get("rule_id")
    if not rule_id:
        raise ToolError("rule_id is required")
    record = rules_stub.get(rule_id)
    if record is None:
        raise ToolError(f"rule {rule_id!r} not found")
    return record


_RULE_PATCH_FIELDS = ("action_kind", "pattern", "mode", "severity", "is_active")


def _mcp_rule_update(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    rule_id = args.get("rule_id")
    if not rule_id:
        raise ToolError("rule_id is required")
    patch: dict[str, Any] = {}
    for key in _RULE_PATCH_FIELDS:
        if key in args and args[key] is not None:
            patch[key] = args[key]
    if not patch:
        raise ToolError("at least one patch field is required")
    record = rules_stub.update(rule_id, patch)
    if record is None:
        raise ToolError(f"rule {rule_id!r} not found")
    return record


def _mcp_rule_delete(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    rule_id = args.get("rule_id")
    if not rule_id:
        raise ToolError("rule_id is required")
    if not rules_stub.delete(rule_id):
        raise ToolError(f"rule {rule_id!r} not found")
    return {"deleted": True, "rule_id": rule_id}


# ---------------------------------------------------------------------------
# World policy handlers
# ---------------------------------------------------------------------------


def _mcp_policy_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    return mcp_stub.get_world_policy(world_id)


def _mcp_policy_set(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    kwargs: dict[str, Any] = {}
    if "default" in args and args["default"] is not None:
        kwargs["default"] = args["default"]
    if "lockout_until" in args and args["lockout_until"] is not None:
        kwargs["lockout_until"] = args["lockout_until"]
    if "clear_lockout" in args and args["clear_lockout"] is not None:
        kwargs["clear_lockout"] = bool(args["clear_lockout"])
    return mcp_stub.set_world_policy(world_id, **kwargs)


def _mcp_policy_reset(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    dropped = mcp_stub.reset_world_policy(world_id)
    return {"reset": True, "world_id": world_id, "dropped": dropped}


# ---------------------------------------------------------------------------
# Audit handlers
# ---------------------------------------------------------------------------


def _mcp_audit_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    kwargs: dict[str, Any] = {}
    for key in ("action_kind", "world_id", "verdict"):
        if key in args and args[key] is not None:
            kwargs[key] = args[key]
    if "limit" in args and args["limit"] is not None:
        kwargs["limit"] = int(args["limit"])
    return mcp_stub.list_audit(**kwargs)


def _mcp_audit_count(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    kwargs: dict[str, Any] = {}
    for key in ("action_kind", "world_id", "verdict"):
        if key in args and args[key] is not None:
            kwargs[key] = args[key]
    return {"count": mcp_stub.count_audit(**kwargs)}


# ---------------------------------------------------------------------------
# Safety-stop handlers
# ---------------------------------------------------------------------------


def _mcp_safety_stop_initiate(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    kwargs: dict[str, Any] = {}
    for key in ("reason", "world_id", "source"):
        if key in args and args[key] is not None:
            kwargs[key] = args[key]
    return mcp_stub.safety_stop(**kwargs)


def _mcp_safety_stop_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    kwargs: dict[str, Any] = {}
    for key in ("world_id", "status"):
        if key in args and args[key] is not None:
            kwargs[key] = args[key]
    if "limit" in args and args["limit"] is not None:
        kwargs["limit"] = int(args["limit"])
    return mcp_stub.list_freezes(**kwargs)


def _mcp_safety_stop_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    freeze_id = args.get("freeze_id")
    if not freeze_id:
        raise ToolError("freeze_id is required")
    record = mcp_stub.get_freeze(freeze_id)
    if record is None:
        raise ToolError(f"freeze {freeze_id!r} not found")
    return record


def _mcp_safety_stop_confirm(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    freeze_id = args.get("freeze_id")
    if not freeze_id:
        raise ToolError("freeze_id is required")
    return mcp_stub.confirm_safety_stop(freeze_id)


def _mcp_safety_stop_cancel(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    freeze_id = args.get("freeze_id")
    if not freeze_id:
        raise ToolError("freeze_id is required")
    kwargs: dict[str, Any] = {}
    if "reason" in args and args["reason"] is not None:
        kwargs["reason"] = args["reason"]
    return mcp_stub.cancel_safety_stop(freeze_id, **kwargs)


# ---------------------------------------------------------------------------
# Snapshot handlers
# ---------------------------------------------------------------------------


def _mcp_snapshot_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    kwargs: dict[str, Any] = {}
    for key in ("world_id", "reason"):
        if key in args and args[key] is not None:
            kwargs[key] = args[key]
    if "limit" in args and args["limit"] is not None:
        kwargs["limit"] = int(args["limit"])
    return mcp_stub.list_snapshots(**kwargs)


def _mcp_snapshot_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    snapshot_id = args.get("snapshot_id")
    if not snapshot_id:
        raise ToolError("snapshot_id is required")
    record = mcp_stub.get_snapshot(snapshot_id)
    if record is None:
        raise ToolError(f"snapshot {snapshot_id!r} not found")
    return record


def _mcp_snapshot_create(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    kwargs: dict[str, Any] = {}
    if "world_id" in args and args["world_id"] is not None:
        kwargs["world_id"] = args["world_id"]
    if "reason" in args and args["reason"] is not None:
        kwargs["reason"] = args["reason"]
    return mcp_stub.dump_snapshot(**kwargs)


def _mcp_snapshot_sanitize(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    snapshot_id = args.get("snapshot_id")
    if not snapshot_id:
        raise ToolError("snapshot_id is required")
    return mcp_stub.sanitize_snapshot(snapshot_id)


def _mcp_snapshot_restore(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    snapshot_id = args.get("snapshot_id")
    if not snapshot_id:
        raise ToolError("snapshot_id is required")
    return mcp_stub.restore_snapshot(snapshot_id)


# ---------------------------------------------------------------------------
# Registration — rules
# ---------------------------------------------------------------------------


register_tool(
    name="mcp_rule_list",
    description="List MCP rules. Set active_only=true to return only active rules.",
    input_schema={
        "type": "object",
        "properties": {
            "active_only": {"type": "boolean", "description": "If true, return only active rules."},
            "action_kind": {"type": "string", "description": "Filter by action kind (e.g. 'shell', 'file_delete')."},
            "mode": {"type": "string", "description": "Filter by mode ('blacklist' or 'whitelist')."},
        },
        "required": [],
    },
    handler=_mcp_rule_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="mcp_rule_create",
    description="Create an MCP protection rule (blacklist/whitelist entry for the gate).",
    input_schema={
        "type": "object",
        "properties": {
            "action_kind": {"type": "string", "description": "One of the 8 A5.2 action kinds."},
            "pattern": {"type": "string", "description": "Regex pattern the gate matches against flattened tool args."},
            "mode": {"type": "string", "description": "'blacklist' (block on hit) or 'whitelist' (allow on hit)."},
            "severity": {"type": "integer", "description": "1..5 (1 advisory, 5 hard block). Defaults to 3."},
            "is_active": {"type": "boolean", "description": "Whether the rule is active. Defaults to true."},
        },
        "required": ["action_kind", "pattern", "mode"],
    },
    handler=_mcp_rule_create,
    action_kind=None,
)


register_tool(
    name="mcp_rule_get",
    description="Fetch a single MCP rule by id.",
    input_schema={
        "type": "object",
        "properties": {
            "rule_id": {"type": "string", "description": "The rule id to fetch."},
        },
        "required": ["rule_id"],
    },
    handler=_mcp_rule_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="mcp_rule_update",
    description="Patch mutable MCP rule fields (action_kind, pattern, mode, severity, is_active).",
    input_schema={
        "type": "object",
        "properties": {
            "rule_id": {"type": "string", "description": "The rule id to update."},
            "action_kind": {"type": "string"},
            "pattern": {"type": "string"},
            "mode": {"type": "string"},
            "severity": {"type": "integer"},
            "is_active": {"type": "boolean"},
        },
        "required": ["rule_id"],
    },
    handler=_mcp_rule_update,
    action_kind=None,
)


register_tool(
    name="mcp_rule_delete",
    description="Delete an MCP rule by id.",
    input_schema={
        "type": "object",
        "properties": {
            "rule_id": {"type": "string", "description": "The rule id to delete."},
        },
        "required": ["rule_id"],
    },
    handler=_mcp_rule_delete,
    action_kind=None,
    annotations={"destructiveHint": True},
)


# ---------------------------------------------------------------------------
# Registration — world policy
# ---------------------------------------------------------------------------


register_tool(
    name="mcp_policy_get",
    description="Read the per-world MCP policy (default verdict + lockout_until).",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to read policy for."},
        },
        "required": ["world_id"],
    },
    handler=_mcp_policy_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="mcp_policy_set",
    description=(
        "Mutate the per-world MCP policy. Pass 'default' (allow/deny), "
        "'lockout_until' (unix ts), and/or 'clear_lockout' (true to drop lockout)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to update policy for."},
            "default": {"type": "string", "description": "Default verdict for non-matching actions: 'allow' or 'deny'."},
            "lockout_until": {"type": "number", "description": "Unix ts until which the world is locked out."},
            "clear_lockout": {"type": "boolean", "description": "If true, clear any active lockout (cold-restart reset)."},
        },
        "required": ["world_id"],
    },
    handler=_mcp_policy_set,
    action_kind=None,
)


register_tool(
    name="mcp_policy_reset",
    description="Drop the per-world MCP policy entry so the world starts from defaults.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to reset policy for."},
        },
        "required": ["world_id"],
    },
    handler=_mcp_policy_reset,
    action_kind=None,
    annotations={"destructiveHint": True},
)


# ---------------------------------------------------------------------------
# Registration — audit
# ---------------------------------------------------------------------------


register_tool(
    name="mcp_audit_list",
    description="List MCP audit entries (per-call verdicts), newest-first, with optional filters.",
    input_schema={
        "type": "object",
        "properties": {
            "action_kind": {"type": "string", "description": "Filter by action kind."},
            "world_id": {"type": "string", "description": "Filter by world id."},
            "verdict": {"type": "string", "description": "Filter by verdict (allowed/denied/denied_lockout/...)."},
            "limit": {"type": "integer", "description": "Max entries to return (default 50)."},
        },
        "required": [],
    },
    handler=_mcp_audit_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="mcp_audit_count",
    description="Count MCP audit entries matching the given filters.",
    input_schema={
        "type": "object",
        "properties": {
            "action_kind": {"type": "string", "description": "Filter by action kind."},
            "world_id": {"type": "string", "description": "Filter by world id."},
            "verdict": {"type": "string", "description": "Filter by verdict."},
        },
        "required": [],
    },
    handler=_mcp_audit_count,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


# ---------------------------------------------------------------------------
# Registration — safety stop
# ---------------------------------------------------------------------------


register_tool(
    name="mcp_safety_stop_initiate",
    description="Initiate a safety stop (freeze MCP, dump snapshot, await confirm/cancel).",
    input_schema={
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "Reason for the safety stop."},
            "world_id": {"type": "string", "description": "World id to freeze."},
            "source": {"type": "string", "description": "Source of the trigger (e.g. 'hotkey', 'api')."},
        },
        "required": [],
    },
    handler=_mcp_safety_stop_initiate,
    action_kind=None,
)


register_tool(
    name="mcp_safety_stop_list",
    description="List safety-stop freeze records, newest-first, with optional filters.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Filter by world id."},
            "status": {"type": "string", "description": "Filter by status (frozen/awaiting_confirm/restored/cancelled/lockout)."},
            "limit": {"type": "integer", "description": "Max entries to return (default 50)."},
        },
        "required": [],
    },
    handler=_mcp_safety_stop_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="mcp_safety_stop_get",
    description="Fetch a single safety-stop freeze record by id.",
    input_schema={
        "type": "object",
        "properties": {
            "freeze_id": {"type": "string", "description": "The freeze id to fetch."},
        },
        "required": ["freeze_id"],
    },
    handler=_mcp_safety_stop_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="mcp_safety_stop_confirm",
    description="Confirm a safety stop: sanitize the snapshot and restore live state from it.",
    input_schema={
        "type": "object",
        "properties": {
            "freeze_id": {"type": "string", "description": "The freeze id to confirm."},
        },
        "required": ["freeze_id"],
    },
    handler=_mcp_safety_stop_confirm,
    action_kind=None,
)


register_tool(
    name="mcp_safety_stop_cancel",
    description="Cancel a pending safety stop (keeps the freeze on disk for inspection).",
    input_schema={
        "type": "object",
        "properties": {
            "freeze_id": {"type": "string", "description": "The freeze id to cancel."},
            "reason": {"type": "string", "description": "Optional reason recorded on the freeze."},
        },
        "required": ["freeze_id"],
    },
    handler=_mcp_safety_stop_cancel,
    action_kind=None,
)


# ---------------------------------------------------------------------------
# Registration — snapshots
# ---------------------------------------------------------------------------


register_tool(
    name="mcp_snapshot_list",
    description="List MCP snapshot summaries (without payload), newest-first, with optional filters.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Filter by world id."},
            "reason": {"type": "string", "description": "Filter by reason (safety_stop/manual/pre_freeze)."},
            "limit": {"type": "integer", "description": "Max entries to return (default 50)."},
        },
        "required": [],
    },
    handler=_mcp_snapshot_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="mcp_snapshot_get",
    description="Fetch a single MCP snapshot by id (includes payload).",
    input_schema={
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string", "description": "The snapshot id to fetch."},
        },
        "required": ["snapshot_id"],
    },
    handler=_mcp_snapshot_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="mcp_snapshot_create",
    description="Dump a new MCP snapshot of the protected state buckets (worlds/characters/memory/sessions).",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Optional world id to scope the snapshot."},
            "reason": {"type": "string", "description": "Reason tag (safety_stop/manual/pre_freeze). Defaults to 'pre_freeze'."},
        },
        "required": [],
    },
    handler=_mcp_snapshot_create,
    action_kind=None,
)


register_tool(
    name="mcp_snapshot_sanitize",
    description="Sanitize a snapshot in place (strip A5.1 forbidden-word substrings from string leaves).",
    input_schema={
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string", "description": "The snapshot id to sanitize."},
        },
        "required": ["snapshot_id"],
    },
    handler=_mcp_snapshot_sanitize,
    action_kind=None,
)


register_tool(
    name="mcp_snapshot_restore",
    description="Restore live state from a snapshot (sanitizes first if not already sanitized).",
    input_schema={
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string", "description": "The snapshot id to restore from."},
        },
        "required": ["snapshot_id"],
    },
    handler=_mcp_snapshot_restore,
    action_kind=None,
    annotations={"destructiveHint": True},
)
