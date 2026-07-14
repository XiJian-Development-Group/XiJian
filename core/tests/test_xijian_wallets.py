"""Tests for ``stubs.wallets`` (A4.4) and the
``/v1/xijian/wallets/*`` endpoints.

Covers:

* **Pure helpers** — owner_kind / id / amount validation.
* **CRUD** — create / ensure / list / get / delete.
* **Mutations** — deposit / withdraw / transfer, including
  overdraft policy and atomicity.
* **Cascading** — delete_for_world / delete_for_owner.
* **Auth** — every endpoint requires a Bearer token.
"""

from __future__ import annotations

import pytest

from xijian_api.stubs import state as stubs_state
from xijian_api.stubs import wallets as wallet_stub
from xijian_api.stubs.wallets import (
    DEFAULT_BALANCE,
    LOCAL_USER_ID,
    MAX_SINGLE_AMOUNT,
    OWNER_NPC,
    OWNER_USER,
    VALID_OWNER_KINDS,
    WalletError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def world(client, auth_headers):
    body = {"name": "Wallet Test World"}
    res = client.post("/v1/xijian/worlds", json=body, headers=auth_headers)
    assert res.status_code == 201
    return res.get_json()["id"]


@pytest.fixture()
def currency(client, auth_headers, world):
    res = client.post(
        "/v1/xijian/currencies",
        json={"world_id": world, "code": "mora", "name": "Mora"},
        headers=auth_headers,
    )
    assert res.status_code == 201
    return res.get_json()


@pytest.fixture()
def funded_user_wallet(client, auth_headers, world, currency):
    return wallet_stub.create(
        OWNER_USER, LOCAL_USER_ID, world, "mora", initial_balance=1000.0,
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestValidateOwnerKind:
    def test_user(self):
        assert wallet_stub._validate_owner_kind(OWNER_USER) == OWNER_USER

    def test_npc(self):
        assert wallet_stub._validate_owner_kind(OWNER_NPC) == OWNER_NPC

    @pytest.mark.parametrize("bad", ["", "system", "guest", None, 123])
    def test_invalid(self, bad):
        with pytest.raises(WalletError):
            wallet_stub._validate_owner_kind(bad)


class TestValidateAmount:
    def test_zero(self):
        assert wallet_stub._validate_amount(0) == 0.0

    def test_positive(self):
        assert wallet_stub._validate_amount(50) == 50.0

    def test_negative(self):
        with pytest.raises(WalletError, match=">= 0"):
            wallet_stub._validate_amount(-1)

    def test_over_cap(self):
        with pytest.raises(WalletError, match="cap"):
            wallet_stub._validate_amount(MAX_SINGLE_AMOUNT + 1)

    @pytest.mark.parametrize("bad", [True, "50", None, []])
    def test_non_numeric(self, bad):
        with pytest.raises(WalletError):
            wallet_stub._validate_amount(bad)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCreateStub:
    def test_minimal(self, world, currency):
        record = wallet_stub.create(
            OWNER_USER, LOCAL_USER_ID, world, "mora"
        )
        assert record["balance"] == DEFAULT_BALANCE
        assert record["owner_kind"] == OWNER_USER

    def test_with_balance(self, world, currency):
        record = wallet_stub.create(
            OWNER_USER, LOCAL_USER_ID, world, "mora", initial_balance=500,
        )
        assert record["balance"] == 500.0

    def test_duplicate(self, world, currency, funded_user_wallet):
        with pytest.raises(WalletError, match="already exists"):
            wallet_stub.create(OWNER_USER, LOCAL_USER_ID, world, "mora")

    def test_unknown_world(self, currency):
        with pytest.raises(WalletError, match="world"):
            wallet_stub.create(OWNER_USER, LOCAL_USER_ID, "world_phantom", "mora")

    def test_unknown_currency(self, world):
        with pytest.raises(WalletError, match="currency"):
            wallet_stub.create(OWNER_USER, LOCAL_USER_ID, world, "ghost")


class TestEnsureWallet:
    def test_idempotent(self, world, currency):
        a = wallet_stub.ensure_wallet(OWNER_USER, LOCAL_USER_ID, world, "mora")
        b = wallet_stub.ensure_wallet(OWNER_USER, LOCAL_USER_ID, world, "mora")
        assert a is b

    def test_creates_with_initial(self, world, currency):
        record = wallet_stub.ensure_wallet(
            OWNER_USER, LOCAL_USER_ID, world, "mora",
            initial_balance=250.0,
        )
        assert record["balance"] == 250.0

    def test_initial_negative_disallowed(self, world, currency):
        with pytest.raises(WalletError, match="overdraft"):
            wallet_stub.ensure_wallet(
                OWNER_USER, LOCAL_USER_ID, world, "mora",
                initial_balance=-10,
            )

    def test_initial_negative_allowed_with_overdraft(
        self, world, currency, monkeypatch
    ):
        from xijian_api.stubs import world_economy_state as eco_stub
        eco_stub.update(world, {"allow_overdraft": True})
        record = wallet_stub.ensure_wallet(
            OWNER_USER, LOCAL_USER_ID, world, "mora",
            initial_balance=-10,
        )
        assert record["balance"] == -10.0


class TestGetListDelete:
    def test_get(self, world, currency, funded_user_wallet):
        record = wallet_stub.get(OWNER_USER, LOCAL_USER_ID, world, "mora")
        assert record is not None
        assert record["balance"] == 1000.0

    def test_get_unknown(self, world):
        assert wallet_stub.get(OWNER_USER, LOCAL_USER_ID, world, "x") is None

    def test_get_by_id(self, world, currency, funded_user_wallet):
        record = wallet_stub.get_by_id(funded_user_wallet["id"])
        assert record is not None

    def test_list_for_owner(self, world, currency, client, auth_headers):
        # Add a second currency in the same world.
        client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mora2", "name": "Mora2"},
            headers=auth_headers,
        )
        wallet_stub.create(OWNER_USER, "u1", world, "mora", initial_balance=10)
        wallet_stub.create(OWNER_USER, "u1", world, "mora2", initial_balance=20)
        wallet_stub.create(OWNER_NPC, "n1", world, "mora", initial_balance=5)
        out = wallet_stub.list_for_owner(OWNER_USER, "u1")
        assert len(out) == 2

    def test_list_for_world(self, world, currency):
        wallet_stub.create(OWNER_USER, "u1", world, "mora", initial_balance=10)
        wallet_stub.create(OWNER_NPC, "n1", world, "mora", initial_balance=20)
        out = wallet_stub.list_for_world(world)
        assert len(out) == 2

    def test_delete(self, world, currency, funded_user_wallet):
        assert wallet_stub.delete(OWNER_USER, LOCAL_USER_ID, world, "mora") is True
        assert wallet_stub.get(OWNER_USER, LOCAL_USER_ID, world, "mora") is None
        assert wallet_stub.delete(OWNER_USER, LOCAL_USER_ID, world, "mora") is False

    def test_delete_for_owner(self, world, currency, client, auth_headers):
        client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mora2", "name": "Mora2"},
            headers=auth_headers,
        )
        wallet_stub.create(OWNER_NPC, "n1", world, "mora", initial_balance=10)
        wallet_stub.create(OWNER_NPC, "n1", world, "mora2", initial_balance=20)
        removed = wallet_stub.delete_for_owner(OWNER_NPC, "n1")
        assert removed == 2

    def test_delete_for_world(self, world, currency, client, auth_headers):
        client.post(
            "/v1/xijian/currencies",
            json={"world_id": world, "code": "mora2", "name": "Mora2"},
            headers=auth_headers,
        )
        wallet_stub.create(OWNER_USER, "u1", world, "mora", initial_balance=10)
        wallet_stub.create(OWNER_NPC, "n1", world, "mora2", initial_balance=20)
        removed = wallet_stub.delete_for_world(world)
        assert removed == 2


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


