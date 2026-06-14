"""Stub embeddings — deterministic, hash-seeded fake vectors."""

from __future__ import annotations

import hashlib
import math

from xijian_api.utils.time import now_ts

DEFAULT_DIMENSIONS = 1536


def _seeded_vector(text: str, dim: int) -> list[float]:
    """Return a deterministic, unit-ish vector seeded by ``text``.

    We hash the input and use successive bytes as floats in ``[-1, 1]``,
    then normalise to a unit vector so cosine similarity behaves.
    """
    digest = hashlib.sha512(text.encode("utf-8")).digest()
    raw: list[float] = []
    for i in range(dim):
        byte = digest[i % len(digest)]
        raw.append((byte / 255.0) * 2.0 - 1.0)
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / norm for x in raw]


def embed(
    texts: list[str],
    *,
    model: str = "stub-embedding",
    dimensions: int | None = None,
    encoding_format: str = "float",
) -> dict:
    """Return an OAI-style embeddings payload."""
    if isinstance(texts, str):
        texts = [texts]
    dim = dimensions or DEFAULT_DIMENSIONS
    data = [
        {
            "object": "embedding",
            "index": idx,
            "embedding": _seeded_vector(text, dim),
        }
        for idx, text in enumerate(texts)
    ]
    return {
        "object": "list",
        "data": data,
        "model": model,
        "usage": {
            "prompt_tokens": sum(len(t) for t in texts),
            "total_tokens": sum(len(t) for t in texts),
        },
        "xijian": {"dimensions": dim, "created": now_ts()},
    }


def dimensions() -> int:
    return DEFAULT_DIMENSIONS


__all__ = ["embed", "dimensions", "DEFAULT_DIMENSIONS"]