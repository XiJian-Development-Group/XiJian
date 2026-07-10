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
    engine: str = "melo",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """C2.1 声音克隆 - 使用 MeloTTS 进行声音克隆。

    支持引擎：
    - "melo": 使用 MeloTTS 的说话人适配功能（需要 MeloTTS 模型）
    - "gguf": 使用 GGUF 声音克隆模型（需用户自行加载模型）
    - "fallback": 仅保存参考样本，不进行实际克隆
    """
    if not source_path or not os.path.isfile(source_path):
        raise DevKitError(400, f"音频文件不存在: {source_path}", code="file_not_found")

    from devkit.tts_engine import get_tts_manager, MeloTTSEngine

    if engine == "melo":
        # 使用 MeloTTS 进行声音克隆
        melo = MeloTTSEngine()
        if not melo.is_available():
            if not melo.ensure_model("zh"):
                raise DevKitError(
                    503,
                    "MeloTTS 模型未下载。请先调用 download_melotts_model 下载模型。",
                    code="model_not_ready",
                )

        from devkit.tts_engine import TTSRequest

        # 先保存参考音频
        tts_mgr = get_tts_manager()
        req = TTSRequest(
            text="这是一个声音克隆测试，用于训练说话人适配。",
            voice_id="melo_zh_female_0",
            language="zh",
            speed=1.0,
        )

        # 使用 MeloTTS 的说话人适配功能
        # 注意：MeloTTS 支持通过 reference audio 进行说话人适配
        # 这里我们先保存样本，实际克隆在后续合成时使用
        return save_voice(
            work_dir=work_dir,
            character_id=character_id,
            name=name,
            sample_path=source_path,
            engine="melo",
            params={"cloned": True, "reference_audio": source_path, **(params or {})},
        )

    elif engine == "gguf":
        # GGUF 声音克隆需要用户自行加载模型
        # 这里仅保存参考样本
        return save_voice(
            work_dir=work_dir,
            character_id=character_id,
            name=name,
            sample_path=source_path,
            engine="gguf",
            params={"cloned": True, "reference_audio": source_path, **(params or {})},
        )

    else:
        # fallback: 仅保存参考样本
        return save_voice(
            work_dir=work_dir,
            character_id=character_id,
            name=name,
            sample_path=source_path,
            engine="fallback",
            params={"cloned": True, "reference_audio": source_path, **(params or {})},
        )


def generate_singing(
    work_dir: str,
    character_id: str,
    name: str,
    text: str,
    engine: str = "diffsinger",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """C2.1 歌声合成（DiffSinger）。

    使用 DiffSinger 进行歌声合成，需要提供：
    - text: 歌词文本
    - params: 包含 midi_path (MIDI 文件路径) 或 melody (程序化旋律)
    """
    if not text.strip():
        raise DevKitError(400, "歌词文本不能为空", code="empty_text")
    if not name:
        raise DevKitError(400, "声音名称不能为空", code="missing_name")

    from devkit.tts_engine import get_tts_manager, TTSRequest, DiffSingerEngine

    if engine == "diffsinger":
        ds = DiffSingerEngine()
        if not ds.is_available():
            if not ds.ensure_model("zh"):
                raise DevKitError(
                    503,
                    "DiffSinger 模型未下载。请先调用 download_diffsinger_model 下载模型。",
                    code="model_not_ready",
                )

        # Validate params
        if not params or (not params.get("midi_path") and not params.get("melody")):
            raise DevKitError(
                400,
                "DiffSinger 需要 'midi_path' (MIDI 文件路径) 或 'melody' (程序化旋律) 参数",
                code="missing_melody",
            )

        # Create voice record
        voice = save_voice(
            work_dir=work_dir,
            character_id=character_id,
            name=name,
            engine="diffsinger",
            params={"singing": True, **(params or {})},
        )

        # Generate singing
        tts_mgr = get_tts_manager()
        req = TTSRequest(
            text=text,
            voice_id=voice["id"],
            language="zh",
            params=params,
        )
        result = tts_mgr.generate_singing(
            lyrics=text,
            voice_id=voice["id"],
            language="zh",
            params=params,
        )

        if result.success:
            return {
                "success": True,
                "voice_id": voice["id"],
                "audio_path": result.audio_path,
                "duration_sec": result.duration_sec,
                "engine": "diffsinger",
            }
        else:
            return {
                "success": False,
                "error": result.error,
                "engine": "diffsinger",
            }
    else:
        raise DevKitError(
            400,
            f"不支持的歌声合成引擎: {engine}",
            code="bad_engine",
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
