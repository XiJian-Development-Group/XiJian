"""Utility helpers used across the XiJian API server.

This package currently exposes:

* :mod:`xijian_api.utils.ids` — ID generators (request/trace/chat/file/...).
* :mod:`xijian_api.utils.log` — logging configuration.
* :mod:`xijian_api.utils.time` — timestamp helpers.

The utilities are intentionally tiny and dependency-free so that the rest of
the server can import them without creating cycles.
"""

from xijian_api.utils.ids import (
    gen_id,
    gen_request_id,
    gen_trace_id,
    gen_snapshot_id,
    gen_chat_id,
    gen_file_id,
    gen_batch_id,
    gen_fine_tuning_job_id,
    gen_assistant_id,
    gen_thread_id,
    gen_run_id,
    gen_video_id,
    gen_character_id,
    gen_interaction_id,
    gen_world_id,
    gen_memory_id,
    gen_audit_id,
    gen_challenge_id,
    gen_session_id,
    gen_message_id,
    gen_import_job_id,
    gen_load_op_id,
    gen_unload_op_id,
    gen_overload_event_id,
)

from xijian_api.utils.time import now_ts, iso_now

__all__ = [
    "gen_id",
    "gen_request_id",
    "gen_trace_id",
    "gen_snapshot_id",
    "gen_chat_id",
    "gen_file_id",
    "gen_batch_id",
    "gen_fine_tuning_job_id",
    "gen_assistant_id",
    "gen_thread_id",
    "gen_run_id",
    "gen_video_id",
    "gen_character_id",
    "gen_interaction_id",
    "gen_world_id",
    "gen_memory_id",
    "gen_audit_id",
    "gen_challenge_id",
    "gen_session_id",
    "gen_message_id",
    "gen_import_job_id",
    "gen_load_op_id",
    "gen_unload_op_id",
    "gen_overload_event_id",
    "now_ts",
    "iso_now",
]