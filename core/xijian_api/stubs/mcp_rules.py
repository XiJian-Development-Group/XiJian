"""Stub MCP-rule service — A5.2 in the function list v2.

The rulebook that the :func:`xijian_api.stubs.mcp.check` gate
consults *before* any desktop-control action is taken.  Each
rule says one of:

* ``mode=blacklist``  → on hit, deny the action
* ``mode=whitelist``  → on hit, allow the action; on miss, deny

The world's MCP policy (read by ``mcp.get_world_policy``) picks
the default for actions that don't match a rule: ``default=deny``
means a non-matching action is denied unless a whitelist rule
fires; ``default=allow`` means a non-matching action is allowed
unless a blacklist rule fires.

Action kinds
============

===============  =================================================
``file_delete``   Any ``os.remove`` / ``shutil.rmtree``-style call
``file_write``    Any file-mutating write (including the
                  ``settings.json`` file) — not delete
``file_read``     Any read against ``/etc``, ``/var``, or the
                  user-library directory.  Reads elsewhere are
                  not in scope (A5.2 spec only names sensitive
                  paths).
``shell``         Any ``subprocess.Popen`` / ``os.system``-style
                  shell exec — power tools, ``rm -rf``, etc.
``network``       Any outbound HTTP / fetch / socket connect
``app_launch``    Any "launch an app" command (``open -a`` /
                  ``start`` / ``xdg-open``)
``settings_modify`` Any mutation of the safety / overload
                  configuration files
``system_cmd``    ``shutdown`` / ``reboot`` / ``kill`` of
                  protected processes
===============  =================================================

Data model (mirrors §A5.2 SQL schema)
======================================

* ``id``         — ``mcpr_<12 hex>`` (PK)
* ``action_kind``— one of the eight kinds above
* ``pattern``    — the regex / literal the gate matches
* ``mode``       — ``"blacklist"`` or ``"whitelist"``
* ``severity``   — 1..5; 1 = advisory (logged only), 5 = hard
  block.  The gate currently treats every blocked action as
  ``denied`` regardless of severity; the severity is preserved
  for the audit log + the operator dashboard.
* ``is_active``  — bool; inactive rules are skipped without
  being deleted (operator A/B switch)

The hot-path matcher is :func:`match_action_rules` — it walks
only active rules of the requested ``action_kind`` and returns
those that hit.  A broken regex is logged and skipped — one
bad rule must not take down the gate.

Test surface
============

* :func:`create` / :func:`get` / :func:`list_active` /
  :func:`list_all` / :func:`update` / :func:`delete`
* :func:`match_action_rules` — hot path
* :func:`seed_default` / :func:`reset_for_testing`
"""

from __future__ import annotations

import logging
import re
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_mcp_rule_id
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.mcp_rules")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Action kinds.  Forward-compat: unknown kinds are accepted at
#: the stub level (operators may add new categories) but the
#: matcher skips them.  The kind set is the *public API*; new
#: kinds need a spec update.
KIND_FILE_DELETE = "file_delete"
KIND_FILE_WRITE = "file_write"
KIND_FILE_READ = "file_read"
KIND_SHELL = "shell"
KIND_NETWORK = "network"
KIND_APP_LAUNCH = "app_launch"
KIND_SETTINGS_MODIFY = "settings_modify"
KIND_SYSTEM_CMD = "system_cmd"

VALID_KINDS: frozenset[str] = frozenset({
    KIND_FILE_DELETE, KIND_FILE_WRITE, KIND_FILE_READ,
    KIND_SHELL, KIND_NETWORK, KIND_APP_LAUNCH,
    KIND_SETTINGS_MODIFY, KIND_SYSTEM_CMD,
})

#: Rule modes.  ``blacklist`` = block on hit; ``whitelist`` =
#: only allow on hit (a non-match then denies under
#: ``default=deny`` world policy).
MODE_BLACKLIST = "blacklist"
MODE_WHITELIST = "whitelist"
VALID_MODES: frozenset[str] = frozenset({MODE_BLACKLIST, MODE_WHITELIST})

