"""针对 ``stubs.voice_calls``（A6）和 ``/v1/xijian/voice-calls/*`` 的测试。

覆盖范围：

* **CRUD** — 创建 / 列表 / 查询通话会话。
* **生命周期** — ring（idle → ringing）、accept（→ active）、reject、
  end（→ ended + 计算通话时长）。
* **事件** — add_event / list_events（kind 校验、过滤）。
* **Barge-in（AC-3）** — 标志设置/清除 + ``barge_in`` 事件。
* **通话循环** — STT（monkeypatch）→ 回复处理器 → TTS
  （monkeypatch）→ 语音事件，以及 barge-in 打断 TTS
  的路径。
* **歌唱（US-A6-03）** — 默认 DiffSinger stub 返回
  ``unavailable``；注册的引擎钩子会被调用。
* **路由** — 使用 Bearer 认证的 HTTP 冒烟测试。
"""

from __future__ import annotations

import base64
import time

import pytest

from xijian_api.stubs import state as stubs_state
from xijian_api.stubs import voice_calls as vc_stub
from xijian_api.stubs.characters import create as create_character


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture()
def character():
    return create_character({"name": "Call Test Char"})["id"]


@pytest.fixture()
def active_call(character):
    record = vc_stub.create_call(character_id=character)
    return vc_stub.accept_call(record["id"])["id"]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCRUD:
    def test_create_returns_idle_call(self, character):
        record = vc_stub.create_call(character_id=character)
        assert record["id"].startswith("call_")
        assert record["character_id"] == character
        assert record["status"] == vc_stub.CALL_STATUS_IDLE
        assert record["direction"] == vc_stub.DIRECTION_USER_INITIATED
        assert record["started_at"] is not None
        assert record["ended_at"] is None

    def test_create_character_initiated_direction(self, character):
        record = vc_stub.create_call(
            character_id=character, direction=vc_stub.DIRECTION_CHARACTER_INITIATED
        )
        assert record["direction"] == vc_stub.DIRECTION_CHARACTER_INITIATED

    def test_create_requires_character(self):
        with pytest.raises(vc_stub.VoiceCallError):
            vc_stub.create_call(character_id="")

    def test_create_rejects_bad_direction(self, character):
        with pytest.raises(vc_stub.VoiceCallError):
            vc_stub.create_call(character_id=character, direction="sideways")

    def test_list_filters(self, character):
        c1 = vc_stub.create_call(character_id=character)
        c2 = vc_stub.create_call(character_id=character)
        vc_stub.end_call(c1["id"])
        vc_stub.accept_call(c2["id"])
        active = vc_stub.list_calls(status=vc_stub.CALL_STATUS_ACTIVE)
        ended = vc_stub.list_calls(status=vc_stub.CALL_STATUS_ENDED)
        by_char = vc_stub.list_calls(character_id=character)
        assert c2["id"] in [c["id"] for c in active]
        assert c1["id"] in [c["id"] for c in ended]
        assert len(by_char) == 2

    def test_get_missing_returns_none(self):
        assert vc_stub.get_call("call_nope") is None


# ---------------------------------------------------------------------------
# 生命周期状态机
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_ring_transitions_to_ringing(self, character):
        call = vc_stub.create_call(character_id=character)
        assert call["status"] == vc_stub.CALL_STATUS_IDLE
        record = vc_stub.ring(call["id"])
        assert record["status"] == vc_stub.CALL_STATUS_RINGING

    def test_accept_from_idle_and_ringing(self, character):
        call = vc_stub.create_call(character_id=character)
        assert vc_stub.accept_call(call["id"])["status"] == vc_stub.CALL_STATUS_ACTIVE
        call2 = vc_stub.create_call(character_id=character)
        vc_stub.ring(call2["id"])
        assert vc_stub.accept_call(call2["id"])["status"] == vc_stub.CALL_STATUS_ACTIVE

    def test_accept_idempotent(self, active_call):
        record = vc_stub.accept_call(active_call)
        assert record["status"] == vc_stub.CALL_STATUS_ACTIVE

    def test_reject_ends_call(self, character):
        call = vc_stub.create_call(character_id=character)
        vc_stub.ring(call["id"])
        record = vc_stub.reject_call(call["id"])
        assert record["status"] == vc_stub.CALL_STATUS_ENDED
        assert record["ended_reason"] == "rejected"

    def test_cannot_reject_active_call(self, active_call):
        with pytest.raises(vc_stub.VoiceCallError):
            vc_stub.reject_call(active_call)

    def test_end_computes_duration(self, character):
        call = vc_stub.create_call(character_id=character, now=1000.0)
        vc_stub.accept_call(call["id"])
        record = vc_stub.end_call(call["id"], )
        # end_call 使用真实时钟；仅断言形状 + 原因。
        assert record["status"] == vc_stub.CALL_STATUS_ENDED
        assert record["ended_reason"] == "ended"
        assert record["ended_at"] is not None
        assert record["duration_sec"] is not None

    def test_cannot_speak_to_ended_call(self, character):
        call = vc_stub.create_call(character_id=character)
        vc_stub.end_call(call["id"])
        with pytest.raises(vc_stub.VoiceCallError):
            vc_stub.submit_user_speech(call["id"], text="hi")


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------


