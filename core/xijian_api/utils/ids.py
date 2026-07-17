"""ID generators used across the XiJian API server.

Naming convention comes straight from ``DESIGN.md`` §10.1:

============== ============================
Resource       Format
============== ============================
request_id     ``req_<12 hex>``
trace_id       ``trace_<12 hex>``
chat id        ``chatcmpl-<12 hex>``
file id        ``file-<24 hex>``
batch id       ``batch_<24 hex>``
fine-tune id   ``ftjob-<24 hex>``
assistant id   ``asst_<24 hex>``
thread id      ``thread_<24 hex>``
run id         ``run_<24 hex>``
video id       ``video_<24 hex>``
character id   ``char_<12 hex>``
interaction id ``int_<12 hex>``
world id       ``world_<12 hex>``
memory id      ``mem_<12 hex>``
snapshot id    ``snap_<YYYYMMDD>_<6 hex>``
audit id       ``audit_<12 hex>``
challenge id   ``chal_<12 hex>``
session id     ``sess_<12 hex>``
message id     ``msg_<12 hex>``
import job id  ``imp_<12 hex>``
load op id     ``load_op_<12 hex>``
unload op id   ``unload_op_<12 hex>``
============== ============================

All generators use ``secrets.token_hex`` so they are crypto-grade and
collision-resistant.
"""

from __future__ import annotations

import datetime as _dt
import secrets

# Number of hex chars for short (12) and long (24) identifiers.
_SHORT_HEX_LEN = 12
_LONG_HEX_LEN = 24


