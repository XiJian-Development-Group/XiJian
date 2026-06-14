"""Stub OAI batches."""

from __future__ import annotations

import threading
import time

from xijian_api.stubs import state
from xijian_api.utils.time import now_ts


_PROGRESS_DELAY_SECONDS = 0.05  # 50 ms


def schedule_completion(batch_id: str) -> None:
    """Walk the batch through validating → in_progress → completed."""
    def _run():
        time.sleep(_PROGRESS_DELAY_SECONDS)
        record = state.batches.get(batch_id)
        if record is None:
            return
        record["status"] = "in_progress"
        time.sleep(_PROGRESS_DELAY_SECONDS)
        record = state.batches.get(batch_id)
        if record is None:
            return
        record["status"] = "completed"
        record["completed_at"] = now_ts()
        record["request_counts"] = {"total": 1, "completed": 1, "failed": 0}
        # Snapshot the request payload as a result file id.
        from xijian_api.stubs.files import persist
        from xijian_api.utils.ids import gen_file_id
        results_id = gen_file_id()
        body = b'{"result":[]}\n'
        persist(results_id, body, purpose="batch_result", filename=f"batch_{batch_id}.jsonl")
        record["output_file_id"] = results_id
        record["error_file_id"] = None

    threading.Thread(target=_run, daemon=True).start()


__all__ = ["schedule_completion"]