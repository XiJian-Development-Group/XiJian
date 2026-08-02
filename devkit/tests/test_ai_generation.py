"""C2.8 / C2.9 AI 生成服务测试。

覆盖：
- AI 模型生成服务（C2.8）：提供商抽象、submit/poll 状态机、fallback 链、
  确定性 VRM 生成、与 model_viewer.generate_model_from_text 集成
- AI 动作生成服务（C2.9）：人设→BVH 确定性生成、视频输入降级、
  与 motion_editor.generate_motion_from_text / _from_video 集成
"""

from __future__ import annotations

import json
import os
import struct

import pytest

from devkit import DevKitError
from devkit.ai_generation.model_generation import (
    AIModelGenerationService,
    DeterministicFallbackProvider,
    HuggingFaceFallbackProvider,
    MeshyProvider,
    ModelGenerationJob,
    ModelGenerationStatus,
    TripoProvider,
    _build_minimal_glb,
    create_model_generation_service,
    generate_minimal_vrm,
)
from devkit.ai_generation.motion_generation import (
    AIMotionGenerationService,
    MotionGenerationJob,
    MotionGenerationStatus,
    PersonaToMotionProvider,
    VideoMotionCaptureProvider,
    _analyze_persona,
    create_motion_generation_service,
    generate_bvh_from_persona,
    generate_bvh_from_video_frames,
)

# ---------------------------------------------------------------------------
# C2.8: AI 模型生成服务
# ---------------------------------------------------------------------------


class TestModelGenerationStatus:
    def test_enum_values(self):
        assert ModelGenerationStatus.PENDING.value == "pending"
        assert ModelGenerationStatus.PROCESSING.value == "processing"
        assert ModelGenerationStatus.SUCCEEDED.value == "succeeded"
        assert ModelGenerationStatus.FAILED.value == "failed"


class TestDeterministicVrm:
    def test_glb_magic_and_version(self):
        glb = _build_minimal_glb("测试模型", "tester")
        assert glb[:4] == b"glTF"
        assert struct.unpack("<I", glb[4:8])[0] == 2

    def test_glb_contains_vrmc_vrm_extension(self):
        glb = _build_minimal_glb("一只可爱的猫娘", "cat_girl")
        # 解析 JSON chunk（chunk 0）
        json_len = struct.unpack("<I", glb[12:16])[0]
        chunk_type = glb[16:20]
        assert chunk_type == b"JSON"
        payload = glb[20:20 + json_len]
        gltf = json.loads(payload.rstrip(b" "))
        assert "VRMC_vrm" in gltf["extensions"]
        assert gltf["extensions"]["VRMC_vrm"]["specVersion"] == "1.0"
        assert "humanoid" in gltf["extensions"]["VRMC_vrm"]
        assert gltf["extensions"]["VRMC_vrm"]["meta"]["name"] == "cat_girl"

    def test_generate_minimal_vrm_writes_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        path = generate_minimal_vrm("测试", "hero")
        assert os.path.isfile(path)
        with open(path, "rb") as f:
            assert f.read()[:4] == b"glTF"
        assert path.endswith(".vrm")


class TestRemoteProviders:
    def test_tripo_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("TRIPO_API_KEY", raising=False)
        p = TripoProvider()
        with pytest.raises(RuntimeError, match="TRIPO_API_KEY"):
            p.submit("测试", "t")

    def test_tripo_with_key_creates_pending_job(self, monkeypatch):
        monkeypatch.setenv("TRIPO_API_KEY", "sk_test_1234567890")
        p = TripoProvider()
        job = p.submit("测试", "t")
        assert job.status == ModelGenerationStatus.PENDING
        assert job.provider == "tripo"

    def test_meshy_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("MESHY_API_KEY", raising=False)
        p = MeshyProvider()
        with pytest.raises(RuntimeError, match="MESHY_API_KEY"):
            p.submit("测试", "t")


class TestHuggingFaceFallback:
    def test_skipped_when_hub_missing(self, monkeypatch):
        """huggingface_hub 未安装时 submit 快速失败，让 fallback 链继续。"""
        import builtins
        real_import = builtins.__import__

        def _block(name, *args, **kw):
            if name == "huggingface_hub" or name.startswith("huggingface_hub."):
                raise ImportError("simulated absence")
            return real_import(name, *args, **kw)

        monkeypatch.setattr(builtins, "__import__", _block)
        p = HuggingFaceFallbackProvider()
        with pytest.raises(RuntimeError, match="huggingface_hub"):
            p.submit("测试", "t")


