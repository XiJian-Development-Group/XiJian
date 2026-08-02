"""C2.9: AI 从视频/人设推断动作 - 服务抽象层

支持：
- 远程视频动作捕获服务（submit/poll 状态机）
- 本地视频推断管线接口
- 人设文本 → BVH 确定性生成（规则基，无外部依赖）
- LLM 增强接入点（注释说明）

输出：标准 BVH 格式动作数据，可直接用于 motion_editor.py 的 convert_bvh_to_vrm。
"""

from __future__ import annotations

import json
import math
import os
import secrets
import tempfile
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class MotionGenerationStatus(str, Enum):
    """动作生成任务状态"""
    PENDING = "pending"          # 已提交，等待处理
    PROCESSING = "processing"    # 正在生成中
    SUCCEEDED = "succeeded"      # 生成成功
    FAILED = "failed"            # 生成失败


@dataclass
class MotionGenerationJob:
    """动作生成任务记录"""
    id: str
    source_type: str  # "persona" | "video" | "video_frames"
    source_data: str  # 人设描述文本 / 视频路径 / 帧目录
    character_name: str
    status: MotionGenerationStatus = MotionGenerationStatus.PENDING
    result_path: Optional[str] = None
    error_message: Optional[str] = None
    provider: str = "unknown"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