def _hex(n: int) -> str:
    """Return ``n`` random hex characters using a crypto-grade RNG."""
    # ``token_hex(n // 2)`` produces 2*n hex chars; if n is odd we'd round
    # down.  All callers pass even numbers, so this is safe.
    return secrets.token_hex(n // 2)


def gen_id(prefix: str, length: int = _SHORT_HEX_LEN) -> str:
    """Return a string of the form ``"<prefix><length hex>"``.

    Parameters
    ----------
    prefix:
        Resource prefix such as ``"req_"`` or ``"chatcmpl-"``.
    length:
        Number of hex characters after the prefix (12 or 24 typically).
    """
    return f"{prefix}{_hex(length)}"


# --- Request / trace -------------------------------------------------------

def gen_request_id() -> str:
    """Return a new request id (``req_<12 hex>``)."""
    return gen_id("req_", _SHORT_HEX_LEN)


def gen_trace_id() -> str:
    """Return a new trace id (``trace_<12 hex>``)."""
    return gen_id("trace_", _SHORT_HEX_LEN)


# --- OAI-style resources ---------------------------------------------------

def gen_chat_id() -> str:
    """Return a chat completion id (``chatcmpl-<12 hex>``)."""
    return gen_id("chatcmpl-", _SHORT_HEX_LEN)


def gen_file_id() -> str:
    """Return a file id (``file-<24 hex>``)."""
    return gen_id("file-", _LONG_HEX_LEN)


def gen_batch_id() -> str:
    """Return a batch id (``batch_<24 hex>``)."""
    return gen_id("batch_", _LONG_HEX_LEN)


def gen_fine_tuning_job_id() -> str:
    """Return a fine-tuning job id (``ftjob-<24 hex>``)."""
    return gen_id("ftjob-", _LONG_HEX_LEN)


def gen_assistant_id() -> str:
    """Return an assistant id (``asst_<24 hex>``)."""
    return gen_id("asst_", _LONG_HEX_LEN)


def gen_thread_id() -> str:
    """Return a thread id (``thread_<24 hex>``)."""
    return gen_id("thread_", _LONG_HEX_LEN)


def gen_run_id() -> str:
    """Return a run id (``run_<24 hex>``)."""
    return gen_id("run_", _LONG_HEX_LEN)


def gen_video_id() -> str:
    """Return a video id (``video_<24 hex>``)."""
    return gen_id("video_", _LONG_HEX_LEN)


# --- XiJian extension resources --------------------------------------------

def gen_character_id() -> str:
    """Return a character id (``char_<12 hex>``)."""
    return gen_id("char_", _SHORT_HEX_LEN)


def gen_interaction_id() -> str:
    """Return an interaction id (``int_<12 hex>``)."""
    return gen_id("int_", _SHORT_HEX_LEN)


def gen_world_id() -> str:
    """Return a world id (``world_<12 hex>``)."""
    return gen_id("world_", _SHORT_HEX_LEN)


def gen_memory_id() -> str:
    """Return a memory entry id (``mem_<12 hex>``)."""
    return gen_id("mem_", _SHORT_HEX_LEN)


def gen_audit_id() -> str:
    """Return an audit id (``audit_<12 hex>``)."""
    return gen_id("audit_", _SHORT_HEX_LEN)


def gen_challenge_id() -> str:
    """Return a challenge id (``chal_<12 hex>``)."""
    return gen_id("chal_", _SHORT_HEX_LEN)


def gen_session_id() -> str:
    """Return a session id (``sess_<12 hex>``)."""
    return gen_id("sess_", _SHORT_HEX_LEN)


def gen_message_id() -> str:
    """Return a message id (``msg_<12 hex>``)."""
    return gen_id("msg_", _SHORT_HEX_LEN)


def gen_import_job_id() -> str:
    """Return an import job id (``imp_<12 hex>``)."""
    return gen_id("imp_", _SHORT_HEX_LEN)


def gen_load_op_id() -> str:
    """Return a model load operation id (``load_op_<12 hex>``)."""
    return gen_id("load_op_", _SHORT_HEX_LEN)


def gen_unload_op_id() -> str:
    """Return a model unload operation id (``unload_op_<12 hex>``)."""
    return gen_id("unload_op_", _SHORT_HEX_LEN)


def gen_overload_event_id() -> str:
    """Return an overload event id (``overload_<12 hex>``)."""
    return gen_id("overload_", _SHORT_HEX_LEN)


def gen_state_log_id() -> str:
    """Return a character state log id (``cstate_<12 hex>``)."""
    return gen_id("cstate_", _SHORT_HEX_LEN)


def gen_event_id() -> str:
    """Return a world event definition id (``event_<12 hex>``)."""
    return gen_id("event_", _SHORT_HEX_LEN)


def gen_event_instance_id() -> str:
    """Return a fired world-event instance id (``evinst_<12 hex>``)."""
    return gen_id("evinst_", _SHORT_HEX_LEN)


def gen_npc_id() -> str:
    """Return an NPC id (``npc_<12 hex>``)."""
    return gen_id("npc_", _SHORT_HEX_LEN)


def gen_npc_scheduling_log_id() -> str:
    """Return an NPC-tier-transition log id (``npcsched_<12 hex>``)."""
    return gen_id("npcsched_", _SHORT_HEX_LEN)


def gen_world_audit_id() -> str:
    """Return a world-audit log id (``waudit_<12 hex>``)."""
    return gen_id("waudit_", _SHORT_HEX_LEN)


def gen_poi_id() -> str:
    """Return a point-of-interest id (``poi_<12 hex>``).

    A4.3 scene system: ``pois`` is a 3-level hierarchy (map / region /
    POI).  Each level shares this id format and is differentiated by
    the ``kind`` field on the record.
    """
    return gen_id("poi_", _SHORT_HEX_LEN)


def gen_travel_mode_id() -> str:
    """Return a travel-mode id (``tmode_<12 hex>``).

    A4.3 travel: per-world transport options like ``walk`` / ``horse``
    / ``teleport``.  Each option carries ``speed_factor``,
    ``stamina_cost`` and ``event_chance``.
    """
    return gen_id("tmode_", _SHORT_HEX_LEN)


def gen_scene_interaction_id() -> str:
    """Return a scene-interaction id (``sint_<12 hex>``).

    A4.3 scene interactions: the user triggers an action against an
    NPC / object / mechanism at a POI.  Each definition carries a
    ``cooldown_sec`` so farming / exploitation is bounded.

    The :func:`gen_interaction_id` helper above (prefix ``int_``) is
    for the chat-level interaction templates (拥抱/接吻) — different
    resource, different prefix.
    """
    return gen_id("sint_", _SHORT_HEX_LEN)


def gen_currency_id() -> str:
    """Return a currency id (``curr_<12 hex>``).

    A4.4 economy: each per-world currency definition (``mora`` /
    ``credit`` / ``gold`` etc.) gets its own id.  The ``world_currencies``
    table is keyed on ``(world_id, code)`` — this id is the *internal*
    handle so admin tools can reference a currency record without
    round-tripping the natural key.
    """
    return gen_id("curr_", _SHORT_HEX_LEN)


def gen_wallet_id() -> str:
    """Return a wallet id (``wlt_<12 hex>``).

    A4.4 economy: a wallet is the (owner_kind, owner_id, world_id,
    currency_code) tuple materialized as a single record.  The id is
    the internal handle; the natural composite key is what callers
    use to look it up.
    """
    return gen_id("wlt_", _SHORT_HEX_LEN)


def gen_transaction_id() -> str:
    """Return a transaction id (``txn_<12 hex>``).

    A4.4 economy: every money movement writes one record.  The id is
    the only mutable handle — wallets are looked up by composite key
    but every individual transaction is referenced by this id.
    """
    return gen_id("txn_", _SHORT_HEX_LEN)


def gen_economy_state_id() -> str:
    """Return an economy-state id (``eco_<12 hex>``).

    A4.4 economy: there's at most one state record per world; the id
    is for the storage layer's convenience (the bucket is keyed on
    ``world_id`` but the id gives a stable handle for audit logs).
    """
    return gen_id("eco_", _SHORT_HEX_LEN)


def gen_safety_audit_id() -> str:
    """Return a safety-audit log id (``saf_<12 hex>``).

    A5.1 output-safety: every scan (input pre-screen / output
    post-screen) lands one of these.  Operators query ``list_log``
    by id to answer "why did the safety layer block that?" — see
    AC-3 ("所有拦截事件必须可查询").
    """
    return gen_id("saf_", _SHORT_HEX_LEN)


def gen_safety_rule_id() -> str:
    """Return a safety-rule id (``rule_<12 hex>``).

    A5.1 output-safety: each rule is one of three flavours
    (``ooc_pattern`` / ``injection_pattern`` / ``forbidden_word``)
    and carries a 1..5 severity.  Inactive rules are skipped
    without being deleted so operators can A/B.
    """
    return gen_id("rule_", _SHORT_HEX_LEN)


def gen_submission_id() -> str:
    """Return a Developer-Kit submission id (``sub_<12 hex>``).

    Used by :mod:`xijian_api.devkit` — every archive / SMTP submission
    gets its own short id so it can be referenced from the receiving
    side without leaking sensitive content into the local logs.
    """
    return gen_id("sub_", _SHORT_HEX_LEN)


def gen_snapshot_id(now: _dt.datetime | None = None) -> str:
    """Return a snapshot id (``snap_<YYYYMMDD>_<6 hex>``).

    Parameters
    ----------
    now:
        Override the timestamp source (used for testing).  Defaults to
        :func:`datetime.datetime.now` in UTC.
    """
    moment = now or _dt.datetime.now(_dt.timezone.utc)
    stamp = moment.strftime("%Y%m%d")
    return f"snap_{stamp}_{secrets.token_hex(3)}"