class TestEvents:
    def test_add_event(self, active_call):
        event = vc_stub.add_event(active_call, vc_stub.KIND_SPEECH, {"role": "user"})
        assert event["id"].startswith("callevt_")
        assert event["call_id"] == active_call
        assert event["kind"] == vc_stub.KIND_SPEECH

    def test_add_event_rejects_unknown_kind(self, active_call):
        with pytest.raises(vc_stub.VoiceCallError):
            vc_stub.add_event(active_call, "telepathy", {})

    def test_list_events_oldest_first_and_filtered(self, active_call):
        vc_stub.add_event(active_call, vc_stub.KIND_SPEECH, {"turn": 1})
        vc_stub.add_event(active_call, vc_stub.KIND_MOTION, {"name": "wave"})
        vc_stub.add_event(active_call, vc_stub.KIND_SPEECH, {"turn": 2})
        # 注意：accept_call 已发出一个状态变更 EFFECT 事件。
        speech = vc_stub.list_events(active_call, kind=vc_stub.KIND_SPEECH)
        assert [e["payload"].get("turn") for e in speech] == [1, 2]
        motion = vc_stub.list_events(active_call, kind=vc_stub.KIND_MOTION)
        assert len(motion) == 1
        effect = vc_stub.list_events(active_call, kind=vc_stub.KIND_EFFECT)
        assert any(e["payload"].get("kind") == "state_change" for e in effect)
        assert len(vc_stub.list_events(active_call)) == 4  # state_change + 3 个事件

    def test_events_are_scoped_to_call(self, active_call, character):
        other = vc_stub.accept_call(
            vc_stub.create_call(character_id=character)["id"]
        )["id"]
        vc_stub.add_event(other, vc_stub.KIND_SPEECH, {"turn": 99})
        assert all(e["call_id"] == active_call for e in vc_stub.list_events(active_call))


# ---------------------------------------------------------------------------
# Barge-in（AC-3）
# ---------------------------------------------------------------------------


class TestBargeIn:
    def test_set_and_clear(self, active_call):
        assert vc_stub.is_barge_in(active_call) is False
        vc_stub.set_barge_in(active_call)
        assert vc_stub.is_barge_in(active_call) is True
        vc_stub.clear_barge_in(active_call)
        assert vc_stub.is_barge_in(active_call) is False

    def test_barge_in_records_event(self, active_call):
        vc_stub.set_barge_in(active_call)
        kinds = [e["kind"] for e in vc_stub.list_events(active_call)]
        assert vc_stub.KIND_BARGE_IN in kinds

    def test_ending_call_clears_flag(self, active_call):
        vc_stub.set_barge_in(active_call)
        vc_stub.end_call(active_call)
        assert vc_stub.is_barge_in(active_call) is False


# ---------------------------------------------------------------------------
# 通话循环编排
# ---------------------------------------------------------------------------


