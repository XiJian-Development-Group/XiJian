"""开发者工具的声音克隆 / 声音样本管理器。

让开发者能够管理角色的声音参考样本。
样本可以录音或从音频文件导入。
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from devkit import DevKitError
from devkit.tts_engine import TTSEngine, DiffSingerEngine, get_tts_manager, TTSRequest


_VOICES_SUBDIR = "voices"

_SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}

# 可用的 TTS / 声音克隆引擎（UI 显示名）。
AVAILABLE_ENGINES: tuple[str, ...] = (
    "mlx",
    "gguf",
    "fallback",
    "melo",
    "diffsinger",
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
    engine: str = "melo",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """C2.1 文本生成语音（MeloTTS / MLX / GGUF / Fallback）。

    使用 MeloTTS 作为首选引擎（v2.1 规范要求），自动回退到 MLX、GGUF、Fallback。
    params 可包含：speed, pitch, energy, voice_id, language。
    """
    if not text.strip():
        raise DevKitError(400, "文本内容不能为空", code="empty_text")
    if not name:
        raise DevKitError(400, "声音名称不能为空", code="missing_name")

    tts_mgr = get_tts_manager()
    request = TTSRequest(
        text=text,
        voice_id=params.get("voice_id") if params else None,
        language=params.get("language", "zh") if params else "zh",
        speed=params.get("speed", 1.0) if params else 1.0,
        pitch=params.get("pitch", 1.0) if params else 1.0,
        energy=params.get("energy", 1.0) if params else 1.0,
        params=params,
    )
    result = tts_mgr.synthesize(request, engine=engine)

    if not result.success:
        # 如果首选引擎失败，尝试自动回退
        fallback_result = tts_mgr.synthesize(request)
        if fallback_result.success:
            result = fallback_result
        else:
            raise DevKitError(
                503,
                f"语音合成失败: {result.error or '未知错误'}",
                code="synthesis_failed",
            )

    # 保存为语音记录以便后续复用
    voice_record = save_voice(
        work_dir=work_dir,
        character_id=character_id,
        name=name,
        engine=result.engine,
        params={
            "generated_from_text": True,
            "source_text": text[:200],
            "speed": request.speed,
            "pitch": request.pitch,
            "energy": request.energy,
            "language": request.language,
            "voice_id": request.voice_id,
        },
        audio_data=None,  # 文件已由引擎写入 result.audio_path
    )

    # 将生成的音频文件移动到样本目录
    if result.audio_path and os.path.isfile(result.audio_path):
        samples_dir = _samples_dir(work_dir, character_id)
        os.makedirs(samples_dir, exist_ok=True)
        ext = os.path.splitext(result.audio_path)[1] or ".wav"
        final_path = os.path.join(samples_dir, f"{voice_record['id']}{ext}")
        if result.audio_path != final_path:
            shutil.move(result.audio_path, final_path)
        voice_record["sample_path"] = final_path
        _save_meta(work_dir, character_id, _load_meta(work_dir, character_id))  # 刷新元数据

    return {
        "success": True,
        "voice_id": voice_record["id"],
        "audio_path": voice_record["sample_path"],
        "duration_sec": result.duration_sec,
        "engine": result.engine,
    }


def generate_voice_from_description(
    work_dir: str,
    character_id: str,
    name: str,
    description: str,
    engine: str = "melo",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """C2.1 文本描述生成声音。

    将自然语言描述映射为 TTS 引擎参数，再调用 generate_voice_from_text。
    支持的描述维度：
    - 性别：男声/女声/中性
    - 年龄感：年轻/成熟/老年
    - 音色：温柔/磁性/清亮/深沉/甜美/沙哑
    - 语速：快/正常/慢
    - 音调：高/正常/低
    - 情感：平静/开心/悲伤/严肃/温柔
    - 语言：中文/英文/日文/粤语

    示例：
    - "温柔的年轻女性声音，语速稍慢，音调偏高，带点甜美感"
    - "磁性成熟男声，语速正常，音调偏低，情感深沉"
    """
    if not description.strip():
        raise DevKitError(400, "描述文本不能为空", code="empty_description")
    if not name:
        raise DevKitError(400, "声音名称不能为空", code="missing_name")

    # 解析描述 -> 参数映射
    mapped_params = _parse_voice_description(description)
    if params:
        mapped_params.update(params)  # 显式参数优先

    # 生成一段示例文本用于试听
    sample_text = _get_sample_text(mapped_params.get("language", "zh"))

    return generate_voice_from_text(
        work_dir=work_dir,
        character_id=character_id,
        name=name,
        text=sample_text,
        engine=engine,
        params=mapped_params,
    )


def _parse_voice_description(description: str) -> dict[str, Any]:
    """将自然语言描述解析为 TTS 参数字典。"""
    desc = description.lower()
    params: dict[str, Any] = {}

    # 语言检测
    if any(kw in desc for kw in ["英文", "english", "英语"]):
        params["language"] = "en"
    elif any(kw in desc for kw in ["日文", "日语", "japanese", "日本語"]):
        params["language"] = "jp"
    elif any(kw in desc for kw in ["粤语", "广东话", "cantonese"]):
        params["language"] = "zh"  # MeloTTS 中文模型也可合成粤语
    else:
        params["language"] = "zh"

    # 性别 -> voice_id 偏好
    # 注意：先检查 female/woman，避免 male 匹配到 female 中的 male 子串
    if any(kw in desc for kw in ["女声", "女性", "female", "woman"]):
        params["voice_id"] = "melo_zh_female_0" if params["language"] == "zh" else "melo_en_female_0"
    elif any(kw in desc for kw in ["男声", "男性", "male", "man"]):
        params["voice_id"] = "melo_zh_male_0" if params["language"] == "zh" else "melo_en_male_0"
    # 中性/未指定则使用默认

    # 年龄感 -> pitch 调整
    if any(kw in desc for kw in ["年轻", "稚嫩", "青春", "young"]):
        params["pitch"] = params.get("pitch", 1.0) * 1.15
    elif any(kw in desc for kw in ["成熟", "成年", "mature"]):
        params["pitch"] = params.get("pitch", 1.0) * 0.95
    elif any(kw in desc for kw in ["老年", "苍老", "elder", "old"]):
        params["pitch"] = params.get("pitch", 1.0) * 0.85

    # 音色 -> pitch/energy 微调
    if any(kw in desc for kw in ["温柔", "柔和", "gentle", "soft"]):
        params["pitch"] = params.get("pitch", 1.0) * 1.05
        params["energy"] = params.get("energy", 1.0) * 0.9
    if any(kw in desc for kw in ["磁性", "磁", "magnetic", "rich"]):
        params["pitch"] = params.get("pitch", 1.0) * 0.9
        params["energy"] = params.get("energy", 1.0) * 1.1
    if any(kw in desc for kw in ["清亮", "清脆", "clear", "bright"]):
        params["pitch"] = params.get("pitch", 1.0) * 1.1
        params["energy"] = params.get("energy", 1.0) * 1.05
    if any(kw in desc for kw in ["深沉", "低沉", "deep"]):
        params["pitch"] = params.get("pitch", 1.0) * 0.85
        params["energy"] = params.get("energy", 1.0) * 1.0
    if any(kw in desc for kw in ["甜美", "甜", "sweet"]):
        params["pitch"] = params.get("pitch", 1.0) * 1.12
        params["energy"] = params.get("energy", 1.0) * 0.95
    if any(kw in desc for kw in ["沙哑", "嘶哑", "hoarse", "raspy"]):
        params["energy"] = params.get("energy", 1.0) * 0.8

    # 语速
    if any(kw in desc for kw in ["语速快", "说话快", "快速", "fast"]):
        params["speed"] = params.get("speed", 1.0) * 1.2
    elif any(kw in desc for kw in ["语速慢", "说话慢", "缓慢", "slow"]):
        params["speed"] = params.get("speed", 1.0) * 0.8
    elif any(kw in desc for kw in ["语速正常", "正常语速", "moderate"]):
        params["speed"] = params.get("speed", 1.0)

    # 音调（独立于年龄/音色）
    if any(kw in desc for kw in ["音调高", "音调偏高", "高音", "high pitch"]):
        params["pitch"] = params.get("pitch", 1.0) * 1.15
    elif any(kw in desc for kw in ["音调低", "音调偏低", "低音", "low pitch"]):
        params["pitch"] = params.get("pitch", 1.0) * 0.85

    # 情感 -> energy/pitch 组合
    if any(kw in desc for kw in ["开心", "愉快", "快乐", "happy", "cheerful"]):
        params["energy"] = params.get("energy", 1.0) * 1.15
        params["pitch"] = params.get("pitch", 1.0) * 1.05
        params["speed"] = params.get("speed", 1.0) * 1.05
    elif any(kw in desc for kw in ["悲伤", "难过", "忧郁", "sad", "melancholy"]):
        params["energy"] = params.get("energy", 1.0) * 0.8
        params["pitch"] = params.get("pitch", 1.0) * 0.95
        params["speed"] = params.get("speed", 1.0) * 0.9
    elif any(kw in desc for kw in ["严肃", "严厉", "serious", "stern"]):
        params["energy"] = params.get("energy", 1.0) * 1.05
        params["pitch"] = params.get("pitch", 1.0) * 0.95
        params["speed"] = params.get("speed", 1.0) * 0.95
    elif any(kw in desc for kw in ["温柔", "温和", "gentle", "tender"]):
        params["energy"] = params.get("energy", 1.0) * 0.9
        params["pitch"] = params.get("pitch", 1.0) * 1.05
        params["speed"] = params.get("speed", 1.0) * 0.95

    # 限制参数范围
    params["speed"] = max(0.5, min(2.0, params.get("speed", 1.0)))
    params["pitch"] = max(0.5, min(2.0, params.get("pitch", 1.0)))
    params["energy"] = max(0.3, min(2.0, params.get("energy", 1.0)))

    return params


def _get_sample_text(language: str) -> str:
    """根据语言返回试听用的示例文本。"""
    samples = {
        "zh": "你好，我是你的专属语音助手。今天天气真不错，我们要不要出去走走？",
        "en": "Hello, I am your personal voice assistant. The weather is lovely today, shall we go for a walk?",
        "jp": "こんにちは、私はあなたの専属ボイスアシスタントです。今日はいい天気ですね、お散歩でもどうですか？",
    }
    return samples.get(language, samples["zh"])


def clone_voice_from_file(
    work_dir: str,
    character_id: str,
    name: str,
    source_path: str,
    engine: str = "melo",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """C2.1 声音克隆。

    该功能仍在制作中，暂不开放使用——直接以明确提示告知用户，
    避免用户误以为已生成可用语音。
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

    if engine == "diffsinger":
        # 先做参数校验（输入合法性优先于引擎可用性）
        if not params or (not params.get("midi_path") and not params.get("melody")):
            raise DevKitError(
                400,
                "DiffSinger 需要 'midi_path' (MIDI 文件路径) 或 'melody' (程序化旋律) 参数",
                code="missing_melody",
            )

        ds = _get_diffsinger_engine()
        if not ds.is_available():
            if not ds.ensure_model("zh"):
                raise DevKitError(
                    503,
                    "DiffSinger 模型未下载。请先调用 download_diffsinger_model 下载模型。",
                    code="model_not_ready",
                )

        # 创建声音记录
        voice = save_voice(
            work_dir=work_dir,
            character_id=character_id,
            name=name,
            engine="diffsinger",
            params={"singing": True, **(params or {})},
        )

        # 生成歌声
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
    """将额外字段持久化到现有声音记录上。"""
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


