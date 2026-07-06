"""Voice clone / voice sample manager for the Developer Kit.

Lets developers manage voice reference samples for characters.
Samples can be recorded or imported from audio files.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from typing import Any

from devkit import DevKitError


_VOICES_SUBDIR = "voices"

_SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}


def _gen_id() -> str:
    return f"voice_{secrets.token_hex(8)}"


def _voice_dir(work_dir: str, character_id: str) -> str:
    return os.path.join(work_dir, _VOICES_SUBDIR, character_id)


def _meta_path(work_dir: str, character_id: str) -> str:
    return os.path.join(_voice_dir(work_dir, character_id), "meta.json")


def _samples_dir(work_dir: str, character_id: str) -> str:
    return os.path.join(_voice_dir(work_dir, character_id), "samples")


def _load_meta(work_dir: str, character_id: str) -> list[dict[str, Any]]:
    fpath = _meta_path(work_dir, character_id)
    if not os.path.isfile(fpath):
        return []
    try:
        with open(fpath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_meta(work_dir: str, character_id: str, meta: list[dict[str, Any]]) -> None:
    vdir = _voice_dir(work_dir, character_id)
    os.makedirs(vdir, exist_ok=True)
    fpath = _meta_path(work_dir, character_id)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def list_voices(work_dir: str, character_id: str) -> list[dict[str, Any]]:
    return _load_meta(work_dir, character_id)


def get_voice(work_dir: str, voice_id: str) -> dict[str, Any] | None:
    base = os.path.join(work_dir, _VOICES_SUBDIR)
    if not os.path.isdir(base):
        return None
    for char_dir in os.listdir(base):
        meta = _load_meta(work_dir, char_dir)
        for entry in meta:
            if entry.get("id") == voice_id:
                return entry
    return None


def list_characters_with_voices(work_dir: str) -> list[str]:
    base = os.path.join(work_dir, _VOICES_SUBDIR)
    if not os.path.isdir(base):
        return []
    return sorted(os.listdir(base))


def save_voice(
    work_dir: str,
    character_id: str,
    name: str,
    *,
    sample_path: str | None = None,
    audio_data: bytes | None = None,
    engine: str = "melo-tts",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not character_id:
        raise DevKitError(400, "请指定角色 ID", code="missing_character_id")
    if not name:
        raise DevKitError(400, "请指定声音名称", code="missing_name")
    from devkit._vendor import iso_now
    now = iso_now()
    meta = _load_meta(work_dir, character_id)
    existing = next((v for v in meta if v.get("name") == name), None)
    if existing:
        voice_id = existing["id"]
    else:
        voice_id = _gen_id()
    sample_dest = ""
    if sample_path and os.path.isfile(sample_path):
        samples_dir = _samples_dir(work_dir, character_id)
        os.makedirs(samples_dir, exist_ok=True)
        ext = os.path.splitext(sample_path)[1].lower()
        if ext not in _SUPPORTED_AUDIO_EXTENSIONS:
            raise DevKitError(400, f"不支持的音频格式: {ext}", code="bad_audio_format")
        sample_dest = os.path.join(samples_dir, f"{voice_id}{ext}")
        shutil.copy2(sample_path, sample_dest)
    elif audio_data:
        samples_dir = _samples_dir(work_dir, character_id)
        os.makedirs(samples_dir, exist_ok=True)
        sample_dest = os.path.join(samples_dir, f"{voice_id}.wav")
        with open(sample_dest, "wb") as f:
            f.write(audio_data)
    record = {
        "id": voice_id,
        "character_id": character_id,
        "name": name,
        "engine": engine,
        "sample_path": sample_dest,
        "params": params or {},
        "created_at": existing.get("created_at", now) if existing else now,
        "updated_at": now,
    }
    if existing:
        for i, v in enumerate(meta):
            if v.get("id") == voice_id:
                meta[i] = record
                break
    else:
        meta.append(record)
    _save_meta(work_dir, character_id, meta)
    return record


def delete_voice(work_dir: str, voice_id: str) -> bool:
    base = os.path.join(work_dir, _VOICES_SUBDIR)
    if not os.path.isdir(base):
        return False
    for char_dir in os.listdir(base):
        meta = _load_meta(work_dir, char_dir)
        before = len(meta)
        meta = [v for v in meta if v.get("id") != voice_id]
        if len(meta) < before:
            for v in _load_meta(work_dir, char_dir):
                if v.get("id") == voice_id and v.get("sample_path"):
                    try:
                        if os.path.isfile(v["sample_path"]):
                            os.remove(v["sample_path"])
                    except OSError:
                        pass
            _save_meta(work_dir, char_dir, meta)
            return True
    return False
