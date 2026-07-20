"""Stub MCP (Model Context Protocol) protection service — A5.2.

Sits on top of :mod:`mcp_rules` (rulebook) and the four
:mod:`state` buckets (``mcp_rules`` / ``mcp_audit`` /
``mcp_freezes`` / ``mcp_snapshots``).  Exposes three
concerns:

* :func:`check`  — the hot-path gate.  The desktop client
  calls this *before* running any tool / shell / file-mutation
  command.  Returns ``allow`` / ``denied`` / ``denied_lockout``
  / ``denied_crashed`` plus the matching rule and the audit
  log id (AC-1: "黑名单动作 100% 拦截" → verifiable in the
  audit log).
* :func:`safety_stop` + :func:`confirm_safety_stop` +
  :func:`cancel_safety_stop` — the safety-stop state
  machine.  The global hotkey (or a programmatic call) freezes
  MCP, dumps a context snapshot, and waits for the user to
  either confirm ("清理并恢复") or cancel.  Three freezes within
  60 s flip the system to lockout and refuse further freezes
  until a cold-restart reset.
* :func:`dump_snapshot` + :func:`sanitize_snapshot` +
  :func:`restore_snapshot` — the "专用备份文件夹" half of
  the spec.  Dump covers the "受保护模块" (AC-4):
  ``state.worlds`` / ``state.characters`` / ``state.memory`` /
  ``state.sessions``.  Sanitize reuses A5.1's
  ``forbidden_word`` rules to strip sensitive substrings from
  the payload before it's eligible for restore.

Decision tree — :func:`check`
=============================

::

    [tool call] → flatten args → action_kind
        │
        ├── system in lockout? → denied_lockout + audit
        ├── world has a pending freeze? → denied (no rule needed) + audit
        │
        ▼
    run match_action_rules(action_kind, payload)
        │
        ├── any blacklist hit → denied + audit (rule_id = highest-severity)
        ├── any whitelist hit + policy.default=deny → allowed
        ├── no whitelist hit + policy.default=deny → denied (no rule)
        ├── any whitelist hit + policy.default=allow → allowed
        ├── no whitelist hit + policy.default=allow → allowed
        │
        ▼
    self-crash fallback → denied_crashed + audit
    (any unexpected exception is treated as deny; spec 边界场景)

World policy
============

The "default" knob lives per-world:
``get_world_policy(world_id) → {default, lockout_until}``.

* ``default=deny`` (recommended): if no rule matches, deny.
  Whitelist rules opt actions in.
* ``default=allow``: if no rule matches, allow.  Blacklist
  rules opt actions out.

The ``lockout_until`` is the unix-ts at which the world leaves
the lockout state.  Until then, every ``check()`` returns
``denied_lockout`` and every ``safety_stop()`` returns
``409 lockout_active``.

A5.4 cross-link
===============

When A5.4 overload is in a recovery window, MCP ``check()``
returns ``allowed`` *with* a ``blocked='overload_active'``
marker (mirror of A5.1's pattern).  MCP does **not** escalate
to denial here — the system as a whole is paused, and the
desktop client should be the one refusing the call.  MCP's
job is "is this action allowed once the system resumes?", and
yes, it is.

Test surface
============

* :func:`check` (the gate) / :func:`list_audit` / :func:`count_audit`
* :func:`safety_stop` / :func:`list_freezes` / :func:`get_freeze`
* :func:`confirm_safety_stop` / :func:`cancel_safety_stop`
* :func:`dump_snapshot` / :func:`sanitize_snapshot` /
  :func:`restore_snapshot` / :func:`list_snapshots`
* :func:`get_world_policy` / :func:`set_world_policy` /
  :func:`reset_world_policy`
* :func:`seed_default` / :func:`reset_for_testing`
"""

from __future__ import annotations

import copy
import logging
import re
import threading
from typing import Any

from xijian_api.stubs import mcp_rules as rules_stub
from xijian_api.stubs import state
from xijian_api.utils.ids import (
    gen_mcp_audit_id,
    gen_mcp_freeze_id,
    gen_mcp_snapshot_id,
)
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.mcp")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Verdict values for the audit log.  String-typed for JSON
#: friendliness and human-readable grep.
VERDICT_ALLOWED = "allowed"
VERDICT_DENIED = "denied"
VERDICT_DENIED_LOCKOUT = "denied_lockout"
VERDICT_DENIED_FROZEN = "denied_frozen"
VERDICT_DENIED_CRASHED = "denied_crashed"
VALID_VERDICTS: frozenset[str] = frozenset({
    VERDICT_ALLOWED, VERDICT_DENIED,
    VERDICT_DENIED_LOCKOUT, VERDICT_DENIED_FROZEN,
    VERDICT_DENIED_CRASHED,
})

