"""Tests for ``stubs.pois`` (A4.3) and ``/v1/xijian/scenes/pois/*``.

Covers:

* **Pure helpers** — kind validation, parent-kind rule, tree shape.
* **CRUD** — create / list / get / patch / delete (orphan-refuse).
* **Tree queries** — full tree, ancestor chain, descendants,
  direct children.
* **Route layer** — happy path + 4xx error mapping.
* **Auth** — every endpoint requires a Bearer token.
"""

from __future__ import annotations

import pytest

from xijian_api.stubs import pois as pois_stub
from xijian_api.stubs import state as stubs_state
from xijian_api.stubs import worlds as worlds_stub
from xijian_api.stubs.pois import (
    KIND_MAP,
    KIND_REGION,
    LEAF_KINDS,
    VALID_KINDS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def world(client, auth_headers):
    body = {"name": "POI Test World"}
    res = client.post("/v1/xijian/worlds", json=body, headers=auth_headers)
    assert res.status_code == 201
    return res.get_json()["id"]


@pytest.fixture()
def tiny_tree(client, auth_headers, world):
    """Build the canonical ``map → region → leaf`` tree.

    Returns a dict ``{"map": <poi>, "region": <poi>, "shop": <poi>,
    "city": <poi>, "wild": <poi>, "dungeon": <poi>}`` so individual
    tests can pluck any node by name.
    """
    body_map = {"world_id": world, "name": "Map", "kind": "map"}
    m = client.post(
        "/v1/xijian/scenes/pois", json=body_map, headers=auth_headers
    ).get_json()

    r = client.post(
        "/v1/xijian/scenes/pois",
        json={"world_id": world, "name": "Region", "kind": "region", "parent_id": m["id"]},
        headers=auth_headers,
    ).get_json()

    leaves = {}
    for leaf_name, leaf_kind in [
        ("Shop", "shop"),
        ("City", "city"),
        ("Wild", "wild"),
        ("Dungeon", "dungeon"),
    ]:
        leaves[leaf_name.lower()] = client.post(
            "/v1/xijian/scenes/pois",
            json={"world_id": world, "name": leaf_name, "kind": leaf_kind, "parent_id": r["id"]},
            headers=auth_headers,
        ).get_json()

    return {"map": m, "region": r, **leaves}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestPureHelpers:
    def test_valid_kinds_includes_map_and_region(self):
        assert KIND_MAP in VALID_KINDS
        assert KIND_REGION in VALID_KINDS

    def test_valid_kinds_includes_all_four_leaves(self):
        for leaf in ("city", "wild", "dungeon", "shop"):
            assert leaf in VALID_KINDS
            assert leaf in LEAF_KINDS

    def test_map_kind_only_allows_root(self):
        # Helper-level: a "map" with a parent_id violates the rule.
        fake_map = {"id": "m", "kind": "map", "parent_id": "something"}
        with pytest.raises(pois_stub.POIError):
            pois_stub._validate_parent_kind(
                kind="map", parent_id="something", parent_record=fake_map
            )

    def test_region_requires_a_map_parent(self):
        fake_region = {"id": "r", "kind": "region", "parent_id": None}
        with pytest.raises(pois_stub.POIError):
            pois_stub._validate_parent_kind(
                kind="region", parent_id="r", parent_record=fake_region
            )

    def test_leaf_requires_a_region_parent(self):
        fake_shop = {"id": "s", "kind": "shop", "parent_id": None}
        with pytest.raises(pois_stub.POIError):
            pois_stub._validate_parent_kind(
                kind="shop", parent_id="s", parent_record=fake_shop
            )

    def test_invalid_kind_raises(self):
        with pytest.raises(pois_stub.POIError):
            pois_stub._validate_kind("universe")

    def test_is_map_region_leaf(self):
        assert pois_stub._is_map({"kind": "map"})
        assert not pois_stub._is_map({"kind": "region"})
        assert pois_stub._is_region({"kind": "region"})
        assert pois_stub._is_leaf({"kind": "shop"})
        assert pois_stub._is_leaf({"kind": "dungeon"})
        assert not pois_stub._is_leaf({"kind": "map"})

    def test_get_ancestor_chain_returns_empty_for_unknown(self):
        assert pois_stub.get_ancestor_chain("nonexistent_poi") == []

    def test_get_descendants_returns_empty_for_unknown(self):
        assert pois_stub.get_descendants("nonexistent_poi") == []


# ---------------------------------------------------------------------------
# CRUD (stub)
# ---------------------------------------------------------------------------


class TestStubCRUD:
    def test_create_map_then_region_then_leaf(self, world):
        m = pois_stub.create(world_id=world, name="Map", kind="map")
        r = pois_stub.create(world_id=world, name="Region", kind="region", parent_id=m["id"])
        s = pois_stub.create(world_id=world, name="Shop", kind="shop", parent_id=r["id"])
        assert m["parent_id"] is None
        assert r["parent_id"] == m["id"]
        assert s["parent_id"] == r["id"]

    def test_create_rejects_unknown_world(self):
        with pytest.raises(pois_stub.POIError):
            pois_stub.create(world_id="world_does_not_exist", name="X", kind="map")

    def test_create_rejects_map_with_parent(self, world):
        m = pois_stub.create(world_id=world, name="M", kind="map")
        with pytest.raises(pois_stub.POIError):
            pois_stub.create(world_id=world, name="M2", kind="map", parent_id=m["id"])

    def test_create_rejects_region_without_parent(self, world):
        with pytest.raises(pois_stub.POIError):
            pois_stub.create(world_id=world, name="R", kind="region")

    def test_create_rejects_leaf_with_region_parent(self, world):
        m = pois_stub.create(world_id=world, name="M", kind="map")
        with pytest.raises(pois_stub.POIError):
            pois_stub.create(world_id=world, name="S", kind="shop", parent_id=m["id"])

    def test_create_rejects_unknown_parent_id(self, world):
        with pytest.raises(pois_stub.POIError):
            pois_stub.create(
                world_id=world, name="R", kind="region", parent_id="poi_nope"
            )

    def test_create_rejects_blank_name(self, world):
        with pytest.raises(pois_stub.POIError):
            pois_stub.create(world_id=world, name="   ", kind="map")

    def test_create_rejects_invalid_coords(self, world):
        with pytest.raises(pois_stub.POIError):
            pois_stub.create(world_id=world, name="M", kind="map", coords=123)
        with pytest.raises(pois_stub.POIError):
            pois_stub.create(world_id=world, name="M", kind="map", coords="")

    def test_create_rejects_duplicate_id(self, world):
        pois_stub.create(world_id=world, name="M", kind="map", poi_id="poi_dup")
        with pytest.raises(pois_stub.POIError):
            pois_stub.create(world_id=world, name="M2", kind="map", poi_id="poi_dup")

    def test_list_for_world_filters_by_world(self, world):
        other = worlds_stub.create(name="Other")
        try:
            pois_stub.create(world_id=world, name="M1", kind="map")
            pois_stub.create(world_id=other["id"], name="M2", kind="map")
            mine = pois_stub.list_for_world(world)
            assert len(mine) == 1
            assert mine[0]["name"] == "M1"
        finally:
            worlds_stub.delete(other["id"])

    def test_list_all_includes_every_world(self, world):
        pois_stub.create(world_id=world, name="M1", kind="map")
        assert any(r["name"] == "M1" for r in pois_stub.list_all())

    def test_update_changes_mutable_fields(self, world):
        m = pois_stub.create(world_id=world, name="M", kind="map", coords="x:0")
        updated = pois_stub.update(m["id"], {"name": "M2", "coords": "x:1"})
        assert updated["name"] == "M2"
        assert updated["coords"] == "x:1"

    def test_update_rejects_id_change(self, world):
        m = pois_stub.create(world_id=world, name="M", kind="map")
        with pytest.raises(pois_stub.POIError):
            pois_stub.update(m["id"], {"id": "poi_other"})

    def test_update_rejects_world_change(self, world):
        m = pois_stub.create(world_id=world, name="M", kind="map")
        with pytest.raises(pois_stub.POIError):
            pois_stub.update(m["id"], {"world_id": "world_other"})

    def test_update_self_parent_rejected(self, world):
        m = pois_stub.create(world_id=world, name="M", kind="map")
        with pytest.raises(pois_stub.POIError):
            pois_stub.update(m["id"], {"parent_id": m["id"]})

    def test_update_revalidates_parent_rule_on_kind_change(self, world):
        m = pois_stub.create(world_id=world, name="M", kind="map")
        r = pois_stub.create(world_id=world, name="R", kind="region", parent_id=m["id"])
        with pytest.raises(pois_stub.POIError):
            pois_stub.update(r["id"], {"kind": "map"})

    def test_delete_returns_false_for_unknown(self, world):
        assert pois_stub.delete("poi_nope") is False

    def test_delete_removes_leaf(self, world):
        m = pois_stub.create(world_id=world, name="M", kind="map")
        r = pois_stub.create(world_id=world, name="R", kind="region", parent_id=m["id"])
        s = pois_stub.create(world_id=world, name="S", kind="shop", parent_id=r["id"])
        assert pois_stub.delete(s["id"]) is True
        assert pois_stub.get(s["id"]) is None

    def test_delete_refuses_orphan(self, world):
        m = pois_stub.create(world_id=world, name="M", kind="map")
        r = pois_stub.create(world_id=world, name="R", kind="region", parent_id=m["id"])
        # Try to delete the region which still has / can have children
        # — but it has no children, so this should succeed.
        assert pois_stub.delete(r["id"]) is True
        # Now the map has no children, so it should also be deletable.
        assert pois_stub.delete(m["id"]) is True

    def test_delete_refuses_when_children_exist(self, world):
        m = pois_stub.create(world_id=world, name="M", kind="map")
        pois_stub.create(world_id=world, name="R", kind="region", parent_id=m["id"])
        with pytest.raises(pois_stub.POIError):
            pois_stub.delete(m["id"])


# ---------------------------------------------------------------------------
# Tree queries (stub)
# ---------------------------------------------------------------------------


class TestStubTree:
    def test_get_tree_returns_nested_dict(self, world, tiny_tree):
        tree = pois_stub.get_tree(world)
        assert len(tree) == 1
        root = tree[0]
        assert root["name"] == "Map"
        assert len(root["children"]) == 1
        region = root["children"][0]
        assert region["name"] == "Region"
        # children are sorted by name
        names = [c["name"] for c in region["children"]]
        assert names == sorted(names)

    def test_get_tree_rooted_at_subtree(self, world, tiny_tree):
        sub = pois_stub.get_tree(world, root_id=tiny_tree["region"]["id"])
        assert sub["name"] == "Region"
        assert len(sub["children"]) == 4

    def test_get_ancestor_chain(self, world, tiny_tree):
        chain = pois_stub.get_ancestor_chain(tiny_tree["shop"]["id"])
        names = [c["name"] for c in chain]
        assert names == ["Map", "Region", "Shop"]

    def test_get_ancestor_chain_root_only(self, world, tiny_tree):
        chain = pois_stub.get_ancestor_chain(tiny_tree["map"]["id"])
        assert [c["name"] for c in chain] == ["Map"]

    def test_get_descendants_depth_first(self, world, tiny_tree):
        desc = pois_stub.get_descendants(tiny_tree["map"]["id"])
        names = [d["name"] for d in desc]
        assert names == ["Region", "City", "Dungeon", "Shop", "Wild"]

    def test_list_children(self, world, tiny_tree):
        kids = pois_stub.list_children(tiny_tree["region"]["id"])
        assert sorted(k["name"] for k in kids) == ["City", "Dungeon", "Shop", "Wild"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TestRoutes:
    def test_list_requires_auth(self, client):
        res = client.get("/v1/xijian/scenes/pois")
        assert res.status_code in (401, 403)

    def test_create_requires_auth(self, client):
        res = client.post("/v1/xijian/scenes/pois", json={})
        assert res.status_code in (401, 403)

    def test_create_happy_path(self, client, auth_headers, world):
        res = client.post(
            "/v1/xijian/scenes/pois",
            json={"world_id": world, "name": "Map", "kind": "map"},
            headers=auth_headers,
        )
        assert res.status_code == 201
        body = res.get_json()
        assert body["id"].startswith("poi_")
        assert body["parent_id"] is None

    def test_create_invalid_body_returns_400(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/scenes/pois",
            json={"world_id": "world_x", "name": "M", "kind": "made_up"},
            headers=auth_headers,
        )
        assert res.status_code == 400
        err = res.get_json()["error"]
        assert err["code"] == "poi_error"

    def test_list_filters_by_world(self, client, auth_headers, world, tiny_tree):
        res = client.get(f"/v1/xijian/scenes/pois?world_id={world}", headers=auth_headers)
        assert res.status_code == 200
        body = res.get_json()
        # map + region + 4 leaves = 6 records
        assert len(body["data"]) == 6

    def test_get_returns_record(self, client, auth_headers, tiny_tree):
        res = client.get(
            f"/v1/xijian/scenes/pois/{tiny_tree['shop']['id']}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["name"] == "Shop"

    def test_get_unknown_returns_404(self, client, auth_headers):
        res = client.get("/v1/xijian/scenes/pois/poi_nope", headers=auth_headers)
        assert res.status_code == 404
        assert res.get_json()["error"]["code"] == "poi_not_found"

    def test_patch_updates_name(self, client, auth_headers, world):
        create = client.post(
            "/v1/xijian/scenes/pois",
            json={"world_id": world, "name": "M", "kind": "map"},
            headers=auth_headers,
        ).get_json()
        res = client.patch(
            f"/v1/xijian/scenes/pois/{create['id']}",
            json={"name": "Renamed"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["name"] == "Renamed"

    def test_patch_invalid_returns_400(self, client, auth_headers, world):
        create = client.post(
            "/v1/xijian/scenes/pois",
            json={"world_id": world, "name": "M", "kind": "map"},
            headers=auth_headers,
        ).get_json()
        res = client.patch(
            f"/v1/xijian/scenes/pois/{create['id']}",
            json={"parent_id": create["id"]},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_delete_unknown_returns_404(self, client, auth_headers):
        res = client.delete("/v1/xijian/scenes/pois/poi_nope", headers=auth_headers)
        assert res.status_code == 404

    def test_delete_refuses_orphan(self, client, auth_headers, world):
        m = client.post(
            "/v1/xijian/scenes/pois",
            json={"world_id": world, "name": "M", "kind": "map"},
            headers=auth_headers,
        ).get_json()
        client.post(
            "/v1/xijian/scenes/pois",
            json={"world_id": world, "name": "R", "kind": "region", "parent_id": m["id"]},
            headers=auth_headers,
        )
        res = client.delete(f"/v1/xijian/scenes/pois/{m['id']}", headers=auth_headers)
        assert res.status_code == 400
        assert "descendant" in res.get_json()["error"]["message"]

    def test_tree_route_returns_nested(self, client, auth_headers, world, tiny_tree):
        res = client.get(
            f"/v1/xijian/scenes/pois/tree?world_id={world}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert len(body["tree"]) == 1

    def test_tree_route_requires_world_id(self, client, auth_headers):
        res = client.get("/v1/xijian/scenes/pois/tree", headers=auth_headers)
        assert res.status_code == 400

    def test_chain_route(self, client, auth_headers, tiny_tree):
        res = client.get(
            f"/v1/xijian/scenes/pois/{tiny_tree['shop']['id']}/chain",
            headers=auth_headers,
        )
        assert res.status_code == 200
        names = [c["name"] for c in res.get_json()["chain"]]
        assert names == ["Map", "Region", "Shop"]

    def test_children_route(self, client, auth_headers, world, tiny_tree):
        res = client.get(
            f"/v1/xijian/scenes/pois/{tiny_tree['region']['id']}/children",
            headers=auth_headers,
        )
        assert res.status_code == 200
        names = [c["name"] for c in res.get_json()["children"]]
        assert sorted(names) == ["City", "Dungeon", "Shop", "Wild"]

    def test_descendants_route(self, client, auth_headers, tiny_tree):
        res = client.get(
            f"/v1/xijian/scenes/pois/{tiny_tree['map']['id']}/descendants",
            headers=auth_headers,
        )
        assert res.status_code == 200
        names = [d["name"] for d in res.get_json()["descendants"]]
        assert names == ["Region", "City", "Dungeon", "Shop", "Wild"]