class TestDeposit:
    def test_increases_balance(self, world, currency, funded_user_wallet):
        record = wallet_stub.deposit(OWNER_USER, LOCAL_USER_ID, world, "mora", 100)
        assert record["balance"] == 1100.0

    def test_zero_amount_noop(self, world, currency, funded_user_wallet):
        record = wallet_stub.deposit(OWNER_USER, LOCAL_USER_ID, world, "mora", 0)
        assert record["balance"] == 1000.0

    def test_creates_if_missing(self, world, currency):
        record = wallet_stub.deposit(OWNER_USER, LOCAL_USER_ID, world, "mora", 50)
        assert record["balance"] == 50.0

    def test_no_create_flag(self, world, currency):
        with pytest.raises(WalletError):
            wallet_stub.deposit(
                OWNER_USER, LOCAL_USER_ID, world, "mora", 50,
                allow_create=False,
            )

    def test_negative_rejected(self, world, currency, funded_user_wallet):
        with pytest.raises(WalletError, match=">= 0"):
            wallet_stub.deposit(OWNER_USER, LOCAL_USER_ID, world, "mora", -1)


class TestWithdraw:
    def test_decreases_balance(self, world, currency, funded_user_wallet):
        record = wallet_stub.withdraw(OWNER_USER, LOCAL_USER_ID, world, "mora", 300)
        assert record["balance"] == 700.0

    def test_insufficient_funds(self, world, currency, funded_user_wallet):
        with pytest.raises(WalletError, match="insufficient"):
            wallet_stub.withdraw(OWNER_USER, LOCAL_USER_ID, world, "mora", 2000)

    def test_overdraft_with_flag(self, world, currency, funded_user_wallet):
        record = wallet_stub.withdraw(
            OWNER_USER, LOCAL_USER_ID, world, "mora", 2000,
            allow_overdraft=True,
        )
        assert record["balance"] == -1000.0

    def test_overdraft_via_world_flag(
        self, world, currency, funded_user_wallet
    ):
        from xijian_api.stubs import world_economy_state as eco_stub
        eco_stub.update(world, {"allow_overdraft": True})
        record = wallet_stub.withdraw(
            OWNER_USER, LOCAL_USER_ID, world, "mora", 2000,
        )
        assert record["balance"] == -1000.0

    def test_no_wallet(self, world, currency):
        with pytest.raises(WalletError, match="does not exist"):
            wallet_stub.withdraw(OWNER_USER, LOCAL_USER_ID, world, "mora", 10)