#: Safety-stop status values.  ``frozen`` is the initial state
#: (right after the hotkey fires), ``awaiting_confirm`` is
#: the same with a snapshot already on disk, ``sanitizing`` is
#: when the user clicked confirm and we're running
#: ``sanitize_snapshot``, ``restored`` is the happy-path end,
#: ``cancelled`` is the user-clicked-cancel end,
#: ``lockout`` is the 3-in-60s case.
FREEZE_FROZEN = "frozen"
FREEZE_AWAITING_CONFIRM = "awaiting_confirm"
FREEZE_SANITIZING = "sanitizing"
FREEZE_RESTORED = "restored"
FREEZE_CANCELLED = "cancelled"
FREEZE_LOCKOUT = "lockout"
VALID_FREEZE_STATUSES: frozenset[str] = frozenset({
    FREEZE_FROZEN, FREEZE_AWAITING_CONFIRM, FREEZE_SANITIZING,
    FREEZE_RESTORED, FREEZE_CANCELLED, FREEZE_LOCKOUT,
})

#: Snapshot reasons — mirrors the A5.3 spec's `reason` enum.
#: A5.2 only emits the two that A5.2 spec cares about; the
#: rest are reserved for the future A5.3 hand-off.
SNAPSHOT_REASON_SAFETY_STOP = "safety_stop"
SNAPSHOT_REASON_MANUAL = "manual"
SNAPSHOT_REASON_PRE_FREEZE = "pre_freeze"
VALID_SNAPSHOT_REASONS: frozenset[str] = frozenset({
    SNAPSHOT_REASON_SAFETY_STOP, SNAPSHOT_REASON_MANUAL,
    SNAPSHOT_REASON_PRE_FREEZE,
})

#: World-policy defaults.  ``deny`` = no-match denies (the
#: recommended posture; matches the spec's "黑名单/白名单"
#: wording); ``allow`` = no-match allows.
POLICY_DEFAULT_DENY = "deny"
POLICY_DEFAULT_ALLOW = "allow"
VALID_POLICY_DEFAULTS: frozenset[str] = frozenset({
    POLICY_DEFAULT_DENY, POLICY_DEFAULT_ALLOW,
})

#: Window over which "3 freezes → lockout" is counted.  A5.2
#: spec doesn't pin a number; 60 s is the round value we
#: picked.  Operators can override via :func:`set_lockout_window`.
DEFAULT_LOCKOUT_WINDOW_SECONDS = 60.0
#: Threshold for triggering lockout.
DEFAULT_LOCKOUT_THRESHOLD = 3
#: How long the lockout state persists before ``check()``
#: auto-clears.  Spec says "cold restart" but for the
#: stub-driven test cycle we want a finite timeout; a real
#: cold restart requires the operator to call
#: :func:`clear_lockout` (which mirrors the
#: "kill -9 + restart" path from the spec's 边界场景).
DEFAULT_LOCKOUT_DURATION_SECONDS = 600.0

#: Per-world MCP policy overrides.  Default posture is
#: ``default=deny`` and no active lockout.
_WORLD_POLICY: dict[str, dict] = {}

#: Per-world in-flight freeze tracker — used to gate
#: :func:`check` while a safety-stop is in flight (we want
#: the user to be able to act on the confirmation dialog
#: without the model continuing to call tools in the
#: background).  Keyed by world_id; each value is a list
#: of freeze_ids that are not yet ``restored`` /
#: ``cancelled``.
_PENDING_FREEZES: dict[str, list[str]] = {}

#: Recent-freeze history per world — used to compute
#: "3 freezes in 60 s → lockout".  Pruned to the window on
#: every insert.
_FREEZE_HISTORY: dict[str, list[float]] = {}

#: Monotonic insert-sequence counter.  Same trick as
#: :mod:`safety` — the audit log and the freeze log sort
#: by ``(ts, _seq)`` so same-second inserts get a stable
#: order.
_AUDIT_SEQUENCE: int = 0
_FREEZE_SEQUENCE: int = 0
_SNAPSHOT_SEQUENCE: int = 0

#: Module-level lock for the multi-bucket mutations.  The
#: safety-stop flow touches ``_PENDING_FREEZES`` +
#: ``_FREEZE_HISTORY`` + ``state.mcp_freezes`` in one
#: critical section, so we serialise it.
_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MCPError(ValueError):
    """Raised on MCP-stub validation errors."""


class MCPLockoutError(MCPError):
    """Raised when the world is in lockout and the caller is
    asking for a new safety_stop.  Route layer turns this
    into a 409."""