class TestCallLoop:
    def test_synchronous_text_turn(self, active_call, monkeypatch):
        replies = []
        vc_stub.set_reply_handler(lambda call_id, text: f"echo:{text}")
        monkeypatch.setattr(
            "xijian_api.stubs.audio.synth", lambda *a, **k: b"\x00\x01audio"
        )
        out = vc_stub.submit_user_speech(active_call, text="你好", synchronous=True)
        assert out["ok"] is True
        assert out["turn"] == 1
        assert out["reply"] == "echo:你好"
        assert out["user_event_id"] and out["reply_event_id"]
        speech = vc_stub.list_events(active_call, kind=vc_stub.KIND_SPEECH)
        assert len(speech) == 2  # user + assistant
        assistant = [e for e in speech if e["payload"]["role"] == "assistant"][0]
        assert assistant["payload"]["text"] == "echo:你好"
        assert assistant["payload"]["audio_size_bytes"] == 7

    def test_stt_path_uses_transcribe(self, active_call, monkeypatch):
        calls = {}
        monkeypatch.setattr(
            "xijian_api.stubs.audio.transcribe",
            lambda audio, **k: calls.update(audio=audio) or "转录文本",
        )
        monkeypatch.setattr("xijian_api.stubs.audio.synth", lambda *a, **k: b"x")
        out = vc_stub.submit_user_speech(
            active_call, audio=b"raw-bytes", synchronous=True
        )
        assert out["ok"] is True
        assert out["user_text"] == "转录文本"
        assert calls["audio"] == b"raw-bytes"

    def test_stt_failure_does_not_crash_loop(self, active_call, monkeypatch):
        def boom(audio, **k):
            raise RuntimeError("no stt backend")

        monkeypatch.setattr("xijian_api.stubs.audio.transcribe", boom)
        out = vc_stub.submit_user_speech(active_call, audio=b"x")
        assert out["ok"] is False
        assert "no stt backend" in out["error"]

    def test_barge_in_interrupts_tts(self, active_call, monkeypatch):
        """AC-3: 新语音到达（tts_busy）→ 打断当前 TTS 播放."""
        vc_stub.set_reply_handler(lambda c, t: f"reply-{t}")
        monkeypatch.setattr("xijian_api.stubs.audio.synth", lambda *a, **k: b"x")
        # 第 1 轮使 tts_busy 为 True（同步）。
        vc_stub.submit_user_speech(active_call, text="first", synchronous=True)
        assert vc_stub.get_call(active_call)["tts_busy"] is False
        # 模拟一个进行中的 TTS 轮次：标记忙碌，然后新的
        # 用户语音到达 → 设置 barge-in 标志。
        vc_stub.get_call(active_call)["tts_busy"] = True
        out = vc_stub.submit_user_speech(active_call, text="打断", synchronous=True)
        assert out["interrupted_previous"] is True
        events = vc_stub.list_events(active_call, kind=vc_stub.KIND_BARGE_IN)
        assert events and events[-1]["payload"]["active"] is True

    def test_default_reply_fallback(self, active_call, monkeypatch):
        monkeypatch.setattr("xijian_api.stubs.audio.synth", lambda *a, **k: b"x")
        out = vc_stub.submit_user_speech(active_call, text="hi", synchronous=True)
        assert out["reply"] == vc_stub.DEFAULT_FALLBACK_REPLY

    def test_dialogue_context_is_bounded(self, active_call, monkeypatch):
        vc_stub.set_reply_handler(lambda c, t: "ok")
        monkeypatch.setattr("xijian_api.stubs.audio.synth", lambda *a, **k: b"x")
        for i in range(25):
            vc_stub.submit_user_speech(active_call, text=f"t{i}", synchronous=True)
        ctx = vc_stub.get_call(active_call)["dialogue_context"]
        # 对话两侧都被存储：25 轮 × 2 = 50 条
        # 条目，裁剪到最近 20 条（10 轮 → 从 t15 开始）。
        assert len(ctx) == 20
        assert ctx[0]["text"] == "t15"  # 最旧的被丢弃


# ---------------------------------------------------------------------------
# 歌唱 — DiffSinger 接口 stub（US-A6-03）
# ---------------------------------------------------------------------------