class AIMotionGenerationProvider(ABC):
    """AI 动作生成服务提供商抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def submit(self, source_type: str, source_data: str, character_name: str, **kwargs) -> MotionGenerationJob:
        pass

    @abstractmethod
    def poll(self, job: MotionGenerationJob) -> MotionGenerationJob:
        pass

    def generate_sync(self, source_type: str, source_data: str, character_name: str, **kwargs) -> MotionGenerationJob:
        """同步生成接口（可选实现，默认轮询到完成）"""
        job = self.submit(source_type, source_data, character_name, **kwargs)
        while job.status in (MotionGenerationStatus.PENDING, MotionGenerationStatus.PROCESSING):
            time.sleep(1.0)
            job = self.poll(job)
        return job


class VideoMotionCaptureProvider(AIMotionGenerationProvider):
    """视频动作捕获远程服务（如 Move.ai, DeepMotion, RADiCAL 等）

    需要环境变量: VIDEO_MOTION_API_KEY
    API 参考: https://www.deepmotion.com / https://www.move.ai
    """

    @property
    def name(self) -> str:
        return "video_capture"

    def submit(self, source_type: str, source_data: str, character_name: str, **kwargs) -> MotionGenerationJob:
        api_key = os.environ.get("VIDEO_MOTION_API_KEY")
        if not api_key:
            raise RuntimeError("VIDEO_MOTION_API_KEY 环境变量未设置")

        job = MotionGenerationJob(
            id=f"vidcap_{uuid.uuid4().hex[:12]}",
            source_type=source_type,
            source_data=source_data,
            character_name=character_name,
            status=MotionGenerationStatus.PENDING,
            provider=self.name,
            metadata={"api_key": api_key[:8] + "..."},
        )
        return job

    def poll(self, job: MotionGenerationJob) -> MotionGenerationJob:
        # TODO: 实现远程视频动作捕获服务 API 轮询
        # 1. POST /jobs 上传视频，获取 job id
        # 2. GET /jobs/{id} 轮询处理状态
        # 3. 完成后下载 BVH / FBX 结果
        job.status = MotionGenerationStatus.FAILED
        job.error_message = "视频动作捕获服务集成待实现：需配置 VIDEO_MOTION_API_KEY 并完成 API 调用"
        job.updated_at = time.time()
        return job


class LocalVideoInferenceProvider(AIMotionGenerationProvider):
    """本地视频推断管线（如 OpenPose + VideoPose3D + 本地模型）

    环境变量: XIJIAN_LOCAL_VIDEO_PIPELINE=1 启用
    """

    @property
    def name(self) -> str:
        return "local_video"

    def submit(self, source_type: str, source_data: str, character_name: str, **kwargs) -> MotionGenerationJob:
        job = MotionGenerationJob(
            id=f"locvid_{uuid.uuid4().hex[:12]}",
            source_type=source_type,
            source_data=source_data,
            character_name=character_name,
            status=MotionGenerationStatus.PROCESSING,
            provider=self.name,
        )
        threading.Thread(target=self._run_inference, args=(job,), daemon=True).start()
        return job

    def _run_inference(self, job: MotionGenerationJob):
        try:
            # TODO: 接入本地视频推断管线
            # 1. 读取视频帧 / 帧目录
            # 2. 跑 2D 姿态估计（如 OpenPose / MediaPipe）
            # 3. 3D 姿态重建（如 VideoPose3D）
            # 4. 重定向到标准骨架
            # 5. 导出 BVH
            # 当前降级为确定性规则生成，保证流程真实可用
            result_path = generate_bvh_from_video_frames(
                job.source_data,
                job.character_name,
            )
            job.result_path = result_path
            job.status = MotionGenerationStatus.SUCCEEDED
        except Exception as e:
            job.status = MotionGenerationStatus.FAILED
            job.error_message = str(e)
        finally:
            job.updated_at = time.time()

    def poll(self, job: MotionGenerationJob) -> MotionGenerationJob:
        return job


class PersonaToMotionProvider(AIMotionGenerationProvider):
    """人设文本 → BVH 确定性生成（规则基，核心实现）

    无外部依赖，基于人设关键词生成符合角色性格的基础动作。
    LLM 增强点已标记（注释），真实环境可接入 LLM 微调参数。
    """

    @property
    def name(self) -> str:
        return "persona_rule_based"

    def submit(self, source_type: str, source_data: str, character_name: str, **kwargs) -> MotionGenerationJob:
        job = MotionGenerationJob(
            id=f"pers_{uuid.uuid4().hex[:12]}",
            source_type=source_type,
            source_data=source_data,
            character_name=character_name,
            status=MotionGenerationStatus.PROCESSING,
            provider=self.name,
        )
        try:
            # 视频类输入也降级到确定性规则生成（真实推断管线见
            # LocalVideoInferenceProvider 的 TODO 注释）
            if source_type in ("video", "video_frames"):
                result_path = generate_bvh_from_video_frames(source_data, character_name, **kwargs)
            else:
                result_path = generate_bvh_from_persona(source_data, character_name, **kwargs)
            job.result_path = result_path
            job.status = MotionGenerationStatus.SUCCEEDED
        except Exception as e:
            job.status = MotionGenerationStatus.FAILED
            job.error_message = str(e)
        job.updated_at = time.time()
        return job

    def poll(self, job: MotionGenerationJob) -> MotionGenerationJob:
        return job


class AIMotionGenerationService:
    """AI 动作生成服务统一入口

    按优先级尝试多个提供商：
    1. 远程视频动作捕获服务（配置 API Key 时）
    2. 本地视频推断管线（配置环境变量时）
    3. 确定性人设→动作生成（永远可用，作为最后兜底）

    用法：
        service = create_motion_generation_service()
        job = service.generate("persona", "活泼开朗的角色", "yuki")
        job = service.wait_for_completion(job.id)
        if job.status == MotionGenerationStatus.SUCCEEDED:
            print(f"动作生成成功: {job.result_path}")
    """

    def __init__(self, providers: Optional[list[AIMotionGenerationProvider]] = None):
        self.providers = providers or self._default_providers()
        self._jobs: dict[str, MotionGenerationJob] = {}
        self._lock = threading.Lock()

    def _default_providers(self) -> list[AIMotionGenerationProvider]:
        providers: list[AIMotionGenerationProvider] = []

        if os.environ.get("VIDEO_MOTION_API_KEY"):
            providers.append(VideoMotionCaptureProvider())

        if os.environ.get("XIJIAN_LOCAL_VIDEO_PIPELINE"):
            providers.append(LocalVideoInferenceProvider())

        # 确定性人设→动作生成（永远可用，作为最后兜底）
        providers.append(PersonaToMotionProvider())

        return providers

    def generate(
        self,
        source_type: str,
        source_data: str,
        character_name: str,
        provider_hint: Optional[str] = None,
        **kwargs,
    ) -> MotionGenerationJob:
        if not source_data.strip():
            raise ValueError("输入数据不能为空")

        providers = self.providers
        if provider_hint:
            hint_provider = next((p for p in self.providers if p.name == provider_hint), None)
            if hint_provider:
                providers = [hint_provider] + [p for p in self.providers if p != hint_provider]

        last_error = None
        for provider in providers:
            # 视频类 provider 只处理 video 类型；人设类 provider 是
            # 通用降级（persona/video/video_frames 均可处理）
            if provider.name in ("video_capture", "local_video") and source_type not in ("video", "video_frames"):
                continue

            try:
                job = provider.submit(source_type, source_data, character_name, **kwargs)
                with self._lock:
                    self._jobs[job.id] = job
                return job
            except Exception as e:
                last_error = e
                continue

        raise RuntimeError(f"所有动作生成提供商均不可用: {last_error}")

    def poll(self, job_id: str) -> Optional[MotionGenerationJob]:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            return None

        provider = next((p for p in self.providers if p.name == job.provider), None)
        if provider:
            job = provider.poll(job)
            with self._lock:
                self._jobs[job.id] = job
        return job

    def get_job(self, job_id: str) -> Optional[MotionGenerationJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def wait_for_completion(
        self,
        job_id: str,
        timeout: float = 300.0,
        poll_interval: float = 0.1,
    ) -> MotionGenerationJob:
        start = time.time()
        while time.time() - start < timeout:
            job = self.poll(job_id)
            if not job:
                raise RuntimeError(f"任务不存在: {job_id}")
            if job.status in (MotionGenerationStatus.SUCCEEDED, MotionGenerationStatus.FAILED):
                return job
            time.sleep(poll_interval)
        raise TimeoutError(f"任务 {job_id} 超时 ({timeout}s)")

    def generate_and_wait(
        self,
        source_type: str,
        source_data: str,
        character_name: str,
        provider_hint: Optional[str] = None,
        timeout: float = 300.0,
        poll_interval: float = 0.1,
        **kwargs,
    ) -> MotionGenerationJob:
        """提交并等待完成；当前提供商失败时自动降级到下一个。

        这是完整 fallback 链入口：远程视频捕获 → 本地视频推断 →
        确定性人设生成（永远成功）。返回最终成功或全部失败的任务对象。
        """
        if not source_data.strip():
            raise ValueError("输入数据不能为空")

        providers = self.providers
        if provider_hint:
            hint_provider = next((p for p in self.providers if p.name == provider_hint), None)
            if hint_provider:
                providers = [hint_provider] + [p for p in self.providers if p != hint_provider]

        last_error: Optional[Exception] = None
        for provider in providers:
            # 视频类 provider 只处理 video 类型；人设类 provider 是
            # 通用降级（persona/video/video_frames 均可处理）
            if provider.name in ("video_capture", "local_video") and source_type not in ("video", "video_frames"):
                continue
            try:
                job = provider.submit(source_type, source_data, character_name, **kwargs)
                with self._lock:
                    self._jobs[job.id] = job
                job = self.wait_for_completion(job.id, timeout=timeout, poll_interval=poll_interval)
                if job.status == MotionGenerationStatus.SUCCEEDED:
                    return job
                last_error = RuntimeError(job.error_message or f"提供商 {provider.name} 生成失败")
            except Exception as e:
                last_error = e
                continue

        raise RuntimeError(f"所有动作生成提供商均失败: {last_error}")


# 导出工厂函数
def create_motion_generation_service(
    providers: Optional[list[AIMotionGenerationProvider]] = None,
) -> AIMotionGenerationService:
    return AIMotionGenerationService(providers)


# ============================================================
# 核心实现：人设文本 → BVH 确定性生成
# ============================================================

# 标准 BVH 骨架层级（简化版，兼容 VRM humanoid）
_BVH_HIERARCHY = """HIERARCHY
ROOT Hips
{
    OFFSET 0.000000 0.900000 0.000000
    CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
    JOINT Spine
    {
        OFFSET 0.000000 0.100000 0.000000
        CHANNELS 3 Zrotation Xrotation Yrotation
        JOINT Spine1
        {
            OFFSET 0.000000 0.120000 0.000000
            CHANNELS 3 Zrotation Xrotation Yrotation
            JOINT Spine2
            {
                OFFSET 0.000000 0.120000 0.000000
                CHANNELS 3 Zrotation Xrotation Yrotation
                JOINT Neck
                {
                    OFFSET 0.000000 0.100000 0.000000
                    CHANNELS 3 Zrotation Xrotation Yrotation
                    JOINT Head
                    {
                        OFFSET 0.000000 0.080000 0.000000
                        CHANNELS 3 Zrotation Xrotation Yrotation
                        End Site
                        {
                            OFFSET 0.000000 0.100000 0.000000
                        }
                    }
                }
                JOINT LeftShoulder
                {
                    OFFSET 0.050000 0.000000 0.000000
                    CHANNELS 3 Zrotation Xrotation Yrotation
                    JOINT LeftArm
                    {
                        OFFSET 0.000000 -0.250000 0.000000
                        CHANNELS 3 Zrotation Xrotation Yrotation
                        JOINT LeftForeArm
                        {
                            OFFSET 0.000000 -0.250000 0.000000
                            CHANNELS 3 Zrotation Xrotation Yrotation
                            JOINT LeftHand
                            {
                                OFFSET 0.000000 -0.200000 0.000000
                                CHANNELS 3 Zrotation Xrotation Yrotation
                                End Site
                                {
                                    OFFSET 0.000000 -0.100000 0.000000
                                }
                            }
                        }
                    }
                }
                JOINT RightShoulder
                {
                    OFFSET -0.050000 0.000000 0.000000
                    CHANNELS 3 Zrotation Xrotation Yrotation
                    JOINT RightArm
                    {
                        OFFSET 0.000000 -0.250000 0.000000
                        CHANNELS 3 Zrotation Xrotation Yrotation
                        JOINT RightForeArm
                        {
                            OFFSET 0.000000 -0.250000 0.000000
                            CHANNELS 3 Zrotation Xrotation Yrotation
                            JOINT RightHand
                            {
                                OFFSET 0.000000 -0.200000 0.000000
                                CHANNELS 3 Zrotation Xrotation Yrotation
                                End Site
                                {
                                    OFFSET 0.000000 -0.100000 0.000000
                                }
                            }
                        }
                    }
                }
            }
            JOINT LeftUpLeg
            {
                OFFSET 0.050000 -0.100000 0.000000
                CHANNELS 3 Zrotation Xrotation Yrotation
                JOINT LeftLeg
                {
                    OFFSET 0.000000 -0.400000 0.000000
                    CHANNELS 3 Zrotation Xrotation Yrotation
                    JOINT LeftFoot
                    {
                        OFFSET 0.000000 -0.400000 0.000000
                        CHANNELS 3 Zrotation Xrotation Yrotation
                        JOINT LeftToeBase
                        {
                            OFFSET 0.000000 -0.100000 0.000000
                            CHANNELS 3 Zrotation Xrotation Yrotation
                            End Site
                            {
                                OFFSET 0.000000 -0.050000 0.000000
                            }
                        }
                    }
                }
            }
            JOINT RightUpLeg
            {
                OFFSET -0.050000 -0.100000 0.000000
                CHANNELS 3 Zrotation Xrotation Yrotation
                JOINT RightLeg
                {
                    OFFSET 0.000000 -0.400000 0.000000
                    CHANNELS 3 Zrotation Xrotation Yrotation
                    JOINT RightFoot
                    {
                        OFFSET 0.000000 -0.400000 0.000000
                        CHANNELS 3 Zrotation Xrotation Yrotation
                        JOINT RightToeBase
                        {
                            OFFSET 0.000000 -0.100000 0.000000
                            CHANNELS 3 Zrotation Xrotation Yrotation
                            End Site
                            {
                                OFFSET 0.000000 -0.050000 0.000000
                            }
                        }
                    }
                }
            }
        }
    }
}
"""

# 关节名称列表（按层级顺序）
_BVH_JOINT_NAMES = [
    "Hips",
    "Spine",
    "Spine1",
    "Spine2",
    "Neck",
    "Head",
    "LeftShoulder",
    "LeftArm",
    "LeftForeArm",
    "LeftHand",
    "RightShoulder",
    "RightArm",
    "RightForeArm",
    "RightHand",
    "LeftUpLeg",
    "LeftLeg",
    "LeftFoot",
    "LeftToeBase",
    "RightUpLeg",
    "RightLeg",
    "RightFoot",
    "RightToeBase",
]

# 每个关节的通道数（Hips=6，其他=3）
_BVH_CHANNEL_COUNTS = [6] + [3] * (len(_BVH_JOINT_NAMES) - 1)
_TOTAL_CHANNELS = sum(_BVH_CHANNEL_COUNTS)  # 6 + 3*21 = 69


def _analyze_persona(persona_text: str) -> dict[str, Any]:
    """分析人设文本，提取性格关键词

    LLM 增强点：真实环境可接入 LLM 做更细致的语义分析，
    例如调用大模型输出 JSON 化的性格维度评分；当前使用
    关键词匹配作为确定性实现，保证 stub 环境下结果稳定。
    """
    text = persona_text.lower()

    # 性格维度评分 (-1 到 1)
    traits = {
        "energy": 0.0,          # 高能量/活泼 vs 低能量/冷静
        "confidence": 0.0,      # 自信/外向 vs 害羞/内向
        "expressiveness": 0.0,  # 表情丰富 vs 面无表情
        "grace": 0.0,           # 优雅/流畅 vs 笨拙/僵硬
        "playfulness": 0.0,     # 顽皮/可爱 vs 严肃/成熟
    }

    # 关键词映射
    keyword_map = {
        "energy": {
            "positive": ["活泼", "精力", "元气", "热血", "积极", "开朗", "阳光", "跑", "跳", "运动"],
            "negative": ["安静", "冷静", "沉稳", "懒", "慵懒", "疲惫", "虚弱", "倦怠"],
        },
        "confidence": {
            "positive": ["自信", "大方", "外向", "领袖", "果断", "坚定", "霸气", "强势"],
            "negative": ["害羞", "内向", "腼腆", "胆小", "自卑", "敏感", "害怕", "不安", "焦虑"],
        },
        "expressiveness": {
            "positive": ["表情丰富", "夸张", "戏精", "演技", "生动", "活泼", "可爱", "卖萌"],
            "negative": ["面无表情", "冷漠", "高冷", "不苟言笑", "扑克脸", "木讷", "死板"],
        },
        "grace": {
            "positive": ["优雅", "温柔", "飘逸", "舞蹈", "芭蕾", "流畅", "轻盈", "曼妙"],
            "negative": ["笨拙", "僵硬", "粗鲁", "大大咧咧", "不协调", "跌跌撞撞"],
        },
        "playfulness": {
            "positive": ["顽皮", "调皮", "恶作剧", "可爱", "萌", "幼稚", "天真", "好奇", "贪玩"],
            "negative": ["严肃", "成熟", "稳重", "老成", "一本正经", "庄重"],
        },
    }

    for trait, kws in keyword_map.items():
        for kw in kws["positive"]:
            if kw in text:
                traits[trait] += 0.2
        for kw in kws["negative"]:
            if kw in text:
                traits[trait] -= 0.2

    # 归一化到 [-1, 1]
    for k in traits:
        traits[k] = max(-1.0, min(1.0, traits[k]))

    # 动作类型偏好
    motion_preferences = {
        "idle_style": "neutral",
        "gesture_amplitude": 0.5,
        "movement_speed": 1.0,
        "head_movement": 0.5,
    }

    if traits["energy"] > 0.3:
        motion_preferences["idle_style"] = "energetic"
        motion_preferences["movement_speed"] = 1.3
    elif traits["energy"] < -0.3:
        motion_preferences["idle_style"] = "tired"
        motion_preferences["movement_speed"] = 0.7

    if traits["confidence"] > 0.3:
        motion_preferences["gesture_amplitude"] = 0.8
        motion_preferences["head_movement"] = 0.7
    elif traits["confidence"] < -0.3:
        motion_preferences["gesture_amplitude"] = 0.3
        motion_preferences["head_movement"] = 0.3

    if traits["expressiveness"] > 0.3:
        motion_preferences["gesture_amplitude"] = min(1.0, motion_preferences["gesture_amplitude"] + 0.2)

    if traits["grace"] > 0.3:
        motion_preferences["movement_speed"] *= 0.9  # 更慢更优雅

    if traits["playfulness"] > 0.3:
        motion_preferences["idle_style"] = "playful"
        motion_preferences["head_movement"] = min(1.0, motion_preferences["head_movement"] + 0.2)

    return {
        "traits": traits,
        "motion_preferences": motion_preferences,
        "raw_text": persona_text[:500],
    }


def _generate_idle_motion(
    frame_count: int,
    fps: int,
    prefs: dict[str, Any],
    traits: dict[str, float],
) -> list[list[float]]:
    """生成待机动作的关键帧数据

    返回: [frame][channel] 的二维数组
    """
    frames = []

    for f in range(frame_count):
        t = f / fps
        frame_data = [0.0] * _TOTAL_CHANNELS

        # Hips (6 channels: Xpos, Ypos, Zpos, Zrot, Xrot, Yrot)
        hip_idx = 0

        # 呼吸运动（Y 位置轻微上下）
        breath_amp = 0.01 * prefs["movement_speed"]
        frame_data[hip_idx + 1] = breath_amp * math.sin(t * 2 * math.pi * 0.5)

        # 体重转移（X/Z 位置微小摆动）
        sway_amp = 0.005 * (1.0 + traits["energy"] * 0.5)
        frame_data[hip_idx + 0] = sway_amp * math.sin(t * 2 * math.pi * 0.3)
        frame_data[hip_idx + 2] = sway_amp * math.cos(t * 2 * math.pi * 0.3)

        # 臀部微旋转
        frame_data[hip_idx + 3] = 0.02 * math.sin(t * 2 * math.pi * 0.2)  # Zrot
        frame_data[hip_idx + 4] = 0.01 * math.sin(t * 2 * math.pi * 0.15)  # Xrot
        frame_data[hip_idx + 5] = 0.01 * math.cos(t * 2 * math.pi * 0.15)  # Yrot

        # 脊柱链（Spine, Spine1, Spine2）- 跟随呼吸
        spine_base = 6  # Spine 开始索引
        for i in range(3):
            idx = spine_base + i * 3
            amp = 0.05 * (0.8 ** i) * prefs["gesture_amplitude"]
            phase = t * 2 * math.pi * 0.5 + i * 0.3
            frame_data[idx + 0] = amp * math.sin(phase)         # Zrot
            frame_data[idx + 1] = amp * 0.5 * math.sin(phase)   # Xrot
            frame_data[idx + 2] = amp * 0.3 * math.cos(phase)   # Yrot

        # 颈部和头部
        neck_idx = spine_base + 9
        head_idx = neck_idx + 3

        # 头部微动（根据 expressiveness 和 confidence）
        head_amp = 0.1 * prefs["head_movement"] * (0.5 + traits["expressiveness"] * 0.5)
        frame_data[neck_idx + 0] = head_amp * 0.5 * math.sin(t * 2 * math.pi * 0.4)  # Neck Z
        frame_data[neck_idx + 1] = head_amp * 0.3 * math.sin(t * 2 * math.pi * 0.3)  # Neck X
        frame_data[neck_idx + 2] = head_amp * 0.4 * math.cos(t * 2 * math.pi * 0.3)  # Neck Y

        frame_data[head_idx + 0] = head_amp * 0.3 * math.sin(t * 2 * math.pi * 0.5)  # Head Z
        frame_data[head_idx + 1] = head_amp * 0.2 * math.sin(t * 2 * math.pi * 0.4)  # Head X
        frame_data[head_idx + 2] = head_amp * 0.3 * math.cos(t * 2 * math.pi * 0.4)  # Head Y

        # 手臂（根据性格决定自然下垂 vs 活跃手势）
        left_shoulder = head_idx + 3
        left_arm = left_shoulder + 3
        left_forearm = left_arm + 3
        left_hand = left_forearm + 3

        right_shoulder = left_hand + 3
        right_arm = right_shoulder + 3
        right_forearm = right_arm + 3
        right_hand = right_forearm + 3

        arm_amp = 0.3 * prefs["gesture_amplitude"]
        if prefs["idle_style"] == "energetic":
            arm_amp *= 1.5
        elif prefs["idle_style"] == "tired":
            arm_amp *= 0.5

        # 自然摆臂（反相位）
        arm_phase = t * 2 * math.pi * 0.6
        frame_data[left_shoulder + 0] = arm_amp * 0.5 * math.sin(arm_phase)
        frame_data[left_shoulder + 1] = -0.2 + arm_amp * 0.3 * math.sin(arm_phase)
        frame_data[left_shoulder + 2] = arm_amp * 0.2 * math.cos(arm_phase)

        frame_data[left_arm + 0] = arm_amp * 0.3 * math.sin(arm_phase + 0.5)
        frame_data[left_arm + 1] = arm_amp * 0.2 * math.sin(arm_phase)
        frame_data[left_arm + 2] = arm_amp * 0.1 * math.cos(arm_phase)

        frame_data[left_forearm + 0] = arm_amp * 0.2 * math.sin(arm_phase + 1.0)
        frame_data[left_forearm + 1] = arm_amp * 0.1 * math.sin(arm_phase)
        frame_data[left_forearm + 2] = 0.0

        frame_data[left_hand + 0] = arm_amp * 0.1 * math.sin(arm_phase + 1.5)
        frame_data[left_hand + 1] = arm_amp * 0.1 * math.sin(arm_phase)
        frame_data[left_hand + 2] = 0.0

        # 右臂反相位
        frame_data[right_shoulder + 0] = -arm_amp * 0.5 * math.sin(arm_phase)
        frame_data[right_shoulder + 1] = -0.2 - arm_amp * 0.3 * math.sin(arm_phase)
        frame_data[right_shoulder + 2] = -arm_amp * 0.2 * math.cos(arm_phase)

        frame_data[right_arm + 0] = -arm_amp * 0.3 * math.sin(arm_phase + 0.5)
        frame_data[right_arm + 1] = -arm_amp * 0.2 * math.sin(arm_phase)
        frame_data[right_arm + 2] = -arm_amp * 0.1 * math.cos(arm_phase)

        frame_data[right_forearm + 0] = -arm_amp * 0.2 * math.sin(arm_phase + 1.0)
        frame_data[right_forearm + 1] = -arm_amp * 0.1 * math.sin(arm_phase)
        frame_data[right_forearm + 2] = 0.0

        frame_data[right_hand + 0] = -arm_amp * 0.1 * math.sin(arm_phase + 1.5)
        frame_data[right_hand + 1] = -arm_amp * 0.1 * math.sin(arm_phase)
        frame_data[right_hand + 2] = 0.0

        # 腿部（站立姿态，微小重心移动）
        leg_base = right_hand + 3
        for side in [0, 1]:  # 0=Left, 1=Right
            up_leg = leg_base + side * 12
            leg = up_leg + 3
            foot = leg + 3
            toe = foot + 3

            sign = 1 if side == 0 else -1
            leg_phase = t * 2 * math.pi * 0.3

            frame_data[up_leg + 0] = sign * 0.05 * math.sin(leg_phase)
            frame_data[up_leg + 1] = 0.02 * math.sin(leg_phase * 2)
            frame_data[up_leg + 2] = sign * 0.02 * math.cos(leg_phase)

            frame_data[leg + 0] = 0.01 * math.sin(leg_phase)
            frame_data[leg + 1] = 0.0
            frame_data[leg + 2] = 0.0

            frame_data[foot + 0] = 0.0
            frame_data[foot + 1] = 0.0
            frame_data[foot + 2] = 0.0

            frame_data[toe + 0] = 0.0
            frame_data[toe + 1] = 0.0
            frame_data[toe + 2] = 0.0

        frames.append(frame_data)

    return frames


def _generate_walk_motion(
    frame_count: int,
    fps: int,
    prefs: dict[str, Any],
    traits: dict[str, float],
) -> list[list[float]]:
    """生成行走循环动作"""
    frames = []
    cycle_duration = 1.0 / prefs["movement_speed"]  # 步频
    frames_per_cycle = max(1, int(fps * cycle_duration))

    for f in range(frame_count):
        t = f / fps
        cycle_t = (t % cycle_duration) / cycle_duration  # 0-1
        phase = cycle_t * 2 * math.pi
        frame_data = [0.0] * _TOTAL_CHANNELS

        # Hips - 前进位移 + 上下起伏
        hip_idx = 0
        stride_length = 0.3 * prefs["movement_speed"]
        frame_data[hip_idx + 0] = t * stride_length * 0.5  # X 前进（累积）
        frame_data[hip_idx + 1] = 0.05 * math.sin(phase * 2)  # Y 上下
        frame_data[hip_idx + 2] = 0.02 * math.sin(phase)  # Z 侧向摆动
        frame_data[hip_idx + 3] = 0.1 * math.sin(phase)  # Zrot 骨盆扭转
        frame_data[hip_idx + 4] = 0.05 * math.sin(phase * 2)  # Xrot
        frame_data[hip_idx + 5] = 0.03 * math.sin(phase)  # Yrot

        # 脊柱跟随
        spine_base = 6
        for i in range(3):
            idx = spine_base + i * 3
            amp = 0.15 * (0.7 ** i)
            frame_data[idx + 0] = amp * math.sin(phase + i * 0.2)  # Zrot
            frame_data[idx + 1] = amp * 0.5 * math.sin(phase * 2)  # Xrot
            frame_data[idx + 2] = amp * 0.3 * math.cos(phase)      # Yrot

        # 颈部头部稳定（头部反向稳定视线）
        neck_idx = spine_base + 9
        head_idx = neck_idx + 3
        frame_data[neck_idx + 0] = 0.05 * math.sin(phase)
        frame_data[neck_idx + 1] = -0.1 * math.sin(phase * 2)
        frame_data[neck_idx + 2] = 0.03 * math.cos(phase)
        frame_data[head_idx + 0] = 0.03 * math.sin(phase)
        frame_data[head_idx + 1] = -0.05 * math.sin(phase * 2)
        frame_data[head_idx + 2] = 0.02 * math.cos(phase)

        # 手臂摆动（与腿反相位）
        left_shoulder = head_idx + 3
        left_arm = left_shoulder + 3
        left_forearm = left_arm + 3
        left_hand = left_forearm + 3

        right_shoulder = left_hand + 3
        right_arm = right_shoulder + 3
        right_forearm = right_arm + 3
        right_hand = right_forearm + 3

        arm_amp = 0.35 * prefs["gesture_amplitude"] * prefs["movement_speed"]
        frame_data[left_shoulder + 0] = arm_amp * math.sin(phase)
        frame_data[left_shoulder + 1] = -0.15 + arm_amp * 0.3 * math.sin(phase * 2)
        frame_data[left_shoulder + 2] = arm_amp * 0.2 * math.cos(phase)

        frame_data[left_arm + 0] = arm_amp * 0.5 * math.sin(phase + 0.5)
        frame_data[left_arm + 1] = arm_amp * 0.2 * math.sin(phase)
        frame_data[left_arm + 2] = arm_amp * 0.1 * math.cos(phase)

        frame_data[left_forearm + 0] = arm_amp * 0.3 * math.sin(phase + 1.0)
        frame_data[left_forearm + 1] = 0.0
        frame_data[left_forearm + 2] = 0.0

        frame_data[left_hand + 0] = arm_amp * 0.2 * math.sin(phase + 1.5)
        frame_data[left_hand + 1] = 0.0
        frame_data[left_hand + 2] = 0.0

        # 右臂与左臂反相位
        frame_data[right_shoulder + 0] = -arm_amp * math.sin(phase)
        frame_data[right_shoulder + 1] = -0.15 - arm_amp * 0.3 * math.sin(phase * 2)
        frame_data[right_shoulder + 2] = -arm_amp * 0.2 * math.cos(phase)

        frame_data[right_arm + 0] = -arm_amp * 0.5 * math.sin(phase + 0.5)
        frame_data[right_arm + 1] = -arm_amp * 0.2 * math.sin(phase)
        frame_data[right_arm + 2] = -arm_amp * 0.1 * math.cos(phase)

        frame_data[right_forearm + 0] = -arm_amp * 0.3 * math.sin(phase + 1.0)
        frame_data[right_forearm + 1] = 0.0
        frame_data[right_forearm + 2] = 0.0

        frame_data[right_hand + 0] = -arm_amp * 0.2 * math.sin(phase + 1.5)
        frame_data[right_hand + 1] = 0.0
        frame_data[right_hand + 2] = 0.0

        # 腿部行走
        leg_base = right_hand + 3
        for side in [0, 1]:  # 0=Left, 1=Right
            up_leg = leg_base + side * 12
            leg = up_leg + 3
            foot = leg + 3
            toe = foot + 3

            sign = 1 if side == 0 else -1
            leg_phase = phase + (0 if side == 0 else math.pi)  # 双腿反相位

            lift = 0.35 * math.sin(leg_phase)  # 抬腿
            frame_data[up_leg + 0] = sign * 0.1 * math.sin(leg_phase)
            frame_data[up_leg + 1] = lift
            frame_data[up_leg + 2] = sign * 0.05 * math.cos(leg_phase)

            frame_data[leg + 0] = lift * 0.5
            frame_data[leg + 1] = 0.0
            frame_data[leg + 2] = 0.0

            frame_data[foot + 0] = lift * 0.3
            frame_data[foot + 1] = 0.0
            frame_data[foot + 2] = 0.0

            frame_data[toe + 0] = 0.0
            frame_data[toe + 1] = 0.0
            frame_data[toe + 2] = 0.0

        frames.append(frame_data)

    return frames


def _write_bvh_file(frames: list[list[float]], fps: int) -> str:
    """将关键帧数据写入标准 BVH 文件

    返回: 生成的 BVH 文件路径
    """
    frame_count = len(frames)
    if frame_count == 0:
        raise ValueError("关键帧数据为空")

    lines = [_BVH_HIERARCHY.rstrip(), ""]
    lines.append("MOTION")
    lines.append(f"Frames: {frame_count}")
    lines.append(f"Frame Time: {1.0 / fps:.6f}")

    for frame in frames:
        if len(frame) != _TOTAL_CHANNELS:
            raise ValueError(f"关键帧通道数不匹配: {len(frame)} != {_TOTAL_CHANNELS}")
        values = " ".join(f"{v:.6f}" for v in frame)
        lines.append(values)

    content = "\n".join(lines) + "\n"

    work_dir = os.environ.get("XIJIAN_DEV_WORK_DIR", tempfile.gettempdir())
    os.makedirs(work_dir, exist_ok=True)
    path = os.path.join(work_dir, f"motion_{secrets.token_hex(4)}.bvh")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def generate_bvh_from_persona(
    persona_text: str,
    character_name: str = "",
    fps: int = 30,
    duration: float = 3.0,
    motion_type: str = "idle",
) -> str:
    """人设文本 → BVH 动作文件（确定性规则基生成）

    LLM 增强点：`_analyze_persona` 可替换为 LLM 调用（如让大模型
    输出 JSON 化的性格维度评分），其余管线保持不变。

    参数:
        persona_text: 角色人设描述文本
        character_name: 角色名（用于文件名）
        fps: 帧率
        duration: 动作时长（秒）
        motion_type: "idle"（待机）或 "walk"（行走）

    返回:
        生成的 BVH 文件路径
    """
    if not persona_text.strip():
        raise ValueError("人设描述文本不能为空")

    analysis = _analyze_persona(persona_text)
    prefs = analysis["motion_preferences"]
    traits = analysis["traits"]

    frame_count = max(2, int(fps * duration))

    if motion_type == "walk":
        frames = _generate_walk_motion(frame_count, fps, prefs, traits)
    else:
        frames = _generate_idle_motion(frame_count, fps, prefs, traits)

    return _write_bvh_file(frames, fps)


def generate_bvh_from_video_frames(
    video_or_dir_path: str,
    character_name: str = "",
    fps: int = 30,
    duration: float = 3.0,
    motion_type: str = "walk",
) -> str:
    """视频/帧目录 → BVH 动作文件

    真实实现需接入姿态估计管线（见 LocalVideoInferenceProvider 的
    TODO 注释）。当前实现基于可提取的元信息（如帧数）做确定性生成，
    保证 stub 环境下流程真实可用、输出合法 BVH。
    """
    if not video_or_dir_path.strip():
        raise ValueError("视频/帧目录路径不能为空")

    # 尝试统计帧目录中的帧数作为时长参考
    frame_count_hint = 0
    if os.path.isdir(video_or_dir_path):
        try:
            frame_count_hint = len(
                [n for n in os.listdir(video_or_dir_path)
                 if n.lower().endswith((".png", ".jpg", ".jpeg"))]
            )
        except OSError:
            pass

    if frame_count_hint > 0:
        duration = max(duration, frame_count_hint / fps)

    return generate_bvh_from_persona(
        f"从视频推断的动作: {character_name or 'character'}",
        character_name,
        fps=fps,
        duration=duration,
        motion_type=motion_type,
    )
