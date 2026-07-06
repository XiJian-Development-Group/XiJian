"""World view editor for the Developer Kit.

Lets developers create and edit world-view documents and world
configurations.  Data is stored as JSON + Markdown under the
working directory.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from typing import Any

from devkit import DevKitError


_WORLDS_SUBDIR = "worlds"


def _gen_id() -> str:
    return f"world_{secrets.token_hex(8)}"


def _world_dir(work_dir: str, world_id: str) -> str:
    return os.path.join(work_dir, _WORLDS_SUBDIR, world_id)


def _world_path(work_dir: str, world_id: str) -> str:
    return os.path.join(_world_dir(work_dir, world_id), "world.json")


def _world_doc_path(work_dir: str, world_id: str) -> str:
    return os.path.join(_world_dir(work_dir, world_id), "world_doc.md")


def list_worlds(work_dir: str) -> list[dict[str, Any]]:
    base = os.path.join(work_dir, _WORLDS_SUBDIR)
    if not os.path.isdir(base):
        return []
    results: list[dict[str, Any]] = []
    for entry in sorted(os.listdir(base)):
        dirpath = os.path.join(base, entry)
        if not os.path.isdir(dirpath):
            continue
        fpath = os.path.join(dirpath, "world.json")
        if os.path.isfile(fpath):
            try:
                with open(fpath, encoding="utf-8") as f:
                    results.append(json.load(f))
            except (json.JSONDecodeError, OSError):
                continue
    return results


def get_world(work_dir: str, world_id: str) -> dict[str, Any] | None:
    fpath = _world_path(work_dir, world_id)
    if not os.path.isfile(fpath):
        return None
    try:
        with open(fpath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_world(work_dir: str, data: dict[str, Any]) -> dict[str, Any]:
    if not data.get("name"):
        raise DevKitError(400, "世界观名称不能为空", code="missing_name")
    from devkit._vendor import iso_now
    now = iso_now()
    existing_id = data.get("id", "")
    if existing_id:
        world_id = existing_id
    else:
        world_id = _gen_id()
    world_dir = _world_dir(work_dir, world_id)
    os.makedirs(world_dir, exist_ok=True)
    world_doc = data.get("world_doc", "")
    if world_doc:
        doc_path = _world_doc_path(work_dir, world_id)
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write(world_doc)
    record = {
        "id": world_id,
        "name": data.get("name", ""),
        "world_doc": world_doc,
        "config": data.get("config", {}),
        "created_at": data.get("created_at", now),
        "updated_at": now,
    }
    fpath = _world_path(work_dir, world_id)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return record


def delete_world(work_dir: str, world_id: str) -> bool:
    wdir = _world_dir(work_dir, world_id)
    if not os.path.isdir(wdir):
        return False
    import shutil
    shutil.rmtree(wdir)
    return True


def export_world_for_submit(work_dir: str, world_id: str) -> dict[str, Any]:
    record = get_world(work_dir, world_id)
    if not record:
        raise DevKitError(404, f"世界观 {world_id} 不存在", code="not_found")
    doc_path = _world_doc_path(work_dir, world_id)
    files = []
    if os.path.isfile(doc_path):
        files.append({"path": doc_path, "arcname": "world_doc.md"})
    return {
        "target_kind": "world",
        "target_id": world_id,
        "payload": {
            "notes": f"世界观: {record['name']}",
            "files": [doc_path],
        },
        "files": files,
    }
