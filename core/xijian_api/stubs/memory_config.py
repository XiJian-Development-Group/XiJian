"""Per-character memory configuration (A1.2 §character_memory_config).

Each character can override the defaults used by the context-aware
loader (:func:`xijian_api.stubs.memory.load_context`) and the recall
pipeline.  The schema mirrors the ``character_memory_config`` table in
the spec; sensible defaults are baked in so a fresh character "just
works" without an explicit config row.

Defaults
--------

``max_long_term``         200
``long_term_importance_min`` 0.6
``max_short_term``         50
``short_term_decay_rate``  0.05  (per hour, per spec)
``short_term_importance_min`` 0.3
``max_context_tokens``     8000
``reserve_tokens_for_reply`` 2000
``force_recall_on_history`` 1  (True)

The store is keyed by ``character_id``.  Reads return a defensive copy
so callers can't mutate the canonical record by accident.
"""

from __future__ import annotations

import threading
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.time import now_ts


_LOCK = threading.Lock()


#: Field → default value.  Mirrors the SQL column defaults in the spec.
DEFAULT_FIELDS: dict[str, Any] = {
    "max_long_term": 200,
    "long_term_importance_min": 0.6,
    "max_short_term": 50,
    "short_term_decay_rate": 0.05,
    "short_term_importance_min": 0.3,
    "max_context_tokens": 8000,
    "reserve_tokens_for_reply": 2000,
    "force_recall_on_history": 1,
}


#: Coercion rules — read schema from spec to keep validation in one
#: place.  Each tuple is ``(cast_fn, lower, upper)``.  ``None`` means
#: unbounded.
_FIELD_BOUNDS: dict[str, tuple] = {
    "max_long_term": (int, 0, 100_000),
    "long_term_importance_min": (float, 0.0, 1.0),
    "max_short_term": (int, 0, 100_000),
    "short_term_decay_rate": (float, 0.0, 1.0),
    "short_term_importance_min": (float, 0.0, 1.0),
    "max_context_tokens": (int, 100, 10_000_000),
    "reserve_tokens_for_reply": (int, 0, 10_000_000),
    "force_recall_on_history": (int, 0, 1),
}


def _default_config(character_id: str) -> dict[str, Any]:
    return {
        "object": "character_memory_config",
        "character_id": character_id,
        **dict(DEFAULT_FIELDS),
        "updated_at": now_ts(),
    }


def _coerce_field(name: str, value: Any) -> Any:
    cast_fn, lower, upper = _FIELD_BOUNDS[name]
    try:
        coerced = cast_fn(value)
    except (TypeError, ValueError):
        return DEFAULT_FIELDS[name]
    if lower is not None:
        coerced = max(lower, coerced)
    if upper is not None:
        coerced = min(upper, coerced)
    return coerced


def _normalise(payload: dict | None, *, character_id: str) -> dict[str, Any]:
    base = _default_config(character_id)
    for key in DEFAULT_FIELDS:
        if payload and key in payload and payload[key] is not None:
            base[key] = _coerce_field(key, payload[key])
    # ``force_recall_on_history`` is an int 0/1 flag — keep it
    # JSON-friendly so the route layer can return it verbatim.
    base["force_recall_on_history"] = int(base["force_recall_on_history"])
    base["updated_at"] = now_ts()
    return base


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get(character_id: str) -> dict[str, Any]:
    """Return the effective config for ``character_id``.

    If no record exists, return a freshly-built default — this matches
    the spec's "DEFAULT …" behaviour in the SQL DDL and means callers
    never have to special-case "no row".
    """
    record = state.memory_configs.get(character_id)
    if record is None:
        return _default_config(character_id)
    return dict(record)


def upsert(character_id: str, payload: dict | None = None) -> dict[str, Any]:
    """Create or update the config; returns the new effective state."""
    record = _normalise(payload or {}, character_id=character_id)
    with _LOCK:
        state.memory_configs[character_id] = record
    return dict(record)


def delete(character_id: str) -> bool:
    with _LOCK:
        return state.memory_configs.pop(character_id, None) is not None


def list_all() -> list[dict[str, Any]]:
    """Snapshot of every persisted config.

    Characters without an explicit row are *not* enumerated here —
    :func:`get` returns a default for them, but ``list_all`` only
    surfaces stored overrides.
    """
    return [dict(r) for r in state.memory_configs.values()]


__all__ = [
    "DEFAULT_FIELDS",
    "get",
    "upsert",
    "delete",
    "list_all",
]
