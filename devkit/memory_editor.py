"""Memory entry editor for the Developer Kit.

Lets developers write and manage memory entries (long-term / short-term)
for characters.  Entries are stored as JSON under the work directory.
"""

from __future__ import annotations

import json
import os
import secrets
from typing import Any

from devkit import DevKitError


_MEMORIES_SUBDIR = "memories"


def _gen_id() -> str:
    return f"mem_{secrets.token_hex(8)}"


def _mem_dir(work_dir: str, character_id: str) -> str:
    return os.path.join(work_dir, _MEMORIES_SUBDIR, character_id)


def _entries_path(work_dir: str, character_id: str) -> str:
    return os.path.join(_mem_dir(work_dir, character_id), "entries.json")


def _load_entries(work_dir: str, character_id: str) -> list[dict[str, Any]]:
    fpath = _entries_path(work_dir, character_id)
    if not os.path.isfile(fpath):
        return []
    try:
        with open(fpath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_entries(work_dir: str, character_id: str, entries: list[dict[str, Any]]) -> None:
    mem_dir = _mem_dir(work_dir, character_id)
    os.makedirs(mem_dir, exist_ok=True)
    fpath = _entries_path(work_dir, character_id)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def list_entries(work_dir: str, character_id: str) -> list[dict[str, Any]]:
    entries = _load_entries(work_dir, character_id)
    entries.sort(key=lambda e: e.get("updated_at", ""), reverse=True)
    return entries


def list_characters_with_memories(work_dir: str) -> list[str]:
    base = os.path.join(work_dir, _MEMORIES_SUBDIR)
    if not os.path.isdir(base):
        return []
    return sorted(os.listdir(base))


def get_entry(work_dir: str, entry_id: str) -> dict[str, Any] | None:
    char_base = os.path.join(work_dir, _MEMORIES_SUBDIR)
    if not os.path.isdir(char_base):
        return None
    for char_dir_name in os.listdir(char_base):
        entries = _load_entries(work_dir, char_dir_name)
        for entry in entries:
            if entry.get("id") == entry_id:
                return entry
    return None


def save_entry(work_dir: str, data: dict[str, Any]) -> dict[str, Any]:
    character_id = data.get("character_id", "")
    content = data.get("content", "")
    if not character_id:
        raise DevKitError(400, "请指定角色 ID", code="missing_character_id")
    if not content:
        raise DevKitError(400, "记忆内容不能为空", code="missing_content")
    from devkit._vendor import iso_now
    now = iso_now()
    existing_id = data.get("id", "")
    entries = _load_entries(work_dir, character_id)
    if existing_id:
        entry = next((e for e in entries if e.get("id") == existing_id), None)
        if entry:
            entry["content"] = content
            entry["importance"] = float(data.get("importance", entry.get("importance", 0.5)))
            entry["type"] = data.get("type", entry.get("type", "long"))
            entry["tags"] = data.get("tags", entry.get("tags", []))
            entry["updated_at"] = now
            _save_entries(work_dir, character_id, entries)
            return dict(entry)
        else:
            raise DevKitError(404, f"条目 {existing_id} 不存在", code="not_found")
    entry_id = _gen_id()
    entry = {
        "id": entry_id,
        "character_id": character_id,
        "type": data.get("type", "long"),
        "content": content,
        "importance": min(1.0, max(0.0, float(data.get("importance", 0.5)))),
        "tags": data.get("tags", []),
        "source": "manual",
        "created_at": now,
        "updated_at": now,
    }
    entries.append(entry)
    _save_entries(work_dir, character_id, entries)
    return entry


def delete_entry(work_dir: str, entry_id: str) -> bool:
    char_base = os.path.join(work_dir, _MEMORIES_SUBDIR)
    if not os.path.isdir(char_base):
        return False
    for char_dir_name in os.listdir(char_base):
        entries = _load_entries(work_dir, char_dir_name)
        before = len(entries)
        entries = [e for e in entries if e.get("id") != entry_id]
        if len(entries) < before:
            _save_entries(work_dir, char_dir_name, entries)
            return True
    return False


def export_entries_for_submit(work_dir: str, character_id: str) -> dict[str, Any]:
    entries = _load_entries(work_dir, character_id)
    if not entries:
        raise DevKitError(404, f"角色 {character_id} 没有记忆条目", code="not_found")
    export_path = os.path.join(_mem_dir(work_dir, character_id), "export.json")
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    return {
        "target_kind": "character",
        "target_id": character_id,
        "payload": {
            "notes": f"记忆条目导出: {character_id} ({len(entries)} 条)",
            "files": [export_path],
        },
        "files": [{"path": export_path, "arcname": "memory_entries.json"}],
    }
