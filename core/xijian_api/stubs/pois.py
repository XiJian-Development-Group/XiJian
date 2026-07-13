"""Stub POI service — A4.3 in the function list v2.

A "POI" (point of interest) is one node in a world's three-level
spatial hierarchy.  Per the SQL schema in §A4.3:

* **map**     — top-level continent / dimension ("提瓦特大陆").
* **region**  — a sub-area under a map ("蒙德" / "璃月").
* **poi**     — a concrete location under a region ("天使的馈赠酒馆" /
                "风起地").

The ``parent_id`` field drives the tree:

* ``parent_id is None``  ⇒ this record is a map.
* ``parent.parent_id is None``  ⇒ this record is a region.
* otherwise              ⇒ this record is a leaf POI.

Kinds
=====

The schema constrains ``kind`` to one of four values:

* ``"map"``      — top-level continent.  ``parent_id`` must be ``None``.
* ``"region"``   — a sub-area.  ``parent_id`` must point to a ``"map"``.
* ``"city"``     — a settlement / town (leaf).  ``parent_id`` must
                   point to a ``"region"``.
* ``"wild"``     — a wild area (leaf).  Same parent rules as ``"city"``.
* ``"dungeon"``  — a dungeon instance (leaf).  Same parent rules.
* ``"shop"``     — a point-of-sale inside a region (leaf).  Same rules.

We allow the spec's "city / wild / dungeon / shop" leaf kinds and add
``"map"`` / ``"region"`` to make the hierarchy navigable.  The kind
rules above are enforced at write time so operators can't accidentally
build a region that nests under a region or a shop that has no region
in between.

Coords
======

``coords`` is a free-form string.  The contract says it is "JSON:
x,y or polygon" but the spec doesn't pin a grammar.  We keep it as a
plain string so the operator can store whatever their world tooling
produces (``"x:0.5,y:0.7"``, ``"polygon:[(0,0),(1,0),(1,1)]"``, etc.)
and validate it's a non-empty string when present.

Buckets
=======

* :data:`xijian_api.stubs.state.pois` — ``{poi_id: dict}`` — the only
  bucket.  Per the SQL schema the fields are: id, world_id, parent_id,
  name, kind, coords, description.

Test surface
============

Pure helpers (no I/O):

* :func:`_validate_kind`
* :func:`_validate_parent_kind` — enforces the three-level rule.
* :func:`_is_map` / :func:`_is_region` / :func:`_is_leaf`
* :func:`get_tree` — returns the full nested tree under a root.
* :func:`get_ancestor_chain` — root-most-first list ending at the POI.
* :func:`get_descendants` — flat list of every descendant (depth-first).

Side-effecting functions (CRUD):

* :func:`create` / :func:`get` / :func:`list_for_world` /
  :func:`list_all` / :func:`update` / :func:`delete`
* :func:`seed_default` / :func:`reset_for_testing`
"""

from __future__ import annotations

from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_poi_id
from xijian_api.utils.time import now_ts


#: Allowed ``kind`` values.  Maps and regions are the two non-leaf
#: tiers; the rest are leaves.
VALID_KINDS = frozenset({"map", "region", "city", "wild", "dungeon", "shop"})

#: Convenience aliases used by tests / route handlers.
KIND_MAP = "map"
KIND_REGION = "region"
LEAF_KINDS = frozenset({"city", "wild", "dungeon", "shop"})


class POIError(ValueError):
    """Raised on any POI validation / lookup failure."""


# ---------------------------------------------------------------------------
# Pure helpers (no I/O)
# ---------------------------------------------------------------------------


def _validate_kind(kind: Any) -> str:
    if not isinstance(kind, str):
        raise POIError(f"kind must be a string, got {type(kind).__name__}")
    if kind not in VALID_KINDS:
        raise POIError(
            f"invalid kind {kind!r}; must be one of {sorted(VALID_KINDS)}"
        )
    return kind


def _is_map(record: dict) -> bool:
    return record.get("kind") == KIND_MAP


def _is_region(record: dict) -> bool:
    return record.get("kind") == KIND_REGION


def _is_leaf(record: dict) -> bool:
    return record.get("kind") in LEAF_KINDS