class TestSinging:
    def test_default_stub_reports_unavailable(self, active_call):
        result = vc_stub.sing(active_call, "一闪一闪亮晶晶")
        assert result["ok"] is False
        assert result["status"] == "unavailable"
        song_events = vc_stub.list_events(active_call, kind=vc_stub.KIND_SONG)
        assert len(song_events) == 1
        assert song_events[0]["payload"]["lyrics"] == "一闪一闪亮晶晶"

    def test_voice_part_validation(self, active_call):
        with pytest.raises(vc_stub.VoiceCallError):
            vc_stub.sing(active_call, "歌词", voice_part="kazoo")

    def test_registered_engine_hook_is_used(self, active_call):
        seen = {}

        def fake_engine(call_id, lyrics, voice_part, melody, midi_path):
            seen.update(call_id=call_id, lyrics=lyrics, part=voice_part)
            return {"ok": True, "status": "queued", "audio_path": "/tmp/song.wav"}

        vc_stub.set_sing_engine(fake_engine)
        result = vc_stub.sing(active_call, "歌词", voice_part="harmony")
        assert result["status"] == "queued"
        assert seen["lyrics"] == "歌词"
        assert seen["part"] == "harmony"


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


class TestVoiceCallRoutes:
    def test_create_and_get(self, client, auth_headers, character):
        res = client.post(
            "/v1/xijian/voice-calls",
            json={"character_id": character},
            headers=auth_headers,
        )
        assert res.status_code == 201, res.get_json()
        call_id = res.get_json()["id"]
        got = client.get(f"/v1/xijian/voice-calls/{call_id}", headers=auth_headers)
        assert got.status_code == 200
        assert got.get_json()["status"] == vc_stub.CALL_STATUS_IDLE

    def test_list_requires_no_auth_failure(self, client):
        # 认证中间件拒绝缺失 token 的请求。
        res = client.get("/v1/xijian/voice-calls")
        assert res.status_code in (401, 403)

    def test_full_flow(self, client, auth_headers, character):
        created = client.post(
            "/v1/xijian/voice-calls",
            json={"character_id": character},
            headers=auth_headers,
        ).get_json()
        call_id = created["id"]
        assert client.post(
            f"/v1/xijian/voice-calls/{call_id}/ring", headers=auth_headers
        ).get_json()["status"] == vc_stub.CALL_STATUS_RINGING
        assert client.post(
            f"/v1/xijian/voice-calls/{call_id}/accept", headers=auth_headers
        ).get_json()["status"] == vc_stub.CALL_STATUS_ACTIVE
        speech = client.post(
            f"/v1/xijian/voice-calls/{call_id}/speech",
            json={"text": "你好", "synchronous": True},
            headers=auth_headers,
        )
        assert speech.status_code == 200, speech.get_json()
        assert speech.get_json()["ok"] is True
        events = client.get(
            f"/v1/xijian/voice-calls/{call_id}/events", headers=auth_headers
        ).get_json()["events"]
        assert any(e["payload"].get("role") == "assistant" for e in events)
        ended = client.post(
            f"/v1/xijian/voice-calls/{call_id}/end", headers=auth_headers
        ).get_json()
        assert ended["status"] == vc_stub.CALL_STATUS_ENDED

    def test_speech_with_audio_base64(self, client, auth_headers, character):
        created = client.post(
            "/v1/xijian/voice-calls",
            json={"character_id": character},
            headers=auth_headers,
        ).get_json()
        call_id = created["id"]
        client.post(f"/v1/xijian/voice-calls/{call_id}/accept", headers=auth_headers)
        payload = base64.b64encode(b"fake-audio").decode("ascii")
        res = client.post(
            f"/v1/xijian/voice-calls/{call_id}/speech",
            json={"audio_base64": payload, "synchronous": True},
            headers=auth_headers,
        )
        # 没有真实 STT 后端时，循环记录错误事件
        # 并返回 503 —— 这是设计好的降级路径。
        assert res.status_code in (200, 503)

    def test_song_route(self, client, auth_headers, character):
        created = client.post(
            "/v1/xijian/voice-calls",
            json={"character_id": character},
            headers=auth_headers,
        ).get_json()
        call_id = created["id"]
        client.post(f"/v1/xijian/voice-calls/{call_id}/accept", headers=auth_headers)
        res = client.post(
            f"/v1/xijian/voice-calls/{call_id}/song",
            json={"lyrics": "do re mi", "voice_part": "lead"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["status"] == "unavailable"