class TestTransfer:
    def test_basic(self, world, currency, funded_user_wallet):
        npc_wallet = wallet_stub.create(
            OWNER_NPC, "n1", world, "mora", initial_balance=100,
        )
        from_w, to_w = wallet_stub.transfer(
            OWNER_USER, LOCAL_USER_ID,
            OWNER_NPC, "n1",
            world, "mora", 200,
        )
        assert from_w["balance"] == 800.0
        assert to_w["balance"] == 300.0

    def test_self_transfer_rejected(self, world, currency, funded_user_wallet):
        with pytest.raises(WalletError, match="same wallet"):
            wallet_stub.transfer(
                OWNER_USER, LOCAL_USER_ID, OWNER_USER, LOCAL_USER_ID,
                world, "mora", 100,
            )

    def test_missing_wallet(self, world, currency, funded_user_wallet):
        with pytest.raises(WalletError, match="must exist"):
            wallet_stub.transfer(
                OWNER_USER, LOCAL_USER_ID, OWNER_NPC, "n1",
                world, "mora", 100,
            )

    def test_insufficient_funds(self, world, currency, funded_user_wallet):
        wallet_stub.create(OWNER_NPC, "n1", world, "mora", initial_balance=10)
        with pytest.raises(WalletError, match="insufficient"):
            wallet_stub.transfer(
                OWNER_USER, LOCAL_USER_ID, OWNER_NPC, "n1",
                world, "mora", 9999,
            )


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


