"""Stub voice-call service — A6 in the function list v2.

A6 实时通话：全双工语音通话的**会话管理层**。底层素材（STT/TTS 单点
能力）已经存在于 :mod:`xijian_api.stubs.audio`（转发到
``ai/backends/*/stt.py`` / ``tts.py``）；本模块把它们组装成可运行的
通话循环框架：

* ``voice_calls``  — 通话会话记录（call_id / 角色 / 用户 / 方向 /
  状态机 idle→ringing→active→ended / 时间戳 / 时长 / 录音路径）。
* ``call_events``  — 通话内事件流（speech / motion / effect / song /
  barge_in），payload 按规范以 JSON 存储。

数据模型镜像 §A6 的 SQL 建表语句：``voice_calls`` 字段
(id / character_id / user_id / started_at / ended_at / duration_sec /
direction / recording_path) + 状态机扩展字段 (status / created_at /
updated_at)；``call_events`` 字段 (id / call_id / kind / payload /
created_at)。

通话循环（全双工编排，AC-1 的 < 1.5s 端到端延迟由真实后端保证，
本层只负责编排与状态）：

1. 用户语音到达 → :func:`submit_user_speech` 把音频字节交给
   ``stubs.audio.transcribe``（STT）。
2. 若 TTS 正在播放 → 触发 barge-in（置 ``barge_in_active`` 标志并
   记录 ``barge_in`` 事件，AC-3：打断后 AI 必须能基于上下文继续）。
3. 回复生成 → 通过可插拔的回复处理器（:func:`set_reply_handler`，
   默认回显占位）得到文本，再调用 ``stubs.audio.synth``（TTS）
   得到音频字节，写入 ``speech`` 事件。默认后台线程执行（全双工），
   ``synchronous=True`` 同线程执行（测试用）。

唱歌（DiffSinger）：:func:`sing` 是接口桩 —— 声部选择 / 歌词输入 /
调用点齐备；真实引擎接入走 :func:`set_sing_engine` 钩子。devkit 的
``devkit.voice_cloner.generate_singing`` 已具备 DiffSinger 引擎封装，
正式版把钩子接到它即可；本桩默认返回 ``unavailable`` 并记录事件。

barge-in 语义（AC-3）：打断 = 「新语音输入到达时中断当前 TTS 播放」。
实现为通话记录上的 ``barge_in_active`` 标志 + ``barge_in`` 事件；回复
线程在每段 TTS 输出前检查该标志，被打断则停止并记录 ``interrupted``
事件，上下文（对话轮次）保留在通话记录里供后续继续。

WS 推送：状态迁移发 ``call.state_changed``，事件落库发 ``call.event``，
全部经 :func:`xijian_api.routes.ws_routes.publish_event` 尽力而为广播
（与 events.py 同一姿态，WS 未接线时静默降级）。

环境变量：无。通话循环本身不跑常驻后台线程（回复线程由
:func:`submit_user_speech` 按需派生，daemon）。
"""

from __future__ import annotations

import base64
import logging
import threading
from typing import Any, Callable

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_call_event_id, gen_voice_call_id
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.voice_calls")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Call lifecycle states (spec §A6 state machine).
CALL_STATUS_IDLE = "idle"
CALL_STATUS_RINGING = "ringing"
CALL_STATUS_ACTIVE = "active"
CALL_STATUS_ENDED = "ended"

ALL_CALL_STATUSES: tuple[str, ...] = (
    CALL_STATUS_IDLE,
    CALL_STATUS_RINGING,
    CALL_STATUS_ACTIVE,
    CALL_STATUS_ENDED,
)

#: Direction values (spec §A6).
DIRECTION_USER_INITIATED = "user_initiated"
DIRECTION_CHARACTER_INITIATED = "character_initiated"

ALL_DIRECTIONS: tuple[str, ...] = (
    DIRECTION_USER_INITIATED,
    DIRECTION_CHARACTER_INITIATED,
)

#: Event kinds (spec §A6) + the barge-in lifecycle helper.
KIND_SPEECH = "speech"
KIND_MOTION = "motion"
KIND_EFFECT = "effect"
KIND_SONG = "song"
KIND_BARGE_IN = "barge_in"

ALL_EVENT_KINDS: tuple[str, ...] = (
    KIND_SPEECH,
    KIND_MOTION,
    KIND_EFFECT,
    KIND_SONG,
    KIND_BARGE_IN,
)

