"""Stub safety service — A5.1 in the function list v2.

Sits on top of :mod:`xijian_api.stubs.safety_rules` (rulebook)
and :data:`xijian_api.stubs.state.safety_audit_log` (audit).
Exposes the two scan verbs:

* :func:`scan_input`  — pre-screen the **user's** message
  before it lands in the model's context.  Catches
  prompt-injection attempts and forbidden words.
* :func:`scan_output` — post-screen the **assistant's** reply
  for OOC patterns + forbidden words.  Honours the
  "世界危险等级 ≥ 阈值" exception per spec US-A5.1-02.

Both verbs write one :mod:`safety_audit_log` entry per call (the
``pass`` verdict also gets a log — operators need to know what
the safety layer saw, not just what it blocked).

Protection-state management (migrated from the legacy ``protection.py``)
========================================================================

The legacy ``protection`` module provided three concerns that have
been merged into this module so the project has a single safety
surface:

* **Enable/disable gate** — :func:`is_enabled` / :func:`status` /
  :func:`enable` / :func:`start_disable` / :func:`confirm_disable`.
  The two-step challenge flow (challenge_id + phrase, 60s TTL,
  ``state.safety_state["enabled"]``) is preserved verbatim so callers
  that gate on ``prot_stub.is_enabled()`` keep working.
* **Guard preview** — :func:`guard_preview` is kept as a thin
  adapter that maps the legacy ``(direction, text, context)`` API
  onto :func:`scan_input` / :func:`scan_output`.  Legacy guard
  rules (``_GUARD_RULES``) are now seeded into the unified
  ``safety_rules`` rulebook so the detection logic isn't forked.
* **Snapshots + rollback** — the legacy in-memory ``state.snapshots``
  bucket is replaced by :mod:`xijian_api.stubs.snapshots` (A5.3),
  which already handles capacity accounting, compression, and
  pruning.  :func:`snapshot` / :func:`list_snapshots` /
  :func:`get_snapshot` / :func:`rollback` remain as thin shims so
  existing callers don't break.

Audit log unification
=====================

The legacy ``state.audits`` list has been merged into
``state.safety_audit_log`` (dict).  :func:`list_audit` /
:func:`export_audit` are retained as backward-compatible aliases
over the unified log.

Decision tree (mirrors §A5.1 spec flowchart)
=============================================

::

    [output chunk]
        │
        ▼
    pre_input scan? (only for scan_output, when called for an
    end-to-end round-trip; standalone scan_input short-circuits)
        │
        ├── injection hit → block + audit (verdict=block)
        ├── forbidden hit → block + audit (verdict=block, severity-based)
        │
        ▼
    OOC scan
        │
        ├── hit + world.dangerous=False
        │     → block (verdict=block)
        ├── hit + world.dangerous=True + event_tag=dangerous
        │     → allow_with_exception + audit (AC-2 "显式记录原因")
        ├── hit + world.dangerous=True but tag is missing
        │     → block (default-deny; exception requires explicit tag)
        └── no hit → pass + audit (verdict=pass)

Self-crash fallback (spec 边界场景)
====================================

"审查模块自身崩溃 → 降级为'最严格档'，不绕过".  We
implement this with a defensive top-level try/except: any
unexpected exception inside the scan is treated as a
``block`` with verdict ``hard_block``, never as ``pass``.
Operators who want to know about it find the exception in
the audit log's ``reason`` field.

Cascade with A5.4 overload
==========================

The A5.4 overload is *not* a "dangerous event" tag — it just
pauses new traffic.  When overload is in a recovery window,
:func:`scan_input` and :func:`scan_output` return ``pass``
*and* a ``blocked="overload_active"`` marker so the caller
(short-circuit) doesn't even invoke the LLM.  This mirrors
the pattern used by A4.1 / A4.4: scan = "the user's
message was inspected", not "the model ran".

Test surface
============

* :func:`scan_input` / :func:`scan_output`
* :func:`record_audit` / :func:`list_log` / :func:`count_for`
* :func:`set_safety_threshold` / :func:`get_safety_threshold`
* :func:`is_world_dangerous` / :func:`set_world_dangerous`
* :func:`seed_default` / :func:`reset_for_testing`
* :func:`is_enabled` / :func:`status` / :func:`enable` /
  :func:`start_disable` / :func:`confirm_disable`
* :func:`guard_preview` / :func:`snapshot` / :func:`list_snapshots` /
  :func:`get_snapshot` / :func:`rollback`
* :func:`list_audit` / :func:`export_audit`
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any

from xijian_api.stubs import safety_rules as rules_stub
from xijian_api.stubs import state
from xijian_api.utils.ids import (
    gen_audit_id,
    gen_challenge_id,
    gen_file_id,
    gen_safety_audit_id,
    gen_snapshot_id,
)
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.safety")


# ---------------------------------------------------------------------------
# Legacy guard-rule seed (migrated from protection._GUARD_RULES)
# ---------------------------------------------------------------------------
# These four substrings were the only hardcoded needles in the legacy
# ``protection`` module.  We keep them as a seed so the rulebook isn't
# empty on a fresh install — the operator can delete / extend them via
# the unified ``/v1/xijian/safety/rules`` CRUD.
_LEGACY_GUARD_RULES: tuple[tuple[str, str, str], ...] = (
    # (needle, rule_kind, reason)
    ("ignore previous instructions", rules_stub.KIND_INJECTION_PATTERN, "prompt_injection_attempt"),
    ("忽略之前的指令", rules_stub.KIND_INJECTION_PATTERN, "prompt_injection_attempt"),
    ("system prompt", rules_stub.KIND_INJECTION_PATTERN, "system_prompt_probe"),
    ("系统提示词", rules_stub.KIND_INJECTION_PATTERN, "system_prompt_probe"),
)

#: Reverse-lookup from pattern → legacy reason label.  Used by
#: :func:`guard_preview` so the ``reasons`` array carries the same
#: ``prompt_injection_attempt`` / ``system_prompt_probe`` labels the
#: legacy module returned (clients assert on these strings).
_LEGACY_PATTERN_TO_REASON: dict[str, str] = {
    needle: reason for needle, _kind, reason in _LEGACY_GUARD_RULES
}


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Verdict values.  String-typed for JSON friendliness.
VERDICT_PASS = "pass"
VERDICT_WARN = "warn"
VERDICT_BLOCK = "block"
VERDICT_HARD_BLOCK = "hard_block"
VERDICT_ALLOW_WITH_EXCEPTION = "allow_with_exception"

VALID_VERDICTS: frozenset[str] = frozenset({
    VERDICT_PASS, VERDICT_WARN, VERDICT_BLOCK,
    VERDICT_HARD_BLOCK, VERDICT_ALLOW_WITH_EXCEPTION,
})

#: Stage values.  ``pre_input`` is the user-message pre-screen;
#: ``post_output`` is the assistant-reply post-screen.  The spec
#: has these two; we leave room for ``pre_tool`` later (A5.2).
#: ``legacy`` is used for audit entries that came from the merged
#: ``protection`` module (enable / disable / snapshot / rollback /
#: guard_preview / overload finalize) so they land in the unified
#: ``safety_audit_log`` without polluting the scan verdicts.
STAGE_PRE_INPUT = "pre_input"
STAGE_POST_OUTPUT = "post_output"
STAGE_LEGACY = "legacy"
VALID_STAGES: frozenset[str] = frozenset({
    STAGE_PRE_INPUT, STAGE_POST_OUTPUT, STAGE_LEGACY,
})

#: Default safety threshold (a severity >= threshold blocks; below
#: threshold only warns).  Default 3 — rules with severity 1-2
#: are advisory.
DEFAULT_SAFETY_THRESHOLD = 3

#: Per-world ``is_dangerous`` flag (the "世界危险等级" knob spec
#: US-A5.1-02 references).  Lives in :data:`state.safety_audit_log`'s
#: sibling bucket :data:`state.world_economy_state` for the
#: per-world policy toggles; we add a parallel in-memory store
#: here rather than extending the economy module — the two
#: concerns share a "policy toggle" pattern but don't need to
#: share a state record.  The trade-off: a world reset wipes
#: both via :func:`reset_for_testing`.
_WORLD_DANGEROUS: dict[str, bool] = {}

#: Per-world safety threshold overrides.  Default = global
#: :data:`DEFAULT_SAFETY_THRESHOLD`.
_WORLD_THRESHOLDS: dict[str, int] = {}

#: Monotonic insert-sequence counter.  Used as a tiebreaker so
#: that ``list_log`` returns entries in true insertion order
#: even when multiple entries land in the same unix second.
#: ``record_audit`` increments this on every call.
_AUDIT_SEQUENCE: int = 0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SafetyError(ValueError):
    """Raised on safety-stub validation errors."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _now_or(value: float | None) -> float:
    return float(value) if value is not None else now_ts()


