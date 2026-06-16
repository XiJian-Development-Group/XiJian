"""Embeddings route — ``POST /v1/embeddings``."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.stubs import embedding as embedding_stub


bp = Blueprint("embeddings", __name__)


@bp.post("/v1/embeddings")
def embeddings():
    payload = request.get_json(silent=True) or {}
    if "input" not in payload:
        raise ApiError(
            400,
            "`input` is required",
            "invalid_request_error",
            code="missing_input",
            param="input",
        )
    model = payload.get("model", "stub-embedding")
    response = embedding_stub.embed(
        payload["input"],
        model=model,
        dimensions=payload.get("dimensions"),
        encoding_format=payload.get("encoding_format", "float"),
    )
    return jsonify(response)


__all__ = ["bp"]