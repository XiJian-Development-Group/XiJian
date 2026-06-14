"""Stub video generation — async-style: queued → in_progress → completed."""

from __future__ import annotations

import threading
import time

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_file_id
from xijian_api.utils.time import now_ts


_COMPLETE_DELAY_SECONDS = 0.2  # 200 ms


def submit(
    prompt: str,
    *,
    model: str = "stub-video",
    input_reference: str | None = None,
    seconds: int = 4,
    size: str = "1280x720",
    fps: int = 24,
    seed: int | None = None,
    video_id: str,
) -> None:
    """Schedule a background completion for ``video_id``.

    The route module is responsible for inserting the queued record
    into ``state.videos`` first; this function only flips the status.
    """
    def _complete():
        time.sleep(_COMPLETE_DELAY_SECONDS)
        record = state.videos.get(video_id)
        if not record:
            return
        record["status"] = "completed"
        record["completed_at"] = now_ts()
        record["expires_at"] = now_ts() + 600
        # Create a dummy file to back the URL.
        file_id = gen_file_id()
        state.files[file_id] = {
            "id": file_id,
            "bytes": b"\x00" * 64,
            "purpose": "vision",
            "filename": f"video_{video_id}.bin",
            "content_type": "video/mp4",
        }
        record["url"] = f"/v1/files/{file_id}/content"

    threading.Thread(target=_complete, daemon=True).start()


__all__ = ["submit"]