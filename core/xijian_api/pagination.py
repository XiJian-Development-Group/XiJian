"""Cursor-based pagination helpers.

Implements the OAI-style pagination contract from ``DESIGN.md`` §7:

* Query string parameters: ``limit`` (default 20, max 100), ``order``
  (``"asc"`` or ``"desc"``, default ``"asc"``), ``after``, ``before``.
* Returned shape: ``{"object": "list", "data": [...], "has_more": ...,
  "first_id": ..., "last_id": ...}``.

The function is intentionally framework-agnostic: it inspects
``request.args`` directly so it works inside Flask views.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from flask import request

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


@dataclass
class Page:
    """A single page of items.

    Attributes
    ----------
    data:
        The items on this page (already sliced and ordered).
    has_more:
        ``True`` if there are more items after this page.
    first_id:
        The id of the first item, or ``None`` if the page is empty.
    last_id:
        The id of the last item, or ``None`` if the page is empty.
    object:
        Always ``"list"`` — kept on the dataclass so callers can build
        the envelope without remembering the constant.
    """

    data: list = field(default_factory=list)
    has_more: bool = False
    first_id: Optional[str] = None
    last_id: Optional[str] = None
    object: str = "list"

    def to_dict(self) -> dict[str, Any]:
        """Render as the OAI list-envelope dict."""
        return {
            "object": self.object,
            "data": self.data,
            "has_more": self.has_more,
            "first_id": self.first_id,
            "last_id": self.last_id,
        }


def _coerce_limit(raw: Optional[str]) -> int:
    """Parse and clamp the ``limit`` query parameter."""
    if raw is None:
        return DEFAULT_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    if value < 1:
        return 1
    if value > MAX_LIMIT:
        return MAX_LIMIT
    return value


def _coerce_order(raw: Optional[str]) -> str:
    """Parse the ``order`` query parameter; defaults to ``"asc"``."""
    if raw is None:
        return "asc"
    value = raw.lower()
    if value not in {"asc", "desc"}:
        return "asc"
    return value


def _item_id(item: Any) -> Optional[str]:
    """Best-effort extraction of an id from ``item``.

    Accepts dicts with an ``"id"`` key, dataclasses with an ``id``
    attribute, or objects with a string representation that starts
    with a known id prefix.
    """
    if isinstance(item, dict):
        value = item.get("id")
        return value if isinstance(value, str) else None
    return getattr(item, "id", None)


def paginate(items: list, request_obj=None) -> Page:
    """Return a :class:`Page` for ``items`` based on the current request.

    Parameters
    ----------
    items:
        The full collection (already filtered as appropriate).
    request_obj:
        Optional Flask request (defaults to the active ``flask.request``).

    Notes
    -----
    Cursor semantics:

    * ``after=<id>`` keeps items whose id sorts strictly *after* the
      cursor (asc) or strictly *before* (desc).
    * ``before=<id>`` is the symmetric opposite — items before the
      cursor (asc) or after it (desc).
    * When ``after`` and ``before`` are both supplied, ``after`` wins
      (matches the OAI Files/Batches convention).
    """
    req = request_obj if request_obj is not None else request
    args = req.args if req is not None else {}

    limit = _coerce_limit(args.get("limit"))
    order = _coerce_order(args.get("order"))
    after = args.get("after")
    before = args.get("before")
    cursor = after if after is not None else before

    # Build (id, item) pairs, then sort by id (None ids sort to the end).
    # Sorting the pair list (rather than the items directly) keeps ``id``
    # available for cursor filtering and for first_id / last_id derivation
    # without re-walking the slice.
    pairs: list[tuple[Optional[str], Any]] = [(_item_id(it), it) for it in items]

    if order == "desc":
        pairs.sort(key=lambda p: (p[0] is None, p[0]), reverse=True)
    else:
        pairs.sort(key=lambda p: (p[0] is None, p[0]))

    # Apply cursor against id strings. Items with a None id are dropped
    # when a cursor is provided (they have no stable position relative
    # to the cursor).
    if cursor is not None:
        if order == "desc":
            pairs = [p for p in pairs if p[0] is not None and p[0] < cursor]
        else:
            pairs = [p for p in pairs if p[0] is not None and p[0] > cursor]

    # ``has_more``: peek at limit + 1 to know if there is more.
    page_slice = pairs[: limit + 1]
    has_more = len(page_slice) > limit
    page_slice = page_slice[:limit]

    data = [item for _id, item in page_slice]
    ids = [_id for _id, _item in page_slice if _id is not None]
    first_id = ids[0] if ids else None
    last_id = ids[-1] if ids else None

    return Page(
        data=data,
        has_more=has_more,
        first_id=first_id,
        last_id=last_id,
    )


__all__ = ["Page", "paginate", "DEFAULT_LIMIT", "MAX_LIMIT"]