"""Process-wide in-memory state for the Developer-Kit (Pywebview app).

The DevKit is **deliberately standalone** — it does not share state with
the main Flask API server.  This module owns its own three buckets,
unrelated to the main API's ``xijian_api.stubs.state``:

=========== ============================================================
Key         Shape
=========== ============================================================
submissions     ``{submission_id: dict}`` — full record per submission
last_submit_at  ``{developer_id: iso8601_string}`` — for the 1h cooldown
local_archives  ``{submission_id: archive_path}`` — for later cleanup
=========== ============================================================

Keeping the buckets here means:

* the DevKit process can be started even when the main API server is
  completely offline (or never installed);
* unit tests can wipe the DevKit state via
  :func:`reset_for_testing` without touching any other stub;
* a future migration to disk-backed storage only touches this file.
"""

from __future__ import annotations


#: ``{submission_id: dict}`` — each record carries developer_id,
#: target_kind, target_id, archive_path, archive_size, archive_format,
#: content_sha256, ai_ratio, smtp_status, smtp_code, smtp_response,
#: submitted_at, email_subject, notes.
submissions: dict = {}

#: ``{developer_id: iso8601 string}`` — the most recent submission
#: timestamp per developer; used by the 1-hour rate limiter.
last_submit_at: dict = {}

#: ``{submission_id: archive_path}`` — filesystem path of the 7Z/zip
#: archive produced for each submission, so the cleanup job (and
#: tests) can find it again.
local_archives: dict = {}


def reset_for_testing() -> None:
    """Wipe every DevKit bucket.  Test-only — never call from app code."""
    submissions.clear()
    last_submit_at.clear()
    local_archives.clear()


__all__ = [
    "submissions",
    "last_submit_at",
    "local_archives",
    "reset_for_testing",
]