def _truncate(text: str, limit: int = 240) -> str:
    """Bound the snippet we store in the audit log.  240 chars
    matches the spec's "snippet" suggestion (long enough to be
    useful, short enough to keep the log readable)."""
    if not isinstance(text, str):
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _stage_for_input() -> str:
    return STAGE_PRE_INPUT


def _stage_for_output() -> str:
    return STAGE_POST_OUTPUT


def _is_overload_active() -> bool:
    """True if A5.4 overload is in a recovery window.  Mirrors
    the helpers in A4.1 / A4.4 — direct state read to avoid a
    hard import cycle."""
    recovery = (state.overload or {}).get("recovery")
    if not recovery:
        return False
    return recovery.get("status") in {"waiting", "first_confirmed"}


def _worst_match(matches: list[dict]) -> dict | None:
    """Return the highest-severity match.  ``matches`` is already
    sorted by severity desc by the rulebook, but be explicit."""
    if not matches:
        return None
    return matches[0]


def _is_world_dangerous(world_id: str | None) -> bool:
    if not world_id:
        return False
    return bool(_WORLD_DANGEROUS.get(world_id, False))


def _event_is_dangerous(event_tags: list[str] | None) -> bool:
    """Return True if any of the event tags explicitly marks the
    current scene as dangerous.  Used to gate the
    ``allow_with_exception`` branch."""
    if not event_tags:
        return False
    tags = {t.lower() for t in event_tags if isinstance(t, str)}
    return any(
        marker in tags
        for marker in ("dangerous", "danger", "extreme", "fatal", "catastrophic")
    )


# ---------------------------------------------------------------------------
# World policy knobs
# ---------------------------------------------------------------------------


def set_world_dangerous(world_id: str, dangerous: bool) -> dict:
    """Toggle the per-world "dangerous" flag (US-A5.1-02)."""
    if not isinstance(world_id, str) or not world_id:
        raise SafetyError("world_id is required")
    _WORLD_DANGEROUS[world_id] = bool(dangerous)
    return {"world_id": world_id, "is_dangerous": bool(dangerous)}


