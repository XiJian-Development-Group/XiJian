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