def _validate_parent_kind(
    *,
    kind: str,
    parent_id: str | None,
    parent_record: dict | None,
) -> None:
    """Enforce the three-level rule.

    * ``kind == "map"``     ⇒ ``parent_id`` must be ``None``.
    * ``kind == "region"``  ⇒ ``parent_id`` must point to a ``"map"``.
    * leaf kinds            ⇒ ``parent_id`` must point to a ``"region"``.

    A non-existent parent id always raises — the caller must create
    the ancestor first.
    """
    if kind == KIND_MAP:
        if parent_id is not None:
            raise POIError(
                f"map POIs must have parent_id=None, got {parent_id!r}"
            )
        return
    if kind == KIND_REGION:
        if parent_id is None:
            raise POIError("region POIs must have a parent_id pointing to a map")
        if parent_record is None:
            raise POIError(f"parent {parent_id!r} not found")
        if not _is_map(parent_record):
            raise POIError(
                f"region's parent must be a map, got kind {parent_record.get('kind')!r}"
            )
        return
    # Leaf kind.
    if parent_id is None:
        raise POIError(f"leaf POIs ({kind!r}) must have a parent_id pointing to a region")
    if parent_record is None:
        raise POIError(f"parent {parent_id!r} not found")
    if not _is_region(parent_record):
        raise POIError(
            f"leaf POI's parent must be a region, got kind {parent_record.get('kind')!r}"
        )


def get_tree(world_id: str, root_id: str | None = None) -> dict | list:
    """Return the nested tree of POIs for ``world_id``.

    If ``root_id`` is ``None``, returns a list of all top-level maps
    (i.e. every record with ``parent_id is None``) and their
    descendants.  Otherwise returns the subtree rooted at ``root_id``
    as a single nested dict.

    The returned shape is ``{"id": ..., "name": ..., "kind": ...,
    "children": [<subtree>...]}`` — ``children`` is always a list,
    possibly empty.
    """
    if root_id is None:
        return [
            get_tree(world_id, root_id=rec["id"])
            for rec in state.pois.values()
            if rec.get("world_id") == world_id and rec.get("parent_id") is None
        ]
    record = state.pois.get(root_id)
    if record is None or record.get("world_id") != world_id:
        raise POIError(f"root {root_id!r} not found in world {world_id!r}")
    children = [
        get_tree(world_id, root_id=child_id)
        for child_id, child_rec in state.pois.items()
        if child_rec.get("parent_id") == root_id
    ]
    children.sort(key=lambda c: c["name"])
    return {
        "id": record["id"],
        "name": record["name"],
        "kind": record["kind"],
        "children": children,
    }


def get_ancestor_chain(poi_id: str) -> list[dict]:
    """Return ``[root, ..., parent, self]`` (root-most first).

    Useful for breadcrumbs in the UI.  Returns an empty list if
    ``poi_id`` is unknown so callers can distinguish "doesn't exist"
    from "is a root".
    """
    record = state.pois.get(poi_id)
    if record is None:
        return []
    chain: list[dict] = []
    cursor: dict | None = record
    seen: set[str] = set()
    while cursor is not None and cursor["id"] not in seen:
        seen.add(cursor["id"])
        chain.append(cursor)
        parent_id = cursor.get("parent_id")
        cursor = state.pois.get(parent_id) if parent_id else None
    chain.reverse()
    return chain


def get_descendants(poi_id: str) -> list[dict]:
    """Return a flat, depth-first list of every descendant of ``poi_id``.

    Excludes ``poi_id`` itself.  Order: depth-first, name tie-break.
    """
    root = state.pois.get(poi_id)
    if root is None:
        return []
    out: list[dict] = []

    def _walk(node_id: str) -> None:
        kids = [
            rec for rec in state.pois.values()
            if rec.get("parent_id") == node_id
        ]
        kids.sort(key=lambda r: r.get("name", ""))
        for kid in kids:
            out.append(kid)
            _walk(kid["id"])

    _walk(poi_id)
    return out


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def _validate_coords(coords: Any) -> str | None:
    """Coerce ``coords`` to a non-empty string or ``None``."""
    if coords is None:
        return None
    if not isinstance(coords, str):
        raise POIError(f"coords must be a string, got {type(coords).__name__}")
    if not coords.strip():
        raise POIError("coords must not be blank")
    return coords


def _validate_name(name: Any) -> str:
    if not isinstance(name, str):
        raise POIError(f"name must be a string, got {type(name).__name__}")
    name = name.strip()
    if not name:
        raise POIError("name must not be blank")
    return name


def _validate_description(description: Any) -> str:
    if description is None:
        return ""
    if not isinstance(description, str):
        raise POIError(
            f"description must be a string, got {type(description).__name__}"
        )
    return description


def _validate_world_id(world_id: Any) -> str:
    if not isinstance(world_id, str) or not world_id.strip():
        raise POIError("world_id must be a non-empty string")
    return world_id


