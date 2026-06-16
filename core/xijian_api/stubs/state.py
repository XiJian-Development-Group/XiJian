"""Process-wide in-memory state containers for the API stubs.

Each attribute below is a plain ``dict`` (or ``list`` for ``audits``)
mapping resource id → record.  Other tasks (oai-routes, xijian-routes,
websocket) will populate these dicts as their routes are exercised.
The container module itself is intentionally minimal — it only
guarantees that the names exist so ``stubs.state.characters`` etc.
always resolves.

Per ``DESIGN.md`` §10 the buckets are:

============ ===========================================
Key          Shape
============ ===========================================
characters   ``{character_id: dict}``
interactions ``{interaction_id: dict}``
worlds       ``{world_id: dict}``
memory       ``{memory_id: dict}``
protection   ``dict`` — single object (status / settings)
sessions     ``{session_id: dict}``
files        ``{file_id: dict}``
batches      ``{batch_id: dict}``
fine_tuning_jobs ``{job_id: dict}``
assistants   ``{assistant_id: dict}``
threads      ``{thread_id: dict}``
runs         ``{run_id: dict}``
messages     ``{message_id: dict}``
videos       ``{video_id: dict}``
models       ``{model_id: dict}``
audits       list — audit log is append-only
snapshots    ``{snapshot_id: dict}``
import_jobs  ``{job_id: dict}``
============ ===========================================

The API is single-user / local, so a simple container with no
external locking is sufficient — callers can grab the GIL when they
need to perform compound mutations.
"""

from __future__ import annotations

# XiJian extension resources.
characters: dict = {}
interactions: dict = {}
worlds: dict = {}
memory: dict = {}
protection: dict = {}
sessions: dict = {}
snapshots: dict = {}
import_jobs: dict = {}

# OAI-compatible resources.
files: dict = {}
batches: dict = {}
fine_tuning_jobs: dict = {}
assistants: dict = {}
threads: dict = {}
runs: dict = {}
messages: dict = {}
videos: dict = {}
models: dict = {}

# Audit log is append-only.
audits: list = []


def reset_for_testing() -> None:
    """Wipe every bucket (used by tests).

    Each call clears the underlying objects in-place so that other
    modules that imported references (e.g. ``from xijian_api.stubs
    import state``) keep pointing at the same (now empty) containers.

    The reset is also followed by a re-seed via :func:`xijian_api.stubs.seed_all`
    so tests that depend on ``char_yuki`` / ``world_modern_tokyo`` /
    default memory entries have them in place.  Lazy import to avoid
    a circular import at module-load time.
    """
    characters.clear()
    interactions.clear()
    worlds.clear()
    memory.clear()
    protection.clear()
    sessions.clear()
    snapshots.clear()
    import_jobs.clear()
    files.clear()
    batches.clear()
    fine_tuning_jobs.clear()
    assistants.clear()
    threads.clear()
    runs.clear()
    messages.clear()
    videos.clear()
    models.clear()
    audits.clear()

    # Re-seed defaults so each test starts from a known state.
    from xijian_api.stubs import seed_all
    seed_all()


__all__ = [
    "characters",
    "interactions",
    "worlds",
    "memory",
    "protection",
    "sessions",
    "snapshots",
    "import_jobs",
    "files",
    "batches",
    "fine_tuning_jobs",
    "assistants",
    "threads",
    "runs",
    "messages",
    "videos",
    "models",
    "audits",
    "reset_for_testing",
]