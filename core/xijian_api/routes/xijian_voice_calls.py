"""``/v1/xijian/voice-calls/*`` routes — A6 realtime calls.

Call sessions
=============

* ``GET    /v1/xijian/voice-calls``                        — list
                                                             (?character_id,
                                                              ?status,
                                                              ?direction)
* ``POST   /v1/xijian/voice-calls``                        — create
* ``GET    /v1/xijian/voice-calls/<call_id>``              — get
* ``POST   /v1/xijian/voice-calls/<call_id>/ring``         — offer the call
                                                             (idle → ringing)
* ``POST   /v1/xijian/voice-calls/<call_id>/accept``       — accept (→ active)
* ``POST   /v1/xijian/voice-calls/<call_id>/reject``       — reject (→ ended)
* ``POST   /v1/xijian/voice-calls/<call_id>/end``          — end (→ ended)

Call events & the full-duplex loop
==================================

* ``GET    /v1/xijian/voice-calls/<call_id>/events``       — event stream
                                                             (?kind, ?limit)
* ``POST   /v1/xijian/voice-calls/<call_id>/speech``       — feed user audio
                                                             (STT → AI → TTS)
                                                             body: {audio_base64}
                                                             or {text}
* ``POST   /v1/xijian/voice-calls/<call_id>/barge-in``     — set/clear the
                                                             AC-3 interrupt
                                                             flag {active: bool}
* ``POST   /v1/xijian/voice-calls/<call_id>/song``         — DiffSinger
                                                             singing stub
                                                             {lyrics,
                                                              voice_part}

WS push
=======

* ``call.state_changed`` — on every lifecycle transition
* ``call.event``         — on every appended call event

The state machine (idle → ringing → active → ended), barge-in semantics
and the DiffSinger stub all live in :mod:`xijian_api.stubs.voice_calls`.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import voice_calls as vc_stub


bp = Blueprint("xijian_voice_calls", __name__)
_LOGGER = logging.getLogger("xijian_api.routes.xijian_voice_calls")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_json() -> dict:
    """Return the parsed JSON body or raise a 400."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ApiError(
            400,
            "request body must be a JSON object",
            "invalid_request_error",
            code="invalid_body",
        )
    return body


def _error(exc: Exception) -> ApiError:
    """Map a stub VoiceCallError to an ApiError."""
    return ApiError(
        400, str(exc), "invalid_request_error", code="voice_call_error"
    )


def _get_or_404(call_id: str) -> dict:
    record = vc_stub.get_call(call_id)
    if record is None:
        raise ApiError(
            404, "voice call not found", "not_found_error",
            code="voice_call_not_found",
        )
    return record


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/voice-calls")
def list_voice_calls():
    args = request.args
    items = vc_stub.list_calls(
        character_id=args.get("character_id"),
        status=args.get("status"),
        direction=args.get("direction"),
    )
    return jsonify(paginate(items).to_dict())


@bp.post("/v1/xijian/voice-calls")
def create_voice_call():
    body = _require_json()
    character_id = body.get("character_id")
    if not isinstance(character_id, str) or not character_id:
        raise ApiError(
            400, "`character_id` is required", "invalid_request_error",
            code="missing_character_id", param="character_id",
        )
    try:
        record = vc_stub.create_call(
            character_id=character_id,
            direction=body.get("direction", vc_stub.DIRECTION_USER_INITIATED),
            user_id=body.get("user_id", "local_user"),
        )
    except vc_stub.VoiceCallError as exc:
        raise _error(exc)
    return jsonify(record), 201


@bp.get("/v1/xijian/voice-calls/<call_id>")
def get_voice_call(call_id: str):
    return jsonify(_get_or_404(call_id))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/voice-calls/<call_id>/ring")
def ring_voice_call(call_id: str):
    _get_or_404(call_id)
    try:
        return jsonify(vc_stub.ring(call_id))
    except vc_stub.VoiceCallError as exc:
        raise _error(exc)


@bp.post("/v1/xijian/voice-calls/<call_id>/accept")
def accept_voice_call(call_id: str):
    _get_or_404(call_id)
    try:
        return jsonify(vc_stub.accept_call(call_id))
    except vc_stub.VoiceCallError as exc:
        raise _error(exc)


@bp.post("/v1/xijian/voice-calls/<call_id>/reject")
def reject_voice_call(call_id: str):
    _get_or_404(call_id)
    try:
        return jsonify(vc_stub.reject_call(call_id))
    except vc_stub.VoiceCallError as exc:
        raise _error(exc)


@bp.post("/v1/xijian/voice-calls/<call_id>/end")
def end_voice_call(call_id: str):
    _get_or_404(call_id)
    try:
        return jsonify(vc_stub.end_call(call_id))
    except vc_stub.VoiceCallError as exc:
        raise _error(exc)


# ---------------------------------------------------------------------------
# Events & the call loop
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/voice-calls/<call_id>/events")
def list_call_events(call_id: str):
    _get_or_404(call_id)
    args = request.args
    try:
        limit = int(args.get("limit", 100))
    except ValueError:
        limit = 100
    return jsonify({
        "call_id": call_id,
        "events": vc_stub.list_events(
            call_id, kind=args.get("kind"), limit=limit
        ),
    })


@bp.post("/v1/xijian/voice-calls/<call_id>/speech")
def submit_speech(call_id: str):
    _get_or_404(call_id)
    body = _require_json()
    audio: bytes | None = None
    raw_b64 = body.get("audio_base64")
    if isinstance(raw_b64, str) and raw_b64:
        try:
            audio = base64.b64decode(raw_b64)
        except Exception:  # noqa: BLE001
            raise ApiError(
                400, "`audio_base64` is not valid base64",
                "invalid_request_error", code="invalid_audio", param="audio_base64",
            )
    text = body.get("text")
    if text is not None and not isinstance(text, str):
        raise ApiError(
            400, "`text` must be a string", "invalid_request_error",
            code="invalid_text", param="text",
        )
    try:
        out = vc_stub.submit_user_speech(
            call_id,
            audio=audio,
            text=text,
            language=body.get("language"),
            synchronous=bool(body.get("synchronous", False)),
        )
    except vc_stub.VoiceCallError as exc:
        raise _error(exc)
    if not out.get("ok"):
        return jsonify(out), 503
    return jsonify(out)


@bp.post("/v1/xijian/voice-calls/<call_id>/barge-in")
def set_barge_in(call_id: str):
    _get_or_404(call_id)
    body = _require_json()
    active = bool(body.get("active", True))
    try:
        if active:
            return jsonify(vc_stub.set_barge_in(call_id))
        return jsonify(vc_stub.clear_barge_in(call_id))
    except vc_stub.VoiceCallError as exc:
        raise _error(exc)


@bp.post("/v1/xijian/voice-calls/<call_id>/song")
def sing_song(call_id: str):
    _get_or_404(call_id)
    body = _require_json()
    lyrics = body.get("lyrics")
    if not isinstance(lyrics, str) or not lyrics:
        raise ApiError(
            400, "`lyrics` is required", "invalid_request_error",
            code="missing_lyrics", param="lyrics",
        )
    try:
        result = vc_stub.sing(
            call_id,
            lyrics,
            voice_part=body.get("voice_part", "lead"),
            melody=body.get("melody"),
            midi_path=body.get("midi_path"),
        )
    except vc_stub.VoiceCallError as exc:
        raise _error(exc)
    return jsonify(result)


__all__ = ["bp"]