# =============================================================================
# C2.1 DiffSinger 引擎管理钩子（按项目惯例：引擎接口 + 默认 unavailable + set_engine 钩子）
# =============================================================================

# 全局自定义 DiffSinger 引擎（用于测试或替换实现）
_custom_diffsinger_engine: TTSEngine | None = None


def set_diffsinger_engine(engine: TTSEngine | None) -> None:
    """设置自定义 DiffSinger 引擎实例（用于测试或替换实现）。

    按项目惯例提供 set_engine 钩子，真实 DiffSinger 引擎不可用时
    可注入 Mock/替代实现。传入 None 重置为默认实现。
    """
    global _custom_diffsinger_engine
    _custom_diffsinger_engine = engine


def _get_diffsinger_engine() -> TTSEngine:
    """获取 DiffSinger 引擎实例（支持自定义注入）。"""
    if _custom_diffsinger_engine is not None:
        return _custom_diffsinger_engine
    return DiffSingerEngine()


def download_diffsinger_model(language: str = "zh") -> bool:
    """下载 DiffSinger 模型。

    从 Hugging Face (或镜像站) 下载指定语言的 DiffSinger 模型。
    支持的语言：zh (中文), en (英文), jp (日文)
    """
    ds = _get_diffsinger_engine()
    if hasattr(ds, 'ensure_model'):
        return ds.ensure_model(language)
    return False