class MCPFrozenError(MCPError):
    """Raised when the world has a pending safety_stop and the
    caller is asking for a new one.  Route layer turns this
    into a 409."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _now_or(value: float | None) -> float:
    """Return ``value`` if non-None, else :func:`now_ts`."""
    if value is None:
        return float(now_ts())
    return float(value)


def _is_overload_active() -> bool:
    """True if A5.4 overload is in a recovery window.  Mirrors
    the helpers in A4.1 / A4.4 / A5.1 — direct state read to
    avoid a hard import cycle."""
    recovery = (state.overload or {}).get("recovery")
    if not recovery:
        return False
    return recovery.get("status") in {"waiting", "first_confirmed"}


def _flatten_payload(args: Any, max_depth: int = 4, _depth: int = 0) -> str:
    """Canonicalise the tool-call args into a flat string for
    regex matching.  JSON / dict / list / scalars all funnel
    through here.  We bound depth so a runaway nested arg
    doesn't blow the regex on the rulebook side.

    Bounded to ``max_depth`` levels; beyond that we truncate
    with a sentinel.  Returns ``""`` for ``None`` / empty.
    """
    if _depth > max_depth:
        return "[truncated]"
    if args is None:
        return ""
    if isinstance(args, str):
        return args
    if isinstance(args, (int, float, bool)):
        return str(args)
    if isinstance(args, dict):
        parts = []
        for k, v in args.items():
            parts.append("%s=%s" % (k, _flatten_payload(v, max_depth, _depth + 1)))
        return " ".join(parts)
    if isinstance(args, (list, tuple)):
        return " ".join(_flatten_payload(v, max_depth, _depth + 1) for v in args)
    return str(args)


def _truncate(text: str, limit: int = 240) -> str:
    """Bound the snippet we store in the audit log.  240 chars
    matches the spec's snippet suggestion (same value A5.1
    uses)."""
    if not isinstance(text, str):
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _seq_next(kind: str) -> int:
    """Increment and return the per-bucket sequence counter."""
    global _AUDIT_SEQUENCE, _FREEZE_SEQUENCE, _SNAPSHOT_SEQUENCE
    if kind == "audit":
        _AUDIT_SEQUENCE += 1
        return _AUDIT_SEQUENCE
    if kind == "freeze":
        _FREEZE_SEQUENCE += 1
        return _FREEZE_SEQUENCE
    if kind == "snapshot":
        _SNAPSHOT_SEQUENCE += 1
        return _SNAPSHOT_SEQUENCE
    raise MCPError("unknown sequence kind: %r" % kind)


# ---------------------------------------------------------------------------
# World policy
# ---------------------------------------------------------------------------


def get_world_policy(world_id: str | None) -> dict:
    """Return the per-world MCP policy.  Missing world → the
    default (``default=deny``, no lockout)."""
    if not world_id:
        return {"default": POLICY_DEFAULT_DENY, "lockout_until": None}
    return _WORLD_POLICY.get(world_id, {
        "default": POLICY_DEFAULT_DENY,
        "lockout_until": None,
    })


def set_world_policy(
    world_id: str,
    *,
    default: str | None = None,
    lockout_until: float | None = None,
    clear_lockout: bool = False,
) -> dict:
    """Mutate the per-world MCP policy.  Both fields are
    optional; pass ``clear_lockout=True`` to drop
    ``lockout_until`` (the operator-driven "cold restart
    reset" path)."""
    if not isinstance(world_id, str) or not world_id:
        raise MCPError("world_id is required")
    if default is not None and default not in VALID_POLICY_DEFAULTS:
        raise MCPError(
            "default must be one of %s, got %r"
            % (sorted(VALID_POLICY_DEFAULTS), default)
        )
    current = get_world_policy(world_id)
    new_default = default if default is not None else current.get("default", POLICY_DEFAULT_DENY)
    if clear_lockout:
        new_lockout_until: float | None = None
    elif lockout_until is not None:
        new_lockout_until = float(lockout_until)
    else:
        new_lockout_until = current.get("lockout_until")
    _WORLD_POLICY[world_id] = {
        "default": new_default,
        "lockout_until": new_lockout_until,
    }
    return _WORLD_POLICY[world_id]


def reset_world_policy(world_id: str) -> int:
    """Drop the per-world policy entry.  Called by the world
    reset flow so a reset world starts with the defaults."""
    return 1 if _WORLD_POLICY.pop(world_id, None) is not None else 0


def _is_world_locked_out(world_id: str | None, *, now: float | None = None) -> bool:
    """Return True if the world is currently in lockout.
    Auto-clears if the ``lockout_until`` deadline has passed."""
    if not world_id:
        return False
    policy = get_world_policy(world_id)
    until = policy.get("lockout_until")
    if until is None:
        return False
    moment = float(now) if now is not None else now_ts()
    if moment >= float(until):
        # Auto-clear stale lockout.
        _WORLD_POLICY[world_id] = {
            "default": policy.get("default", POLICY_DEFAULT_DENY),
            "lockout_until": None,
        }
        return False
    return True


def clear_lockout(world_id: str) -> dict:
    """Operator-driven "cold restart" — drop the lockout
    state.  The spec's 边界场景 calls for a full restart but
    for the stub we expose the explicit clear (mirrors the
    "kill -9 + restart" recovery)."""
    return set_world_policy(world_id, clear_lockout=True)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def record_audit(
    *,
    action_kind: str,
    payload: str,
    verdict: str,
    rule_id: str | None = None,
    world_id: str | None = None,
    reason: str | None = None,
    now: float | None = None,
) -> dict:
    """Append an MCP-audit entry.  Returns the stored record."""
    if verdict not in VALID_VERDICTS:
        raise MCPError(
            "verdict must be one of %s, got %r"
            % (sorted(VALID_VERDICTS), verdict)
        )
    record_id = gen_mcp_audit_id()
    sequence = _seq_next("audit")
    entry = {
        "id": record_id,
        "action_kind": action_kind,
        "args_summary": _truncate(payload),
        "verdict": verdict,
        "rule_id": rule_id,
        "world_id": world_id,
        "reason": reason,
        "created_at": _now_or(now),
        "_seq": sequence,
    }
    state.mcp_audit[record_id] = entry
    return entry


def list_audit(
    *,
    action_kind: str | None = None,
    world_id: str | None = None,
    verdict: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return audit entries newest-first, optionally filtered."""
    out: list[dict] = []
    for entry in state.mcp_audit.values():
        if action_kind is not None and entry.get("action_kind") != action_kind:
            continue
        if world_id is not None and entry.get("world_id") != world_id:
            continue
        if verdict is not None and entry.get("verdict") != verdict:
            continue
        out.append(entry)
    out.sort(
        key=lambda e: (e.get("created_at", 0.0), e.get("_seq", 0)),
        reverse=True,
    )
    if limit < 1:
        limit = 1
    return out[:limit]


def count_audit(
    *,
    action_kind: str | None = None,
    world_id: str | None = None,
    verdict: str | None = None,
) -> int:
    """Count audit entries matching the given filter.  Used by
    AC-1 dashboards ("黑名单拦截率 100%")."""
    out = 0
    for entry in state.mcp_audit.values():
        if action_kind is not None and entry.get("action_kind") != action_kind:
            continue
        if world_id is not None and entry.get("world_id") != world_id:
            continue
        if verdict is not None and entry.get("verdict") != verdict:
            continue
        out += 1
    return out


# ---------------------------------------------------------------------------
# Gate — the hot path
# ---------------------------------------------------------------------------


def _verdict_for_match(
    rule: dict,
    policy_default: str,
) -> str:
    """Given a matched rule + the world default, decide whether
    the call is allowed.

    * ``mode=blacklist`` → always denied on hit
    * ``mode=whitelist`` → always allowed on hit (this is a
      hit, so we say yes regardless of default)

    Note: the second branch is the "explicit opt-in" path —
    the rule is a positive grant, not a default-allow fallback.
    """
    mode = rule.get("mode")
    if mode == rules_stub.MODE_BLACKLIST:
        return VERDICT_DENIED
    if mode == rules_stub.MODE_WHITELIST:
        return VERDICT_ALLOWED
    # Defensive — should be unreachable because the rulebook
    # validates mode on create/patch.  Treat unknown as deny.
    return VERDICT_DENIED


def check(
    *,
    action_kind: str,
    args: Any = None,
    world_id: str | None = None,
    now: float | None = None,
) -> dict:
    """Gate.  Returns ``{"verdict", "blocked", "matched_rule",
    "audit_id", "freeze_id"}`` (the last two may be None).

    Hot path.  Guarded by a top-level try/except so a
    rulebook crash is treated as ``denied_crashed`` (spec
    边界场景: don't let a buggy rule take down the gate by
    accident).
    """
    payload = _flatten_payload(args)
    try:
        # A5.4 overload — same pattern as A5.1: the system is
        # in a recovery window, so we let the call through with
        # a marker rather than escalating to a hard denial.
        # The desktop client is the one that should refuse the
        # call while the system is paused; MCP's job is to
        # answer "is this action allowed once the system
        # resumes?", and yes, it is.
        if _is_overload_active():
            entry = record_audit(
                action_kind=action_kind, payload=payload,
                verdict=VERDICT_ALLOWED,
                world_id=world_id, reason="overload_active_short_circuit",
                now=now,
            )
            return {
                "verdict": VERDICT_ALLOWED,
                "blocked": "overload_active",
                "matched_rule": None,
                "audit_id": entry["id"],
                "freeze_id": None,
            }
        # Lockout short-circuits everything.
        if _is_world_locked_out(world_id, now=now):
            entry = record_audit(
                action_kind=action_kind, payload=payload,
                verdict=VERDICT_DENIED_LOCKOUT,
                world_id=world_id, reason="world_lockout",
                now=now,
            )
            return {
                "verdict": VERDICT_DENIED_LOCKOUT,
                "blocked": "world_lockout",
                "matched_rule": None,
                "audit_id": entry["id"],
                "freeze_id": None,
            }
        # Pending freeze on this world: deny + audit (no rule
        # needed).  We don't return the freeze_id here because
        # the caller (the desktop client) is the one in the
        # middle of the safety-stop handshake; the desktop
        # already knows which freeze it's acting on.
        if _pending_freeze_id(world_id) is not None:
            entry = record_audit(
                action_kind=action_kind, payload=payload,
                verdict=VERDICT_DENIED_FROZEN,
                world_id=world_id, reason="world_frozen",
                now=now,
            )
            return {
                "verdict": VERDICT_DENIED_FROZEN,
                "blocked": "world_frozen",
                "matched_rule": None,
                "audit_id": entry["id"],
                "freeze_id": None,
            }
        matches = rules_stub.match_action_rules(action_kind, payload)
        policy = get_world_policy(world_id)
        default = policy.get("default", POLICY_DEFAULT_DENY)
        # 1) Any blacklist hit → deny.
        blacklist_hits = [m for m in matches if m.get("mode") == rules_stub.MODE_BLACKLIST]
        if blacklist_hits:
            match = blacklist_hits[0]  # sorted by severity desc
            entry = record_audit(
                action_kind=action_kind, payload=payload,
                verdict=VERDICT_DENIED,
                rule_id=match["id"], world_id=world_id,
                reason="blacklist_hit",
                now=now,
            )
            return {
                "verdict": VERDICT_DENIED,
                "blocked": "blacklist_hit",
                "matched_rule": match,
                "audit_id": entry["id"],
                "freeze_id": None,
            }
        # 2) Any whitelist hit → allow (this is a positive grant).
        whitelist_hits = [m for m in matches if m.get("mode") == rules_stub.MODE_WHITELIST]
        if whitelist_hits:
            match = whitelist_hits[0]
            entry = record_audit(
                action_kind=action_kind, payload=payload,
                verdict=VERDICT_ALLOWED,
                rule_id=match["id"], world_id=world_id,
                reason="whitelist_hit",
                now=now,
            )
            return {
                "verdict": VERDICT_ALLOWED,
                "blocked": None,
                "matched_rule": match,
                "audit_id": entry["id"],
                "freeze_id": None,
            }
        # 3) No match → defer to the world default.
        if default == POLICY_DEFAULT_ALLOW:
            entry = record_audit(
                action_kind=action_kind, payload=payload,
                verdict=VERDICT_ALLOWED,
                world_id=world_id, reason="default_allow_no_match",
                now=now,
            )
            return {
                "verdict": VERDICT_ALLOWED,
                "blocked": None,
                "matched_rule": None,
                "audit_id": entry["id"],
                "freeze_id": None,
            }
        entry = record_audit(
            action_kind=action_kind, payload=payload,
            verdict=VERDICT_DENIED,
            world_id=world_id, reason="default_deny_no_match",
            now=now,
        )
        return {
            "verdict": VERDICT_DENIED,
            "blocked": "default_deny_no_match",
            "matched_rule": None,
            "audit_id": entry["id"],
            "freeze_id": None,
        }
    except Exception as exc:  # noqa: BLE001 — spec fallback
        _LOGGER.warning("MCP check crashed: %s", exc)
        entry = record_audit(
            action_kind=action_kind, payload=payload,
            verdict=VERDICT_DENIED_CRASHED,
            world_id=world_id,
            reason="check_crashed: %s" % type(exc).__name__,
            now=now,
        )
        return {
            "verdict": VERDICT_DENIED_CRASHED,
            "blocked": "check_crashed",
            "matched_rule": None,
            "audit_id": entry["id"],
            "freeze_id": None,
        }


# ---------------------------------------------------------------------------
# Freeze state machine
# ---------------------------------------------------------------------------


def _pending_freeze_id(world_id: str | None) -> str | None:
    if not world_id:
        return None
    pending = _PENDING_FREEZES.get(world_id) or []
    if not pending:
        return None
    return pending[0]


def _push_pending(world_id: str, freeze_id: str) -> None:
    bucket = _PENDING_FREEZES.setdefault(world_id, [])
    if freeze_id not in bucket:
        bucket.append(freeze_id)


def _remove_pending(world_id: str, freeze_id: str) -> None:
    bucket = _PENDING_FREEZES.get(world_id) or []
    if freeze_id in bucket:
        bucket.remove(freeze_id)
    if not bucket:
        _PENDING_FREEZES.pop(world_id, None)


def _record_freeze_in_history(world_id: str, ts: float) -> int:
    """Insert a freeze timestamp into the per-world history,
    prune anything older than the lockout window, and return
    the count of freezes in the window."""
    history = _FREEZE_HISTORY.setdefault(world_id, [])
    cutoff = ts - DEFAULT_LOCKOUT_WINDOW_SECONDS
    pruned = [t for t in history if t >= cutoff]
    pruned.append(ts)
    _FREEZE_HISTORY[world_id] = pruned
    return len(pruned)


def safety_stop(
    *,
    reason: str | None = None,
    world_id: str | None = None,
    source: str | None = None,
    now: float | None = None,
) -> dict:
    """Initiate a safety-stop.  The global hotkey (or a
    programmatic call) lands here.

    Three freezes within :data:`DEFAULT_LOCKOUT_WINDOW_SECONDS`
    flip the world to ``lockout`` and refuse further freezes
    until :func:`clear_lockout` is called (or the lockout
    expires).
    """
    moment = _now_or(now)
    with _LOCK:
        if world_id is not None and _is_world_locked_out(world_id, now=moment):
            raise MCPLockoutError(
                "world %r is in lockout; refuse safety_stop" % world_id
            )
        if world_id is not None and _pending_freeze_id(world_id) is not None:
            raise MCPFrozenError(
                "world %r already has a pending safety_stop" % world_id
            )
        freeze_id = gen_mcp_freeze_id()
        sequence = _seq_next("freeze")
        record = {
            "id": freeze_id,
            "object": "mcp_freeze",
            "reason": reason or "unspecified",
            "source": source or "api",
            "world_id": world_id,
            "requested_at": moment,
            "confirmed_at": None,
            "cancelled_at": None,
            "snapshot_id": None,
            "status": FREEZE_FROZEN,
            "lockout_count": 0,
            "lockout_at": None,
            "restore_summary": None,
            "_seq": sequence,
        }
        state.mcp_freezes[freeze_id] = record
        if world_id is not None:
            _push_pending(world_id, freeze_id)
        if world_id is not None:
            count = _record_freeze_in_history(world_id, moment)
            if count >= DEFAULT_LOCKOUT_THRESHOLD:
                # Transition this freeze + the world to lockout.
                lockout_until = moment + DEFAULT_LOCKOUT_DURATION_SECONDS
                set_world_policy(world_id, lockout_until=lockout_until)
                record["status"] = FREEZE_LOCKOUT
                record["lockout_at"] = moment
                record["lockout_count"] = count
                if world_id is not None:
                    _remove_pending(world_id, freeze_id)
                # Audit-log the lockout event so operators can
                # see it.  Verdict=denied_lockout isn't a great
                # fit (no tool call), so we record the rule
                # match as None and reason="lockout_triggered".
                record_audit(
                    action_kind="safety_stop", payload=reason or "",
                    verdict=VERDICT_DENIED_LOCKOUT,
                    world_id=world_id, reason="lockout_triggered",
                    now=moment,
                )
        return record


def list_freezes(
    *,
    world_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return freeze records newest-first, optionally filtered."""
    out: list[dict] = []
    for record in state.mcp_freezes.values():
        if world_id is not None and record.get("world_id") != world_id:
            continue
        if status is not None and record.get("status") != status:
            continue
        out.append(record)
    out.sort(
        key=lambda r: (r.get("requested_at", 0.0), r.get("_seq", 0)),
        reverse=True,
    )
    if limit < 1:
        limit = 1
    return out[:limit]


def get_freeze(freeze_id: str) -> dict | None:
    return state.mcp_freezes.get(freeze_id)


def _set_freeze_status(
    freeze_id: str,
    status: str,
    *,
    snapshot_id: str | None = None,
    restore_summary: dict | None = None,
    now: float | None = None,
) -> dict:
    record = state.mcp_freezes.get(freeze_id)
    if record is None:
        raise MCPError("freeze %r not found" % freeze_id)
    if status not in VALID_FREEZE_STATUSES:
        raise MCPError(
            "status must be one of %s, got %r"
            % (sorted(VALID_FREEZE_STATUSES), status)
        )
    moment = _now_or(now)
    record["status"] = status
    if snapshot_id is not None:
        record["snapshot_id"] = snapshot_id
    if restore_summary is not None:
        record["restore_summary"] = restore_summary
    if status == FREEZE_RESTORED:
        record["confirmed_at"] = moment
        world_id = record.get("world_id")
        if world_id:
            _remove_pending(world_id, freeze_id)
    elif status == FREEZE_CANCELLED:
        record["cancelled_at"] = moment
        world_id = record.get("world_id")
        if world_id:
            _remove_pending(world_id, freeze_id)
    return record


def confirm_safety_stop(
    freeze_id: str,
    *,
    now: float | None = None,
) -> dict:
    """User clicked "清理并恢复".  The flow is:

    1. Transition the freeze to ``sanitizing``.
    2. Dump a snapshot (if we don't have one yet — usually
       the safety-stop itself dumped a snapshot, but a
       re-confirm on a cancelled freeze should not overwrite
       the old one).
    3. Sanitize the snapshot in place.
    4. Restore the live state from the snapshot.
    5. Transition the freeze to ``restored``.

    Errors at any step leave the freeze in
    ``sanitizing`` + log the error reason on the record so
    the operator can investigate.
    """
    moment = _now_or(now)
    with _LOCK:
        record = state.mcp_freezes.get(freeze_id)
        if record is None:
            raise MCPError("freeze %r not found" % freeze_id)
        if record["status"] not in {FREEZE_FROZEN, FREEZE_AWAITING_CONFIRM}:
            raise MCPError(
                "freeze %r is in status %r, not awaiting confirm"
                % (freeze_id, record["status"])
            )
        snapshot_id = record.get("snapshot_id")
        if not snapshot_id:
            world_id = record.get("world_id")
            snap = dump_snapshot(
                world_id=world_id,
                reason=SNAPSHOT_REASON_SAFETY_STOP,
                now=moment,
            )
            snapshot_id = snap["id"]
            record["snapshot_id"] = snapshot_id
            _set_freeze_status(
                freeze_id, FREEZE_AWAITING_CONFIRM,
                snapshot_id=snapshot_id, now=moment,
            )
        # Sanitize in place.
        sanitize_snapshot(snapshot_id)
        # Restore from the (now sanitized) snapshot.
        summary = restore_snapshot(snapshot_id, now=moment)
        _set_freeze_status(
            freeze_id, FREEZE_RESTORED,
            snapshot_id=snapshot_id, restore_summary=summary, now=moment,
        )
        return state.mcp_freezes[freeze_id]


def cancel_safety_stop(
    freeze_id: str,
    *,
    reason: str | None = None,
    now: float | None = None,
) -> dict:
    """User clicked "保持冻结 + 提示手动处理".  The freeze
    transitions to ``cancelled``; the snapshot (if any) is
    kept on disk so the operator can inspect it later."""
    moment = _now_or(now)
    with _LOCK:
        record = state.mcp_freezes.get(freeze_id)
        if record is None:
            raise MCPError("freeze %r not found" % freeze_id)
        if record["status"] not in {FREEZE_FROZEN, FREEZE_AWAITING_CONFIRM}:
            raise MCPError(
                "freeze %r is in status %r, not awaiting confirm"
                % (freeze_id, record["status"])
            )
        if reason is not None:
            record["reason"] = reason
        return _set_freeze_status(
            freeze_id, FREEZE_CANCELLED, now=moment,
        )


# ---------------------------------------------------------------------------
# Snapshots — the "专用备份文件夹" half of the spec
# ---------------------------------------------------------------------------


#: Which ``state`` buckets the snapshot covers.  The "受保护
#: 模块" set per spec AC-4.  Operators can extend by passing
#: ``extra_buckets`` to :func:`dump_snapshot`.
PROTECTED_BUCKETS: tuple[str, ...] = (
    "worlds", "characters", "memory", "sessions",
)


def dump_snapshot(
    *,
    world_id: str | None = None,
    reason: str = SNAPSHOT_REASON_PRE_FREEZE,
    extra_buckets: tuple[str, ...] | None = None,
    now: float | None = None,
) -> dict:
    """Snapshot the live state.  Returns the record.

    The payload is a deep-copied dict (caller can mutate
    freely; the live state is untouched).  ``file_path`` is
    derived from the snapshot id — server-controlled, so the
    request body can never escape the backup directory.
    """
    if reason not in VALID_SNAPSHOT_REASONS:
        raise MCPError(
            "reason must be one of %s, got %r"
            % (sorted(VALID_SNAPSHOT_REASONS), reason)
        )
    moment = _now_or(now)
    sequence = _seq_next("snapshot")
    snapshot_id = gen_mcp_snapshot_id()
    buckets_to_dump = list(PROTECTED_BUCKETS) + list(extra_buckets or [])
    payload: dict = {
        "__meta": {
            "snapshot_id": snapshot_id,
            "world_id": world_id,
            "reason": reason,
            "created_at": moment,
            "protected_buckets": list(PROTECTED_BUCKETS),
            "extra_buckets": list(extra_buckets or []),
        },
    }
    for bucket_name in buckets_to_dump:
        bucket = getattr(state, bucket_name, None)
        if not isinstance(bucket, dict):
            # Skip non-dict buckets silently (e.g. ``audits``
            # is a list).  Operators asking for a list bucket
            # can extend the snapshot via ``extra_buckets`` and
            # a custom route later.
            continue
        payload[bucket_name] = copy.deepcopy(dict(bucket))
    file_path = "mcp_snapshots/%s.json" % snapshot_id
    record = {
        "id": snapshot_id,
        "object": "mcp_snapshot",
        "world_id": world_id,
        "scope": "world" if world_id else "mixed",
        "file_path": file_path,
        "size_bytes": len(repr(payload).encode("utf-8")),
        "reason": reason,
        "includes_protected": True,
        "sanitized": False,
        "created_at": moment,
        "payload": payload,
        "_seq": sequence,
    }
    state.mcp_snapshots[snapshot_id] = record
    return record


def list_snapshots(
    *,
    world_id: str | None = None,
    reason: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return snapshot summaries (without the payload) newest-first."""
    out: list[dict] = []
    for record in state.mcp_snapshots.values():
        if world_id is not None and record.get("world_id") != world_id:
            continue
        if reason is not None and record.get("reason") != reason:
            continue
        out.append({k: v for k, v in record.items() if k != "payload"})
    out.sort(
        key=lambda r: (r.get("created_at", 0.0), r.get("_seq", 0)),
        reverse=True,
    )
    if limit < 1:
        limit = 1
    return out[:limit]


def get_snapshot(snapshot_id: str) -> dict | None:
    return state.mcp_snapshots.get(snapshot_id)


def sanitize_snapshot(snapshot_id: str) -> dict:
    """Run a light sanitize pass on the snapshot's payload —
    strip A5.1 ``forbidden_word`` substrings from every string
    leaf.  A5.1 rules are reused so we don't duplicate content
    policy between chapters.

    The sanitize pass is **deliberately conservative**: we
    only touch string values, we replace matches with the
    sentinel ``"[sanitized]"``, and we never recurse into the
    ``__meta`` block (it carries the snapshot_id / world_id /
    timestamps the restore step needs intact).

    Returns the sanitized record.  The ``sanitized`` flag is
    flipped so :func:`restore_snapshot` can refuse a
    not-yet-sanitized snapshot if the operator wants that
    safety check.
    """
    record = state.mcp_snapshots.get(snapshot_id)
    if record is None:
        raise MCPError("snapshot %r not found" % snapshot_id)
    if record.get("sanitized"):
        return record
    payload = record.get("payload") or {}
    forbidden_rules = [
        r for r in state.safety_rules.values()
        if r.get("is_active")
        and r.get("rule_kind") == "forbidden_word"
    ]
    if forbidden_rules:
        compiled = []
        for rule in forbidden_rules:
            try:
                compiled.append(re.compile(re.escape(rule["pattern"]), re.IGNORECASE))
            except re.error:
                continue
        _scrub_strings(payload, compiled)
    record["sanitized"] = True
    record["sanitized_at"] = now_ts()
    return record


def _scrub_strings(obj: Any, compiled: list, _seen: set | None = None) -> None:
    """Walk a JSON-shaped tree and replace any regex match
    inside string leaves with ``"[sanitized]"``.  Defensive
    against cycles (state.safety_rules etc. is reachable via
    the snapshot's payload if it ever leaks in)."""
    if _seen is None:
        _seen = set()
    oid = id(obj)
    if oid in _seen:
        return
    _seen.add(oid)
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            if k == "__meta":
                continue
            v = obj[k]
            if isinstance(v, str):
                scrubbed = v
                for pat in compiled:
                    scrubbed = pat.sub("[sanitized]", scrubbed)
                if scrubbed != v:
                    obj[k] = scrubbed
            else:
                _scrub_strings(v, compiled, _seen)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                scrubbed = v
                for pat in compiled:
                    scrubbed = pat.sub("[sanitized]", scrubbed)
                if scrubbed != v:
                    obj[i] = scrubbed
            else:
                _scrub_strings(v, compiled, _seen)


def restore_snapshot(
    snapshot_id: str,
    *,
    now: float | None = None,
) -> dict:
    """Restore the live state from a snapshot.  Returns a
    summary ``{"restored_buckets": [...], "skipped_buckets":
    [...], "sanitized": bool}``.

    The restore walks every protected bucket in the snapshot
    payload and overwrites the corresponding
    ``state.<bucket>`` dict in place.  ``__meta`` is the only
    key skipped.  Buckets the snapshot doesn't cover are
    reported as ``skipped_buckets``.

    If the snapshot hasn't been sanitized yet, we sanitize
    first (a defence-in-depth measure: if the caller skipped
    the explicit sanitize step, we still scrub before
    restoring).  This is what AC-3 ("恢复后 AI 必须从备份的
    上下文继续") relies on.
    """
    record = state.mcp_snapshots.get(snapshot_id)
    if record is None:
        raise MCPError("snapshot %r not found" % snapshot_id)
    if not record.get("sanitized"):
        sanitize_snapshot(snapshot_id)
    payload = record.get("payload") or {}
    restored: list[str] = []
    skipped: list[str] = []
    for bucket_name in PROTECTED_BUCKETS:
        if bucket_name not in payload:
            skipped.append(bucket_name)
            continue
        bucket = getattr(state, bucket_name, None)
        if not isinstance(bucket, dict):
            skipped.append(bucket_name)
            continue
        bucket.clear()
        bucket.update(payload[bucket_name])
        restored.append(bucket_name)
    return {
        "snapshot_id": snapshot_id,
        "restored_buckets": restored,
        "skipped_buckets": skipped,
        "sanitized": bool(record.get("sanitized", False)),
    }


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.  No rules, freezes, or
    snapshots ship by default — the rulebook is
    operator-curated."""
    return None


def reset_for_testing() -> None:
    """Wipe every MCP bucket and reset the per-world
    state."""
    global _AUDIT_SEQUENCE, _FREEZE_SEQUENCE, _SNAPSHOT_SEQUENCE
    with _LOCK:
        _AUDIT_SEQUENCE = 0
        _FREEZE_SEQUENCE = 0
        _SNAPSHOT_SEQUENCE = 0
        state.mcp_rules.clear()
        state.mcp_audit.clear()
        state.mcp_freezes.clear()
        state.mcp_snapshots.clear()
        _WORLD_POLICY.clear()
        _PENDING_FREEZES.clear()
        _FREEZE_HISTORY.clear()


__all__ = [
    # Constants
    "VERDICT_ALLOWED", "VERDICT_DENIED",
    "VERDICT_DENIED_LOCKOUT", "VERDICT_DENIED_FROZEN",
    "VERDICT_DENIED_CRASHED", "VALID_VERDICTS",
    "FREEZE_FROZEN", "FREEZE_AWAITING_CONFIRM", "FREEZE_SANITIZING",
    "FREEZE_RESTORED", "FREEZE_CANCELLED", "FREEZE_LOCKOUT",
    "VALID_FREEZE_STATUSES",
    "SNAPSHOT_REASON_SAFETY_STOP", "SNAPSHOT_REASON_MANUAL",
    "SNAPSHOT_REASON_PRE_FREEZE", "VALID_SNAPSHOT_REASONS",
    "POLICY_DEFAULT_DENY", "POLICY_DEFAULT_ALLOW",
    "VALID_POLICY_DEFAULTS",
    "DEFAULT_LOCKOUT_WINDOW_SECONDS", "DEFAULT_LOCKOUT_THRESHOLD",
    "DEFAULT_LOCKOUT_DURATION_SECONDS",
    "PROTECTED_BUCKETS",
    # Errors
    "MCPError", "MCPLockoutError", "MCPFrozenError",
    # Pure helpers
    "_flatten_payload", "_truncate", "_scrub_strings",
    "_is_world_locked_out", "_pending_freeze_id",
    # World policy
    "get_world_policy", "set_world_policy", "reset_world_policy",
    "clear_lockout",
    # Audit
    "record_audit", "list_audit", "count_audit",
    # Gate
    "check", "_verdict_for_match",
    # Safety-stop
    "safety_stop", "list_freezes", "get_freeze",
    "confirm_safety_stop", "cancel_safety_stop",
    # Snapshots
    "dump_snapshot", "list_snapshots", "get_snapshot",
    "sanitize_snapshot", "restore_snapshot",
    # Lifecycle
    "seed_default", "reset_for_testing",
]