#: Default fallback reply when no reply handler is registered.
DEFAULT_FALLBACK_REPLY = "（通话循环框架已就绪：请注册回复处理器以获得真实回复。）"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class VoiceCallError(ValueError):
    """Raised on call validation / lifecycle errors."""


# ---------------------------------------------------------------------------
# Pluggable hooks (reply generation + singing engine)
# ---------------------------------------------------------------------------

#: ``fn(call_id, user_text) -> str`` — the "AI 模块" in the A6 loop.
#: Defaults to :data:`DEFAULT_FALLBACK_REPLY`.  The chat pipeline
#: (or a real backend) can register here.
_reply_handler: Callable[[str, str], str] | None = None

#: ``fn(call_id, lyrics, voice_part, melody, midi_path) -> dict`` —
#: DiffSinger 引擎接入点。devkit 的 ``generate_singing`` 是正式版
#: 的天然实现；默认钩子返回 ``unavailable``。
_sing_handler: Callable[..., dict] | None = None


def set_reply_handler(fn: Callable[[str, str], str] | None) -> None:
    """Register the reply generator used by the call loop.

    注册通话循环使用的回复生成器。签名：``fn(call_id, user_text) -> str``。
    传 ``None`` 恢复默认回显占位。
    """
    global _reply_handler
    _reply_handler = fn


def set_sing_engine(fn: Callable[..., dict] | None) -> None:
    """Register the singing (DiffSinger) engine hook.

    注册唱歌（DiffSinger）引擎钩子。正式版把
    ``devkit.voice_cloner.generate_singing``（或等价实现）接在这里；
    签名：``fn(call_id, lyrics, voice_part, melody, midi_path) -> dict``。
    传 ``None`` 恢复默认 ``unavailable`` 桩。
    """
    global _sing_handler
    _sing_handler = fn


# ---------------------------------------------------------------------------
# WS broadcast (best-effort, same posture as events.py)
# ---------------------------------------------------------------------------


def _publish(event_type: str, data: dict[str, Any]) -> None:
    try:
        from xijian_api.routes.ws_routes import publish_event
        publish_event(event_type, data)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("voice_calls WS publish failed: %s", event_type)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _now_or(value: float | None) -> float:
    return float(value) if value is not None else now_ts()


def _new_call_record(
    *,
    character_id: str,
    user_id: str,
    direction: str,
    call_id: str,
    status: str,
    now: float,
) -> dict:
    return {
        "id": call_id,
        "character_id": character_id,
        "user_id": user_id,
        "direction": direction,
        "status": status,
        "started_at": now,
        "ended_at": None,
        "duration_sec": None,
        "recording_path": None,
        "ended_reason": None,
        "barge_in_active": False,
        "tts_busy": False,
        "current_turn": 0,
        "dialogue_context": [],  # bounded — AC-3 上下文续接
        "created_at": now,
        "updated_at": now,
    }


def _validate_direction(direction: str) -> None:
    if direction not in ALL_DIRECTIONS:
        raise VoiceCallError(
            f"direction must be one of {ALL_DIRECTIONS}, got {direction!r}"
        )


def _require_call(call_id: str) -> dict:
    record = state.voice_calls.get(call_id)
    if record is None:
        raise VoiceCallError("call not found")
    return record