def get_diffsinger_model_status(language: str = "zh") -> dict[str, Any]:
    """获取 DiffSinger 模型状态。"""
    ds = _get_diffsinger_engine()
    if hasattr(ds, '_get_cache_dir') and hasattr(ds, 'DIFFSINGER_MODELS'):
        cache_dir = ds._get_cache_dir()
        model_repo = ds.DIFFSINGER_MODELS.get(language, ds.DIFFSINGER_MODELS["zh"])
        model_dir = os.path.join(cache_dir, f"diffsinger_{language}")

        return {
            "language": language,
            "model_repo": model_repo,
            "local_path": model_dir,
            "is_downloaded": os.path.isdir(model_dir) and os.listdir(model_dir),
            "is_available": ds.is_available() and getattr(ds, '_language', 'zh') == language,
        }
    return {
        "language": language,
        "model_repo": "unknown",
        "local_path": "",
        "is_downloaded": False,
        "is_available": False,
    }


# =============================================================================
# C2.1 版权确认系统（AC-1：上传声音样本前必须确认版权）
# =============================================================================


class CopyrightStatus(Enum):
    """版权确认状态。"""
    PENDING = "pending"           # 待确认
    CONFIRMED = "confirmed"       # 已确认（用户声明拥有版权或已获授权）
    REJECTED = "rejected"         # 用户拒绝确认
    EXPIRED = "expired"           # 确认过期（需重新确认）
    DISPUTED = "disputed"         # 版权争议中