class TestHttpList:
    def test_list_global(self, client, auth_headers, world, currency, funded_user_wallet):
        res = client.get("/v1/xijian/wallets", headers=auth_headers)
        assert res.status_code == 200

    def test_list_by_world(self, client, auth_headers, world, currency, funded_user_wallet):
        res = client.get(
            f"/v1/xijian/wallets?world_id={world}", headers=auth_headers
        )
        assert res.status_code == 200
        assert len(res.get_json()["wallets"]) == 1

    def test_list_by_owner(self, client, auth_headers, world, currency, funded_user_wallet):
        res = client.get(
            f"/v1/xijian/wallets?owner_kind={OWNER_USER}&owner_id={LOCAL_USER_ID}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert len(res.get_json()["wallets"]) == 1

    def test_list_for_owner_path(self, client, auth_headers, world, currency, funded_user_wallet):
        res = client.get(
            f"/v1/xijian/wallets/{OWNER_USER}/{LOCAL_USER_ID}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert len(res.get_json()["wallets"]) == 1


class TestHttpEnsureGetDelete:
    def test_ensure(self, client, auth_headers, world, currency):
        res = client.post(
            "/v1/xijian/wallets/ensure",
            json={
                "owner_kind": OWNER_USER,
                "owner_id": LOCAL_USER_ID,
                "world_id": world,
                "currency_code": "mora",
                "initial_balance": 200,
            },
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["balance"] == 200.0

    def test_ensure_idempotent(self, client, auth_headers, world, currency, funded_user_wallet):
        res = client.post(
            "/v1/xijian/wallets/ensure",
            json={
                "owner_kind": OWNER_USER,
                "owner_id": LOCAL_USER_ID,
                "world_id": world,
                "currency_code": "mora",
                "initial_balance": 200,
            },
            headers=auth_headers,
        )
        assert res.status_code == 200
        # Already exists with 1000, the second ensure is a no-op.
        assert res.get_json()["balance"] == 1000.0

    def test_get(self, client, auth_headers, world, currency, funded_user_wallet):
        res = client.get(
            f"/v1/xijian/wallets/{OWNER_USER}/{LOCAL_USER_ID}/{world}/mora",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["balance"] == 1000.0

    def test_get_unknown(self, client, auth_headers, world, currency):
        res = client.get(
            f"/v1/xijian/wallets/{OWNER_USER}/{LOCAL_USER_ID}/{world}/x",
            headers=auth_headers,
        )
        assert res.status_code == 404

    def test_delete(self, client, auth_headers, world, currency, funded_user_wallet):
        res = client.delete(
            f"/v1/xijian/wallets/{OWNER_USER}/{LOCAL_USER_ID}/{world}/mora",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["deleted"] is True

    def test_delete_unknown(self, client, auth_headers, world, currency):
        res = client.delete(
            f"/v1/xijian/wallets/{OWNER_USER}/{LOCAL_USER_ID}/{world}/x",
            headers=auth_headers,
        )
        assert res.status_code == 404


class TestHttpDepositWithdraw:
    def test_deposit(self, client, auth_headers, world, currency, funded_user_wallet):
        res = client.post(
            f"/v1/xijian/wallets/{OWNER_USER}/{LOCAL_USER_ID}/{world}/mora/deposit",
            json={"amount": 50},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["balance"] == 1050.0

    def test_deposit_negative_rejected(self, client, auth_headers, world, currency, funded_user_wallet):
        res = client.post(
            f"/v1/xijian/wallets/{OWNER_USER}/{LOCAL_USER_ID}/{world}/mora/deposit",
            json={"amount": -1},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_withdraw(self, client, auth_headers, world, currency, funded_user_wallet):
        res = client.post(
            f"/v1/xijian/wallets/{OWNER_USER}/{LOCAL_USER_ID}/{world}/mora/withdraw",
            json={"amount": 100},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["balance"] == 900.0

    def test_withdraw_insufficient(self, client, auth_headers, world, currency, funded_user_wallet):
        res = client.post(
            f"/v1/xijian/wallets/{OWNER_USER}/{LOCAL_USER_ID}/{world}/mora/withdraw",
            json={"amount": 9999},
            headers=auth_headers,
        )
        assert res.status_code == 409
        assert res.get_json()["error"]["code"] == "insufficient_funds"


# ---------------------------------------------------------------------------
# Auth coverage
# ---------------------------------------------------------------------------


class TestAuthCoverage:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/v1/xijian/wallets"),
            ("POST", "/v1/xijian/wallets/ensure"),
            ("GET", "/v1/xijian/wallets/user/user_local"),
            ("GET", "/v1/xijian/wallets/user/user_local/world_modern_tokyo/mora"),
            ("DELETE", "/v1/xijian/wallets/user/user_local/world_modern_tokyo/mora"),
            ("POST", "/v1/xijian/wallets/user/user_local/world_modern_tokyo/mora/deposit"),
            ("POST", "/v1/xijian/wallets/user/user_local/world_modern_tokyo/mora/withdraw"),
        ],
    )
    def test_requires_bearer(self, client, method, path):
        res = client.open(method=method, path=path)
        assert res.status_code in (401, 403), (
            "%s %s should require auth, got %d" % (method, path, res.status_code)
        )