def create(
    *,
    world_id: str,
    name: str,
    kind: str,
    parent_id: str | None = None,
    coords: str | None = None,
    description: str = "",
    poi_id: str | None = None,
) -> dict:
    """Insert a new POI.  Returns the stored record.

    Raises :class:`POIError` on validation failure or when:

    * the world does not exist;
    * the parent_id violates the three-level rule;
    * the new id collides with an existing record.
    """
    world_id = _validate_world_id(world_id)
    name = _validate_name(name)
    kind = _validate_kind(kind)
    coords = _validate_coords(coords)
    description = _validate_description(description)

    if world_id not in state.worlds:
        raise POIError(f"world {world_id!r} does not exist")

    parent_record = state.pois.get(parent_id) if parent_id else None
    _validate_parent_kind(
        kind=kind, parent_id=parent_id, parent_record=parent_record
    )

    new_id = poi_id or gen_poi_id()
    if new_id in state.pois:
        raise POIError(f"poi id {new_id!r} already exists")

    record = {
        "id": new_id,
        "world_id": world_id,
        "parent_id": parent_id,
        "name": name,
        "kind": kind,
        "coords": coords,
        "description": description,
        "created_at": now_ts(),
    }
    state.pois[new_id] = record
    return dict(record)


def get(poi_id: str) -> dict | None:
    return state.pois.get(poi_id)


def get_required(poi_id: str) -> dict:
    record = state.pois.get(poi_id)
    if record is None:
        raise POIError(f"poi {poi_id!r} not found")
    return record


def list_for_world(world_id: str) -> list[dict]:
    return [
        dict(rec) for rec in state.pois.values()
        if rec.get("world_id") == world_id
    ]


def list_all() -> list[dict]:
    return [dict(rec) for rec in state.pois.values()]


def list_children(parent_id: str) -> list[dict]:
    """Return direct children of ``parent_id`` (one level down)."""
    return [
        dict(rec) for rec in state.pois.values()
        if rec.get("parent_id") == parent_id
    ]


def update(poi_id: str, patch: dict) -> dict | None:
    """Patch mutable fields.  ``id`` and ``world_id`` are immutable.

    If the patch changes ``parent_id`` or ``kind`` we re-validate the
    three-level rule; otherwise it's a shallow field update.
    """
    if not isinstance(patch, dict):
        raise POIError("patch must be a dict")
    record = state.pois.get(poi_id)
    if record is None:
        return None
    if "id" in patch and patch["id"] != poi_id:
        raise POIError("id is immutable; create a new POI")
    if "world_id" in patch and patch["world_id"] != record["world_id"]:
        raise POIError("world_id is immutable; create a new POI")

    # Apply validation to incoming fields.
    new_name = _validate_name(patch["name"]) if "name" in patch else record["name"]
    new_kind = (
        _validate_kind(patch["kind"]) if "kind" in patch else record["kind"]
    )
    new_coords = (
        _validate_coords(patch["coords"])
        if "coords" in patch
        else record["coords"]
    )
    new_description = (
        _validate_description(patch["description"])
        if "description" in patch
        else record["description"]
    )
    new_parent_id = (
        patch["parent_id"] if "parent_id" in patch else record["parent_id"]
    )
    if new_parent_id == poi_id:
        raise POIError("a POI cannot be its own parent")

    # Re-validate the parent rule.
    parent_record = (
        state.pois.get(new_parent_id) if new_parent_id else None
    )
    _validate_parent_kind(
        kind=new_kind, parent_id=new_parent_id, parent_record=parent_record
    )

    record["name"] = new_name
    record["kind"] = new_kind
    record["coords"] = new_coords
    record["description"] = new_description
    record["parent_id"] = new_parent_id
    return dict(record)


def delete(poi_id: str) -> bool:
    """Remove the POI.  Refuses if any other POI has it as parent.

    The SQL schema doesn't define ON DELETE behaviour explicitly, but
    deleting a node with children would leave them dangling.  We
    raise rather than silently orphan them.
    """
    record = state.pois.get(poi_id)
    if record is None:
        return False
    children = [
        rec for rec in state.pois.values()
        if rec.get("parent_id") == poi_id
    ]
    if children:
        names = ", ".join(sorted(c.get("name", "?") for c in children))
        raise POIError(
            f"cannot delete {poi_id!r}: {len(children)} descendant(s) "
            f"still reference it ({names})"
        )
    del state.pois[poi_id]
    return True


# ---------------------------------------------------------------------------
# Seed / reset
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """No-op seed.

    Per the v2 spec, the world library is operator-curated, so we
    intentionally do not pre-populate any default POIs.  The function
    exists so :func:`xijian_api.stubs.seed_all` has a stable hook to
    call.
    """


def reset_for_testing() -> None:
    state.pois.clear()


__all__ = [
    "POIError",
    "VALID_KINDS",
    "KIND_MAP",
    "KIND_REGION",
    "LEAF_KINDS",
    "create",
    "get",
    "get_required",
    "list_for_world",
    "list_all",
    "list_children",
    "update",
    "delete",
    "get_tree",
    "get_ancestor_chain",
    "get_descendants",
    "seed_default",
    "reset_for_testing",
]
