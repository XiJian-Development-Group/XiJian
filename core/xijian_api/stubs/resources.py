"""Stub resources import — async fake zip."""

from __future__ import annotations

import threading
import time
import zipfile
from io import BytesIO

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_file_id
from xijian_api.utils.time import now_ts


_COMPLETE_DELAY_SECONDS = 0.1  # 100 ms


def start_import(payload: dict, job_id: str) -> None:
    """Schedule completion for ``job_id`` and build a placeholder zip."""
    file_id = gen_file_id()
    state.import_jobs[job_id] = {
        "id": job_id,
        "object": "resource.import",
        "status": "queued",
        "kind": payload.get("kind", "character"),
        "name": payload.get("name", ""),
        "file_id": file_id,
        "created_at": now_ts(),
    }

    def _run():
        time.sleep(_COMPLETE_DELAY_SECONDS)
        record = state.import_jobs.get(job_id)
        if record is None:
            return
        record["status"] = "completed"
        record["completed_at"] = now_ts()
        # Write a tiny zip with a manifest so /v1/files/<id>/content
        # returns something meaningful.
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", '{"stub": true}')
        body = buf.getvalue()
        from xijian_api.stubs.files import persist
        persist(
            file_id,
            body,
            purpose="user_data",
            filename=f"import_{job_id}.zip",
        )

    threading.Thread(target=_run, daemon=True).start()


def get(job_id: str) -> dict | None:
    return state.import_jobs.get(job_id)


__all__ = ["start_import", "get"]