#: Severity range.  1 = advisory (log only, do not block), 5 =
#: hard block.  Mirrors A5.1's severity semantics so the audit
#: log can be cross-referenced.
MIN_SEVERITY = 1
MAX_SEVERITY = 5
DEFAULT_SEVERITY = 3

#: Upper bound on the pattern length.  Beyond a few KB the rule
#: itself is almost certainly misconfigured.
MAX_PATTERN_LEN = 4_096


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MCPRuleError(ValueError):
    """Raised on rule validation errors."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _validate_kind(kind: Any) -> str:
    if not isinstance(kind, str) or not kind:
        raise MCPRuleError("action_kind is required")
    if kind not in VALID_KINDS:
        raise MCPRuleError(
            "action_kind must be one of %s, got %r"
            % (sorted(VALID_KINDS), kind)
        )
    return kind


def _validate_mode(mode: Any) -> str:
    if not isinstance(mode, str) or not mode:
        raise MCPRuleError("mode is required")
    if mode not in VALID_MODES:
        raise MCPRuleError(
            "mode must be one of %s, got %r"
            % (sorted(VALID_MODES), mode)
        )
    return mode


def _validate_pattern(pattern: Any) -> str:
    if not isinstance(pattern, str) or not pattern:
        raise MCPRuleError("pattern is required")
    if len(pattern) > MAX_PATTERN_LEN:
        raise MCPRuleError(
            "pattern too long: %d > %d" % (len(pattern), MAX_PATTERN_LEN)
        )
    return pattern


def _validate_severity(severity: Any) -> int:
    if isinstance(severity, bool) or not isinstance(severity, int):
        raise MCPRuleError(
            "severity must be an int, got %s" % type(severity).__name__
        )
    if severity < MIN_SEVERITY or severity > MAX_SEVERITY:
        raise MCPRuleError(
            "severity must be in [%d, %d], got %d"
            % (MIN_SEVERITY, MAX_SEVERITY, severity)
        )
    return severity


def _compile_pattern(action_kind: str, pattern: str) -> re.Pattern | None:
    """Compile a rule pattern.  All MCP rule patterns are
    treated as regex (the action kinds are diverse enough that
    literal substring matching would force operators to escape
    every metachar anyway).  Returns ``None`` if the regex
    doesn't compile — the caller is expected to log + skip.
    """
    try:
        return re.compile(pattern, re.IGNORECASE | re.DOTALL)
    except re.error as exc:
        _LOGGER.warning(
            "MCP rule pattern %r failed to compile: %s", pattern, exc
        )
        return None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create(
    *,
    action_kind: str,
    pattern: str,
    mode: str,
    severity: int = DEFAULT_SEVERITY,
    is_active: bool = True,
    rule_id: str | None = None,
    now: float | None = None,
) -> dict:
    """Create an MCP rule and return the record.

    Validates the pattern is a compilable regex.  Broken
    patterns are *not* rejected at create time — we still
    store them so the operator can fix them later.  The
    matcher :func:`match_action_rules` is the one that
    skips them.
    """
    _validate_kind(action_kind)
    _validate_mode(mode)
    _validate_pattern(pattern)
    _validate_severity(severity)
    new_id = rule_id or gen_mcp_rule_id()
    if new_id in state.mcp_rules:
        raise MCPRuleError("rule id %r already exists" % new_id)
    timestamp = float(now) if now is not None else now_ts()
    record = {
        "id": new_id,
        "action_kind": action_kind,
        "pattern": pattern,
        "mode": mode,
        "severity": int(severity),
        "is_active": bool(is_active),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    state.mcp_rules[new_id] = record
    return record


def get(rule_id: str) -> dict | None:
    return state.mcp_rules.get(rule_id)


def list_active(
    *,
    action_kind: str | None = None,
    mode: str | None = None,
) -> list[dict]:
    """Return every active rule, optionally filtered by kind
    and/or mode, sorted by severity desc then created_at asc
    (stable, highest-severity first on the hot path)."""
    out = [
        r for r in state.mcp_rules.values()
        if r.get("is_active")
        and (action_kind is None or r.get("action_kind") == action_kind)
        and (mode is None or r.get("mode") == mode)
    ]
    out.sort(key=lambda r: (-int(r.get("severity", 0)), r.get("created_at", 0.0)))
    return out


def list_all() -> list[dict]:
    """Return every rule (active + inactive), sorted by created_at
    asc.  Used by the operator dashboard."""
    out = list(state.mcp_rules.values())
    out.sort(key=lambda r: r.get("created_at", 0.0))
    return out


def update(rule_id: str, patch: dict) -> dict | None:
    """Patch mutable fields.  ``id`` and ``created_at`` are
    immutable; renames of a rule's ``action_kind`` / ``mode``
    are allowed (operators may want to flip a pattern from one
    category to another)."""
    record = state.mcp_rules.get(rule_id)
    if record is None:
        return None
    if "id" in patch or "created_at" in patch:
        raise MCPRuleError("id, created_at are immutable")
    for key, value in patch.items():
        if key == "action_kind":
            record["action_kind"] = _validate_kind(value)
        elif key == "mode":
            record["mode"] = _validate_mode(value)
        elif key == "pattern":
            record["pattern"] = _validate_pattern(value)
        elif key == "severity":
            record["severity"] = _validate_severity(value)
        elif key == "is_active":
            if not isinstance(value, bool):
                raise MCPRuleError(
                    "is_active must be a bool, got %s"
                    % type(value).__name__
                )
            record["is_active"] = value
    record["updated_at"] = now_ts()
    return record


def delete(rule_id: str) -> bool:
    """Delete a rule.  Returns True if it existed."""
    return state.mcp_rules.pop(rule_id, None) is not None


# ---------------------------------------------------------------------------
# Hot path
# ---------------------------------------------------------------------------


def match_action_rules(
    action_kind: str,
    payload: str,
) -> list[dict]:
    """Walk every active rule of ``action_kind`` and return those
    that hit ``payload``.

    Returns a list of matched rules (sorted by severity desc).
    An empty list means "no hits".  A broken regex is logged
    and skipped — one bad rule must not take down the gate.

    The ``payload`` is the canonicalised form of the
    tool-call argument.  The gate layer is responsible for
    flattening whatever the tool received into a string; the
    rulebook just runs the regex.  This keeps the rulebook
    side-effect-free and easy to test.
    """
    if not isinstance(payload, str) or not payload:
        return []
    if action_kind not in VALID_KINDS:
        # Unknown kind — refuse to match anything.  Better to
        # deny-by-default than to silently fall through to a
        # different kind's rules.
        return []
    out: list[dict] = []
    for rule in list_active(action_kind=action_kind):
        compiled = _compile_pattern(rule["action_kind"], rule["pattern"])
        if compiled is None:
            continue
        if compiled.search(payload):
            out.append(rule)
    # Already sorted by severity desc via list_active, but be
    # explicit so callers don't depend on internal order.
    out.sort(key=lambda r: -int(r.get("severity", 0)))
    return out


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.  No rules ship by default — the
    rulebook is operator-curated.  Future rule-bundle imports
    have a stable entry point here."""
    return None


def reset_for_testing() -> None:
    """Wipe every rule."""
    state.mcp_rules.clear()


__all__ = [
    # Constants
    "KIND_FILE_DELETE", "KIND_FILE_WRITE", "KIND_FILE_READ",
    "KIND_SHELL", "KIND_NETWORK", "KIND_APP_LAUNCH",
    "KIND_SETTINGS_MODIFY", "KIND_SYSTEM_CMD",
    "VALID_KINDS",
    "MODE_BLACKLIST", "MODE_WHITELIST", "VALID_MODES",
    "MIN_SEVERITY", "MAX_SEVERITY", "DEFAULT_SEVERITY",
    "MAX_PATTERN_LEN",
    # Errors
    "MCPRuleError",
    # Pure helpers
    "_validate_kind", "_validate_mode", "_validate_pattern",
    "_validate_severity", "_compile_pattern",
    # CRUD
    "create", "get", "list_active", "list_all", "update", "delete",
    # Hot path
    "match_action_rules",
    # Lifecycle
    "seed_default", "reset_for_testing",
]
