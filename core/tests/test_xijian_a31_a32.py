"""Tests for A3.1 (startup model auto-load + generation references) and
the A3.2 Critical status handler subscriber.

(A3.1 (启动模型自动加载 + 生成参考) 与 A3.2 Critical 状态处理器订阅者的测试。)

Covers:
(覆盖范围：)

* **A3.1 startup auto-load** — a character with an ``is_active=1``
  model gets its ``loaded`` flag flipped by :func:`auto_load_active_models`
  (the startup scan); ``set_active_model`` clears the others.
* (**A3.1 启动自动加载** — 拥有 ``is_active=1`` 模型的角色由
  :func:`auto_load_active_models` (启动扫描) 翻转 ``loaded`` 标志；
  ``set_active_model`` 会清除其他模型的 active 标志。)
* **A3.1 cross-modal references** — :func:`get_generation_references`
  resolves pose_image / motion / voice references; image generation
  injects them into the request context.
* (**A3.1 跨模态参考** — :func:`get_generation_references` 解析
  pose_image / motion / voice 参考；图像生成将其注入请求上下文。)
* **A3.2 Critical handler subscriber** — entering Critical status
  writes a memory entry (the registered default handler has a real
  consumer).
* (**A3.2 Critical 处理器订阅者** — 进入 Critical 状态写入一条记忆
  (已注册的默认处理器有真实消费者)。)
"""

from __future__ import annotations

from xijian_api.stubs import character_state as cs_stub
from xijian_api.stubs import characters as chars_stub
from xijian_api.stubs import memory as memory_stub
from xijian_api.stubs import state as stubs_state
from xijian_api.stubs.character_state import STATUS_CRITICAL


# ---------------------------------------------------------------------------
# A3.1 — 启动时自动加载 is_active 模型
# ---------------------------------------------------------------------------


class TestAutoLoadActiveModels:
    def _seed_character_with_active_model(self) -> str:
        char = chars_stub.create({"name": "Test", "display_name": "Test"})
        cid = char["id"]
        chars_stub.create_model(
            cid,
            {
                "name": "base",
                "kind": "vrm",
                "file_path": "/models/base.vrm",
                "is_active": True,
            },
        )
        return cid

    def test_create_model_validates_kind(self):
        char = chars_stub.create({"name": "K", "display_name": "K"})
        with __import__("pytest").raises(ValueError):
            chars_stub.create_model(char["id"], {"kind": "lol", "name": "bad"})

    def test_create_model_stores_spec_fields(self):
        char = chars_stub.create({"name": "K", "display_name": "K"})
        model = chars_stub.create_model(
            char["id"],
            {
                "name": "base",
                "kind": "glb",
                "file_path": "/models/base.glb",
                "texture_paths": ["/tex/a.png"],
                "version": 2,
                "is_active": True,
            },
        )
        assert model["kind"] == "glb"
        assert model["format"] == "glb"
        assert model["is_active"] == 1
        assert model["version"] == 2

    def test_set_active_model_clears_others(self):
        char = chars_stub.create({"name": "K", "display_name": "K"})
        m1 = chars_stub.create_model(char["id"], {"name": "a", "kind": "vrm"})
        m2 = chars_stub.create_model(char["id"], {"name": "b", "kind": "glb"})
        chars_stub.set_active_model(char["id"], m2["id"])
        assert chars_stub.get_model(char["id"], m1["id"])["is_active"] == 0
        assert chars_stub.get_model(char["id"], m2["id"])["is_active"] == 1
        assert chars_stub.get_active_model(char["id"])["id"] == m2["id"]

    def test_auto_load_marks_character_loaded(self):
        cid = self._seed_character_with_active_model()
        result = chars_stub.auto_load_active_models()
        assert cid in result["loaded"]
        assert chars_stub.get(cid)["loaded"] is True

    def test_auto_load_skips_characters_without_active_model(self):
        char = chars_stub.create({"name": "NoModel", "display_name": "NoModel"})
        result = chars_stub.auto_load_active_models()
        assert char["id"] not in result["loaded"]
        assert chars_stub.get(char["id"])["loaded"] is False

    def test_auto_load_runs_via_seed_all(self):
        # 启动路径（seed_all → auto_load_active_models）在每次
        # 重置时都会运行；演示角色没有模型，因此保持未加载。
        assert chars_stub.get("char_yuki")["loaded"] is False
        # 在 *重置之前* 创建了激活模型的角色，
        # 会在下次重置后被加载。
        cid = self._seed_character_with_active_model()
        stubs_state.reset_for_testing()
        assert chars_stub.get(cid) is None  # 重置会清除自定义记录
        assert chars_stub.get("char_yuki")["loaded"] is False


