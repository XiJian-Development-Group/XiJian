"""Tests for ``stubs.world_currencies`` (A4.4) and the
``/v1/xijian/currencies/*`` endpoints.

Covers:

* **Pure helpers** — code/name/decimals validation.
* **CRUD** — create / list / get / patch / delete with cascade.
* **Lazy default** — :func:`ensure_currency` materialises
  placeholders so the orchestrator can land transactions against
  unknown codes without crashing.
* **Auth** — every endpoint requires a Bearer token.
"""

from __future__ import annotations

import pytest

from xijian_api.stubs import state as stubs_state
from xijian_api.stubs import world_currencies as currency_stub
from xijian_api.stubs.world_currencies import (
    DEFAULT_DECIMALS,
    MAX_DECIMALS,
    MIN_DECIMALS,
    CurrencyError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def world(client, auth_headers):
    body = {"name": "Currency Test World"}
    res = client.post("/v1/xijian/worlds", json=body, headers=auth_headers)
    assert res.status_code == 201
    return res.get_json()["id"]


@pytest.fixture()
def currency(client, auth_headers, world):
    body = {
        "world_id": world,
        "code": "mora",
        "name": "Mora",
        "symbol": "M",
        "decimals": 0,
    }
    res = client.post("/v1/xijian/currencies", json=body, headers=auth_headers)
    assert res.status_code == 201
    return res.get_json()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestValidateCode:
    def test_simple(self):
        assert currency_stub._validate_code("mora") == "mora"

    def test_underscore(self):
        assert currency_stub._validate_code("big_gold") == "big_gold"

    def test_uppercase(self):
        assert currency_stub._validate_code("GOLD") == "GOLD"

    def test_alphanumeric(self):
        assert currency_stub._validate_code("Coin1") == "Coin1"

    @pytest.mark.parametrize("bad", ["", "mor a", "mor-a", "mor!", "mor/a", "x" * 17])
    def test_invalid(self, bad):
        with pytest.raises(CurrencyError):
            currency_stub._validate_code(bad)

    @pytest.mark.parametrize("bad", [None, 123, [], {}])
    def test_non_string(self, bad):
        with pytest.raises(CurrencyError):
            currency_stub._validate_code(bad)


class TestValidateName:
    def test_simple(self):
        assert currency_stub._validate_name("Mora") == "Mora"

    @pytest.mark.parametrize("bad", ["", None, 123, []])
    def test_invalid(self, bad):
        with pytest.raises(CurrencyError):
            currency_stub._validate_name(bad)

    def test_too_long(self):
        with pytest.raises(CurrencyError, match="too long"):
            currency_stub._validate_name("x" * 65)


class TestValidateDecimals:
    def test_zero(self):
        assert currency_stub._validate_decimals(0) == 0

    def test_two(self):
        assert currency_stub._validate_decimals(2) == 2

    def test_max(self):
        assert currency_stub._validate_decimals(MAX_DECIMALS) == MAX_DECIMALS

    @pytest.mark.parametrize("bad", [MIN_DECIMALS - 1, MAX_DECIMALS + 1, 18])
    def test_out_of_range(self, bad):
        with pytest.raises(CurrencyError):
            currency_stub._validate_decimals(bad)

    @pytest.mark.parametrize("bad", [True, "2", 2.0, None, []])
    def test_non_int(self, bad):
        with pytest.raises(CurrencyError):
            currency_stub._validate_decimals(bad)


# ---------------------------------------------------------------------------
# CRUD — stub-level
# ---------------------------------------------------------------------------


class TestCreateStub:
    def test_minimal(self):
        from xijian_api.stubs import worlds as worlds_stub
        w = worlds_stub.create(name="W")
        try:
            record = currency_stub.create(world_id=w["id"], code="mora", name="Mora")
            assert record["code"] == "mora"
            assert record["name"] == "Mora"
            assert record["decimals"] == DEFAULT_DECIMALS
            assert record["symbol"] is None
        finally:
            worlds_stub.delete(w["id"])

    def test_full(self):
        from xijian_api.stubs import worlds as worlds_stub
        w = worlds_stub.create(name="W")
        try:
            record = currency_stub.create(
                world_id=w["id"], code="gold", name="Gold",
                symbol="G", decimals=2,
            )
            assert record["symbol"] == "G"
            assert record["decimals"] == 2
        finally:
            worlds_stub.delete(w["id"])

    def test_duplicate_code(self):
        from xijian_api.stubs import worlds as worlds_stub
        w = worlds_stub.create(name="W")
        try:
            currency_stub.create(world_id=w["id"], code="mora", name="Mora")
            with pytest.raises(CurrencyError, match="already exists"):
                currency_stub.create(world_id=w["id"], code="mora", name="Mora 2")
        finally:
            worlds_stub.delete(w["id"])

    def test_unknown_world(self):
        with pytest.raises(CurrencyError, match="does not exist"):
            currency_stub.create(world_id="world_phantom", code="x", name="X")

    def test_same_code_different_worlds_ok(self):
        from xijian_api.stubs import worlds as worlds_stub
        wa = worlds_stub.create(name="A")
        wb = worlds_stub.create(name="B")
        try:
            currency_stub.create(world_id=wa["id"], code="mora", name="Mora A")
            # Same code in a different world is fine.
            record = currency_stub.create(world_id=wb["id"], code="mora", name="Mora B")
            assert record["name"] == "Mora B"
        finally:
            worlds_stub.delete(wa["id"])
            worlds_stub.delete(wb["id"])


class TestGetListUpdate:
    def test_get_by_composite_key(self):
        from xijian_api.stubs import worlds as worlds_stub
        w = worlds_stub.create(name="W")
        try:
            currency_stub.create(world_id=w["id"], code="mora", name="Mora")
            record = currency_stub.get(w["id"], "mora")
            assert record is not None
            assert record["name"] == "Mora"
        finally:
            worlds_stub.delete(w["id"])

    def test_get_unknown(self):
        assert currency_stub.get("world_phantom", "x") is None

    def test_get_by_id(self):
        from xijian_api.stubs import worlds as worlds_stub
        w = worlds_stub.create(name="W")
        try:
            record = currency_stub.create(
                world_id=w["id"], code="mora", name="Mora"
            )
            found = currency_stub.get_by_id(record["id"])
            assert found is not None
            assert found["code"] == "mora"
        finally:
            worlds_stub.delete(w["id"])

    def test_list_for_world(self):
        from xijian_api.stubs import worlds as worlds_stub
        wa = worlds_stub.create(name="A")
        wb = worlds_stub.create(name="B")
        try:
            currency_stub.create(world_id=wa["id"], code="a", name="A")
            currency_stub.create(world_id=wa["id"], code="b", name="B")
            currency_stub.create(world_id=wb["id"], code="x", name="X")
            a_list = currency_stub.list_for_world(wa["id"])
            assert {c["code"] for c in a_list} == {"a", "b"}
        finally:
            worlds_stub.delete(wa["id"])
            worlds_stub.delete(wb["id"])

    def test_update_name(self):
        from xijian_api.stubs import worlds as worlds_stub
        w = worlds_stub.create(name="W")
        try:
            currency_stub.create(world_id=w["id"], code="mora", name="Mora")
            record = currency_stub.update(w["id"], "mora", {"name": "Mora!"})
            assert record["name"] == "Mora!"
        finally:
            worlds_stub.delete(w["id"])

    def test_update_immutable_keys(self):
        from xijian_api.stubs import worlds as worlds_stub
        w = worlds_stub.create(name="W")
        try:
            currency_stub.create(world_id=w["id"], code="mora", name="Mora")
            with pytest.raises(CurrencyError, match="immutable"):
                currency_stub.update(w["id"], "mora", {"code": "gold"})
            with pytest.raises(CurrencyError, match="immutable"):
                currency_stub.update(w["id"], "mora", {"world_id": "world_other"})
        finally:
            worlds_stub.delete(w["id"])

    def test_update_unknown_currency(self):
        assert currency_stub.update("world_phantom", "mora", {"name": "X"}) is None


class TestDelete:
    def test_delete_ok(self):
        from xijian_api.stubs import worlds as worlds_stub
        w = worlds_stub.create(name="W")
        try:
            currency_stub.create(world_id=w["id"], code="mora", name="Mora")
            assert currency_stub.delete(w["id"], "mora") is True
            assert currency_stub.get(w["id"], "mora") is None
        finally:
            worlds_stub.delete(w["id"])

    def test_delete_unknown(self):
        assert currency_stub.delete("world_phantom", "mora") is False

    def test_delete_refuses_when_wallets_exist(self):
        from xijian_api.stubs import worlds as worlds_stub
        from xijian_api.stubs import wallets as wallet_stub
        w = worlds_stub.create(name="W")
        try:
            currency_stub.create(world_id=w["id"], code="mora", name="Mora")
            wallet_stub.create(
                wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
                w["id"], "mora", initial_balance=100,
            )
            with pytest.raises(CurrencyError, match="wallet"):
                currency_stub.delete(w["id"], "mora")
        finally:
            worlds_stub.delete(w["id"])

    def test_delete_cascade_wipes_wallets(self):
        from xijian_api.stubs import worlds as worlds_stub
        from xijian_api.stubs import wallets as wallet_stub
        w = worlds_stub.create(name="W")
        try:
            currency_stub.create(world_id=w["id"], code="mora", name="Mora")
            wallet_stub.create(
                wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
                w["id"], "mora", initial_balance=100,
            )
            assert currency_stub.delete(w["id"], "mora", cascade=True) is True
            assert wallet_stub.get(
                wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
                w["id"], "mora",
            ) is None
        finally:
            worlds_stub.delete(w["id"])

    def test_delete_cascade_wipes_transactions(self):
        from xijian_api.stubs import worlds as worlds_stub
        from xijian_api.stubs import transactions as txn_stub
        w = worlds_stub.create(name="W")
        try:
            currency_stub.create(world_id=w["id"], code="mora", name="Mora")
            txn_stub.record(
                world_id=w["id"],
                from_kind="user", from_id="u1",
                to_kind="npc", to_id="npc1",
                currency_code="mora", amount=10,
                kind="purchase",
            )
            assert currency_stub.delete(w["id"], "mora", cascade=True) is True
            assert all(
                t.get("currency_code") != "mora"
                for t in stubs_state.transactions.values()
            )
        finally:
            worlds_stub.delete(w["id"])


# ---------------------------------------------------------------------------
# Lazy default
# ---------------------------------------------------------------------------


class TestEnsureCurrency:
    def test_creates_when_missing(self):
        from xijian_api.stubs import worlds as worlds_stub
        w = worlds_stub.create(name="W")
        try:
            record = currency_stub.ensure_currency(w["id"], "auto_coin")
            assert record["code"] == "auto_coin"
            assert "auto" in record["name"]
        finally:
            worlds_stub.delete(w["id"])

    def test_returns_existing(self):
        from xijian_api.stubs import worlds as worlds_stub
        w = worlds_stub.create(name="W")
        try:
            currency_stub.create(
                world_id=w["id"], code="mora", name="Mora"
            )
            record = currency_stub.ensure_currency(w["id"], "mora")
            assert record["name"] == "Mora"
        finally:
            worlds_stub.delete(w["id"])


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


class TestHttpCreate:
    def test_create(self, client, auth_headers, world):
        body = {"world_id": world, "code": "mora", "name": "Mora"}
        res = client.post("/v1/xijian/currencies", json=body, headers=auth_headers)
        assert res.status_code == 201
        data = res.get_json()
        assert data["code"] == "mora"
        assert data["name"] == "Mora"

    def test_missing_world(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/currencies",
            json={"code": "mora", "name": "Mora"},
            headers=auth_headers,
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "missing_world_id"

    def test_missing_code(self, client, auth_headers, world):
        res = client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "name": "Mora"},
            headers=auth_headers,
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "missing_code"

    def test_missing_name(self, client, auth_headers, world):
        res = client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mora"},
            headers=auth_headers,
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "missing_name"

    def test_unknown_world(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/currencies",
            json={"world_id": "world_phantom", "code": "x", "name": "X"},
            headers=auth_headers,
        )
        assert res.status_code == 404
        assert res.get_json()["error"]["code"] == "world_not_found"

    def test_duplicate(self, client, auth_headers, world, currency):
        res = client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mora", "name": "Mora 2"},
            headers=auth_headers,
        )
        assert res.status_code == 409
        assert res.get_json()["error"]["code"] == "currency_conflict"

    def test_invalid_code_format(self, client, auth_headers, world):
        res = client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mor-a", "name": "X"},
            headers=auth_headers,
        )
        assert res.status_code == 400


