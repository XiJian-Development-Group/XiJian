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
from devkit.tts_engine import get_tts_manager, TTSRequest


_VOICES_SUBDIR = "voices"

_SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}

# Available TTS / voice-clone engines (display names for UI).
AVAILABLE_ENGINES: tuple[str, ...] = (
    "mlx",
    "gguf",
    "fallback",
)


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


def list_engines() -> list[str]:
    return list(AVAILABLE_ENGINES)


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
    engine: str = "fallback",
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


def export_voice_for_submit(work_dir: str, voice_id: str) -> dict[str, Any]:
    entry = get_voice(work_dir, voice_id)
    if not entry:
        raise DevKitError(404, f"声音不存在: {voice_id}", code="not_found")

    files: list[dict[str, Any]] = []
    sp = entry.get("sample_path", "")
    if sp and os.path.isfile(sp):
        files.append({
            "path": sp,
            "arcname": f"voices/{entry['character_id']}/{voice_id}{os.path.splitext(sp)[1]}",
            "size": os.path.getsize(sp),
        })

    char_id = entry.get("character_id", "")
    meta_path = _meta_path(work_dir, char_id)
    if os.path.isfile(meta_path):
        files.append({
            "path": meta_path,
            "arcname": f"voices/{char_id}/meta.json",
            "size": os.path.getsize(meta_path),
        })

    return {
        "target_kind": "character",
        "files": files,
        "payload": {
            "notes": f"声音样本: {entry.get('name', '')} ({entry.get('engine', '')})",
            "files": [f["path"] for f in files],
        },
    }


def generate_voice_from_text(
    work_dir: str,
    character_id: str,
    name: str,
    text: str,
    engine: str = "fallback",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """C2.1 文本生成语音（MeloTTS）。

    该功能仍在制作中，暂不开放使用——直接以明确提示告知用户，而非产出
    占位音频，避免用户误以为已生成可用语音。
    """
    if not text.strip():
        raise DevKitError(400, "文本内容不能为空", code="empty_text")
    if not name:
        raise DevKitError(400, "声音名称不能为空", code="missing_name")
    raise DevKitError(
        501,
        "语音合成（MeloTTS）功能仍在制作中，暂不开放使用。",
        code="feature_not_available",
    )


def clone_voice_from_file(
    work_dir: str,
    character_id: str,
    name: str,
    source_path: str,
    engine: str = "gguf",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """C2.1 声音克隆。

    该功能仍在制作中，暂不开放使用——明确提示用户，而非假装已克隆。
    若需保存自己的参考样本，请使用「选择文件 / 录制样本」（save_voice）。
    """
    if not source_path or not os.path.isfile(source_path):
        raise DevKitError(400, f"音频文件不存在: {source_path}", code="file_not_found")
    raise DevKitError(
        501,
        "声音克隆功能仍在制作中，暂不开放使用。",
        code="feature_not_available",
    )


def generate_singing(
    work_dir: str,
    character_id: str,
    name: str,
    text: str,
    engine: str = "fallback",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """C2.1 歌声合成（DiffSinger）。

    该功能仍在制作中，暂不开放使用——明确提示用户，而非产出占位音频。
    """
    if not text.strip():
        raise DevKitError(400, "歌词文本不能为空", code="empty_text")
    if not name:
        raise DevKitError(400, "声音名称不能为空", code="missing_name")
    raise DevKitError(
        501,
        "歌声合成（DiffSinger）功能仍在制作中，暂不开放使用。",
        code="feature_not_available",
    )


def _patch_voice_record(
    work_dir: str, character_id: str, voice_id: str, patch: dict[str, Any]
) -> None:
    """Persist extra fields onto an existing voice record."""
    meta = _load_meta(work_dir, character_id)
    for i, v in enumerate(meta):
        if v.get("id") == voice_id:
            meta[i].update(patch)
            _save_meta(work_dir, character_id, meta)
            return


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