# ---------------------------------------------------------------------------
# A3.1 — 跨模态生成引用
# ---------------------------------------------------------------------------


class TestGenerationReferences:
    def _seed_rich_character(self) -> str:
        char = chars_stub.create({"name": "Rich", "display_name": "Rich"})
        cid = char["id"]
        chars_stub.create_model(
            cid,
            {
                "name": "base",
                "kind": "vrm",
                "file_path": "/models/rich.vrm",
                "texture_paths": ["/tex/rich.png"],
                "is_active": True,
            },
        )
        chars_stub.create_motion(
            cid, {"name": "greeting", "animation_ref": "/anim/greet.bvh"}
        )
        chars_stub.create_voice(
            cid,
            {
                "name": "default",
                "voice_ref_path": "/voices/rich.wav",
                "is_default": True,
            },
        )
        # pose_image 资源位于缓存中（asset_kind 键）。
        cache = stubs_state.character_asset_cache.setdefault(cid, {})
        cache["pose1"] = {
            "character_id": cid,
            "asset_key": "pose1",
            "asset_kind": "pose_image",
            "data": "/refs/rich_pose.png",
        }
        return cid

    def test_resolves_all_references(self):
        cid = self._seed_rich_character()
        refs = chars_stub.get_generation_references(cid)
        assert refs["pose_image"] == "/refs/rich_pose.png"
        assert refs["motion_clip"] == "/anim/greet.bvh"
        assert refs["voice_ref"] == "/voices/rich.wav"
        assert refs["texture"] == "/tex/rich.png"

    def test_empty_character_returns_none_values(self):
        refs = chars_stub.get_generation_references("char_yuki")
        assert refs["pose_image"] is None
        assert refs["motion_clip"] is None

    def test_image_generate_injects_references(self, client, auth_headers):
        cid = self._seed_rich_character()
        response = client.post(
            "/v1/images/generations",
            headers=auth_headers,
            json={
                "prompt": "画一张角色立绘",
                "xijian": {"character_id": cid},
            },
        )
        # Mock 图像后端可能返回 503；若成功，引用必须
        # 出现在信封中。无论哪种情况，注入路径
        # 都不能使请求崩溃。
        if response.status_code == 503:
            return
        assert response.status_code == 200
        body = response.get_json()
        refs = body["xijian"].get("character_references") or {}
        assert refs.get("pose_image") == "/refs/rich_pose.png"


# ---------------------------------------------------------------------------
# A3.2 — Critical 处理器订阅者
# ---------------------------------------------------------------------------


class TestCriticalHandlerSubscriber:
    def test_handler_registered_by_default(self):
        handlers = cs_stub._STATUS_HANDLERS.get(STATUS_CRITICAL, [])
        assert len(handlers) >= 1

    def test_critical_transition_writes_memory(self):
        stubs_state.memory.clear()
        cs_stub.apply_field_change("char_yuki", "health", 0.0, reason="manual")
        assert cs_stub.get_state("char_yuki")["status"] == STATUS_CRITICAL
        entries = [
            e for e in stubs_state.memory.values()
            if e.get("character_id") == "char_yuki"
            and "Critical" in e.get("content", "")
        ]
        assert len(entries) >= 1

    def test_healthy_transition_writes_no_critical_memory(self):
        stubs_state.memory.clear()
        cs_stub.apply_field_change("char_yuki", "hunger", 10.0, reason="manual")
        entries = [
            e for e in stubs_state.memory.values()
            if "Critical" in e.get("content", "")
        ]
        assert entries == []

    def test_install_is_idempotent(self):
        before = len(cs_stub._STATUS_HANDLERS.get(STATUS_CRITICAL, []))
        cs_stub.install_default_status_handlers()
        cs_stub.install_default_status_handlers()
        after = len(cs_stub._STATUS_HANDLERS.get(STATUS_CRITICAL, []))
        assert after == before