class CopyrightType(Enum):
    """版权类型。"""
    ORIGINAL = "original"         # 原创内容
    LICENSED = "licensed"         # 已获授权
    PUBLIC_DOMAIN = "public_domain"  # 公共领域
    FAIR_USE = "fair_use"         # 合理使用
    UNKNOWN = "unknown"           # 不确定


@dataclass
class CopyrightRecord:
    """版权确认记录。"""
    id: str
    character_id: str
    voice_id: str
    status: CopyrightStatus
    copyright_type: CopyrightType
    declared_by: str              # 声明者（用户 ID）
    declared_at: str              # ISO 8601 时间戳
    expires_at: str | None        # 过期时间（可选）
    license_info: str | None      # 授权信息（如有）
    evidence_urls: list[str]      # 证据链接（可选）
    audit_trail: list[dict]       # 审计轨迹

    def __post_init__(self) -> None:
        """从 JSON 加载时，将字符串字段还原为枚举。"""
        if isinstance(self.status, str):
            self.status = CopyrightStatus(self.status)
        if isinstance(self.copyright_type, str):
            self.copyright_type = CopyrightType(self.copyright_type)

    def to_dict(self) -> dict:
        """转换为可 JSON 序列化的字典。"""
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, CopyrightStatus) else self.status
        d["copyright_type"] = (
            self.copyright_type.value if isinstance(self.copyright_type, CopyrightType) else self.copyright_type
        )
        return d


_COPYRIGHT_SUBDIR = "copyright"


def _copyright_dir(work_dir: str, character_id: str) -> str:
    return os.path.join(work_dir, _COPYRIGHT_SUBDIR, character_id)


def _copyright_meta_path(work_dir: str, character_id: str) -> str:
    return os.path.join(_copyright_dir(work_dir, character_id), "meta.json")


