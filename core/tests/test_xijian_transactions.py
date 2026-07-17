"""Tests for ``stubs.transactions`` (A4.4) and the
``/v1/xijian/economy/transactions*`` endpoints.

Covers:

* **Pure helpers** — party kind / id / amount validation.
* **CRUD** — record / get / list / summary.
* **Cascading** — delete_for_world / delete_for_owner.
* **Auth** — every endpoint requires a Bearer token.
"""

from __future__ import annotations

import pytest

from xijian_api.stubs import state as stubs_state
from xijian_api.stubs import transactions as txn_stub
from xijian_api.stubs import worlds as worlds_stub
from xijian_api.stubs.transactions import (
    KIND_PURCHASE,
    KIND_SALE,
    KIND_THEFT,
    KIND_REWARD,
    KIND_TRANSFER,
    MAX_TXN_AMOUNT,
    TransactionError,
    VALID_KINDS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def world():
    w = worlds_stub.create(name="Txn Test World")
    yield w["id"]
    worlds_stub.delete(w["id"])


@pytest.fixture()
def world_via_http(client, auth_headers):
    body = {"name": "Txn HTTP World"}
    res = client.post("/v1/xijian/worlds", json=body, headers=auth_headers)
    assert res.status_code == 201
    return res.get_json()["id"]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestValidateAmount:
    def test_positive(self):
        assert txn_stub._validate_amount(10) == 10.0

    def test_decimal(self):
        assert txn_stub._validate_amount(10.5) == 10.5

    def test_zero(self):
        with pytest.raises(TransactionError, match="> 0"):
            txn_stub._validate_amount(0)

    def test_negative(self):
        with pytest.raises(TransactionError, match="> 0"):
            txn_stub._validate_amount(-1)

    def test_over_cap(self):
        with pytest.raises(TransactionError, match="cap"):
            txn_stub._validate_amount(MAX_TXN_AMOUNT + 1)

    @pytest.mark.parametrize("bad", [True, "10", None, []])
    def test_non_numeric(self, bad):
        with pytest.raises(TransactionError):
            txn_stub._validate_amount(bad)


class TestValidKinds:
    def test_purchase(self):
        assert KIND_PURCHASE in VALID_KINDS

    def test_sale(self):
        assert KIND_SALE in VALID_KINDS

    def test_theft(self):
        assert KIND_THEFT in VALID_KINDS

    def test_reward(self):
        assert KIND_REWARD in VALID_KINDS

    def test_transfer(self):
        assert KIND_TRANSFER in VALID_KINDS


# ---------------------------------------------------------------------------
# CRUD — stub-level
# ---------------------------------------------------------------------------


class TestRecord:
    def test_minimal(self, world):
        record = txn_stub.record(
            world_id=world,
            from_kind="user", from_id="u1",
            to_kind="npc", to_id="n1",
            currency_code="mora",
            amount=50,
            kind=KIND_PURCHASE,
        )
        assert record["world_id"] == world
        assert record["from_kind"] == "user"
        assert record["amount"] == 50.0
        assert record["kind"] == KIND_PURCHASE

    def test_with_ref(self, world):
        record = txn_stub.record(
            world_id=world,
            from_kind="user", from_id="u1",
            to_kind="npc", to_id="n1",
            currency_code="mora",
            amount=50,
            kind=KIND_THEFT,
            ref_id="npcsched_abc",
        )
        assert record["ref_id"] == "npcsched_abc"

    def test_duplicate_id_rejected(self, world):
        txn_stub.record(
            world_id=world,
            from_kind="user", from_id="u1",
            to_kind="npc", to_id="n1",
            currency_code="mora",
            amount=10,
            kind=KIND_PURCHASE,
            transaction_id="txn_dup_test",
        )
        with pytest.raises(TransactionError, match="already exists"):
            txn_stub.record(
                world_id=world,
                from_kind="user", from_id="u1",
                to_kind="npc", to_id="n1",
                currency_code="mora",
                amount=10,
                kind=KIND_PURCHASE,
                transaction_id="txn_dup_test",
            )

    def test_missing_world(self):
        with pytest.raises(TransactionError):
            txn_stub.record(
                world_id="",
                from_kind="user", from_id="u1",
                to_kind="npc", to_id="n1",
                currency_code="mora",
                amount=10,
                kind=KIND_PURCHASE,
            )

    def test_zero_amount_rejected(self, world):
        with pytest.raises(TransactionError, match="> 0"):
            txn_stub.record(
                world_id=world,
                from_kind="user", from_id="u1",
                to_kind="npc", to_id="n1",
                currency_code="mora",
                amount=0,
                kind=KIND_PURCHASE,
            )

    def test_missing_kind(self, world):
        with pytest.raises(TransactionError):
            txn_stub.record(
                world_id=world,
                from_kind="user", from_id="u1",
                to_kind="npc", to_id="n1",
                currency_code="mora",
                amount=10,
                kind="",
            )


class TestListSummary:
    def test_get(self, world):
        record = txn_stub.record(
            world_id=world,
            from_kind="user", from_id="u1",
            to_kind="npc", to_id="n1",
            currency_code="mora",
            amount=10,
            kind=KIND_PURCHASE,
        )
        assert txn_stub.get(record["id"]) is not None
        assert txn_stub.get("txn_phantom") is None

    def test_list_for_world(self, world):
        for i in range(3):
            txn_stub.record(
                world_id=world,
                from_kind="user", from_id="u1",
                to_kind="npc", to_id="n1",
                currency_code="mora", amount=10,
                kind=KIND_PURCHASE,
            )
        assert len(txn_stub.list_for_world(world)) == 3
        # Other world gets nothing.
        assert len(txn_stub.list_for_world("world_other")) == 0

    def test_list_for_world_kind_filter(self, world):
        txn_stub.record(
            world_id=world, from_kind="user", from_id="u1",
            to_kind="npc", to_id="n1", currency_code="mora",
            amount=10, kind=KIND_PURCHASE,
        )
        txn_stub.record(
            world_id=world, from_kind="npc", from_id="n1",
            to_kind="user", to_id="u1", currency_code="mora",
            amount=5, kind=KIND_SALE,
        )
        purchases = txn_stub.list_for_world(world, kind=KIND_PURCHASE)
        assert len(purchases) == 1
        assert purchases[0]["kind"] == KIND_PURCHASE

    def test_list_for_owner(self, world):
        txn_stub.record(
            world_id=world, from_kind="user", from_id="u1",
            to_kind="npc", to_id="n1", currency_code="mora",
            amount=10, kind=KIND_PURCHASE,
        )
        txn_stub.record(
            world_id=world, from_kind="npc", from_id="n1",
            to_kind="user", to_id="u1", currency_code="mora",
            amount=5, kind=KIND_SALE,
        )
        # u1 is sender of first + receiver of second.
        out = txn_stub.list_for_owner("user", "u1")
        assert len(out) == 2

    def test_list_for_kind(self, world):
        txn_stub.record(
            world_id=world, from_kind="user", from_id="u1",
            to_kind="npc", to_id="n1", currency_code="mora",
            amount=10, kind=KIND_THEFT,
        )
        assert len(txn_stub.list_for_kind(KIND_THEFT)) == 1
        assert len(txn_stub.list_for_kind(KIND_REWARD)) == 0

    def test_list_all(self, world):
        for i in range(5):
            txn_stub.record(
                world_id=world, from_kind="user", from_id="u1",
                to_kind="npc", to_id="n1", currency_code="mora",
                amount=10, kind=KIND_PURCHASE,
            )
        assert len(txn_stub.list_all()) == 5

    def test_count_for_world(self, world):
        for i in range(2):
            txn_stub.record(
                world_id=world, from_kind="user", from_id="u1",
                to_kind="npc", to_id="n1", currency_code="mora",
                amount=10, kind=KIND_PURCHASE,
            )
        assert txn_stub.count_for_world(world) == 2

    def test_summary(self, world):
        for i in range(2):
            txn_stub.record(
                world_id=world, from_kind="user", from_id="u1",
                to_kind="npc", to_id="n1", currency_code="mora",
                amount=10, kind=KIND_PURCHASE,
            )
        txn_stub.record(
            world_id=world, from_kind="user", from_id="u1",
            to_kind="npc", to_id="n1", currency_code="mora",
            amount=20, kind=KIND_SALE,
        )
        summary = txn_stub.summary(world)
        assert summary["total"] == 3
        assert summary["total_volume"] == 40.0
        assert summary["by_kind"][KIND_PURCHASE] == 2
        assert summary["by_kind"][KIND_SALE] == 1

    def test_summary_global(self, world):
        txn_stub.record(
            world_id=world, from_kind="user", from_id="u1",
            to_kind="npc", to_id="n1", currency_code="mora",
            amount=10, kind=KIND_PURCHASE,
        )
        summary = txn_stub.summary()
        assert summary["total"] >= 1


class TestFIFO:
    def test_fifo_trim(self, world, monkeypatch):
        # Lower the cap so we can exercise the trim.
        monkeypatch.setattr(txn_stub, "TXN_KEEP_PER_WORLD", 3)
        for i in range(5):
            txn_stub.record(
                world_id=world, from_kind="user", from_id="u1",
                to_kind="npc", to_id="n1", currency_code="mora",
                amount=1, kind=KIND_PURCHASE,
            )
        # Should keep only 3.
        assert len(txn_stub.list_for_world(world)) == 3


# ---------------------------------------------------------------------------
# Cascading
# ---------------------------------------------------------------------------


class TestCascading:
    def test_delete_for_world(self, world):
        for i in range(3):
            txn_stub.record(
                world_id=world, from_kind="user", from_id="u1",
                to_kind="npc", to_id="n1", currency_code="mora",
                amount=1, kind=KIND_PURCHASE,
            )
        removed = txn_stub.delete_for_world(world)
        assert removed == 3
        assert txn_stub.count_for_world(world) == 0

    def test_delete_for_owner(self, world):
        txn_stub.record(
            world_id=world, from_kind="user", from_id="u1",
            to_kind="npc", to_id="n1", currency_code="mora",
            amount=1, kind=KIND_PURCHASE,
        )
        txn_stub.record(
            world_id=world, from_kind="npc", from_id="n1",
            to_kind="user", to_id="u1", currency_code="mora",
            amount=1, kind=KIND_SALE,
        )
        # ``delete_for_owner`` removes every txn where the owner
        # appears in *either* from- or to-side, so n1 (npc) is in
        # both rows → 2 deleted.
        removed = txn_stub.delete_for_owner("npc", "n1")
        assert removed == 2
        assert txn_stub.count_for_world(world) == 0

    def test_delete_for_owner_sender_only(self, world):
        # n1 only as sender in one tx, n1 only as receiver in another.
        # Delete-for-npc-sender: only drops the sender-side row.
        # The other tx is unaffected (n1 is receiver, not sender).
        txn_stub.record(
            world_id=world, from_kind="npc", from_id="n1",
            to_kind="user", to_id="u1", currency_code="mora",
            amount=1, kind=KIND_SALE,
        )
        txn_stub.record(
            world_id=world, from_kind="user", from_id="u1",
            to_kind="npc", to_id="n1", currency_code="mora",
            amount=1, kind=KIND_PURCHASE,
        )
        # Filter by sender-side: drop tx where from_kind=npc & from_id=n1.
        # The current ``delete_for_owner`` is by-occurrence (any side).
        removed = txn_stub.delete_for_owner("npc", "n1")
        # Both tx rows reference n1 (one as sender, one as receiver) → 2.
        assert removed == 2


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


class TestHttpList:
    def test_list_global(self, client, auth_headers, world_via_http):
        # No body — just check the route accepts the call.
        res = client.get(
            "/v1/xijian/economy/transactions", headers=auth_headers
        )
        assert res.status_code == 200
        assert "transactions" in res.get_json()

    def test_list_by_world(self, client, auth_headers, world_via_http):
        res = client.get(
            f"/v1/xijian/economy/transactions?world_id={world_via_http}",
            headers=auth_headers,
        )
        assert res.status_code == 200

    def test_list_by_world_unknown(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/economy/transactions?world_id=world_phantom",
            headers=auth_headers,
        )
        assert res.status_code == 404

    def test_list_by_owner(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/economy/transactions?owner_kind=user&owner_id=u1",
            headers=auth_headers,
        )
        assert res.status_code == 200

    def test_summary_global(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/economy/transactions/summary",
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.get_json()
        assert "total" in data

    def test_summary_by_world(self, client, auth_headers, world_via_http):
        res = client.get(
            f"/v1/xijian/economy/transactions/summary?world_id={world_via_http}",
            headers=auth_headers,
        )
        assert res.status_code == 200

    def test_get_unknown(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/economy/transactions/txn_phantom",
            headers=auth_headers,
        )
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# Auth coverage
# ---------------------------------------------------------------------------


class TestAuthCoverage:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/v1/xijian/economy/transactions"),
            ("GET", "/v1/xijian/economy/transactions/summary"),
            ("GET", "/v1/xijian/economy/transactions/txn_phantom"),
        ],
    )
    def test_requires_bearer(self, client, method, path):
        res = client.open(method=method, path=path)
        assert res.status_code in (401, 403), (
            "%s %s should require auth, got %d" % (method, path, res.status_code)
        )
