"""AI 生成服务抽象层 (C2.8 / C2.9)

提供统一的 AI 生成服务接口，支持：
- 远程服务（Tripo / Meshy 类）submit/poll 状态机
- 本地生成管线接口
- HuggingFace 下载作为 fallback
- 确定性/规则基生成作为 stub 环境下的真实实现
"""

from .model_generation import (
    AIModelGenerationService,
    ModelGenerationJob,
    ModelGenerationStatus,
    create_model_generation_service,
    generate_vrm_from_text,
)

from .motion_generation import (
    AIMotionGenerationService,
    MotionGenerationJob,
    MotionGenerationStatus,
    create_motion_generation_service,
    generate_bvh_from_persona,
    generate_bvh_from_video_frames,
)

__all__ = [
    "AIModelGenerationService",
    "ModelGenerationJob",
    "ModelGenerationStatus",
    "create_model_generation_service",
    "generate_vrm_from_text",
    "AIMotionGenerationService",
    "MotionGenerationJob",
    "MotionGenerationStatus",
    "create_motion_generation_service",
    "generate_bvh_from_persona",
    "generate_bvh_from_video_frames",
]