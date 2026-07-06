"""Character persona editor for the Developer Kit.

Lets developers create, edit, and manage character persona documents
locally.  Output can be fed into the submission pipeline (C5) for
packing and email delivery.

Data is stored as JSON files under the user's working directory.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from typing import Any

from devkit import DevKitError


_CHARACTERS_SUBDIR = "characters"


def _gen_id() -> str:
    return f"char_{secrets.token_hex(8)}"


def _char_dir(work_dir: str, char_id: str) -> str:
    return os.path.join(work_dir, _CHARACTERS_SUBDIR, char_id)


def _char_path(work_dir: str, char_id: str) -> str:
    return os.path.join(_char_dir(work_dir, char_id), "character.json")


def _persona_path(work_dir: str, char_id: str) -> str:
    return os.path.join(_char_dir(work_dir, char_id), "persona.md")


def list_characters(work_dir: str) -> list[dict[str, Any]]:
    base = os.path.join(work_dir, _CHARACTERS_SUBDIR)
    if not os.path.isdir(base):
        return []
    results: list[dict[str, Any]] = []
    for entry in sorted(os.listdir(base)):
        dirpath = os.path.join(base, entry)
        if not os.path.isdir(dirpath):
            continue
        fpath = os.path.join(dirpath, "character.json")
        if os.path.isfile(fpath):
            try:
                with open(fpath, encoding="utf-8") as f:
                    data = json.load(f)
                results.append(data)
            except (json.JSONDecodeError, OSError):
                continue
    return results


def get_character(work_dir: str, char_id: str) -> dict[str, Any] | None:
    fpath = _char_path(work_dir, char_id)
    if not os.path.isfile(fpath):
        return None
    try:
        with open(fpath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def import_persona(work_dir: str, char_id: str, file_path: str) -> str:
    if not os.path.isfile(file_path):
        raise DevKitError(400, f"文件不存在: {file_path}", code="file_not_found")
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in (".md", ".markdown", ".txt"):
        raise DevKitError(400, f"不支持的文件格式: {ext}（仅支持 .md / .markdown / .txt）", code="bad_format")
    with open(file_path, encoding="utf-8") as f:
        content = f.read()
    if not content.strip():
        raise DevKitError(400, "文件内容为空", code="empty_file")
    record = get_character(work_dir, char_id)
    if not record:
        raise DevKitError(404, f"角色 {char_id} 不存在", code="not_found")
    persona_path = _persona_path(work_dir, char_id)
    os.makedirs(os.path.dirname(persona_path), exist_ok=True)
    with open(persona_path, "w", encoding="utf-8") as f:
        f.write(content)
    record["persona_doc"] = content
    record["updated_at"] = __import__("devkit._vendor", fromlist=["iso_now"]).iso_now()
    with open(_char_path(work_dir, char_id), "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return content


def save_character(work_dir: str, data: dict[str, Any]) -> dict[str, Any]:
    if not data.get("name"):
        raise DevKitError(400, "角色名称不能为空", code="missing_name")
    from devkit._vendor import iso_now
    now = iso_now()
    existing_id = data.get("id", "")
    if existing_id:
        char_id = existing_id
    else:
        char_id = _gen_id()
    char_dir = _char_dir(work_dir, char_id)
    os.makedirs(char_dir, exist_ok=True)

    persona_doc = data.get("persona_doc", "")
    if persona_doc:
        persona_path = _persona_path(work_dir, char_id)
        with open(persona_path, "w", encoding="utf-8") as f:
            f.write(persona_doc)

    record = {
        "id": char_id,
        "name": data.get("name", ""),
        "display_name": data.get("display_name", data.get("name", "")),
        "persona_doc": persona_doc,
        "voice_profile": data.get("voice_profile", ""),
        "default_emotion": data.get("default_emotion", "neutral"),
        "language_style": data.get("language_style", ""),
        "tags": data.get("tags", []),
        "models": data.get("models", []),
        "memory_config": {
            "max_long_term": int(data.get("memory_config", {}).get("max_long_term", 200)),
            "long_term_importance_min": float(data.get("memory_config", {}).get("long_term_importance_min", 0.6)),
            "max_short_term": int(data.get("memory_config", {}).get("max_short_term", 50)),
            "short_term_decay_rate": float(data.get("memory_config", {}).get("short_term_decay_rate", 0.05)),
            "short_term_importance_min": float(data.get("memory_config", {}).get("short_term_importance_min", 0.3)),
            "max_context_tokens": int(data.get("memory_config", {}).get("max_context_tokens", 8000)),
            "reserve_tokens_for_reply": int(data.get("memory_config", {}).get("reserve_tokens_for_reply", 2000)),
            "force_recall_on_history": bool(data.get("memory_config", {}).get("force_recall_on_history", True)),
        },
        "assigned_memory_pack": data.get("assigned_memory_pack", ""),
        "assigned_voice_pack": data.get("assigned_voice_pack", ""),
        "assigned_model": data.get("assigned_model", ""),
        "assigned_world": data.get("assigned_world", ""),
        "created_at": data.get("created_at", now),
        "updated_at": now,
    }
    fpath = _char_path(work_dir, char_id)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return record


def delete_character(work_dir: str, char_id: str) -> bool:
    char_dir = _char_dir(work_dir, char_id)
    if not os.path.isdir(char_dir):
        return False
    shutil.rmtree(char_dir)
    return True


def export_character_for_submit(work_dir: str, char_id: str) -> dict[str, Any]:
    record = get_character(work_dir, char_id)
    if not record:
        raise DevKitError(404, f"角色 {char_id} 不存在", code="not_found")
    files = []
    persona_path = _persona_path(work_dir, char_id)
    if os.path.isfile(persona_path):
        files.append({"path": persona_path, "arcname": "persona.md"})
    export = {
        "target_kind": "character",
        "target_id": char_id,
        "payload": {
            "notes": f"角色: {record.get('display_name', record['name'])} ({record['name']})",
            "files": [persona_path] if files else [],
        },
        "files": files,
    }
    return export
