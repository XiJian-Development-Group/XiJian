"""Audio routes — speech / transcriptions / translations."""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.stubs import audio as audio_stub


bp = Blueprint("audio", __name__)


@bp.post("/v1/audio/speech")
def speech():
    payload = request.get_json(silent=True) or {}
    if "input" not in payload:
        raise ApiError(
            400,
            "`input` is required",
            "invalid_request_error",
            code="missing_input",
            param="input",
        )
    voice = payload.get("voice", "default")
    response_format = payload.get("response_format", "mp3")
    data = audio_stub.synth(payload["input"], voice=voice, response_format=response_format)
    mime = {
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "opus": "audio/ogg",
        "flac": "audio/flac",
        "pcm": "audio/pcm",
    }.get(response_format, "application/octet-stream")
    return Response(data, mimetype=mime)


def _read_uploaded_audio() -> bytes:
    """Pull a single file from a multipart upload (or fall back to raw body)."""
    files = request.files
    if files:
        first = next(iter(files.values()))
        return first.read()
    return request.get_data(cache=True) or b""


@bp.post("/v1/audio/transcriptions")
def transcriptions():
    if not request.files and not request.get_data():
        raise ApiError(
            400,
            "audio file is required (multipart `file` or raw body)",
            "invalid_request_error",
            code="missing_file",
        )
    payload = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
    response_format = payload.get("response_format", "json")
    result = audio_stub.transcribe(_read_uploaded_audio(), response_format=response_format)
    if response_format == "text":
        return Response(result, mimetype="text/plain; charset=utf-8")
    return jsonify(result)


@bp.post("/v1/audio/translations")
def translations():
    if not request.files and not request.get_data():
        raise ApiError(
            400,
            "audio file is required (multipart `file` or raw body)",
            "invalid_request_error",
            code="missing_file",
        )
    payload = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
    response_format = payload.get("response_format", "json")
    result = audio_stub.translate(_read_uploaded_audio(), response_format=response_format)
    if response_format == "text":
        return Response(result, mimetype="text/plain; charset=utf-8")
    return jsonify(result)


__all__ = ["bp"]