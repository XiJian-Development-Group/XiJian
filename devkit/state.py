"""Disk-backed state for the Developer-Kit (Pywebview app).

The DevKit is **deliberately standalone** — it does not share state with
the main Flask API server.  This module owns its own three buckets,
unrelated to the main API's ``xijian_api.stubs.state``:

=========== ============================================================
Key         Shape
=========== ============================================================
submissions     ``{submission_id: dict}`` — full record per submission
last_submit_at  ``{developer_id: iso8601_string}`` — for the 1h cooldown
local_archives  ``{submission_id: archive_path}`` — for later cleanup
session         ``{"developer_id": str|None}`` — last logged-in developer
=========== ============================================================

All three buckets are persisted to a JSON file in the work directory
so submission history survives a DevKit restart (C5-03).  The file is
loaded once at module import and saved after every mutation.
"""

from __future__ import annotations

import json
import os


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

#: ``{"developer_id": str | None}`` — the developer who was logged in
#: when the DevKit last shut down.  Persisted so that a restart does
#: **not** silently drop the session and reset the per-developer
#: cooldown display (C5 anti-abuse: a restart must never let a
#: developer bypass the 1-hour submit cooldown).
session: dict = {"developer_id": None}


def _state_path(work_dir: str) -> str:
    return os.path.join(work_dir, "devkit_state.json")


def load(work_dir: str) -> None:
    """Load DevKit state from a JSON file in ``work_dir``, replacing all
    in-memory buckets.  Safe to call multiple times — resets first."""
    reset_for_testing()
    fpath = _state_path(work_dir)
    if not os.path.isfile(fpath):
        return
    try:
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    submissions.update(data.get("submissions", {}))
    last_submit_at.update(data.get("last_submit_at", {}))
    local_archives.update(data.get("local_archives", {}))
    session["developer_id"] = data.get("session", {}).get("developer_id")


def save(work_dir: str) -> None:
    """Persist the in-memory buckets to a JSON file in ``work_dir``."""
    os.makedirs(work_dir, exist_ok=True)
    data = {
        "submissions": dict(submissions),
        "last_submit_at": dict(last_submit_at),
        "local_archives": dict(local_archives),
        "session": dict(session),
    }
    with open(_state_path(work_dir), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def reset_for_testing() -> None:
    """Wipe every DevKit bucket.  Test-only — never call from app code."""
    submissions.clear()
    last_submit_at.clear()
    local_archives.clear()
    session["developer_id"] = None


__all__ = [
    "submissions",
    "last_submit_at",
    "local_archives",
    "session",
    "load",
    "save",
    "reset_for_testing",
]