class TestModelGenerationService:
    def test_default_providers_include_fallbacks(self, monkeypatch):
        monkeypatch.delenv("TRIPO_API_KEY", raising=False)
        monkeypatch.delenv("MESHY_API_KEY", raising=False)
        monkeypatch.delenv("XIJIAN_LOCAL_3D_PIPELINE", raising=False)
        s = create_model_generation_service()
        names = [p.name for p in s.providers]
        assert "huggingface" in names
        assert "deterministic" in names

    def test_empty_description_rejected(self, monkeypatch):
        monkeypatch.delenv("TRIPO_API_KEY", raising=False)
        s = create_model_generation_service()
        with pytest.raises(ValueError):
            s.generate("   ")

    def test_fallback_chain_succeeds_without_remote_keys(self, monkeypatch, tmp_path):
        """无远程 API Key 时走 fallback 链，最终确定性生成必须成功。"""
        monkeypatch.delenv("TRIPO_API_KEY", raising=False)
        monkeypatch.delenv("MESHY_API_KEY", raising=False)
        monkeypatch.delenv("XIJIAN_LOCAL_3D_PIPELINE", raising=False)
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        s = create_model_generation_service()
        job = s.generate_and_wait("一只可爱的猫娘", "cat_girl")
        assert job.status == ModelGenerationStatus.SUCCEEDED
        assert job.result_path and os.path.isfile(job.result_path)
        assert job.provider == "deterministic"

    def test_provider_hint_priority(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TRIPO_API_KEY", raising=False)
        monkeypatch.delenv("MESHY_API_KEY", raising=False)
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        s = create_model_generation_service()
        job = s.generate_and_wait("测试", "t", provider_hint="deterministic")
        assert job.provider == "deterministic"

    def test_submit_poll_state_machine(self, monkeypatch, tmp_path):
        """验证 submit → poll → succeeded 状态机流转。"""
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        s = create_model_generation_service()
        job = s.generate("测试", "t")
        assert job.id
        # DeterministicFallbackProvider 同步完成，poll 一次即终态
        done = s.wait_for_completion(job.id)
        assert done.status in (ModelGenerationStatus.SUCCEEDED, ModelGenerationStatus.FAILED)
        assert s.get_job(job.id) is not None


# ---------------------------------------------------------------------------
# C2.8 集成：model_viewer.generate_model_from_text
# ---------------------------------------------------------------------------


class TestGenerateModelFromTextIntegration:
    def test_generates_and_registers_vrm(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TRIPO_API_KEY", raising=False)
        monkeypatch.delenv("MESHY_API_KEY", raising=False)
        from devkit.model_viewer import (
            generate_model_from_text, get_model_info, list_models,
        )
        rec = generate_model_from_text(str(tmp_path), "一只可爱的猫娘角色", "cat_girl")
        assert rec["format"] == "vrm"
        assert os.path.isfile(rec["path"])
        assert len(list_models(str(tmp_path))) == 1
        assert get_model_info(str(tmp_path), rec["id"]) is not None

    def test_empty_description_raises_devkit_error(self, tmp_path):
        from devkit.model_viewer import generate_model_from_text
        with pytest.raises(DevKitError) as ei:
            generate_model_from_text(str(tmp_path), "   ")
        assert ei.value.code == "empty_description"


# ---------------------------------------------------------------------------
# C2.9: AI 动作生成服务
# ---------------------------------------------------------------------------


class TestMotionGenerationStatus:
    def test_enum_values(self):
        assert MotionGenerationStatus.PENDING.value == "pending"
        assert MotionGenerationStatus.PROCESSING.value == "processing"
        assert MotionGenerationStatus.SUCCEEDED.value == "succeeded"
        assert MotionGenerationStatus.FAILED.value == "failed"


class TestPersonaAnalysis:
    def test_energetic_keywords_raise_energy(self):
        result = _analyze_persona("活泼开朗阳光的角色，精力充沛")
        assert result["traits"]["energy"] > 0.3
        assert result["motion_preferences"]["idle_style"] == "energetic"

    def test_shy_keywords_lower_confidence(self):
        result = _analyze_persona("害羞内向腼腆的角色")
        assert result["traits"]["confidence"] < -0.3
        assert result["motion_preferences"]["gesture_amplitude"] < 0.5

    def test_neutral_persona_defaults(self):
        result = _analyze_persona("一个普通角色")
        assert result["motion_preferences"]["idle_style"] == "neutral"
        assert result["motion_preferences"]["movement_speed"] == 1.0


class TestBvhGeneration:
    def test_generate_bvh_from_persona_produces_valid_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        path = generate_bvh_from_persona("活泼开朗自信的角色", "yuki", fps=30, duration=2.0)
        assert os.path.isfile(path)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "HIERARCHY" in content
        assert "ROOT Hips" in content
        assert "MOTION" in content
        assert "Frames:" in content
        assert "Frame Time: 0.033333" in content

    def test_bvh_frame_channel_count_matches_hierarchy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        path = generate_bvh_from_persona("测试", "t", fps=30, duration=1.0)
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        # 找到 MOTION 后的第一帧数据行
        idx = lines.index("MOTION")
        frame_line = lines[idx + 3]  # MOTION / Frames / Frame Time / 数据
        values = frame_line.split()
        # Hips 6 通道 + 21 关节 × 3 = 69
        assert len(values) == 69

    def test_empty_persona_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        with pytest.raises(ValueError):
            generate_bvh_from_persona("   ", "t")

    def test_walk_motion_advances_forward(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        path = generate_bvh_from_persona("测试", "t", fps=30, duration=2.0, motion_type="walk")
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        idx = lines.index("MOTION")
        first = [float(v) for v in lines[idx + 3].split()]
        last = [float(v) for v in lines[-1].split()]
        # Hips X 位置（通道 0）应前进
        assert last[0] > first[0]

    def test_video_frames_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()
        (frames_dir / "f0001.png").write_bytes(b"x")
        (frames_dir / "f0002.png").write_bytes(b"x")
        path = generate_bvh_from_video_frames(str(frames_dir), "char")
        assert os.path.isfile(path)
        with open(path, encoding="utf-8") as f:
            assert "MOTION" in f.read()


class TestMotionGenerationService:
    def test_default_providers_include_persona(self, monkeypatch):
        monkeypatch.delenv("VIDEO_MOTION_API_KEY", raising=False)
        monkeypatch.delenv("XIJIAN_LOCAL_VIDEO_PIPELINE", raising=False)
        s = create_motion_generation_service()
        names = [p.name for p in s.providers]
        assert "persona_rule_based" in names

    def test_video_capture_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("VIDEO_MOTION_API_KEY", raising=False)
        p = VideoMotionCaptureProvider()
        with pytest.raises(RuntimeError, match="VIDEO_MOTION_API_KEY"):
            p.submit("video", "/tmp/v.mp4", "char")

    def test_persona_generation_flow(self, monkeypatch, tmp_path):
        """人设输入走确定性提供商，任务成功并产出 BVH。"""
        monkeypatch.delenv("VIDEO_MOTION_API_KEY", raising=False)
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        s = create_motion_generation_service()
        job = s.generate_and_wait("persona", "活泼开朗自信的角色", "yuki")
        assert job.status == MotionGenerationStatus.SUCCEEDED
        assert job.provider == "persona_rule_based"
        assert job.result_path and os.path.isfile(job.result_path)

    def test_video_input_falls_back_to_rule_based(self, monkeypatch, tmp_path):
        """视频输入在 stub 环境降级到确定性生成，任务仍成功。"""
        monkeypatch.delenv("VIDEO_MOTION_API_KEY", raising=False)
        monkeypatch.delenv("XIJIAN_LOCAL_VIDEO_PIPELINE", raising=False)
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        s = create_motion_generation_service()
        job = s.generate_and_wait("video", "/tmp/nonexistent.mp4", "char")
        assert job.status == MotionGenerationStatus.SUCCEEDED
        assert job.result_path and os.path.isfile(job.result_path)

    def test_empty_input_rejected(self, monkeypatch):
        monkeypatch.delenv("VIDEO_MOTION_API_KEY", raising=False)
        s = create_motion_generation_service()
        with pytest.raises(ValueError):
            s.generate("persona", "   ", "char")


# ---------------------------------------------------------------------------
# C2.9 集成：motion_editor.generate_motion_from_text / _from_video
# ---------------------------------------------------------------------------


class TestGenerateMotionIntegration:
    def test_generate_motion_from_text_imports_bvh(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VIDEO_MOTION_API_KEY", raising=False)
        monkeypatch.delenv("XIJIAN_LOCAL_VIDEO_PIPELINE", raising=False)
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        from devkit.motion_editor import (
            generate_motion_from_text, list_motions,
        )
        rec = generate_motion_from_text(
            str(tmp_path), "char_1", "活泼开朗自信的角色", "happy_walk",
            motion_type="walk",
        )
        assert rec["type"] == "imported"
        assert rec["imported_format"] == "bvh"
        assert os.path.isfile(rec["file_path"])
        params = rec.get("parameters", {})
        assert params.get("skeleton_joint_count") == 22
        # 6 个内置动效 + 1 个新导入
        assert len(list_motions(str(tmp_path), "char_1")) == 7

    def test_generate_motion_from_video_imports_bvh(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VIDEO_MOTION_API_KEY", raising=False)
        monkeypatch.delenv("XIJIAN_LOCAL_VIDEO_PIPELINE", raising=False)
        monkeypatch.setenv("XIJIAN_DEV_WORK_DIR", str(tmp_path))
        from devkit.motion_editor import (
            generate_motion_from_video, list_motions,
        )
        rec = generate_motion_from_video(str(tmp_path), "char_2", "/tmp/video.mp4", "ai_video")
        assert rec["type"] == "imported"
        assert rec["imported_format"] == "bvh"
        assert os.path.isfile(rec["file_path"])
        assert len(list_motions(str(tmp_path), "char_2")) == 7

    def test_empty_persona_raises(self, tmp_path):
        from devkit.motion_editor import generate_motion_from_text
        with pytest.raises(DevKitError) as ei:
            generate_motion_from_text(str(tmp_path), "char_1", "   ")
        assert ei.value.code == "empty_description"

    def test_empty_video_path_raises(self, tmp_path):
        from devkit.motion_editor import generate_motion_from_video
        with pytest.raises(DevKitError) as ei:
            generate_motion_from_video(str(tmp_path), "char_1", "   ")
        assert ei.value.code == "empty_path"
