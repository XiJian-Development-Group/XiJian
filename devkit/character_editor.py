"""Character persona editor for the Developer Kit.

Lets developers create, edit, and manage character persona documents
locally.  Output can be fed into the submission pipeline (C5) for
packing and email delivery.

Data is stored as JSON files under the user's working directory
(``devkit_work_dir()``).
"""

from __future__ import annotations

import json
import os
import re
import secrets
from typing import Any

from devkit import DevKitError


_CHARACTERS_SUBDIR = "characters"


def _gen_id() -> str:
    return f"char_{secrets.token_hex(8)}"


def _sanitise_id(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", name.lower().strip())
    return safe[:32] or "character"


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


def _default_character(name: str, display_name: str) -> dict[str, Any]:
    return {
        "id": "",
        "name": name,
        "display_name": display_name or name,
        "persona_doc": "",
        "voice_profile": "",
        "default_emotion": "neutral",
        "tags": [],
        "models": [],
        "created_at": "",
        "updated_at": "",
    }


def save_character(work_dir: str, data: dict[str, Any]) -> dict[str, Any]:
    if not data.get("name"):
        raise DevKitError(400, "角色名称不能为空", code="missing_name")
    existing_id = data.get("id", "")
    if existing_id:
        char_id = existing_id
    else:
        char_id = _gen_id()
    from devkit._vendor import iso_now

    now = iso_now()
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
        "tags": data.get("tags", []),
        "models": data.get("models", []),
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
    import shutil
    shutil.rmtree(char_dir)
    return True


def export_character_for_submit(work_dir: str, char_id: str) -> dict[str, Any]:
    record = get_character(work_dir, char_id)
    if not record:
        raise DevKitError(404, f"角色 {char_id} 不存在", code="not_found")
    persona_path = _persona_path(work_dir, char_id)
    files = []
    if os.path.isfile(persona_path):
        files.append({"path": persona_path, "arcname": "persona.md"})
    return {
        "target_kind": "character",
        "target_id": char_id,
        "payload": {
            "notes": f"角色: {record['display_name']} ({record['name']})",
            "files": [persona_path],
        },
        "files": files,
    }