class TestHttpListGet:
    def test_list_global(self, client, auth_headers, currency):
        res = client.get("/v1/xijian/currencies", headers=auth_headers)
        assert res.status_code == 200
        codes = [c["code"] for c in res.get_json()["data"]]
        assert "mora" in codes

    def test_list_by_world(self, client, auth_headers, world, currency):
        res = client.get(
            f"/v1/xijian/currencies?world_id={world}", headers=auth_headers
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["world_id"] == world
        codes = [c["code"] for c in data["currencies"]]
        assert "mora" in codes

    def test_list_by_world_unknown(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/currencies?world_id=world_phantom",
            headers=auth_headers,
        )
        assert res.status_code == 404

    def test_get(self, client, auth_headers, world, currency):
        res = client.get(
            f"/v1/xijian/currencies/{world}/mora", headers=auth_headers
        )
        assert res.status_code == 200
        assert res.get_json()["name"] == "Mora"

    def test_get_unknown(self, client, auth_headers, world):
        res = client.get(
            f"/v1/xijian/currencies/{world}/x", headers=auth_headers
        )
        assert res.status_code == 404


class TestHttpPatchDelete:
    def test_patch(self, client, auth_headers, world, currency):
        res = client.patch(
            f"/v1/xijian/currencies/{world}/mora",
            json={"name": "Renamed", "symbol": "R"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["name"] == "Renamed"
        assert data["symbol"] == "R"

    def test_patch_immutable_code(self, client, auth_headers, world, currency):
        res = client.patch(
            f"/v1/xijian/currencies/{world}/mora",
            json={"code": "gold"},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_patch_unknown(self, client, auth_headers, world):
        res = client.patch(
            f"/v1/xijian/currencies/{world}/x",
            json={"name": "X"},
            headers=auth_headers,
        )
        assert res.status_code == 404

    def test_delete(self, client, auth_headers, world, currency):
        res = client.delete(
            f"/v1/xijian/currencies/{world}/mora", headers=auth_headers
        )
        assert res.status_code == 200
        assert res.get_json()["deleted"] is True

    def test_delete_unknown(self, client, auth_headers, world):
        res = client.delete(
            f"/v1/xijian/currencies/{world}/x", headers=auth_headers
        )
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# Auth coverage
# ---------------------------------------------------------------------------


class TestAuthCoverage:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/v1/xijian/currencies"),
            ("POST", "/v1/xijian/currencies"),
            ("GET", "/v1/xijian/currencies/world_modern_tokyo/mora"),
            ("PATCH", "/v1/xijian/currencies/world_modern_tokyo/mora"),
            ("DELETE", "/v1/xijian/currencies/world_modern_tokyo/mora"),
        ],
    )
    def test_requires_bearer(self, client, method, path):
        res = client.open(method=method, path=path)
        assert res.status_code in (401, 403), (
            "%s %s should require auth, got %d" % (method, path, res.status_code)
        )
