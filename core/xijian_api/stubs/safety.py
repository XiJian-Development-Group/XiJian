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
"""

from __future__ import annotations

import logging
from typing import Any

from xijian_api.stubs import safety_rules as rules_stub
from xijian_api.stubs import state
from xijian_api.utils.ids import gen_safety_audit_id
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.safety")


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
STAGE_PRE_INPUT = "pre_input"
STAGE_POST_OUTPUT = "post_output"
VALID_STAGES: frozenset[str] = frozenset({STAGE_PRE_INPUT, STAGE_POST_OUTPUT})

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
            if _is_world_dangerous(world_id) and _event_is_dangerous(event_tags):
                # US-A5.1-02 exception path.  AC-2 "显式记录原因".
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
    """Idempotent default-seed.  No rules or audit entries by
    default — operators build the rulebook."""
    return None


def reset_for_testing() -> None:
    """Wipe audit log, rulebook (caller's responsibility — see
    :mod:`safety_rules`), and per-world policy."""
    global _AUDIT_SEQUENCE
    _AUDIT_SEQUENCE = 0
    state.safety_audit_log.clear()
    _WORLD_DANGEROUS.clear()
    _WORLD_THRESHOLDS.clear()


__all__ = [
    # Constants
    "VERDICT_PASS", "VERDICT_WARN", "VERDICT_BLOCK",
    "VERDICT_HARD_BLOCK", "VERDICT_ALLOW_WITH_EXCEPTION",
    "VALID_VERDICTS",
    "STAGE_PRE_INPUT", "STAGE_POST_OUTPUT", "VALID_STAGES",
    "DEFAULT_SAFETY_THRESHOLD",
    # Errors
    "SafetyError",
    # Pure helpers
    "_truncate", "_is_overload_active",
    "_worst_match", "_verdict_from_match", "_is_world_dangerous",
    "_event_is_dangerous",
    # World policy
    "set_world_dangerous", "is_world_dangerous",
    "get_safety_threshold", "set_safety_threshold",
    "reset_world_policy",
    # Audit
    "record_audit", "list_log", "count_for",
    # Hot path
    "scan_input", "scan_output",
    # Lifecycle
    "seed_default", "reset_for_testing",
]
