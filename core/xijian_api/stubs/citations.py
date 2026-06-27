"""Citation audit module — verifies model responses against the memory store.

This module implements the "引用审查" half of the A1.2 forced-recall
pipeline (see ``docs/Dev. Function List功能清单v2.md`` A1.2 §强制调用规则
and §验收标准 AC-3 / AC-4):

* AC-3: "当模型回复中包含可被记忆库验证的事实但未引用时，审查模块必须
  记录 warning。"
* AC-4: "模型回复中不允许凭空捏造过去对话内容（无对应 memory_entry）。"

Workflow
--------

The chat pipeline feeds the assistant's final response text plus the
    list of ``entry_id``\\s the model claimed to have cited into
:func:`audit`.  The function:

1. Resolves every candidate ``entry_id`` against :mod:`memory` and
   partitions them into *real* vs *missing*.
2. Scans the response text for "you said" / "you mentioned" / "last time"
   style phrases that imply knowledge of past events.  When such
   phrases appear **without** any real ``entry_id`` citation, the audit
   records an ``uncited_history_reference`` warning.
3. Returns a verdict — ``"pass"`` / ``"warn"`` — plus the warning list
   and the (de-duplicated) audited entry id set.
4. Appends an audit event to :data:`xijian_api.stubs.state.audits` so the
   protection module can surface it via ``GET /v1/xijian/protection/audit``.

The audit is intentionally deterministic and local: no LLM is
involved.  Real production deployments would swap :func:`audit` for a
learned classifier, but the contract (inputs, return shape, audit
event payload) stays the same.
"""

from __future__ import annotations

import re
import threading
from typing import Iterable

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_audit_id
from xijian_api.utils.time import now_ts


# ---------------------------------------------------------------------------
# Heuristics — phrases that imply a reference to past events.
# ---------------------------------------------------------------------------


#: Lowercased substrings that strongly imply "I am referring to past
#: information".  Matched anywhere in the response text.  Both Chinese
#: and English phrasings are covered; the spec calls these out as
#: primary hallucination patterns (AC-4).
_PAST_REFERENCE_PHRASES: tuple[str, ...] = (
    # Chinese
    "你上次说",
    "你说过",
    "你之前提到",
    "你之前说过",
    "你曾经",
    "你之前",
    "你提到过",
    "记得你",
    "我记得你",
    "上次你",
    "之前你",
    # English
    "you said",
    "you mentioned",
    "you told me",
    "you once said",
    "last time you",
    "last time we",
    "remember when",
    "as you mentioned",
    "as you said",
    "previously you",
)


#: Compiled regex used for fast substring checks.
_PAST_REFERENCE_RE = re.compile(
    "|".join(re.escape(p) for p in _PAST_REFERENCE_PHRASES),
    re.IGNORECASE,
)


#: Verdict constants — stable strings callers can switch on.
VERDICT_PASS = "pass"
VERDICT_WARN = "warn"
VERDICT_BLOCK = "block"


#: Audit event kinds so callers can filter the audit log.
KIND_AUDITED = "citation_audited"
KIND_MISSING = "citation_missing_entry"
KIND_UNCITED = "citation_uncited_history"


_AUDIT_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def has_past_reference(response_text: str) -> bool:
    """Return True when ``response_text`` mentions past events.

    Public helper so the chat pipeline can short-circuit when neither
    side needs the full :func:`audit` machinery.
    """
    if not response_text:
        return False
    return _PAST_REFERENCE_RE.search(response_text) is not None


def audit(
    *,
    response_text: str,
    candidate_entry_ids: Iterable[str] | None = None,
    response_id: str | None = None,
) -> dict:
    """Audit an assistant response for citation faithfulness.

    Parameters
    ----------
    response_text:
        The assistant's final reply text (already-decoded, no JSON).
    candidate_entry_ids:
        The ``entry_id``\\s the model claimed to have cited (typically
        forwarded from the ``recall_memory`` tool result).
    response_id:
        Optional upstream response / message id for traceability.

    Returns
    -------
    dict
        ``{
            "verdict": "pass" | "warn" | "block",
            "warnings": [{"kind": ..., "entry_ids": [...], "detail": ...}, ...],
            "audited_entry_ids": [...],
            "missing_entry_ids": [...],
        }``

        ``verdict`` is ``"block"`` only when :func:`_append_audit` itself
        raises — i.e. never under normal operation.  Callers should
        treat ``"warn"`` as "the response made an unsupported claim"
        and either surface the warning to the user or trigger a
        regenerate (the spec caps regeneration at 2 attempts).
    """
    candidates = _dedup(candidate_entry_ids or [])
    real: list[str] = []
    missing: list[str] = []
    for entry_id in candidates:
        if entry_id and state.memory.get(entry_id) is not None:
            real.append(entry_id)
        else:
            missing.append(entry_id)

    warnings: list[dict] = []
    if missing:
        warnings.append(
            {
                "kind": "missing_citation",
                "entry_ids": list(missing),
                "detail": "model cited memory entries that don't exist in the store",
            }
        )

    uncited = has_past_reference(response_text) and not real
    if uncited:
        warnings.append(
            {
                "kind": "uncited_history_reference",
                "entry_ids": [],
                "detail": (
                    "response references past events but no real memory "
                    "entry was cited"
                ),
            }
        )

    if not warnings:
        verdict = VERDICT_PASS
    elif missing:
        # Missing citations are an explicit AC-3 violation — that's a
        # strong "warn" but not a hard block (the model may simply
        # have referenced stale ids after consolidation).
        verdict = VERDICT_WARN
    elif uncited:
        verdict = VERDICT_WARN
    else:
        verdict = VERDICT_PASS

    # Append audit events.  We log one per distinct warning kind so
    # the protection audit view can filter precisely.
    severity = "info" if verdict == VERDICT_PASS else "warning"
    _append_audit(
        KIND_AUDITED,
        severity,
        source="chat",
        details={
            "response_id": response_id,
            "audited_entry_ids": real,
            "missing_entry_ids": list(missing),
            "warnings": warnings,
            "verdict": verdict,
        },
    )
    if missing:
        for entry_id in missing:
            _append_audit(
                KIND_MISSING,
                "warning",
                source="chat",
                details={"response_id": response_id, "entry_id": entry_id},
            )
    if uncited:
        _append_audit(
            KIND_UNCITED,
            "warning",
            source="chat",
            details={"response_id": response_id, "snippet": (response_text or "")[:120]},
        )

    return {
        "verdict": verdict,
        "warnings": warnings,
        "audited_entry_ids": real,
        "missing_entry_ids": list(missing),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _dedup(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _append_audit(kind: str, severity: str, *, source: str, details: dict | None = None) -> None:
    entry = {
        "id": gen_audit_id(),
        "object": "audit.entry",
        "ts": now_ts(),
        "kind": kind,
        "severity": severity,
        "source": source,
        "details": details or {},
    }
    # The audit log is append-only; we take a lock so concurrent
    # chat completions don't interleave appends in a way that breaks
    # test assertions about ordering.
    with _AUDIT_LOCK:
        state.audits.append(entry)


__all__ = [
    "VERDICT_PASS",
    "VERDICT_WARN",
    "VERDICT_BLOCK",
    "KIND_AUDITED",
    "KIND_MISSING",
    "KIND_UNCITED",
    "has_past_reference",
    "audit",
]