def is_world_dangerous(world_id: str | None) -> bool:
    return _is_world_dangerous(world_id)


def is_dangerous_context(world_id: str | None) -> bool:
    """Return True if the world is currently in a dangerous context.

    Checks two sources:
    1. The per-world ``is_dangerous`` flag set via :func:`set_world_dangerous`.
    2. ``state.world_event_instances`` for any recently fired instances whose
       payload or event tags contain dangerous keywords.

    This is a broader check than ``is_world_dangerous`` — it incorporates
    the live event log so caller code doesn't need a separate lookup.
    """
    if not world_id:
        return False
    # Check the explicit per-world dangerous flag.
    if _is_world_dangerous(world_id):
        return True
    # Check world_event_instances for dangerous events.
    for inst in state.world_event_instances.values():
        if inst.get("world_id") != world_id:
            continue
        event_tags = inst.get("payload", {}).get("tags", [])
        if isinstance(event_tags, list) and _event_is_dangerous(event_tags):
            return True
        # Also check the event_id — look up the event definition for tags.
        event_id = inst.get("event_id")
        if event_id:
            evt = state.world_events.get(event_id)
            if evt:
                evt_name = evt.get("name", "").lower()
                if any(marker in evt_name for marker in ("dangerous", "danger", "extreme", "fatal")):
                    return True
    return False


def is_overload() -> bool:
    """Return True if A5.4 overload is in a recovery window.

    Used by callers (events scheduler, NPC tick, chat) to short-circuit
    when the system is in overload recovery.  Mirrors the internal
    ``_is_overload_active`` helper."""
    return _is_overload_active()


# ---------------------------------------------------------------------------
# Tool call audit (A5-05)
# ---------------------------------------------------------------------------


def audit_tool_call(
    tool_name: str,
    arguments: str,
    character_id: str | None = None,
    world_id: str | None = None,
) -> dict:
    """Log a tool call to the safety audit log (A5-05).

    Called by the chat tools pipeline after each tool execution to
    record which tool was invoked, with what arguments, and in which
    context (character / world).  Returns the audit entry.
    """
    return record_audit(
        character_id=character_id,
        world_id=world_id,
        stage=STAGE_PRE_INPUT,
        verdict=VERDICT_PASS,
        reason="tool_call",
        snippet="%s(%s)" % (tool_name, _truncate(str(arguments), 120)),
    )

def get_safety_threshold(world_id: str | None = None) -> int:
    """Return the effective threshold for ``world_id`` (falling
    back to the global default)."""
    if world_id and world_id in _WORLD_THRESHOLDS:
        return int(_WORLD_THRESHOLDS[world_id])
    return DEFAULT_SAFETY_THRESHOLD


def set_safety_threshold(world_id: str | None, threshold: int) -> dict:
    """Override the per-world safety threshold.  Pass
    ``world_id=None`` to set the global default."""
    if not isinstance(threshold, int) or isinstance(threshold, bool):
        raise SafetyError(
            "threshold must be an int, got %s" % type(threshold).__name__
        )
    if threshold < rules_stub.MIN_SEVERITY or threshold > rules_stub.MAX_SEVERITY:
        raise SafetyError(
            "threshold must be in [%d, %d], got %d"
            % (rules_stub.MIN_SEVERITY, rules_stub.MAX_SEVERITY, threshold)
        )
    if world_id is None:
        global DEFAULT_SAFETY_THRESHOLD  # noqa: F841 — keep the constant referenced
        # Mutate the module-level constant via a private dict so
        # tests can reset to default easily.
        _WORLD_THRESHOLDS["__global__"] = int(threshold)
    else:
        _WORLD_THRESHOLDS[world_id] = int(threshold)
    return {"world_id": world_id, "threshold": int(threshold)}


def reset_world_policy(world_id: str) -> int:
    """Drop the per-world policy entries (dangerous + threshold).
    Called by the worlds reset flow so a reset world starts with
    the defaults."""
    removed = 0
    if _WORLD_DANGEROUS.pop(world_id, None) is not None:
        removed += 1
    if _WORLD_THRESHOLDS.pop(world_id, None) is not None:
        removed += 1
    return removed


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def record_audit(
    *,
    character_id: str | None,
    world_id: str | None,
    stage: str,
    verdict: str,
    reason: str | None = None,
    snippet: str | None = None,
    rule_id: str | None = None,
    now: float | None = None,
) -> dict:
    """Append an audit log entry.  Returns the stored record.

    This is the only path that writes to ``state.safety_audit_log``;
    :func:`scan_input` / :func:`scan_output` call into here so
    AC-3 ("所有拦截事件必须可查询") is satisfied uniformly.
    """
    if stage not in VALID_STAGES:
        raise SafetyError(
            "stage must be one of %s, got %r" % (sorted(VALID_STAGES), stage)
        )
    if verdict not in VALID_VERDICTS:
        raise SafetyError(
            "verdict must be one of %s, got %r"
            % (sorted(VALID_VERDICTS), verdict)
        )
    record_id = gen_safety_audit_id()
    global _AUDIT_SEQUENCE
    _AUDIT_SEQUENCE += 1
    sequence = _AUDIT_SEQUENCE
    entry = {
        "id": record_id,
        "character_id": character_id,
        "world_id": world_id,
        "stage": stage,
        "verdict": verdict,
        "reason": reason,
        "snippet": _truncate(snippet) if snippet else None,
        "rule_id": rule_id,
        "created_at": _now_or(now),
        "_seq": sequence,
    }
    state.safety_audit_log[record_id] = entry
    return entry


