from __future__ import annotations

import json
import os
import secrets
from typing import Any

from devkit import DevKitError


_MOTION_SUBDIR = "motions"
_DEFAULT_MOTIONS = ("idle", "happy", "sad", "angry", "surprised", "neutral")


def _gen_id() -> str:
    return f"motion_{secrets.token_hex(8)}"


def _motion_dir(work_dir: str, character_id: str) -> str:
    return os.path.join(work_dir, _MOTION_SUBDIR, character_id)


def _meta_path(work_dir: str, character_id: str) -> str:
    return os.path.join(_motion_dir(work_dir, character_id), "motions.json")


def _load_motions(work_dir: str, character_id: str) -> list[dict[str, Any]]:
    fpath = _meta_path(work_dir, character_id)
    if not os.path.isfile(fpath):
        return []
    try:
        with open(fpath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_motions(work_dir: str, character_id: str, motions: list[dict[str, Any]]) -> None:
    d = _motion_dir(work_dir, character_id)
    os.makedirs(d, exist_ok=True)
    with open(_meta_path(work_dir, character_id), "w", encoding="utf-8") as f:
        json.dump(motions, f, ensure_ascii=False, indent=2)


def list_motion_characters(work_dir: str) -> list[str]:
    base = os.path.join(work_dir, _MOTION_SUBDIR)
    if not os.path.isdir(base):
        return []
    return sorted(os.listdir(base))


def list_motions(work_dir: str, character_id: str) -> list[dict[str, Any]]:
    existing = _load_motions(work_dir, character_id)
    existing_names = {m.get("name") for m in existing}
    for default_name in _DEFAULT_MOTIONS:
        if default_name not in existing_names:
            existing.append({
                "id": _gen_id(),
                "name": default_name,
                "character_id": character_id,
                "type": "builtin",
                "description": _default_motion_desc(default_name),
                "parameters": _default_motion_params(default_name),
            })
            existing_names.add(default_name)
    if len([e for e in existing if e.get("type") == "builtin"]) == len(_DEFAULT_MOTIONS):
        pass
    return sorted(existing, key=lambda m: list(_DEFAULT_MOTIONS).index(m["name"]) if m["name"] in _DEFAULT_MOTIONS else 99)


def get_motion(work_dir: str, motion_id: str) -> dict[str, Any] | None:
    base = os.path.join(work_dir, _MOTION_SUBDIR)
    if not os.path.isdir(base):
        return None
    for char_dir in os.listdir(base):
        motions = _load_motions(work_dir, char_dir)
        for m in motions:
            if m.get("id") == motion_id:
                return m
    return None


def save_motion(work_dir: str, character_id: str, data: dict[str, Any]) -> dict[str, Any]:
    if not character_id:
        raise DevKitError(400, "角色 ID 不能为空", code="missing_character_id")
    name = data.get("name", "").strip()
    if not name:
        raise DevKitError(400, "动作名称不能为空", code="missing_name")

    motions = _load_motions(work_dir, character_id)
    motion_id = data.get("id", _gen_id())

    now = __import__("devkit._vendor", fromlist=["iso_now"]).iso_now()
    record = {
        "id": motion_id,
        "character_id": character_id,
        "name": name,
        "type": data.get("type", "custom"),
        "description": data.get("description", ""),
        "parameters": data.get("parameters", {}),
        "file_path": data.get("file_path", ""),
        "duration_seconds": data.get("duration_seconds", 2.0),
        "loop": data.get("loop", False),
        "created_at": data.get("created_at", now) if motion_id else now,
        "updated_at": now,
    }

    existing_idx = next((i for i, m in enumerate(motions) if m.get("id") == motion_id), -1)
    if existing_idx >= 0:
        motions[existing_idx] = record
    else:
        motions.append(record)

    _save_motions(work_dir, character_id, motions)
    return record


def delete_motion(work_dir: str, motion_id: str) -> bool:
    base = os.path.join(work_dir, _MOTION_SUBDIR)
    if not os.path.isdir(base):
        return False
    for char_dir in os.listdir(base):
        motions = _load_motions(work_dir, char_dir)
        before = len(motions)
        motions = [m for m in motions if m.get("id") != motion_id]
        if len(motions) < before:
            _save_motions(work_dir, char_dir, motions)
            return True
    return False


def import_motion_file(work_dir: str, character_id: str, file_path: str, name: str) -> dict[str, Any]:
    if not os.path.isfile(file_path):
        raise DevKitError(400, f"文件不存在: {file_path}", code="file_not_found")
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in (".bvh", ".fbx", ".glb", ".gltf"):
        raise DevKitError(400, f"不支持的动效格式: {ext}（仅支持 .bvh / .fbx / .glb / .gltf）", code="bad_format")

    d = _motion_dir(work_dir, character_id)
    os.makedirs(d, exist_ok=True)

    import shutil
    motion_id = _gen_id()
    dest = os.path.join(d, f"{motion_id}{ext}")
    shutil.copy2(file_path, dest)

    record = {
        "id": motion_id,
        "character_id": character_id,
        "name": name or os.path.basename(file_path),
        "type": "imported",
        "description": f"从 {os.path.basename(file_path)} 导入",
        "file_path": dest,
        "parameters": {},
        "duration_seconds": 2.0,
        "loop": False,
    }

    motions = _load_motions(work_dir, character_id)
    motions.append(record)
    _save_motions(work_dir, character_id, motions)
    return record


def export_motions_for_submit(work_dir: str, character_id: str) -> dict[str, Any]:
    motions = _load_motions(work_dir, character_id)
    files: list[dict[str, Any]] = []
    meta_path = _meta_path(work_dir, character_id)

    if os.path.isfile(meta_path):
        files.append({
            "path": meta_path,
            "arcname": f"motions/{character_id}/motions.json",
            "size": os.path.getsize(meta_path),
        })

    for motion in motions:
        fp = motion.get("file_path", "")
        if fp and os.path.isfile(fp):
            files.append({
                "path": fp,
                "arcname": f"motions/{character_id}/{motion['id']}{os.path.splitext(fp)[1]}",
                "size": os.path.getsize(fp),
            })

    return {
        "target_kind": "character",
        "files": files,
        "payload": {
            "notes": f"{len(motions)} 个动作",
            "files": [f["path"] for f in files],
        },
    }


def _default_motion_desc(name: str) -> str:
    _descs = {
        "idle": "待机姿态",
        "happy": "高兴/开心",
        "sad": "悲伤/难过",
        "angry": "生气/愤怒",
        "surprised": "惊讶/吃惊",
        "neutral": "中立/平静",
    }
    return _descs.get(name, "")


def _default_motion_params(name: str) -> dict[str, Any]:
    _params = {
        "idle": {"blend_duration": 0.5, "loop": True},
        "happy": {"blend_duration": 0.3, "loop": False, "intensity": 0.8},
        "sad": {"blend_duration": 0.4, "loop": False, "intensity": 0.6},
        "angry": {"blend_duration": 0.2, "loop": False, "intensity": 0.9},
        "surprised": {"blend_duration": 0.15, "loop": False, "intensity": 1.0},
        "neutral": {"blend_duration": 0.5, "loop": True},
    }
    return _params.get(name, {"blend_duration": 0.3, "loop": False})