def _load_copyright_meta(work_dir: str, character_id: str) -> list[dict]:
    fpath = _copyright_meta_path(work_dir, character_id)
    if not os.path.isfile(fpath):
        return []
    try:
        with open(fpath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_copyright_meta(work_dir: str, character_id: str, meta: list[dict]) -> None:
    cdir = _copyright_dir(work_dir, character_id)
    os.makedirs(cdir, exist_ok=True)
    fpath = _copyright_meta_path(work_dir, character_id)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _gen_copyright_id() -> str:
    return f"cr_{secrets.token_hex(8)}"


def create_copyright_confirmation(
    work_dir: str,
    character_id: str,
    voice_id: str,
    copyright_type: str,
    declared_by: str,
    license_info: str | None = None,
    evidence_urls: list[str] | None = None,
    expires_in_days: int | None = None,
) -> dict[str, Any]:
    """创建版权确认记录（AC-1：上传声音样本前必须确认版权）。

    用户必须在上传声音样本前调用此函数完成版权确认。
    状态机流程：PENDING -> CONFIRMED/REJECTED -> (EXPIRED/DISPUTED)
    """
    from devkit._vendor import iso_now

    if not character_id:
        raise DevKitError(400, "请指定角色 ID", code="missing_character_id")
    if not voice_id:
        raise DevKitError(400, "请指定声音 ID", code="missing_voice_id")
    if not declared_by:
        raise DevKitError(400, "请指定声明者", code="missing_declared_by")

    # 验证声音是否存在
    voice = get_voice(work_dir, voice_id)
    if not voice or voice.get("character_id") != character_id:
        raise DevKitError(404, f"声音不存在: {voice_id}", code="voice_not_found")

    try:
        ctype = CopyrightType(copyright_type)
    except ValueError:
        raise DevKitError(
            400,
            f"无效的版权类型: {copyright_type}，可选: {[c.value for c in CopyrightType]}",
            code="bad_copyright_type",
        )

    now = iso_now()
    expires_at = None
    if expires_in_days is not None:
        from datetime import datetime, timedelta
        expires_at = (datetime.fromisoformat(now.replace("Z", "+00:00")) + timedelta(days=expires_in_days)).isoformat().replace("+00:00", "Z")

    record = CopyrightRecord(
        id=_gen_copyright_id(),
        character_id=character_id,
        voice_id=voice_id,
        status=CopyrightStatus.PENDING,
        copyright_type=ctype,
        declared_by=declared_by,
        declared_at=now,
        expires_at=expires_at,
        license_info=license_info,
        evidence_urls=evidence_urls or [],
        audit_trail=[{
            "action": "create",
            "status": CopyrightStatus.PENDING.value,
            "actor": declared_by,
            "timestamp": now,
            "details": f"版权类型: {ctype.value}",
        }],
    )

    meta = _load_copyright_meta(work_dir, character_id)
    meta.append(record.to_dict())
    _save_copyright_meta(work_dir, character_id, meta)

    return record.to_dict()


def confirm_copyright(
    work_dir: str,
    copyright_id: str,
    actor: str,
    confirm: bool = True,
) -> dict[str, Any]:
    """确认或拒绝版权声明。

    状态迁移：PENDING -> CONFIRMED (confirm=True) 或 REJECTED (confirm=False)
    """
    from devkit._vendor import iso_now

    meta = _find_copyright_record(work_dir, copyright_id)
    if not meta:
        raise DevKitError(404, f"版权记录不存在: {copyright_id}", code="not_found")

    record_dict, char_id, index = meta
    record = CopyrightRecord(**record_dict)

    if record.status != CopyrightStatus.PENDING:
        raise DevKitError(
            400,
            f"版权记录当前状态为 {record.status.value}，无法再次确认",
            code="invalid_state_transition",
        )

    new_status = CopyrightStatus.CONFIRMED if confirm else CopyrightStatus.REJECTED
    now = iso_now()

    record.status = new_status
    record.audit_trail.append({
        "action": "confirm" if confirm else "reject",
        "status": new_status.value,
        "actor": actor,
        "timestamp": now,
        "details": "用户确认拥有版权或已获授权" if confirm else "用户拒绝版权确认",
    })

    all_meta = _load_copyright_meta(work_dir, char_id)
    all_meta[index] = record.to_dict()
    _save_copyright_meta(work_dir, char_id, all_meta)

    return record.to_dict()


def get_copyright_status(work_dir: str, voice_id: str) -> dict[str, Any] | None:
    """获取声音的版权确认状态。"""
    voice = get_voice(work_dir, voice_id)
    if not voice:
        return None

    char_id = voice.get("character_id", "")
    meta = _load_copyright_meta(work_dir, char_id)
    for record_dict in meta:
        if record_dict.get("voice_id") == voice_id:
            record = CopyrightRecord(**record_dict)

            # 检查是否过期
            if record.expires_at and record.status == CopyrightStatus.CONFIRMED:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                expires = datetime.fromisoformat(record.expires_at.replace("Z", "+00:00"))
                if now > expires:
                    record.status = CopyrightStatus.EXPIRED
                    record.audit_trail.append({
                        "action": "expire",
                        "status": CopyrightStatus.EXPIRED.value,
                        "actor": "system",
                        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "details": "版权确认已过期，需重新确认",
                    })
                    # 更新存储
                    all_meta = _load_copyright_meta(work_dir, char_id)
                    for i, r in enumerate(all_meta):
                        if r.get("id") == record.id:
                            all_meta[i] = record.to_dict()
                            break
                    _save_copyright_meta(work_dir, char_id, all_meta)

            return record.to_dict()

    return None


def check_copyright_before_upload(work_dir: str, voice_id: str) -> dict[str, Any]:
    """上传声音样本前的版权检查（AC-1 强制门禁）。

    返回：
    - allowed: bool - 是否允许上传
    - reason: str - 原因
    - record: dict|None - 版权记录
    """
    record = get_copyright_status(work_dir, voice_id)

    if not record:
        return {
            "allowed": False,
            "reason": "未找到版权确认记录，请先完成版权确认",
            "record": None,
        }

    status = CopyrightStatus(record["status"])

    if status == CopyrightStatus.CONFIRMED:
        return {
            "allowed": True,
            "reason": "版权已确认",
            "record": record,
        }
    elif status == CopyrightStatus.REJECTED:
        return {
            "allowed": False,
            "reason": "版权确认被拒绝，无法上传",
            "record": record,
        }
    elif status == CopyrightStatus.EXPIRED:
        return {
            "allowed": False,
            "reason": "版权确认已过期，需重新确认",
            "record": record,
        }
    elif status == CopyrightStatus.DISPUTED:
        return {
            "allowed": False,
            "reason": "版权处于争议中，暂无法上传",
            "record": record,
        }
    else:  # PENDING
        return {
            "allowed": False,
            "reason": "版权确认待处理中",
            "record": record,
        }


def list_copyright_records(work_dir: str, character_id: str) -> list[dict]:
    """列出角色的所有版权确认记录。"""
    return _load_copyright_meta(work_dir, character_id)


def _find_copyright_record(work_dir: str, copyright_id: str) -> tuple[dict, str, int] | None:
    """查找版权记录，返回 (record_dict, character_id, index)。"""
    base = os.path.join(work_dir, _COPYRIGHT_SUBDIR)
    if not os.path.isdir(base):
        return None
    for char_dir in os.listdir(base):
        meta = _load_copyright_meta(work_dir, char_dir)
        for i, record in enumerate(meta):
            if record.get("id") == copyright_id:
                return record, char_dir, i
    return None


def dispute_copyright(
    work_dir: str,
    copyright_id: str,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    """发起版权争议。"""
    from devkit._vendor import iso_now

    meta = _find_copyright_record(work_dir, copyright_id)
    if not meta:
        raise DevKitError(404, f"版权记录不存在: {copyright_id}", code="not_found")

    record_dict, char_id, index = meta
    record = CopyrightRecord(**record_dict)

    if record.status not in (CopyrightStatus.CONFIRMED, CopyrightStatus.PENDING):
        raise DevKitError(
            400,
            f"版权记录当前状态为 {record.status.value}，无法发起争议",
            code="invalid_state_transition",
        )

    now = iso_now()
    record.status = CopyrightStatus.DISPUTED
    record.audit_trail.append({
        "action": "dispute",
        "status": CopyrightStatus.DISPUTED.value,
        "actor": actor,
        "timestamp": now,
        "details": reason,
    })

    all_meta = _load_copyright_meta(work_dir, char_id)
    all_meta[index] = record.to_dict()
    _save_copyright_meta(work_dir, char_id, all_meta)

    return record.to_dict()


def resolve_copyright_dispute(
    work_dir: str,
    copyright_id: str,
    actor: str,
    resolved_status: str,
    reason: str,
) -> dict[str, Any]:
    """解决版权争议。

    resolved_status: "confirmed" | "rejected"
    """
    from devkit._vendor import iso_now

    meta = _find_copyright_record(work_dir, copyright_id)
    if not meta:
        raise DevKitError(404, f"版权记录不存在: {copyright_id}", code="not_found")

    record_dict, char_id, index = meta
    record = CopyrightRecord(**record_dict)

    if record.status != CopyrightStatus.DISPUTED:
        raise DevKitError(
            400,
            f"版权记录当前状态为 {record.status.value}，非争议状态",
            code="invalid_state_transition",
        )

    try:
        new_status = CopyrightStatus(resolved_status)
    except ValueError:
        raise DevKitError(400, f"无效的解决状态: {resolved_status}", code="bad_status")

    if new_status not in (CopyrightStatus.CONFIRMED, CopyrightStatus.REJECTED):
        raise DevKitError(400, "解决状态必须是 confirmed 或 rejected", code="bad_status")

    now = iso_now()
    record.status = new_status
    record.audit_trail.append({
        "action": "resolve_dispute",
        "status": new_status.value,
        "actor": actor,
        "timestamp": now,
        "details": reason,
    })

    all_meta = _load_copyright_meta(work_dir, char_id)
    all_meta[index] = record.to_dict()
    _save_copyright_meta(work_dir, char_id, all_meta)

    return record.to_dict()
