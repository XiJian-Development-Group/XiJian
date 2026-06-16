"""Image routes — generations / edits / variations."""

from __future__ import annotations

import base64

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.stubs import image as image_stub


bp = Blueprint("images", __name__)


@bp.post("/v1/images/generations")
def generations():
    payload = request.get_json(silent=True) or {}
    if "prompt" not in payload:
        raise ApiError(
            400,
            "`prompt` is required",
            "invalid_request_error",
            code="missing_prompt",
            param="prompt",
        )
    response = image_stub.generate(
        payload["prompt"],
        n=int(payload.get("n", 1)),
        size=payload.get("size", "1024x1024"),
        response_format=payload.get("response_format", "b64_json"),
        model=payload.get("model", "stub-image"),
    )
    return jsonify(response)


def _read_uploaded_image(field: str = "image") -> bytes:
    files = request.files
    if field in files:
        return files[field].read()
    if files:
        first = next(iter(files.values()))
        return first.read()
    return b""


@bp.post("/v1/images/edits")
def edits():
    image_bytes = _read_uploaded_image("image")
    if not image_bytes:
        raise ApiError(
            400,
            "multipart `image` is required",
            "invalid_request_error",
            code="missing_image",
        )
    prompt = request.form.get("prompt") or (request.get_json(silent=True) or {}).get("prompt", "")
    if not prompt:
        raise ApiError(
            400,
            "`prompt` is required",
            "invalid_request_error",
            code="missing_prompt",
            param="prompt",
        )
    response_format = request.form.get("response_format", "b64_json")
    response = image_stub.edit(
        image_bytes,
        prompt,
        n=int(request.form.get("n", 1)),
        size=request.form.get("size", "1024x1024"),
        response_format=response_format,
    )
    return jsonify(response)


@bp.post("/v1/images/variations")
def variations():
    image_bytes = _read_uploaded_image("image")
    if not image_bytes:
        raise ApiError(
            400,
            "multipart `image` is required",
            "invalid_request_error",
            code="missing_image",
        )
    response_format = request.form.get("response_format", "b64_json")
    response = image_stub.variation(
        image_bytes,
        n=int(request.form.get("n", 1)),
        size=request.form.get("size", "1024x1024"),
        response_format=response_format,
    )
    return jsonify(response)


__all__ = ["bp"]
# Tiny reference to base64 keeps the import path meaningful for future
# edits that want to decode uploaded content.
_ = base64.b64encode