def _public_call_view(record: dict) -> dict:
    """JSON-friendly view of a call (what the WS layer / routes serve)."""
    return {
        "call_id": record.get("id"),
        "character_id": record.get("character_id"),
        "user_id": record.get("user_id"),
        "direction": record.get("direction"),
        "status": record.get("status"),
        "started_at": record.get("started_at"),
        "ended_at": record.get("ended_at"),
        "duration_sec": record.get("duration_sec"),
        "ended_reason": record.get("ended_reason"),
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_call(
    *,
    character_id: str,
    direction: str = DIRECTION_USER_INITIATED,
    user_id: str = "local_user",
    call_id: str | None = None,
    now: float | None = None,
) -> dict:
    """Create a call session in ``idle`` state.

    创建一个 ``idle`` 状态的通话会话。``direction`` 决定是用户发起
    （``user_initiated``）还是角色主动来电（``character_initiated``，
    配合 :func:`ring` 进入响铃态）。
    """
    _validate_direction(direction)
    if not character_id:
        raise VoiceCallError("character_id is required")
    timestamp = _now_or(now)
    new_id = call_id or gen_voice_call_id()
    if new_id in state.voice_calls:
        raise VoiceCallError(f"call id {new_id!r} already exists")
    record = _new_call_record(
        character_id=character_id,
        user_id=user_id,
        direction=direction,
        call_id=new_id,
        status=CALL_STATUS_IDLE,
        now=timestamp,
    )
    state.voice_calls[new_id] = record
    _publish("call.state_changed", _public_call_view(record))
    return record


def get_call(call_id: str) -> dict | None:
    """Return the call record or ``None``."""
    return state.voice_calls.get(call_id)


def list_calls(
    *,
    character_id: str | None = None,
    status: str | None = None,
    direction: str | None = None,
) -> list[dict]:
    """List call records, newest first.  Optional filters."""
    items = list(state.voice_calls.values())
    if character_id:
        items = [it for it in items if it.get("character_id") == character_id]
    if status:
        items = [it for it in items if it.get("status") == status]
    if direction:
        items = [it for it in items if it.get("direction") == direction]
    items.sort(key=lambda r: r.get("started_at", 0), reverse=True)
    return items


# ---------------------------------------------------------------------------
# Lifecycle state machine (idle → ringing → active → ended)
# ---------------------------------------------------------------------------


def _transition(
    call_id: str,
    new_status: str,
    *,
    ended_reason: str | None = None,
) -> dict:
    """Apply a state transition with event + WS broadcast."""
    record = _require_call(call_id)
    record["status"] = new_status
    if new_status == CALL_STATUS_ENDED:
        if record.get("ended_at") is None:
            record["ended_at"] = now_ts()
        started = float(record.get("started_at") or record["ended_at"])
        record["duration_sec"] = max(0, int(record["ended_at"] - started))
        record["ended_reason"] = ended_reason
        record["tts_busy"] = False
        record["barge_in_active"] = False
    record["updated_at"] = now_ts()
    add_event(
        call_id,
        KIND_EFFECT,
        {"kind": "state_change", "to": new_status, "ended_reason": ended_reason},
        publish=False,
    )
    _publish("call.state_changed", _public_call_view(record))
    return record


def ring(call_id: str) -> dict:
    """Offer an incoming call: ``idle`` → ``ringing``."""
    record = _require_call(call_id)
    if record["status"] not in (CALL_STATUS_IDLE, CALL_STATUS_RINGING):
        raise VoiceCallError(
            f"cannot ring a call in status {record['status']!r}"
        )
    return _transition(call_id, CALL_STATUS_RINGING)


def accept_call(call_id: str) -> dict:
    """Accept the call: ``idle``/``ringing`` → ``active`` (idempotent)."""
    record = _require_call(call_id)
    if record["status"] == CALL_STATUS_ENDED:
        raise VoiceCallError("call already ended")
    if record["status"] == CALL_STATUS_ACTIVE:
        return record
    return _transition(call_id, CALL_STATUS_ACTIVE)


def reject_call(call_id: str) -> dict:
    """Reject the call: ``idle``/``ringing`` → ``ended`` (reason=rejected)."""
    record = _require_call(call_id)
    if record["status"] == CALL_STATUS_ENDED:
        return record  # idempotent
    if record["status"] == CALL_STATUS_ACTIVE:
        raise VoiceCallError("cannot reject an active call; end it instead")
    return _transition(call_id, CALL_STATUS_ENDED, ended_reason="rejected")


def end_call(call_id: str) -> dict:
    """End an active call: → ``ended`` (reason=ended), computes duration."""
    record = _require_call(call_id)
    if record["status"] == CALL_STATUS_ENDED:
        return record
    return _transition(call_id, CALL_STATUS_ENDED, ended_reason="ended")


# ---------------------------------------------------------------------------
# Call events
# ---------------------------------------------------------------------------


def add_event(
    call_id: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    now: float | None = None,
    publish: bool = True,
) -> dict:
    """Append an event to a call's event stream.

    ``payload`` 按规范以 JSON 存储（stub 里直接存 dict，DictDB 落库时
    即序列化为 JSON TEXT）。``kind`` 限定在 §A6 的四种 + ``barge_in``。
    """
    record = _require_call(call_id)
    if kind not in ALL_EVENT_KINDS:
        raise VoiceCallError(
            f"event kind must be one of {ALL_EVENT_KINDS}, got {kind!r}"
        )
    event = {
        "id": gen_call_event_id(),
        "call_id": call_id,
        "kind": kind,
        "payload": payload or {},
        "created_at": _now_or(now),
    }
    state.call_events[event["id"]] = event
    record["updated_at"] = now_ts()
    if publish:
        _publish("call.event", {
            "call_id": call_id,
            "event_id": event["id"],
            "kind": kind,
            "payload": event["payload"],
        })
    return event


def list_events(
    call_id: str,
    *,
    kind: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """List a call's events, oldest first.  Optional ``kind`` filter."""
    _require_call(call_id)
    items = [it for it in state.call_events.values() if it.get("call_id") == call_id]
    if kind:
        items = [it for it in items if it.get("kind") == kind]
    items.sort(key=lambda e: e.get("created_at", 0))
    return items[-limit:] if limit and limit > 0 else items


# ---------------------------------------------------------------------------
# Barge-in (AC-3)
# ---------------------------------------------------------------------------


def is_barge_in(call_id: str) -> bool:
    """Return whether the call currently has an active barge-in flag."""
    record = _require_call(call_id)
    return bool(record.get("barge_in_active"))


def set_barge_in(call_id: str, active: bool = True) -> dict:
    """Set / clear the barge-in flag.

    打断 = 「新语音输入到达时中断当前 TTS 播放」。置位时记录一条
    ``barge_in`` 事件；回复线程在每段 TTS 输出前检查该标志。
    """
    record = _require_call(call_id)
    record["barge_in_active"] = bool(active)
    record["updated_at"] = now_ts()
    add_event(call_id, KIND_BARGE_IN, {
        "active": bool(active),
        "interrupted_turn": record.get("current_turn"),
    })
    return record


def clear_barge_in(call_id: str) -> dict:
    """Clear the barge-in flag (idempotent)."""
    return set_barge_in(call_id, False)


# ---------------------------------------------------------------------------
# Call loop orchestration — 全双工语音流编排层
# ---------------------------------------------------------------------------


def _generate_reply(call_id: str, user_text: str) -> str:
    """Produce the assistant reply text for a turn.

    可插拔：默认回显占位；注册 :func:`set_reply_handler` 后走真实
    "AI 模块"（聊天管线 / 后端）。
    """
    if _reply_handler is not None:
        try:
            return _reply_handler(call_id, user_text)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("reply handler failed: %s", exc)
    return DEFAULT_FALLBACK_REPLY


def _synth_audio(text: str) -> tuple[bytes | None, str | None]:
    """TTS via ``stubs.audio.synth``.  Returns ``(audio_bytes, error)``."""
    try:
        from xijian_api.stubs import audio as audio_stub
        data = audio_stub.synth(text)
        return data, None
    except Exception as exc:  # noqa: BLE001 — 后端不可用等
        _LOGGER.warning("call TTS failed: %s", exc)
        return None, f"{type(exc).__name__}: {exc}"


def _respond_sync(record: dict, turn: int, user_text: str) -> tuple[str, dict | None]:
    """Run one assistant turn (reply + TTS) synchronously.

    每段输出前检查 ``barge_in_active`` —— 被打断则停止并记录
    ``interrupted`` 事件（AC-3 语义）。TTS 失败不阻塞循环：仍记录
    ``speech`` 事件（无音频），通话继续。
    """
    call_id = record["id"]
    if record.get("barge_in_active"):
        record["barge_in_active"] = False
        add_event(call_id, KIND_SPEECH, {
            "role": "assistant",
            "text": "",
            "turn": turn,
            "interrupted": True,
            "reason": "barge_in",
        })
        return "", None

    reply = _generate_reply(call_id, user_text)
    record["tts_busy"] = True
    audio, tts_error = _synth_audio(reply)

    # 输出前再次检查打断标志（TTS 生成期间用户可能已再次说话）。
    interrupted = bool(record.get("barge_in_active"))
    if interrupted:
        record["barge_in_active"] = False
        record["tts_busy"] = False
        add_event(call_id, KIND_SPEECH, {
            "role": "assistant",
            "text": reply,
            "turn": turn,
            "interrupted": True,
            "reason": "barge_in",
        })
        return reply, None

    payload: dict[str, Any] = {
        "role": "assistant",
        "text": reply,
        "turn": turn,
        "tts_error": tts_error,
    }
    if audio:
        payload["audio_base64"] = base64.b64encode(audio).decode("ascii")
        payload["audio_size_bytes"] = len(audio)
    record["tts_busy"] = False
    record.setdefault("dialogue_context", []).append({"role": "assistant", "text": reply})
    record["dialogue_context"] = record["dialogue_context"][-20:]
    record["updated_at"] = now_ts()
    event = add_event(call_id, KIND_SPEECH, payload)
    return reply, event


def _respond_async(record: dict, turn: int, user_text: str) -> None:
    """Background worker for the full-duplex path (daemon thread)."""
    try:
        _respond_sync(record, turn, user_text)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("call %s respond worker failed: %s", record.get("id"), exc)


def submit_user_speech(
    call_id: str,
    audio: bytes | None = None,
    *,
    text: str | None = None,
    language: str | None = None,
    synchronous: bool = False,
) -> dict:
    """Feed one user speech segment into the call loop (STT → AI → TTS).

    **STT**：``audio`` 字节交给 ``stubs.audio.transcribe``（真实后端）；
    显式传 ``text`` 可跳过 STT（开发/测试快捷路径）。后端不可用时记录
    ``speech`` 错误事件并返回结构化错误，不抛异常（通话必须能继续）。

    **Barge-in**：若 TTS 正忙（``tts_busy``），置位打断标志。

    **回复**：默认后台线程执行（全双工）；``synchronous=True`` 时
    同线程执行（测试用）。

    返回：``{"ok": True, "turn": n, "user_text": ..., "reply": ...,
    "interrupted_previous": ...}``。
    """
    record = _require_call(call_id)
    if record["status"] != CALL_STATUS_ACTIVE:
        raise VoiceCallError(
            f"call is not active (status={record['status']!r})"
        )

    # 1) STT（可跳过）
    user_text = text
    stt_error: str | None = None
    if user_text is None:
        if not audio:
            raise VoiceCallError("either audio bytes or text is required")
        try:
            from xijian_api.stubs import audio as audio_stub
            result = audio_stub.transcribe(
                audio, response_format="text", language=language
            )
            user_text = str(result or "").strip()
        except Exception as exc:  # noqa: BLE001 — 后端不可用等
            stt_error = f"{type(exc).__name__}: {exc}"
            _LOGGER.warning("call %s STT failed: %s", call_id, stt_error)
            add_event(call_id, KIND_SPEECH, {
                "role": "user", "error": stt_error,
            })
            return {"ok": False, "error": stt_error, "turn": record["current_turn"]}
    if not user_text:
        user_text = ""

    # 2) Barge-in：新语音到达且 TTS 正忙 → 打断当前播放
    interrupted = False
    if record.get("tts_busy"):
        set_barge_in(call_id, True)
        interrupted = True

    record["current_turn"] = int(record.get("current_turn", 0)) + 1
    turn = record["current_turn"]
    record.setdefault("dialogue_context", []).append({"role": "user", "text": user_text})
    record["dialogue_context"] = record["dialogue_context"][-20:]  # bounded
    record["updated_at"] = now_ts()

    user_event = add_event(call_id, KIND_SPEECH, {
        "role": "user",
        "text": user_text,
        "turn": turn,
        "interrupted_previous": interrupted,
    })

    if synchronous:
        reply, reply_event = _respond_sync(record, turn, user_text)
        reply_event_id = reply_event["id"] if reply_event else None
    else:
        thread = threading.Thread(
            target=_respond_async,
            args=(record, turn, user_text),
            name=f"xijian-call-{call_id}-turn-{turn}",
            daemon=True,
        )
        thread.start()
        reply, reply_event_id = "", None

    return {
        "ok": True,
        "turn": turn,
        "user_text": user_text,
        "reply": reply,
        "interrupted_previous": interrupted,
        "user_event_id": user_event["id"],
        "reply_event_id": reply_event_id,
        "synchronous": synchronous,
    }


# ---------------------------------------------------------------------------
# Singing (DiffSinger interface stub — AC-3 / US-A6-03)
# ---------------------------------------------------------------------------

#: Singing voice parts (声部选择).
VOICE_PARTS: tuple[str, ...] = ("lead", "harmony", "bass", "alto", "tenor")


def _default_sing_engine(
    call_id: str,
    lyrics: str,
    voice_part: str,
    melody: dict[str, Any] | None,
    midi_path: str | None,
) -> dict:
    """Default DiffSinger stub — records a ``song`` event and reports
    ``unavailable``.

    **正式版接入点**：把 :func:`set_sing_engine` 接到
    ``devkit.voice_cloner.generate_singing``（已封装 DiffSingerEngine，
    需要 work_dir / character_id / 模型已下载）。真实合成需要
    MIDI 或程序化旋律参数，且模型文件在桌侧 —— 因此本桩不伪造音频，
    只把请求落库成 ``song`` 事件供客户端展示状态。
    """
    add_event(call_id, KIND_SONG, {
        "lyrics": lyrics,
        "voice_part": voice_part,
        "melody": melody or {},
        "midi_path": midi_path,
        "status": "unavailable",
        "reason": "diffsinger_engine_not_wired",
    })
    return {
        "ok": False,
        "status": "unavailable",
        "reason": "diffsinger_engine_not_wired",
        "message": (
            "DiffSinger 唱歌引擎未接入：请注册 set_sing_engine 钩子"
            "（正式版对接 devkit.voice_cloner.generate_singing）。"
        ),
    }


def sing(
    call_id: str,
    lyrics: str,
    *,
    voice_part: str = "lead",
    melody: dict[str, Any] | None = None,
    midi_path: str | None = None,
) -> dict:
    """Request the character to sing (US-A6-03).

    接口桩：声部选择（:data:`VOICE_PARTS`）/ 歌词输入 / 调用点齐备。
    若注册了唱歌引擎钩子则转发；否则走 :func:`_default_sing_engine`。
    """
    record = _require_call(call_id)
    if record["status"] != CALL_STATUS_ACTIVE:
        raise VoiceCallError(
            f"call is not active (status={record['status']!r})"
        )
    if not lyrics or not isinstance(lyrics, str):
        raise VoiceCallError("lyrics is required")
    if voice_part not in VOICE_PARTS:
        raise VoiceCallError(
            f"voice_part must be one of {VOICE_PARTS}, got {voice_part!r}"
        )
    if _sing_handler is not None:
        try:
            result = _sing_handler(
                call_id, lyrics, voice_part, melody, midi_path
            )
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "status": "error", "reason": str(exc)}
        add_event(call_id, KIND_SONG, {
            "lyrics": lyrics,
            "voice_part": voice_part,
            "melody": melody or {},
            "midi_path": midi_path,
            "status": result.get("status", "error"),
        })
        return result
    return _default_sing_engine(call_id, lyrics, voice_part, melody, midi_path)


# ---------------------------------------------------------------------------
# Seed / reset
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.  A6 keeps no default call records —
    calls are ephemeral, user-driven sessions.  Hook kept for the
    package-level seed entry point."""
    return None


def reset_for_testing() -> None:
    """Wipe call buckets and clear pluggable hooks."""
    state.voice_calls.clear()
    state.call_events.clear()
    set_reply_handler(None)
    set_sing_engine(None)


__all__ = [
    # Constants
    "CALL_STATUS_IDLE", "CALL_STATUS_RINGING", "CALL_STATUS_ACTIVE",
    "CALL_STATUS_ENDED", "ALL_CALL_STATUSES",
    "DIRECTION_USER_INITIATED", "DIRECTION_CHARACTER_INITIATED",
    "ALL_DIRECTIONS",
    "KIND_SPEECH", "KIND_MOTION", "KIND_EFFECT", "KIND_SONG",
    "KIND_BARGE_IN", "ALL_EVENT_KINDS",
    "VOICE_PARTS",
    "DEFAULT_FALLBACK_REPLY",
    # Errors
    "VoiceCallError",
    # Hooks
    "set_reply_handler", "set_sing_engine",
    # CRUD
    "create_call", "get_call", "list_calls",
    # Lifecycle
    "ring", "accept_call", "reject_call", "end_call",
    # Events
    "add_event", "list_events",
    # Barge-in
    "is_barge_in", "set_barge_in", "clear_barge_in",
    # Loop
    "submit_user_speech",
    # Singing
    "sing",
    # Lifecycle
    "seed_default", "reset_for_testing",
]
