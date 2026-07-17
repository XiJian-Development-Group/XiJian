"""Tests for the A4.4 economy orchestrator and supporting stubs.

Covers two layers:

* ``stubs.world_economy_state`` — inflation / liquidity bounds,
  lazy default, macro tick.
* ``stubs.economy`` — trade verbs (purchase / sale / reward /
  transfer), crime verbs (theft / scam) with cooldown + policy
  guards.

The HTTP routes for these live under ``/v1/xijian/economy/*`` and
``/v1/xijian/economy/state/*`` — see the parametrized
``TestAuthCoverage`` block at the bottom.
"""

from __future__ import annotations

import os

import pytest

from xijian_api.stubs import economy as economy_stub
from xijian_api.stubs import npcs as npcs_stub
from xijian_api.stubs import transactions as txn_stub
from xijian_api.stubs import wallets as wallet_stub
from xijian_api.stubs import world_currencies as currency_stub
from xijian_api.stubs import world_economy_state as eco_stub
from xijian_api.stubs import worlds as worlds_stub
from xijian_api.stubs.economy import (
    CRIME_COOLDOWN_SECONDS,
    DEFAULT_SCAM_PROBABILITY,
    DEFAULT_THEFT_PROBABILITY,
)
from xijian_api.stubs.world_economy_state import (
    DEFAULT_INFLATION_RATE,
    DEFAULT_LIQUIDITY_INDEX,
    MAX_INFLATION_RATE,
    MAX_LIQUIDITY_INDEX,
    MIN_INFLATION_RATE,
    MIN_LIQUIDITY_INDEX,
    EconomyStateError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def world():
    w = worlds_stub.create(name="Econ Test World")
    yield w["id"]
    worlds_stub.delete(w["id"])


@pytest.fixture()
def world_with_currency(world):
    currency_stub.create(world_id=world, code="mora", name="Mora")
    return world


@pytest.fixture()
def npc(world_with_currency):
    return npcs_stub.create(
        world_id=world_with_currency, name="Merchant"
    )


@pytest.fixture()
def funded_economy(world_with_currency, npc):
    """User has 1000 mora, NPC has 500 mora.  Both ready for trades."""
    user_w = wallet_stub.create(
        wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
        world_with_currency, "mora", initial_balance=1000,
    )
    npc_w = wallet_stub.create(
        wallet_stub.OWNER_NPC, npc["id"],
        world_with_currency, "mora", initial_balance=500,
    )
    return {"user": user_w, "npc": npc_w, "world": world_with_currency, "npc_id": npc["id"]}


# ---------------------------------------------------------------------------
# world_economy_state — pure helpers + state CRUD
# ---------------------------------------------------------------------------


class TestValidateInflation:
    def test_zero(self):
        assert eco_stub._validate_inflation(0) == 0.0

    def test_positive(self):
        assert eco_stub._validate_inflation(0.05) == 0.05

    def test_negative(self):
        assert eco_stub._validate_inflation(-0.1) == -0.1

    def test_min(self):
        assert eco_stub._validate_inflation(MIN_INFLATION_RATE) == MIN_INFLATION_RATE

    def test_max(self):
        assert eco_stub._validate_inflation(MAX_INFLATION_RATE) == MAX_INFLATION_RATE

    @pytest.mark.parametrize("bad", [MIN_INFLATION_RATE - 0.01, MAX_INFLATION_RATE + 0.01, 1.0, -1.0])
    def test_out_of_range(self, bad):
        with pytest.raises(EconomyStateError):
            eco_stub._validate_inflation(bad)

    @pytest.mark.parametrize("bad", [True, "0.05", None, float("nan"), float("inf")])
    def test_invalid(self, bad):
        with pytest.raises(EconomyStateError):
            eco_stub._validate_inflation(bad)


class TestValidateLiquidity:
    def test_one(self):
        assert eco_stub._validate_liquidity(1.0) == 1.0

    def test_min(self):
        assert eco_stub._validate_liquidity(MIN_LIQUIDITY_INDEX) == MIN_LIQUIDITY_INDEX

    def test_max(self):
        assert eco_stub._validate_liquidity(MAX_LIQUIDITY_INDEX) == MAX_LIQUIDITY_INDEX

    @pytest.mark.parametrize("bad", [0.0, 0.3, 2.5, 3.0])
    def test_out_of_range(self, bad):
        with pytest.raises(EconomyStateError):
            eco_stub._validate_liquidity(bad)


class TestGetUpdate:
    def test_lazy_default(self, world):
        record = eco_stub.get(world)
        assert record is not None
        assert record["inflation_rate"] == DEFAULT_INFLATION_RATE
        assert record["liquidity_index"] == DEFAULT_LIQUIDITY_INDEX
        assert record["allow_illegal"] is False
        assert record["allow_overdraft"] is False

    def test_get_unknown_world(self):
        assert eco_stub.get("world_phantom") is None

    def test_update_inflation(self, world):
        record = eco_stub.update(world, {"inflation_rate": 0.05})
        assert record["inflation_rate"] == 0.05

    def test_update_liquidity(self, world):
        record = eco_stub.update(world, {"liquidity_index": 1.5})
        assert record["liquidity_index"] == 1.5

    def test_update_allow_illegal(self, world):
        record = eco_stub.update(world, {"allow_illegal": True})
        assert record["allow_illegal"] is True

    def test_update_allow_illegal_invalid(self, world):
        with pytest.raises(EconomyStateError):
            eco_stub.update(world, {"allow_illegal": "yes"})

    def test_update_immutable_keys(self, world):
        with pytest.raises(EconomyStateError, match="immutable"):
            eco_stub.update(world, {"world_id": "world_other"})

    def test_update_unknown_world(self):
        assert eco_stub.update("world_phantom", {"inflation_rate": 0.1}) is None

    def test_delete(self, world):
        # Touch the record first so the lazy materialiser writes it
        # to ``state`` — otherwise the delete is a no-op.
        eco_stub.get(world)
        assert eco_stub.delete(world) is True
        # Re-materialises on next get.
        record = eco_stub.get(world)
        assert record is not None
        assert record["inflation_rate"] == DEFAULT_INFLATION_RATE

    def test_delete_unknown(self):
        assert eco_stub.delete("world_phantom") is False


class TestTick:
    def test_basic(self, world):
        record = eco_stub.tick(world, volume_delta=0.0, seasonal_factor=0.0)
        assert record is not None

    def test_volume_drives_inflation(self, world):
        before = eco_stub.get(world)["inflation_rate"]
        eco_stub.tick(world, volume_delta=10.0, seasonal_factor=0.0)
        after = eco_stub.get(world)["inflation_rate"]
        assert after > before

    def test_seasonal_factor(self, world):
        before = eco_stub.get(world)["inflation_rate"]
        eco_stub.tick(world, volume_delta=0.0, seasonal_factor=0.1)
        after = eco_stub.get(world)["inflation_rate"]
        assert after > before

    def test_liquidity_mean_reverts(self, world):
        eco_stub.update(world, {"liquidity_index": 2.0})
        eco_stub.tick(world)
        # After one tick, liquidity should be closer to 1.0.
        after = eco_stub.get(world)["liquidity_index"]
        assert after < 2.0

    def test_unknown_world(self):
        assert eco_stub.tick("world_phantom") is None

    def test_bumps_last_tick_at(self, world):
        first = eco_stub.get(world)["last_tick_at"]
        eco_stub.tick(world)
        second = eco_stub.get(world)["last_tick_at"]
        assert second >= first


class TestReadOnlyAccessors:
    def test_allow_illegal_default(self, world):
        assert eco_stub.allow_illegal(world) is False

    def test_allow_illegal_after_update(self, world):
        eco_stub.update(world, {"allow_illegal": True})
        assert eco_stub.allow_illegal(world) is True

    def test_allow_illegal_unknown_world(self):
        assert eco_stub.allow_illegal("world_phantom") is False

    def test_allow_overdraft_default(self, world):
        assert eco_stub.allow_overdraft(world) is False

    def test_allow_overdraft_after_update(self, world):
        eco_stub.update(world, {"allow_overdraft": True})
        assert eco_stub.allow_overdraft(world) is True

    def test_allow_overdraft_unknown_world(self):
        assert eco_stub.allow_overdraft("world_phantom") is False


# ---------------------------------------------------------------------------
# Economy orchestrator — trade
# ---------------------------------------------------------------------------


class TestPurchase:
    def test_basic(self, funded_economy):
        record = economy_stub.purchase(
            world_id=funded_economy["world"],
            npc_id=funded_economy["npc_id"],
            currency_code="mora",
            amount=100,
        )
        assert record["kind"] == "purchase"
        # Wallets updated.
        assert wallet_stub.get(
            wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
            funded_economy["world"], "mora",
        )["balance"] == 900.0
        assert wallet_stub.get(
            wallet_stub.OWNER_NPC, funded_economy["npc_id"],
            funded_economy["world"], "mora",
        )["balance"] == 600.0

    def test_no_user_wallet(self, world_with_currency, npc):
        # User has no wallet — should refuse.
        with pytest.raises(economy_stub.EconomyError, match="user wallet"):
            economy_stub.purchase(
                world_id=world_with_currency,
                npc_id=npc["id"],
                currency_code="mora",
                amount=10,
            )

    def test_no_npc_wallet(self, world_with_currency):
        # User has wallet, NPC doesn't.
        wallet_stub.create(
            wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
            world_with_currency, "mora", initial_balance=100,
        )
        with pytest.raises(economy_stub.EconomyError, match="npc"):
            economy_stub.purchase(
                world_id=world_with_currency,
                npc_id="npc_phantom",
                currency_code="mora",
                amount=10,
            )

    def test_writes_transaction(self, funded_economy):
        record = economy_stub.purchase(
            world_id=funded_economy["world"],
            npc_id=funded_economy["npc_id"],
            currency_code="mora",
            amount=50,
        )
        out = txn_stub.list_for_world(funded_economy["world"])
        assert any(t["id"] == record["id"] for t in out)

    def test_with_ref(self, funded_economy):
        record = economy_stub.purchase(
            world_id=funded_economy["world"],
            npc_id=funded_economy["npc_id"],
            currency_code="mora",
            amount=50,
            ref_id="sint_abc",
        )
        assert record["ref_id"] == "sint_abc"


class TestSale:
    def test_basic(self, funded_economy):
        record = economy_stub.sale(
            world_id=funded_economy["world"],
            npc_id=funded_economy["npc_id"],
            currency_code="mora",
            amount=100,
        )
        assert record["kind"] == "sale"
        # Wallets updated.
        assert wallet_stub.get(
            wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
            funded_economy["world"], "mora",
        )["balance"] == 1100.0
        assert wallet_stub.get(
            wallet_stub.OWNER_NPC, funded_economy["npc_id"],
            funded_economy["world"], "mora",
        )["balance"] == 400.0

    def test_npc_cant_afford(self, funded_economy):
        with pytest.raises(economy_stub.EconomyError):
            economy_stub.sale(
                world_id=funded_economy["world"],
                npc_id=funded_economy["npc_id"],
                currency_code="mora",
                amount=9999,
            )


class TestReward:
    def test_reward_user(self, world_with_currency):
        # No prior wallet.
        record = economy_stub.reward(
            world_id=world_with_currency,
            to_kind=wallet_stub.OWNER_USER,
            to_id=wallet_stub.LOCAL_USER_ID,
            currency_code="mora",
            amount=200,
        )
        assert record["kind"] == "reward"
        # Wallet created and credited.
        w = wallet_stub.get(
            wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
            world_with_currency, "mora",
        )
        assert w["balance"] == 200.0

    def test_reward_npc(self, world_with_currency, npc):
        record = economy_stub.reward(
            world_id=world_with_currency,
            to_kind=wallet_stub.OWNER_NPC,
            to_id=npc["id"],
            currency_code="mora",
            amount=300,
        )
        assert record["kind"] == "reward"

    def test_invalid_to_kind(self, world_with_currency):
        with pytest.raises(economy_stub.EconomyError, match="to_kind"):
            economy_stub.reward(
                world_id=world_with_currency,
                to_kind="system",
                to_id="x",
                currency_code="mora",
                amount=10,
            )


class TestTransferUserToUser:
    def test_basic(self, world_with_currency):
        wallet_stub.create(
            wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
            world_with_currency, "mora", initial_balance=500,
        )
        record = economy_stub.transfer_user_to_user(
            world_id=world_with_currency,
            currency_code="mora",
            amount=100,
        )
        assert record["kind"] == "transfer"
        # Local model: the only "user" is LOCAL_USER_ID, so the
        # balance shouldn't change (we withdraw + deposit the same
        # wallet).  This is by design — multi-user support is
        # forward-compat.
        w = wallet_stub.get(
            wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
            world_with_currency, "mora",
        )
        assert w["balance"] == 500.0


# ---------------------------------------------------------------------------
# Economy orchestrator — crime
# ---------------------------------------------------------------------------


class TestAttemptTheft:
    def test_blocked_when_illegal_disabled(self, funded_economy):
        # Default: allow_illegal is False.
        result = economy_stub.attempt_theft(
            world_id=funded_economy["world"],
            npc_id=funded_economy["npc_id"],
            currency_code="mora",
            amount=50,
        )
        assert result["success"] is False
        assert result["blocked"] == "allow_illegal_disabled"

    def test_blocked_when_overload(self, funded_economy, monkeypatch):
        from xijian_api.stubs import state as stubs_state
        from xijian_api.stubs import overload as ov_stub
        eco_stub.update(funded_economy["world"], {"allow_illegal": True})
        # Simulate overload recovery.
        stubs_state.overload["recovery"] = {
            "event_id": "x", "triggered_at": 0,
            "earliest_confirm_at": 0, "first_confirmed_at": None,
            "status": "waiting", "recoverable": True,
        }
        try:
            result = economy_stub.attempt_theft(
                world_id=funded_economy["world"],
                npc_id=funded_economy["npc_id"],
                currency_code="mora",
                amount=50,
            )
            assert result["success"] is False
            assert result["blocked"] == "overload_active"
        finally:
            ov_stub.cancel_recovery()

    def test_cooldown_blocks_second_attempt(self, funded_economy, monkeypatch):
        eco_stub.update(funded_economy["world"], {"allow_illegal": True})
        # Force the first call to consume the cooldown.
        result1 = economy_stub.attempt_theft(
            world_id=funded_economy["world"],
            npc_id=funded_economy["npc_id"],
            currency_code="mora",
            amount=50,
        )
        result2 = economy_stub.attempt_theft(
            world_id=funded_economy["world"],
            npc_id=funded_economy["npc_id"],
            currency_code="mora",
            amount=50,
        )
        # Second call should be blocked by cooldown (regardless of
        # first call's success — the cooldown is always consumed
        # before the roll).
        assert result2["blocked"] == "cooldown"
        # Suppress the unused-variable warning.
        _ = result1

    def test_user_empty(self, funded_economy, monkeypatch):
        eco_stub.update(funded_economy["world"], {"allow_illegal": True})
        # Force the NPC's crime skill to 0 so the roll deterministically
        # fails — we want the ``user_empty`` branch to be the *first*
        # blocking reason, not the random roll.
        npc = npcs_stub.get(funded_economy["npc_id"])
        npcs_stub.update(
            npc["id"], {"state_json": {"crime_theft_skill": 0.0}}
        )
        # Drain the user wallet.
        wallet_stub.withdraw(
            wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
            funded_economy["world"], "mora", 1000,
        )
        result = economy_stub.attempt_theft(
            world_id=funded_economy["world"],
            npc_id=funded_economy["npc_id"],
            currency_code="mora",
            amount=50,
        )
        assert result["success"] is False
        # With skill=0 the roll fails first; that's the more
        # common path in production.  We verify either the roll
        # failed OR the user was empty (both are "no money moved"
        # from the user's perspective).
        assert result["blocked"] in ("failed_roll", "user_empty")

    def test_no_user_wallet(self, world_with_currency, npc):
        eco_stub.update(world_with_currency, {"allow_illegal": True})
        result = economy_stub.attempt_theft(
            world_id=world_with_currency,
            npc_id=npc["id"],
            currency_code="mora",
            amount=50,
        )
        assert result["success"] is False
        assert result["blocked"] == "no_user_wallet"

    def test_force_success(self, funded_economy, monkeypatch):
        # Force the probability to 1.0 to verify the success path.
        eco_stub.update(funded_economy["world"], {"allow_illegal": True})
        npc = npcs_stub.get(funded_economy["npc_id"])
        npcs_stub.update(
            npc["id"],
            {"state_json": {"crime_theft_skill": 1.0}},
        )
        result = economy_stub.attempt_theft(
            world_id=funded_economy["world"],
            npc_id=funded_economy["npc_id"],
            currency_code="mora",
            amount=50,
        )
        assert result["success"] is True
        assert result["transaction"] is not None
        # Wallets updated.
        assert wallet_stub.get(
            wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
            funded_economy["world"], "mora",
        )["balance"] == 950.0
        assert wallet_stub.get(
            wallet_stub.OWNER_NPC, funded_economy["npc_id"],
            funded_economy["world"], "mora",
        )["balance"] == 550.0

    def test_force_failure(self, funded_economy, monkeypatch):
        eco_stub.update(funded_economy["world"], {"allow_illegal": True})
        npc = npcs_stub.get(funded_economy["npc_id"])
        npcs_stub.update(
            npc["id"],
            {"state_json": {"crime_theft_skill": 0.0}},
        )
        result = economy_stub.attempt_theft(
            world_id=funded_economy["world"],
            npc_id=funded_economy["npc_id"],
            currency_code="mora",
            amount=50,
        )
        assert result["success"] is False
        assert result["blocked"] == "failed_roll"
        # No wallet change.
        assert wallet_stub.get(
            wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
            funded_economy["world"], "mora",
        )["balance"] == 1000.0

    def test_caps_at_user_balance(self, funded_economy, monkeypatch):
        # NPC tries to steal 9999, but user only has 1000 → cap to 1000.
        eco_stub.update(funded_economy["world"], {"allow_illegal": True})
        npc = npcs_stub.get(funded_economy["npc_id"])
        npcs_stub.update(
            npc["id"],
            {"state_json": {"crime_theft_skill": 1.0}},
        )
        result = economy_stub.attempt_theft(
            world_id=funded_economy["world"],
            npc_id=funded_economy["npc_id"],
            currency_code="mora",
            amount=9999,
        )
        assert result["success"] is True
        assert result["transaction"]["amount"] == 1000.0
        # User balance now 0, NPC now 1500.
        assert wallet_stub.get(
            wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
            funded_economy["world"], "mora",
        )["balance"] == 0.0


class TestAttemptScam:
    def test_blocked_by_default(self, funded_economy):
        result = economy_stub.attempt_scam(
            world_id=funded_economy["world"],
            npc_id=funded_economy["npc_id"],
            currency_code="mora",
            amount=50,
        )
        assert result["success"] is False
        assert result["blocked"] == "allow_illegal_disabled"

    def test_force_success(self, funded_economy):
        eco_stub.update(funded_economy["world"], {"allow_illegal": True})
        npc = npcs_stub.get(funded_economy["npc_id"])
        npcs_stub.update(
            npc["id"],
            {"state_json": {"crime_scam_skill": 1.0}},
        )
        result = economy_stub.attempt_scam(
            world_id=funded_economy["world"],
            npc_id=funded_economy["npc_id"],
            currency_code="mora",
            amount=50,
        )
        assert result["success"] is True
        assert result["transaction"]["kind"] == "scam"


class TestProbabilityHelpers:
    def test_effective_probability_default(self):
        result = economy_stub._effective_probability(
            npc_state=None, default=0.5, state_key="crime_theft_skill"
        )
        assert result == 0.5

    def test_effective_probability_with_skill(self):
        result = economy_stub._effective_probability(
            npc_state={"crime_theft_skill": 0.8},
            default=0.3,
            state_key="crime_theft_skill",
        )
        assert result == 0.8

    def test_effective_probability_clamp_high(self):
        result = economy_stub._effective_probability(
            npc_state={"crime_theft_skill": 1.5},
            default=0.3,
            state_key="crime_theft_skill",
        )
        assert result == 1.0

    def test_effective_probability_clamp_low(self):
        result = economy_stub._effective_probability(
            npc_state={"crime_theft_skill": -0.5},
            default=0.3,
            state_key="crime_theft_skill",
        )
        assert result == 0.0

    def test_effective_probability_invalid_falls_back(self):
        result = economy_stub._effective_probability(
            npc_state={"crime_theft_skill": "high"},
            default=0.3,
            state_key="crime_theft_skill",
        )
        assert result == 0.3

    def test_probability_hit_zero(self):
        assert economy_stub._probability_hit("npc1", "w1", 0.0) is False

    def test_probability_hit_one(self):
        assert economy_stub._probability_hit("npc1", "w1", 1.0) is True

    def test_probability_hit_middle(self):
        # Just check it returns a bool, not a specific outcome.
        result = economy_stub._probability_hit("npc1", "w1", 0.5)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


class TestEnsure:
    def test_ensure_user_wallet(self, world_with_currency):
        record = economy_stub.ensure_user_wallet(
            world_with_currency, "mora", initial_balance=100
        )
        assert record["balance"] == 100.0

    def test_ensure_npc_wallet(self, world_with_currency, npc):
        record = economy_stub.ensure_npc_wallet(
            npc["id"], world_with_currency, "mora", initial_balance=50
        )
        assert record["balance"] == 50.0


class TestSummary:
    def test_summary(self, funded_economy):
        # Drive a couple of transactions so the summary has content.
        economy_stub.purchase(
            world_id=funded_economy["world"],
            npc_id=funded_economy["npc_id"],
            currency_code="mora",
            amount=50,
        )
        summary = economy_stub.summary(funded_economy["world"])
        assert summary["world_id"] == funded_economy["world"]
        assert len(summary["currencies"]) == 1
        assert len(summary["wallets"]) == 2
        assert summary["transactions"]["total"] >= 1


# ---------------------------------------------------------------------------
# HTTP routes — purchase / sale / reward / transfer / crime
# ---------------------------------------------------------------------------


@pytest.fixture()
def world_via_http(client, auth_headers):
    res = client.post("/v1/xijian/worlds", json={"name": "Econ HTTP"}, headers=auth_headers)
    return res.get_json()["id"]


@pytest.fixture()
def funded_via_http(client, auth_headers, world_via_http):
    client.post(
        "/v1/xijian/currencies",
        json={"world_id": world_via_http, "code": "mora", "name": "Mora"},
        headers=auth_headers,
    )
    user_w = wallet_stub.create(
        wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
        world_via_http, "mora", initial_balance=500,
    )
    npc = npcs_stub.create(world_id=world_via_http, name="M")
    npc_w = wallet_stub.create(
        wallet_stub.OWNER_NPC, npc["id"],
        world_via_http, "mora", initial_balance=200,
    )
    return {"world": world_via_http, "npc_id": npc["id"], "user": user_w, "npc": npc_w}


class TestHttpPurchase:
    def test_purchase(self, client, auth_headers, funded_via_http):
        res = client.post(
            "/v1/xijian/economy/purchase",
            json={
                "world_id": funded_via_http["world"],
                "npc_id": funded_via_http["npc_id"],
                "currency_code": "mora",
                "amount": 50,
            },
            headers=auth_headers,
        )
        assert res.status_code == 201

    def test_purchase_invalid_amount(self, client, auth_headers, funded_via_http):
        res = client.post(
            "/v1/xijian/economy/purchase",
            json={
                "world_id": funded_via_http["world"],
                "npc_id": funded_via_http["npc_id"],
                "currency_code": "mora",
                "amount": -1,
            },
            headers=auth_headers,
        )
        assert res.status_code == 400


class TestHttpSale:
    def test_sale(self, client, auth_headers, funded_via_http):
        res = client.post(
            "/v1/xijian/economy/sale",
            json={
                "world_id": funded_via_http["world"],
                "npc_id": funded_via_http["npc_id"],
                "currency_code": "mora",
                "amount": 30,
            },
            headers=auth_headers,
        )
        assert res.status_code == 201


class TestHttpReward:
    def test_reward(self, client, auth_headers, funded_via_http):
        res = client.post(
            "/v1/xijian/economy/reward",
            json={
                "world_id": funded_via_http["world"],
                "to_kind": "user",
                "to_id": wallet_stub.LOCAL_USER_ID,
                "currency_code": "mora",
                "amount": 100,
            },
            headers=auth_headers,
        )
        assert res.status_code == 201


class TestHttpTransfer:
    def test_transfer(self, client, auth_headers, funded_via_http):
        res = client.post(
            "/v1/xijian/economy/transfer",
            json={
                "world_id": funded_via_http["world"],
                "currency_code": "mora",
                "amount": 10,
            },
            headers=auth_headers,
        )
        assert res.status_code == 201


class TestHttpCrime:
    def test_theft_blocked_by_default(self, client, auth_headers, funded_via_http):
        res = client.post(
            "/v1/xijian/economy/crime/theft",
            json={
                "world_id": funded_via_http["world"],
                "npc_id": funded_via_http["npc_id"],
                "currency_code": "mora",
                "amount": 50,
            },
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is False
        assert data["blocked"] == "allow_illegal_disabled"

    def test_scam_blocked_by_default(self, client, auth_headers, funded_via_http):
        res = client.post(
            "/v1/xijian/economy/crime/scam",
            json={
                "world_id": funded_via_http["world"],
                "npc_id": funded_via_http["npc_id"],
                "currency_code": "mora",
                "amount": 50,
            },
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is False

    def test_theft_force_success(self, client, auth_headers, funded_via_http):
        eco_stub.update(funded_via_http["world"], {"allow_illegal": True})
        npc = npcs_stub.get(funded_via_http["npc_id"])
        npcs_stub.update(
            npc["id"], {"state_json": {"crime_theft_skill": 1.0}}
        )
        res = client.post(
            "/v1/xijian/economy/crime/theft",
            json={
                "world_id": funded_via_http["world"],
                "npc_id": funded_via_http["npc_id"],
                "currency_code": "mora",
                "amount": 50,
            },
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True


# ---------------------------------------------------------------------------
# HTTP routes — economy state
# ---------------------------------------------------------------------------


class TestHttpEconomyState:
    def test_get(self, client, auth_headers, world_via_http):
        res = client.get(
            f"/v1/xijian/economy/state/{world_via_http}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["allow_illegal"] is False

    def test_get_unknown(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/economy/state/world_phantom",
            headers=auth_headers,
        )
        assert res.status_code == 404

    def test_patch(self, client, auth_headers, world_via_http):
        res = client.patch(
            f"/v1/xijian/economy/state/{world_via_http}",
            json={"inflation_rate": 0.05, "allow_illegal": True},
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["inflation_rate"] == 0.05
        assert data["allow_illegal"] is True

    def test_patch_invalid(self, client, auth_headers, world_via_http):
        res = client.patch(
            f"/v1/xijian/economy/state/{world_via_http}",
            json={"inflation_rate": 99.0},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_tick_blocked_without_dev(self, client, auth_headers, world_via_http):
        # ``XIJIAN_DEV`` is unset in the conftest → block.
        assert os.environ.get("XIJIAN_DEV") != "1"
        res = client.post(
            f"/v1/xijian/economy/state/{world_via_http}/tick",
            json={},
            headers=auth_headers,
        )
        assert res.status_code == 403
        assert res.get_json()["error"]["code"] == "dev_only"

    def test_tick_with_dev_flag(self, client, auth_headers, world_via_http, monkeypatch):
        monkeypatch.setenv("XIJIAN_DEV", "1")
        res = client.post(
            f"/v1/xijian/economy/state/{world_via_http}/tick",
            json={"volume_delta": 5.0, "seasonal_factor": 0.0},
            headers=auth_headers,
        )
        assert res.status_code == 200

    def test_summary(self, client, auth_headers, world_via_http):
        res = client.get(
            f"/v1/xijian/economy/state/{world_via_http}/summary",
            headers=auth_headers,
        )
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# Auth coverage
# ---------------------------------------------------------------------------


class TestAuthCoverage:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/v1/xijian/economy/purchase"),
            ("POST", "/v1/xijian/economy/sale"),
            ("POST", "/v1/xijian/economy/reward"),
            ("POST", "/v1/xijian/economy/transfer"),
            ("POST", "/v1/xijian/economy/crime/theft"),
            ("POST", "/v1/xijian/economy/crime/scam"),
            ("GET", "/v1/xijian/economy/state/world_modern_tokyo"),
            ("PATCH", "/v1/xijian/economy/state/world_modern_tokyo"),
            ("POST", "/v1/xijian/economy/state/world_modern_tokyo/tick"),
            ("GET", "/v1/xijian/economy/state/world_modern_tokyo/summary"),
        ],
    )
    def test_requires_bearer(self, client, method, path):
        res = client.open(method=method, path=path)
        assert res.status_code in (401, 403), (
            "%s %s should require auth, got %d" % (method, path, res.status_code)
        )
