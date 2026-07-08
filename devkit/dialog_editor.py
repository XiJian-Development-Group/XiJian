from __future__ import annotations

import json
import os
import secrets
from typing import Any

from devkit import DevKitError


_DIALOG_SUBDIR = "dialogs"
_MIN_DIALOG_COUNT = 8


def _gen_id() -> str:
    return f"dialog_{secrets.token_hex(8)}"


def _dialog_dir(work_dir: str, character_id: str) -> str:
    return os.path.join(work_dir, _DIALOG_SUBDIR, character_id)


def _meta_path(work_dir: str, character_id: str) -> str:
    return os.path.join(_dialog_dir(work_dir, character_id), "dialogs.json")


def _load_dialogs(work_dir: str, character_id: str) -> list[dict[str, Any]]:
    fpath = _meta_path(work_dir, character_id)
    if not os.path.isfile(fpath):
        return []
    try:
        with open(fpath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_dialogs(work_dir: str, character_id: str, dialogs: list[dict[str, Any]]) -> None:
    d = _dialog_dir(work_dir, character_id)
    os.makedirs(d, exist_ok=True)
    with open(_meta_path(work_dir, character_id), "w", encoding="utf-8") as f:
        json.dump(dialogs, f, ensure_ascii=False, indent=2)


def list_dialog_characters(work_dir: str) -> list[str]:
    base = os.path.join(work_dir, _DIALOG_SUBDIR)
    if not os.path.isdir(base):
        return []
    return sorted(os.listdir(base))


def list_dialogs(work_dir: str, character_id: str) -> list[dict[str, Any]]:
    return _load_dialogs(work_dir, character_id)


def get_dialog(work_dir: str, dialog_id: str) -> dict[str, Any] | None:
    base = os.path.join(work_dir, _DIALOG_SUBDIR)
    if not os.path.isdir(base):
        return None
    for char_dir in os.listdir(base):
        dialogs = _load_dialogs(work_dir, char_dir)
        for d in dialogs:
            if d.get("id") == dialog_id:
                return d
    return None


def save_dialog(work_dir: str, character_id: str, data: dict[str, Any]) -> dict[str, Any]:
    if not character_id:
        raise DevKitError(400, "角色 ID 不能为空", code="missing_character_id")

    dialogs = _load_dialogs(work_dir, character_id)
    dialog_id = data.get("id", _gen_id())

    now = __import__("devkit._vendor", fromlist=["iso_now"]).iso_now()
    record = {
        "id": dialog_id,
        "character_id": character_id,
        "turn": data.get("turn", len(dialogs) + 1),
        "scene": data.get("scene", ""),
        "user_message": data.get("user_message", ""),
        "character_message": data.get("character_message", ""),
        "emotion": data.get("emotion", "neutral"),
        "notes": data.get("notes", ""),
        "source": data.get("source", "manual"),
        "created_at": data.get("created_at", now) if dialog_id else now,
        "updated_at": now,
    }

    existing_idx = next((i for i, d in enumerate(dialogs) if d.get("id") == dialog_id), -1)
    if existing_idx >= 0:
        dialogs[existing_idx] = record
    else:
        dialogs.append(record)

    _save_dialogs(work_dir, character_id, dialogs)
    return record


def delete_dialog(work_dir: str, dialog_id: str) -> bool:
    base = os.path.join(work_dir, _DIALOG_SUBDIR)
    if not os.path.isdir(base):
        return False
    for char_dir in os.listdir(base):
        dialogs = _load_dialogs(work_dir, char_dir)
        before = len(dialogs)
        dialogs = [d for d in dialogs if d.get("id") != dialog_id]
        if len(dialogs) < before:
            _save_dialogs(work_dir, char_dir, dialogs)
            return True
    return False


def check_dialog_minimum(work_dir: str, character_id: str) -> dict[str, Any]:
    dialogs = _load_dialogs(work_dir, character_id)
    count = len(dialogs)
    ok = count >= _MIN_DIALOG_COUNT
    return {
        "character_id": character_id,
        "current_count": count,
        "minimum_required": _MIN_DIALOG_COUNT,
        "meets_requirement": ok,
        "ok": ok,
        "message": (
            f"当前 {count} 条对话样本，已满足最少 {_MIN_DIALOG_COUNT} 条要求"
            if ok
            else f"当前仅 {count} 条对话样本，至少需要 {_MIN_DIALOG_COUNT} 条（还差 {_MIN_DIALOG_COUNT - count} 条）"
        ),
    }


def export_dialogs_for_submit(work_dir: str, character_id: str) -> dict[str, Any]:
    dialogs = _load_dialogs(work_dir, character_id)
    d = _dialog_dir(work_dir, character_id)
    os.makedirs(d, exist_ok=True)
    path = _meta_path(work_dir, character_id)

    return {
        "target_kind": "character",
        "files": [{
            "path": path,
            "arcname": f"dialogs/{character_id}/dialogs.json",
            "size": os.path.getsize(path) if os.path.isfile(path) else 0,
        }],
        "payload": {
            "notes": f"{len(dialogs)} 条对话样本",
            "files": [path] if os.path.isfile(path) else [],
        },
    }
