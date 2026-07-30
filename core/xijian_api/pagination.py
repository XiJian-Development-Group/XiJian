"""Cursor-based pagination helpers.

基于游标的分页辅助函数。

Implements the OAI-style pagination contract from ``DESIGN.md`` §7:

实现 ``DESIGN.md`` §7 中的 OAI 风格分页契约：

* Query string parameters: ``limit`` (default 20, max 100), ``order``
  (``"asc"`` or ``"desc"``, default ``"asc"``), ``after``, ``before``.
  查询字符串参数：``limit``（默认 20，最大 100）、``order``
  （``"asc"`` 或 ``"desc"``，默认 ``"asc"``）、``after``、``before``。
* Returned shape: ``{"object": "list", "data": [...], "has_more": ...,
  "first_id": ..., "last_id": ...}``.
  返回格式：``{"object": "list", "data": [...], "has_more": ...,
  "first_id": ..., "last_id": ...}``。

The function is intentionally framework-agnostic: it inspects
``request.args`` directly so it works inside Flask views.

该函数有意设计为框架无关：它直接检查 ``request.args``，
因此可以在 Flask 视图内部工作。
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

    单个分页的项目列表。

    Attributes
    ----------
    data:
        The items on this page (already sliced and ordered).
        此页面上的项目（已切片和排序）。
    has_more:
        ``True`` if there are more items after this page.
        如果此页之后还有更多项目则为 ``True``。
    first_id:
        The id of the first item, or ``None`` if the page is empty.
        第一个项目的 ID，如果页面为空则为 ``None``。
    last_id:
        The id of the last item, or ``None`` if the page is empty.
        最后一个项目的 ID，如果页面为空则为 ``None``。
    object:
        Always ``"list"`` — kept on the dataclass so callers can build
        the envelope without remembering the constant.
        始终为 ``"list"`` — 保留在数据类上，使调用者无需记住常量即可构建信封。
    """

    data: list = field(default_factory=list)
    has_more: bool = False
    first_id: Optional[str] = None
    last_id: Optional[str] = None
    object: str = "list"

    def to_dict(self) -> dict[str, Any]:
        """Render as the OAI list-envelope dict.

        渲染为 OAI 列表信封字典。
        """
        return {
            "object": self.object,
            "data": self.data,
            "has_more": self.has_more,
            "first_id": self.first_id,
            "last_id": self.last_id,
        }


def _coerce_limit(raw: Optional[str]) -> int:
    """Parse and clamp the ``limit`` query parameter.

    解析并钳制 ``limit`` 查询参数。
    """
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
    """Parse the ``order`` query parameter; defaults to ``"asc"``.

    解析 ``order`` 查询参数；默认为 ``"asc"``。
    """
    if raw is None:
        return "asc"
    value = raw.lower()
    if value not in {"asc", "desc"}:
        return "asc"
    return value


def _item_id(item: Any) -> Optional[str]:
    """Best-effort extraction of an id from ``item``.

    从 ``item`` 中尽力提取 ID。

    Accepts dicts with an ``"id"`` key, dataclasses with an ``id``
    attribute, or objects with a string representation that starts
    with a known id prefix.

    接受带有 ``"id"`` 键的字典、带有 ``id`` 属性的数据类，
    或字符串表示以已知 ID 前缀开头的对象。
    """
    if isinstance(item, dict):
        value = item.get("id")
        return value if isinstance(value, str) else None
    return getattr(item, "id", None)


def paginate(items: list, request_obj=None) -> Page:
    """Return a :class:`Page` for ``items`` based on the current request.

    根据当前请求为 ``items`` 返回一个 :class:`Page`。

    Parameters
    ----------
    items:
        The full collection (already filtered as appropriate).
        完整的集合（已按需过滤）。
    request_obj:
        Optional Flask request (defaults to the active ``flask.request``).
        可选的 Flask 请求对象（默认为当前的 ``flask.request``）。

    Notes
    -----
    注意
    -----

    Cursor semantics:

    游标语义：

    * ``after=<id>`` keeps items whose id sorts strictly *after* the
      cursor (asc) or strictly *before* (desc).
      保留 ID 排序严格在游标 *之后*（升序）或严格 *之前*（降序）的项目。
    * ``before=<id>`` is the symmetric opposite — items before the
      cursor (asc) or after it (desc).
      是上述的对称相反——游标之前（升序）或之后（降序）的项目。
    * When ``after`` and ``before`` are both supplied, ``after`` wins
      (matches the OAI Files/Batches convention).
      当同时提供 ``after`` 和 ``before`` 时，``after`` 优先
      （与 OAI Files/Batches 约定一致）。
    """
    req = request_obj if request_obj is not None else request
    args = req.args if req is not None else {}

    limit = _coerce_limit(args.get("limit"))
    order = _coerce_order(args.get("order"))
    after = args.get("after")
    before = args.get("before")
    cursor = after if after is not None else before

    # Build (id, item) pairs, then sort by id (None ids sort to the end).
    # 构建 (id, item) 对，然后按 id 排序（None id 排序到末尾）。
    # Sorting the pair list (rather than the items directly) keeps ``id``
    # available for cursor filtering and for first_id / last_id derivation
    # without re-walking the slice.
    # 排序对列表（而非直接排序项目）使 ``id`` 可用于游标过滤以及
    # first_id / last_id 的推导，无需重新遍历切片。
    pairs: list[tuple[Optional[str], Any]] = [(_item_id(it), it) for it in items]

    if order == "desc":
        pairs.sort(key=lambda p: (p[0] is None, p[0]), reverse=True)
    else:
        pairs.sort(key=lambda p: (p[0] is None, p[0]))

    # Apply cursor against id strings. Items with a None id are dropped
    # when a cursor is provided (they have no stable position relative
    # to the cursor).
    # 根据 ID 字符串应用游标。当提供游标时，ID 为 None 的项目被丢弃
    # （它们相对于游标没有稳定的位置）。
    if cursor is not None:
        if order == "desc":
            pairs = [p for p in pairs if p[0] is not None and p[0] < cursor]
        else:
            pairs = [p for p in pairs if p[0] is not None and p[0] > cursor]

    # ``has_more``: peek at limit + 1 to know if there is more.
    # ``has_more``：查看 limit + 1 以确定是否还有更多。
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