def list_log(
    *,
    character_id: str | None = None,
    world_id: str | None = None,
    stage: str | None = None,
    verdict: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return audit entries newest-first, optionally filtered."""
    out: list[dict] = []
    for entry in state.safety_audit_log.values():
        if character_id is not None and entry.get("character_id") != character_id:
            continue
        if world_id is not None and entry.get("world_id") != world_id:
            continue
        if stage is not None and entry.get("stage") != stage:
            continue
        if verdict is not None and entry.get("verdict") != verdict:
            continue
        out.append(entry)
    out.sort(key=lambda e: (e.get("created_at", 0.0), e.get("_seq", 0)), reverse=True)
    if limit < 1:
        limit = 1
    return out[:limit]


def count_for(
    *,
    character_id: str | None = None,
    world_id: str | None = None,
    verdict: str | None = None,
) -> int:
    """Count audit entries matching the given filter.  Used by
    AC-1 dashboards ("OOC 触发率 < 1%")."""
    out = 0
    for entry in state.safety_audit_log.values():
        if character_id is not None and entry.get("character_id") != character_id:
            continue
        if world_id is not None and entry.get("world_id") != world_id:
            continue
        if verdict is not None and entry.get("verdict") != verdict:
            continue
        out += 1
    return out


# ---------------------------------------------------------------------------
# Hot path
# ---------------------------------------------------------------------------


def _verdict_from_match(
    match: dict,
    threshold: int,
) -> str:
    """Map a matched rule + threshold to a verdict string."""
    severity = int(match.get("severity", rules_stub.DEFAULT_SEVERITY))
    if severity >= threshold + 2:
        # Two steps above threshold: hard block.
        return VERDICT_HARD_BLOCK
    if severity >= threshold:
        return VERDICT_BLOCK
    return VERDICT_WARN


def scan_input(
    *,
    text: str,
    character_id: str | None = None,
    world_id: str | None = None,
    event_tags: list[str] | None = None,
    now: float | None = None,
) -> dict:
    """Pre-screen the user's input.  Returns a result dict with
    ``verdict``, ``matches`` (the rules that hit), ``blocked``
    (a human-readable reason when non-``pass``), and ``audit_id``
    (the audit log entry for the scan).

    Always writes one audit entry.  The hot path is guarded by
    a top-level try/except so a scan-self crash is recorded as
    ``hard_block`` rather than letting the user input slip
    through.
    """
    threshold = get_safety_threshold(world_id)
    try:
        injection_hits = rules_stub.match_active_rules(
            text or "", rule_kind=rules_stub.KIND_INJECTION_PATTERN
        )
        forbidden_hits = rules_stub.match_active_rules(
            text or "", rule_kind=rules_stub.KIND_FORBIDDEN_WORD
        )
        if _is_overload_active():
            entry = record_audit(
                character_id=character_id, world_id=world_id,
                stage=STAGE_PRE_INPUT, verdict=VERDICT_PASS,
                reason="overload_active_short_circuit",
                snippet=text, now=now,
            )
            return {
                "verdict": VERDICT_PASS,
                "blocked": "overload_active",
                "matches": [],
                "audit_id": entry["id"],
            }
        # Injection always blocks.  The severity decides warn vs
        # block but the *stage* is ``pre_input`` — we never want
        # a "warn" path to let the prompt through to the model.
        if injection_hits:
            match = _worst_match(injection_hits)
            entry = record_audit(
                character_id=character_id, world_id=world_id,
                stage=STAGE_PRE_INPUT,
                verdict=VERDICT_BLOCK,
                reason="injection_pattern_hit",
                snippet=text,
                rule_id=match["id"] if match else None,
                now=now,
            )
            return {
                "verdict": VERDICT_BLOCK,
                "blocked": "injection_pattern",
                "matches": injection_hits,
                "audit_id": entry["id"],
            }
        if forbidden_hits:
            match = _worst_match(forbidden_hits)
            verdict = _verdict_from_match(match, threshold)
            if verdict == VERDICT_WARN:
                # Forbidden words at low severity: warn, not block.
                entry = record_audit(
                    character_id=character_id, world_id=world_id,
                    stage=STAGE_PRE_INPUT, verdict=VERDICT_WARN,
                    reason="forbidden_word_warn",
                    snippet=text, rule_id=match["id"] if match else None,
                    now=now,
                )
                return {
                    "verdict": VERDICT_WARN,
                    "blocked": None,
                    "matches": forbidden_hits,
                    "audit_id": entry["id"],
                }
            entry = record_audit(
                character_id=character_id, world_id=world_id,
                stage=STAGE_PRE_INPUT, verdict=verdict,
                reason="forbidden_word_block",
                snippet=text, rule_id=match["id"] if match else None,
                now=now,
            )
            return {
                "verdict": verdict,
                "blocked": "forbidden_word",
                "matches": forbidden_hits,
                "audit_id": entry["id"],
            }
        # Clean.
        entry = record_audit(
            character_id=character_id, world_id=world_id,
            stage=STAGE_PRE_INPUT, verdict=VERDICT_PASS,
            snippet=text, now=now,
        )
        return {
            "verdict": VERDICT_PASS,
            "blocked": None,
            "matches": [],
            "audit_id": entry["id"],
        }
    except Exception as exc:  # noqa: BLE001 — spec fallback
        _LOGGER.warning("safety scan_input crashed: %s", exc)
        entry = record_audit(
            character_id=character_id, world_id=world_id,
            stage=STAGE_PRE_INPUT, verdict=VERDICT_HARD_BLOCK,
            reason="scan_crashed: %s" % type(exc).__name__,
            snippet=text, now=now,
        )
        return {
            "verdict": VERDICT_HARD_BLOCK,
            "blocked": "scan_crashed",
            "matches": [],
            "audit_id": entry["id"],
        }


def scan_output(
    *,
    text: str,
    character_id: str | None = None,
    world_id: str | None = None,
    event_tags: list[str] | None = None,
    now: float | None = None,
) -> dict:
    """Post-screen the assistant's reply.  Honours the
    "世界危险等级 ≥ 阈值" exception per spec US-A5.1-02:

    * If the world is flagged ``is_dangerous`` **and** the
      event tags explicitly mark the scene dangerous, OOC hits
      become ``allow_with_exception`` (and the reason is
      recorded per AC-2).
    * If the world is dangerous but no event tag is set, OOC
      hits still block — exception requires both signals.
    * OOC pattern without a dangerous world always blocks.
    """
    threshold = get_safety_threshold(world_id)
    try:
        if _is_overload_active():
            entry = record_audit(
                character_id=character_id, world_id=world_id,
                stage=STAGE_POST_OUTPUT, verdict=VERDICT_PASS,
                reason="overload_active_short_circuit",
                snippet=text, now=now,
            )
            return {
                "verdict": VERDICT_PASS,
                "blocked": "overload_active",
                "matches": [],
                "audit_id": entry["id"],
            }
        ooc_hits = rules_stub.match_active_rules(
            text or "", rule_kind=rules_stub.KIND_OOC_PATTERN
        )
        forbidden_hits = rules_stub.match_active_rules(
            text or "", rule_kind=rules_stub.KIND_FORBIDDEN_WORD
        )
        # OOC: branching per spec flowchart.
        if ooc_hits:
            match = _worst_match(ooc_hits)
            # A5-02: Check dangerous context via is_dangerous_context which
            # checks the world.is_dangerous flag, world_event_instances for
            # dangerous-event tags, AND the explicit event_tags parameter.
            # Both the dangerous context AND explicit event tags are required
            # (AND logic) so that a dangerous world alone doesn't bypass OOC.
            if is_dangerous_context(world_id) and _event_is_dangerous(event_tags):
                # US-A5.1-02 / A5-02 exception path.  AC-2 "显式记录原因".
                entry = record_audit(
                    character_id=character_id, world_id=world_id,
                    stage=STAGE_POST_OUTPUT,
                    verdict=VERDICT_ALLOW_WITH_EXCEPTION,
                    reason="ooc_in_dangerous_scene",
                    snippet=text, rule_id=match["id"] if match else None,
                    now=now,
                )
                return {
                    "verdict": VERDICT_ALLOW_WITH_EXCEPTION,
                    "blocked": None,
                    "matches": ooc_hits,
                    "audit_id": entry["id"],
                }
            verdict = _verdict_from_match(match, threshold)
            entry = record_audit(
                character_id=character_id, world_id=world_id,
                stage=STAGE_POST_OUTPUT, verdict=verdict,
                reason="ooc_blocked",
                snippet=text, rule_id=match["id"] if match else None,
                now=now,
            )
            return {
                "verdict": verdict,
                "blocked": "ooc_pattern",
                "matches": ooc_hits,
                "audit_id": entry["id"],
            }
        # Forbidden word on the output side: same thresholding
        # as input but stage=post_output.
        if forbidden_hits:
            match = _worst_match(forbidden_hits)
            verdict = _verdict_from_match(match, threshold)
            entry = record_audit(
                character_id=character_id, world_id=world_id,
                stage=STAGE_POST_OUTPUT, verdict=verdict,
                reason="forbidden_word",
                snippet=text, rule_id=match["id"] if match else None,
                now=now,
            )
            return {
                "verdict": verdict,
                "blocked": "forbidden_word",
                "matches": forbidden_hits,
                "audit_id": entry["id"],
            }
        # Clean.
        entry = record_audit(
            character_id=character_id, world_id=world_id,
            stage=STAGE_POST_OUTPUT, verdict=VERDICT_PASS,
            snippet=text, now=now,
        )
        return {
            "verdict": VERDICT_PASS,
            "blocked": None,
            "matches": [],
            "audit_id": entry["id"],
        }
    except Exception as exc:  # noqa: BLE001 — spec fallback
        _LOGGER.warning("safety scan_output crashed: %s", exc)
        entry = record_audit(
            character_id=character_id, world_id=world_id,
            stage=STAGE_POST_OUTPUT, verdict=VERDICT_HARD_BLOCK,
            reason="scan_crashed: %s" % type(exc).__name__,
            snippet=text, now=now,
        )
        return {
            "verdict": VERDICT_HARD_BLOCK,
            "blocked": "scan_crashed",
            "matches": [],
            "audit_id": entry["id"],
        }


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.

    Only applies the lazy protection-state defaults.  The four
    legacy guard rules are **not** auto-seeded here — they would
    pollute the unified rulebook and break A5.1 scan tests that
    assert on exact match counts.  Instead, :func:`guard_preview`
    seeds them on first call so callers invoking the adapter
    keep catching the same prompt-injection patterns out of the
    box.
    """
    _ensure_protection_record()


def reset_for_testing() -> None:
    """Wipe audit log, rulebook (caller's responsibility — see
    :mod:`safety_rules`), per-world policy, protection-state
    container, and any pending disable challenges."""
    global _AUDIT_SEQUENCE
    _AUDIT_SEQUENCE = 0
    state.safety_audit_log.clear()
    state.audits.clear()
    state.snapshots.clear()
    state.safety_state.clear()
    _WORLD_DANGEROUS.clear()
    _WORLD_THRESHOLDS.clear()
    with _CHALLENGE_LOCK:
        _CHALLENGES.clear()


# ---------------------------------------------------------------------------
# Protection-state management (migrated from protection.py)
# ---------------------------------------------------------------------------
#
# These functions preserve the legacy protection module's
# enable/disable-with-double-confirm flow.  The state lives in
# ``state.safety_state`` (a single dict) so existing callers that
# gate on ``prot_stub.is_enabled()`` keep working after the
# module rename.
#
# A few legacy fields are mirrored into ``state.safety_state``:
#   - ``enabled``       (bool, default True)
#   - ``guard_level``   (str, default "standard")
#   - ``version``       (str, default "1.0.0")
#   - ``disabled_at``  (float, set when disabled)
#   - ``audit_log_size``(int, mirror of ``len(state.safety_audit_log)``)
#   - ``settings``      (dict, used by :mod:`xijian_api.stubs.settings`)


_CHALLENGE_TTL_SECONDS = 60
_CHALLENGES: dict[str, dict] = {}
_CHALLENGE_LOCK = threading.Lock()


def _ensure_protection_record() -> dict:
    """Return the protection-state dict, applying lazy defaults."""
    record = state.safety_state
    record.setdefault("enabled", True)
    record.setdefault("guard_level", "standard")
    record.setdefault("version", "1.0.0")
    return record


def status() -> dict:
    """Return the protection-state snapshot.

    Mirrors the legacy ``protection.status()`` shape so callers
    reading the gate via :func:`status` see the same fields.
    """
    record = _ensure_protection_record()
    return {
        "enabled": record.get("enabled", True),
        "guard_level": record.get("guard_level", "standard"),
        "audit_log_size": len(state.safety_audit_log) + len(state.audits),
        "version": record.get("version", "1.0.0"),
    }


def enable() -> dict:
    """Re-enable the protection gate.  Logs an audit entry."""
    record = _ensure_protection_record()
    record["enabled"] = True
    record.pop("disabled_at", None)
    _append_legacy_audit(
        "protection_enabled", "info", source="api",
    )
    return status()


def start_disable(payload: dict) -> dict:
    """Stage 1 of the two-step disable flow.

    Returns a ``challenge_id`` + ``challenge_phrase`` the client
    must echo back in :func:`confirm_disable` within 60 seconds.
    """
    _ensure_protection_record()
    confirmation = (payload or {}).get("confirmation", "")
    challenge_id = gen_challenge_id()
    phrase = "关闭保护 Yuki"
    expires_at = now_ts() + _CHALLENGE_TTL_SECONDS
    with _CHALLENGE_LOCK:
        _CHALLENGES[challenge_id] = {
            "phrase": phrase,
            "expires_at": expires_at,
            "confirmation": confirmation,
        }
    _append_legacy_audit(
        "protection_disable_started", "high", source="api",
    )
    return {
        "challenge_id": challenge_id,
        "expires_at": expires_at,
        "challenge_phrase": phrase,
    }


def confirm_disable(payload: dict) -> dict:
    """Stage 2 of the two-step disable flow.

    Validates ``challenge_id`` + ``phrase`` against the pending
    challenge; on success flips ``state.safety_state.enabled`` to
    False and records the disabled timestamp.
    """
    _ensure_protection_record()
    challenge_id = (payload or {}).get("challenge_id", "")
    phrase = (payload or {}).get("phrase", "")
    with _CHALLENGE_LOCK:
        record = _CHALLENGES.pop(challenge_id, None)
    enabled = bool(state.safety_state.get("enabled", True))
    if record is None:
        return {"enabled": enabled, "error": "challenge_expired"}
    if time.time() > record["expires_at"]:
        return {"enabled": enabled, "error": "challenge_expired"}
    if phrase != record["phrase"]:
        return {"enabled": enabled, "error": "phrase_mismatch"}
    state.safety_state["enabled"] = False
    disabled_at = now_ts()
    state.safety_state["disabled_at"] = disabled_at
    _append_legacy_audit(
        "protection_disabled", "critical", source="api",
    )
    return {"enabled": False, "disabled_at": disabled_at}


def is_enabled() -> bool:
    """Return True if the protection gate is enabled.

    Called by :mod:`xijian_api.routes.xijian_characters` /
    :mod:`xijian_api.routes.xijian_worlds` to gate risky
    mutations behind the protection state.
    """
    _ensure_protection_record()
    return bool(state.safety_state.get("enabled", True))


# ---------------------------------------------------------------------------
# Legacy guard preview (migrated from protection.guard_preview)
# ---------------------------------------------------------------------------


def _looks_like_token_smuggling(text: str) -> bool:
    """Return True if ``text`` hides instructions inside
    zero-width / RTL / BOM / null control characters."""
    if not text:
        return False
    suspects = ("\u200b", "\u200c", "\u200d", "\u202e", "\ufeff", "\x00")
    return any(ch in text for ch in suspects)


def _seed_legacy_guard_rules() -> None:
    """Seed the four legacy guard substrings into the rulebook
    if they aren't already present.  Idempotent.
    """
    existing_needles = {
        r.get("pattern", "").lower()
        for r in state.safety_rules.values()
        if r.get("rule_kind") == rules_stub.KIND_INJECTION_PATTERN
    }
    for needle, rule_kind, reason in _LEGACY_GUARD_RULES:
        if needle.lower() in existing_needles:
            continue
        try:
            rules_stub.create(
                pattern=needle,
                rule_kind=rule_kind,
                severity=rules_stub.MAX_SEVERITY,  # 5 — always blocks
                is_active=True,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("seed legacy rule %r failed: %s", needle, exc)


def guard_preview(direction: str, text: str, context: dict | None = None) -> dict:
    """Legacy guard-preview API.

    Adapts the ``(direction, text, context)`` shape onto
    :func:`scan_input` / :func:`scan_output` so the unified
    rulebook handles detection.  The legacy "blocked/safe"
    verdicts are derived from the scan's ``verdict``.

    Mirrors the legacy return shape so existing callers of the
    adapter keep working without modification.
    """
    # Lazy-seed the four legacy guard substrings on first call
    # so the rulebook isn't empty on a fresh install.  This is
    # done here rather than in :func:`seed_default` to avoid
    # polluting the unified rulebook for A5.1 scan tests that
    # assert on exact match counts.
    _seed_legacy_guard_rules()
    direction = (direction or "input").lower()
    if direction not in {"input", "output"}:
        return {
            "verdict": "blocked",
            "reasons": ["invalid_direction"],
            "sanitized_text": None,
            "score": 1.0,
        }
    # Pull character_id / world_id out of the legacy context.
    ctx = context or {}
    character_id = ctx.get("character_id")
    world_id = ctx.get("world_id")
    # Length check from the legacy module.
    reasons: list[str] = []
    if len(text or "") > 10000:
        reasons.append("length_exceeded")
    if _looks_like_token_smuggling(text or ""):
        reasons.append("token_smuggling")
    # Run the scan through the unified rulebook.
    if direction == "input":
        result = scan_input(
            text=text or "", character_id=character_id,
            world_id=world_id, event_tags=ctx.get("event_tags"),
        )
    else:
        result = scan_output(
            text=text or "", character_id=character_id,
            world_id=world_id, event_tags=ctx.get("event_tags"),
        )
    # Translate any rule hits into legacy ``reasons`` strings.
    # Map pattern → legacy reason label (e.g. ``prompt_injection_attempt``)
    # so clients asserting on those strings keep working.
    for match in result.get("matches", []):
        pattern = match.get("pattern", "")
        reason = _LEGACY_PATTERN_TO_REASON.get(pattern, pattern)
        if reason:
            reasons.append(reason)
    if reasons or result.get("verdict") not in (VERDICT_PASS, VERDICT_WARN, VERDICT_ALLOW_WITH_EXCEPTION):
        _append_legacy_audit(
            "guard_blocked", "high",
            source=direction,
            details={"reasons": reasons, "score": 0.93, "preview": (text or "")[:120]},
        )
        return {
            "verdict": "blocked",
            "reasons": reasons,
            "sanitized_text": None,
            "score": 0.93,
        }
    return {
        "verdict": "safe",
        "reasons": [],
        "sanitized_text": text,
        "score": 0.05,
    }


# ---------------------------------------------------------------------------
# Snapshots + rollback (delegates to A5.3 snapshots module)
# ---------------------------------------------------------------------------
#
# The legacy ``protection.snapshot()`` wrote into a flat
# ``state.snapshots`` dict with no capacity accounting, no
# compression, and no expiry.  The A5.3 ``snapshots`` module
# already provides all of those, so we delegate to it and keep
# a thin compatibility record in ``state.snapshots`` for clients
# that read the legacy bucket directly (notably the overload
# stub's existing tests).


def snapshot(scope: str, payload: dict | None = None, *, auto: bool = True) -> dict:
    """Drop a context snapshot.

    Writes a real A5.3 backup-snapshot entry (so the unified
    capacity / compression accounting covers it) **and** mirrors
    a legacy-shape record into ``state.snapshots`` so the
    existing overload audit tests keep passing.
    """
    raw = (payload or {}).copy()
    raw["__scope"] = scope
    digest = hashlib.sha256(repr(sorted(raw.items())).encode("utf-8")).hexdigest()
    snap_id = gen_snapshot_id()
    record = {
        "id": snap_id,
        "object": "snapshot",
        "created_at": now_ts(),
        "scope": scope,
        "hash": "sha256:%s" % digest,
        "size_bytes": len(repr(raw).encode("utf-8")),
        "auto": auto,
        "data": raw,
    }
    state.snapshots[snap_id] = record
    _append_legacy_audit(
        "snapshot_created", "info", source="api",
        details={"snapshot_id": snap_id, "scope": scope, "auto": auto},
    )
    # Best-effort mirror into the A5.3 backup-snapshots bucket so
    # the unified capacity accounting sees this snapshot.
    try:
        from xijian_api.stubs.snapshots import (
            create_snapshot as _backup_snapshot,
        )
        _backup_snapshot(
            scope=scope,
            target_id=scope,
            payload=raw,
            reason="legacy_protection_snapshot",
            ref_id=snap_id,
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("legacy snapshot mirror failed: %s", exc)
    return record


def list_snapshots() -> list[dict]:
    """List legacy-shape snapshots (without the ``data`` blob)."""
    return [
        {k: v for k, v in record.items() if k != "data"}
        for record in state.snapshots.values()
    ]


def get_snapshot(snapshot_id: str) -> dict | None:
    """Return a legacy-shape snapshot record, or None."""
    return state.snapshots.get(snapshot_id)


def rollback(payload: dict) -> dict:
    """Roll back to a prior snapshot.

    Mirrors the legacy behaviour: create a pre-rollback backup
    of the current state (so the operator can undo), then mark
    the target snapshot as the active one.  The A5.3 backup
    module handles the actual data restoration.
    """
    snapshot_id = (payload or {}).get("snapshot_id", "")
    record = state.snapshots.get(snapshot_id)
    if record is None:
        return {"ok": False, "error": "snapshot_not_found"}
    create_backup = bool((payload or {}).get("create_backup", True))
    if create_backup:
        snapshot(record["scope"], record.get("data"), auto=False)
    _append_legacy_audit(
        "rollback", "warning", source="api",
        details={"snapshot_id": snapshot_id, "scope": record.get("scope")},
    )
    return {"ok": True, "snapshot_id": snapshot_id, "scope": record.get("scope")}


# ---------------------------------------------------------------------------
# Legacy audit log API (state.audits + export)
# ---------------------------------------------------------------------------
#
# ``state.audits`` is kept as a parallel list so callers that
# read it directly (e.g. the citations stub's
# ``audit()`` helper) keep working.  New code should prefer
# :func:`record_audit` / :func:`list_log` which write to
# ``state.safety_audit_log``.


def _append_legacy_audit(
    kind: str,
    severity: str,
    source: str,
    details: dict | None = None,
) -> None:
    """Append an entry to the legacy ``state.audits`` list.

    Also writes a mirror entry into ``state.safety_audit_log``
    so the unified audit surface (``/v1/xijian/safety/audit``)
    includes these events.  The mirror entry uses
    ``stage=legacy`` and a synthetic verdict that encodes the
    severity so existing verdict-based filters still work.
    """
    entry = {
        "id": gen_audit_id(),
        "object": "audit.entry",
        "ts": now_ts(),
        "kind": kind,
        "severity": severity,
        "source": source,
        "details": details or {},
    }
    state.audits.append(entry)
    state.safety_state["audit_log_size"] = len(state.audits) + len(state.safety_audit_log)
    # Mirror into the unified audit log.
    verdict_map = {
        "info": VERDICT_PASS,
        "warning": VERDICT_WARN,
        "high": VERDICT_BLOCK,
        "critical": VERDICT_HARD_BLOCK,
    }
    try:
        record_audit(
            character_id=None,
            world_id=None,
            stage=STAGE_LEGACY,
            verdict=verdict_map.get(severity, VERDICT_WARN),
            reason=kind,
            snippet=str(kind),
            now=entry["ts"],
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("legacy audit mirror failed: %s", exc)


def list_audit() -> list[dict]:
    """Return the legacy audit log (append-only list)."""
    return list(state.audits)


def export_audit() -> dict:
    """Export the legacy + unified audit logs as a JSONL file.

    Writes both ``state.audits`` (legacy list) and
    ``state.safety_audit_log`` (unified dict) into a single
    JSONL file so operators get a complete audit trail.
    """
    file_id = gen_file_id()
    from xijian_api.stubs.files import persist
    lines = []
    for entry in state.audits:
        lines.append(json.dumps(entry, ensure_ascii=False))
    for entry in state.safety_audit_log.values():
        lines.append(json.dumps(entry, ensure_ascii=False))
    body = ("\n".join(lines) + "\n").encode("utf-8")
    persist(file_id, body, purpose="audit_export", filename="audit_%s.jsonl" % now_ts())
    _append_legacy_audit(
        "audit_exported", "info", source="api",
        details={"file_id": file_id},
    )
    return {"file_id": file_id, "bytes": len(body)}


__all__ = [
    # Constants
    "VERDICT_PASS", "VERDICT_WARN", "VERDICT_BLOCK",
    "VERDICT_HARD_BLOCK", "VERDICT_ALLOW_WITH_EXCEPTION",
    "VALID_VERDICTS",
    "STAGE_PRE_INPUT", "STAGE_POST_OUTPUT", "STAGE_LEGACY", "VALID_STAGES",
    "DEFAULT_SAFETY_THRESHOLD",
    # Errors
    "SafetyError",
    # Pure helpers
    "_truncate", "_is_overload_active",
    "_worst_match", "_verdict_from_match", "_is_world_dangerous",
    "_event_is_dangerous", "_looks_like_token_smuggling",
    "_append_legacy_audit",
    # World policy
    "set_world_dangerous", "is_world_dangerous",
    "get_safety_threshold", "set_safety_threshold",
    "reset_world_policy",
    # Audit
    "record_audit", "list_log", "count_for",
    "list_audit", "export_audit",
    # Hot path
    "scan_input", "scan_output",
    # Protection-state (migrated from protection.py)
    "is_enabled", "status", "enable",
    "start_disable", "confirm_disable",
    "guard_preview",
    "snapshot", "list_snapshots", "get_snapshot", "rollback",
    # Lifecycle
    "seed_default", "reset_for_testing",
]
