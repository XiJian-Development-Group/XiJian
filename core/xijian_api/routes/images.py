"""图像路由 — 生成 / 编辑 / 变体。"""

from __future__ import annotations

import base64

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.stubs import image as image_stub
from xijian_api.utils.params import parse_float, parse_int


bp = Blueprint("images", __name__)


def _xijian_character_id(payload: dict) -> str | None:
    """Safely extract ``xijian.character_id`` (guard non-dict ``xijian``).
    安全提取 ``xijian.character_id``（防御非字典的 ``xijian``）。"""
    xijian = payload.get("xijian")
    if isinstance(xijian, dict):
        return xijian.get("character_id")
    return None


@bp.post("/v1/images/generations")
def generations():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError(
            400,
            "Request body must be a JSON object",
            "invalid_request_error",
            code="invalid_request_body",
            param="body",
        )
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
        n=parse_int(payload.get("n"), "n", 1),
        size=payload.get("size", "1024x1024"),
        response_format=payload.get("response_format", "b64_json"),
        model=payload.get("model", "stub-image"),
        character_id=_xijian_character_id(payload),
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
        n=parse_int(request.form.get("n"), "n", 1),
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
        n=parse_int(request.form.get("n"), "n", 1),
        size=request.form.get("size", "1024x1024"),
        response_format=response_format,
    )
    return jsonify(response)


@bp.post("/v1/images/understanding")
def understanding():
    """Image understanding (vision) endpoint.

    Accepts an uploaded image (multipart ``image``) or base64-encoded image in
    JSON body, plus an optional ``prompt`` describing what to analyze.  Returns
    understanding text by delegating to the multimodal backend.

    图像理解（视觉）端点。

    接受上传的图像（multipart ``image``）或 JSON 请求体中的 base64 编码图像，
    以及可选的 ``prompt`` 描述要分析的内容。通过委托给全模态后端返回理解文本。
    """
    import base64 as _b64

    files = request.files
    payload = request.get_json(silent=True) or {}

    if files:
        # --- multipart upload: image file + optional prompt ---
        # --- 多部分上传：图像文件 + 可选提示 ---
        image_bytes = _read_uploaded_image("image")
        if not image_bytes:
            raise ApiError(
                400,
                "multipart `image` is required",
                "invalid_request_error",
                code="missing_image",
            )
        prompt = request.form.get("prompt", "Describe this image in detail.")
        model = request.form.get("model", "stub-multimodal")
        # Build data URI from the uploaded bytes.
        # 从上传的字节构建 data URI。
        b64_data = _b64.b64encode(image_bytes).decode("ascii")
        # Attempt MIME detection from the file field's content-type.
        # 尝试从文件字段的 content-type 检测 MIME。
        uploaded = request.files.get("image")
        mime = getattr(uploaded, "content_type", None) or "image/png"
        data_uri = f"data:{mime};base64,{b64_data}"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ]
    elif payload:
        # --- JSON body: base64 image URL or remote URL + optional prompt ---
        # --- JSON 请求体：base64 图像 URL 或远程 URL + 可选提示 ---
        image_url = payload.get("image") or payload.get("url", "")
        if not image_url:
            raise ApiError(
                400,
                "`image` (base64 or URL) is required in JSON body",
                "invalid_request_error",
                code="missing_image",
                param="image",
            )
        prompt = payload.get("prompt", "Describe this image in detail.")
        model = payload.get("model", "stub-multimodal")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ]
    else:
        raise ApiError(
            400,
            "image is required (multipart `image` or JSON `image` field)",
            "invalid_request_error",
            code="missing_image",
        )

    # Delegate to the multimodal stub for understanding.
    # 委托给全模态存根进行理解。
    from xijian_api.stubs.multimodal import understand as multimodal_understand

    result = multimodal_understand(
        messages,
        model=model,
        temperature=parse_float(payload.get("temperature"), "temperature", 0.7),
        max_tokens=payload.get("max_tokens"),
    )
    return jsonify(result)


__all__ = ["bp"]