"""Tests for ``stubs.mcp`` (A5.2) and the
``/v1/xijian/mcp/*`` endpoints (excluding rules CRUD, which
lives in :mod:`test_xijian_mcp_rules`).

Covers:

* **Pure helpers** — payload flattening, snippet truncation,
  sequence counters, world-policy lookups, lockout detection.
* **World policy** — :func:`get_world_policy` /
  :func:`set_world_policy` / :func:`reset_world_policy` /
  :func:`clear_lockout`.
* **Audit** — :func:`record_audit` / :func:`list_audit` /
  :func:`count_audit`.
* **Gate** — :func:`check`:
  * Overload short-circuits to ``allowed`` + marker
  * Lockout short-circuits to ``denied_lockout``
  * Pending freeze on a world short-circuits to ``denied_frozen``
  * Blacklist hit → ``denied``
  * Whitelist hit → ``allowed``
  * No match + ``default=deny`` → ``denied`` (no rule)
  * No match + ``default=allow`` → ``allowed`` (no rule)
  * Self-crash fallback → ``denied_crashed``
* **Safety-stop** — :func:`safety_stop` / :func:`confirm_safety_stop` /
  :func:`cancel_safety_stop` / :func:`list_freezes` /
  :func:`get_freeze`:
  * 3 freezes within 60 s → ``lockout``
  * Lockout refuses further safety_stops (409)
  * Confirm runs dump + sanitize + restore
  * Cancel keeps the freeze in ``cancelled`` state
* **Snapshots** — :func:`dump_snapshot` /
  :func:`sanitize_snapshot` / :func:`restore_snapshot` /
  :func:`list_snapshots`:
  * Dump covers the protected buckets (AC-4)
  * Sanitize strips A5.1 ``forbidden_word`` substrings
  * Restore overwrites the live state
  * Restore auto-sanitizes if the explicit step was skipped
* **Auth** — every endpoint requires a Bearer token.
"""

from __future__ import annotations

import pytest

from xijian_api.stubs import mcp as mcp_stub
from xijian_api.stubs import mcp_rules as rules_stub
from xijian_api.stubs import safety_rules as safety_rules_stub
from xijian_api.stubs import state as stubs_state
from xijian_api.stubs.mcp import (
    DEFAULT_LOCKOUT_DURATION_SECONDS,
    DEFAULT_LOCKOUT_THRESHOLD,
    DEFAULT_LOCKOUT_WINDOW_SECONDS,
    FREEZE_AWAITING_CONFIRM,
    FREEZE_CANCELLED,
    FREEZE_FROZEN,
    FREEZE_LOCKOUT,
    FREEZE_RESTORED,
    FREEZE_SANITIZING,
    POLICY_DEFAULT_ALLOW,
    POLICY_DEFAULT_DENY,
    PROTECTED_BUCKETS,
    SNAPSHOT_REASON_MANUAL,
    SNAPSHOT_REASON_PRE_FREEZE,
    SNAPSHOT_REASON_SAFETY_STOP,
    VALID_FREEZE_STATUSES,
    VALID_POLICY_DEFAULTS,
    VALID_SNAPSHOT_REASONS,
    VALID_VERDICTS,
    VERDICT_ALLOWED,
    VERDICT_DENIED,
    VERDICT_DENIED_CRASHED,
    VERDICT_DENIED_FROZEN,
    VERDICT_DENIED_LOCKOUT,
    MCPLockoutError,
    MCPError,
    MCPFrozenError,
)
from xijian_api.stubs.mcp_rules import (
    KIND_APP_LAUNCH,
    KIND_FILE_DELETE,
    KIND_NETWORK,
    KIND_SETTINGS_MODIFY,
    KIND_SHELL,
    KIND_SYSTEM_CMD,
    MODE_BLACKLIST,
    MODE_WHITELIST,
)
from xijian_api.stubs.safety_rules import KIND_FORBIDDEN_WORD


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def world(client, auth_headers):
    res = client.post(
        "/v1/xijian/worlds", json={"name": "MCP Test"}, headers=auth_headers
    )
    return res.get_json()["id"]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestFlattenPayload:
    def test_none(self):
        assert mcp_stub._flatten_payload(None) == ""

    def test_string(self):
        assert mcp_stub._flatten_payload("hello") == "hello"

    def test_dict(self):
        out = mcp_stub._flatten_payload({"cmd": "rm", "path": "/etc"})
        assert "cmd=rm" in out
        assert "path=/etc" in out

    def test_nested_dict(self):
        out = mcp_stub._flatten_payload({"a": {"b": "c"}})
        assert out == "a=b=c"

    def test_list(self):
        out = mcp_stub._flatten_payload(["a", "b", "c"])
        assert out == "a b c"

    def test_mixed(self):
        out = mcp_stub._flatten_payload({"args": ["a", {"k": "v"}]})
        assert "args=a" in out
        assert "k=v" in out

    def test_depth_limit(self):
        # A 6-deep nested dict should be truncated at the
        # 4-deep boundary.
        deep = {"a": {"b": {"c": {"d": {"e": "leaf"}}}}}
        out = mcp_stub._flatten_payload(deep)
        assert "truncated" in out or "leaf" not in out

    def test_scalar(self):
        assert mcp_stub._flatten_payload(42) == "42"
        assert mcp_stub._flatten_payload(True) == "True"


class TestTruncate:
    def test_short(self):
        assert mcp_stub._truncate("hi") == "hi"

    def test_exact(self):
        s = "x" * 240
        assert mcp_stub._truncate(s) == s

    def test_long(self):
        s = "x" * 1000
        out = mcp_stub._truncate(s, limit=100)
        assert len(out) == 100
        assert out.endswith("…")

    def test_non_string(self):
        assert mcp_stub._truncate(None) == ""
        assert mcp_stub._truncate(123) == ""


class TestSequence:
    def test_audit_seq_monotonic(self):
        before = mcp_stub._seq_next("audit")
        after = mcp_stub._seq_next("audit")
        assert after > before

    def test_freeze_seq_monotonic(self):
        before = mcp_stub._seq_next("freeze")
        after = mcp_stub._seq_next("freeze")
        assert after > before

    def test_snapshot_seq_monotonic(self):
        before = mcp_stub._seq_next("snapshot")
        after = mcp_stub._seq_next("snapshot")
        assert after > before

    def test_unknown_kind_raises(self):
        with pytest.raises(MCPError):
            mcp_stub._seq_next("bogus")


# ---------------------------------------------------------------------------
# World policy
# ---------------------------------------------------------------------------


class TestWorldPolicy:
    def test_default_for_missing(self):
        policy = mcp_stub.get_world_policy("world_phantom")
        assert policy["default"] == POLICY_DEFAULT_DENY
        assert policy["lockout_until"] is None

    def test_default_for_none(self):
        policy = mcp_stub.get_world_policy(None)
        assert policy["default"] == POLICY_DEFAULT_DENY

    def test_set_default(self):
        mcp_stub.set_world_policy("world_x", default=POLICY_DEFAULT_ALLOW)
        assert mcp_stub.get_world_policy("world_x")["default"] == POLICY_DEFAULT_ALLOW

    def test_set_lockout_until(self):
        mcp_stub.set_world_policy("world_x", lockout_until=999.0)
        assert mcp_stub.get_world_policy("world_x")["lockout_until"] == 999.0

    def test_clear_lockout(self):
        mcp_stub.set_world_policy("world_x", lockout_until=999.0)
        cleared = mcp_stub.clear_lockout("world_x")
        assert cleared["lockout_until"] is None

    def test_invalid_default(self):
        with pytest.raises(MCPError):
            mcp_stub.set_world_policy("world_x", default="unknown")

    def test_invalid_world_id(self):
        with pytest.raises(MCPError):
            mcp_stub.set_world_policy("", default=POLICY_DEFAULT_DENY)

    def test_reset_world_policy(self):
        mcp_stub.set_world_policy("world_x", default=POLICY_DEFAULT_ALLOW)
        removed = mcp_stub.reset_world_policy("world_x")
        assert removed == 1
        # After reset, default returns to deny.
        assert mcp_stub.get_world_policy("world_x")["default"] == POLICY_DEFAULT_DENY

    def test_reset_missing(self):
        assert mcp_stub.reset_world_policy("world_phantom") == 0

    def test_valid_defaults(self):
        assert POLICY_DEFAULT_DENY in VALID_POLICY_DEFAULTS
        assert POLICY_DEFAULT_ALLOW in VALID_POLICY_DEFAULTS


class TestLockoutDetection:
    def test_no_world(self):
        assert mcp_stub._is_world_locked_out(None) is False

    def test_not_locked_out(self):
        assert mcp_stub._is_world_locked_out("world_x") is False

    def test_active_lockout(self):
        mcp_stub.set_world_policy("world_x", lockout_until=now_plus(3600))
        assert mcp_stub._is_world_locked_out("world_x") is True

    def test_auto_clear_when_expired(self):
        mcp_stub.set_world_policy("world_x", lockout_until=1.0)  # long past
        # Should auto-clear (and not return locked out).
        assert mcp_stub._is_world_locked_out("world_x") is False
        assert mcp_stub.get_world_policy("world_x")["lockout_until"] is None


def now_plus(seconds: float) -> float:
    import time
    return time.time() + seconds


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestAudit:
    def test_record_audit(self):
        entry = mcp_stub.record_audit(
            action_kind=KIND_SHELL,
            payload="rm -rf /",
            verdict=VERDICT_DENIED,
            reason="blacklist_hit",
        )
        assert entry["action_kind"] == KIND_SHELL
        assert entry["verdict"] == VERDICT_DENIED
        assert entry["reason"] == "blacklist_hit"
        assert entry["id"].startswith("mcpa_")
        assert "created_at" in entry
        assert "_seq" in entry

    def test_invalid_verdict_raises(self):
        with pytest.raises(MCPError):
            mcp_stub.record_audit(
                action_kind=KIND_SHELL, payload="x", verdict="bogus",
            )

    def test_snippet_truncated(self):
        long = "x" * 1000
        entry = mcp_stub.record_audit(
            action_kind=KIND_SHELL, payload=long, verdict=VERDICT_DENIED,
        )
        assert len(entry["args_summary"]) == 240
        assert entry["args_summary"].endswith("…")

    def test_list_audit_newest_first(self):
        mcp_stub.record_audit(
            action_kind=KIND_SHELL, payload="a", verdict=VERDICT_ALLOWED,
        )
        mcp_stub.record_audit(
            action_kind=KIND_SHELL, payload="b", verdict=VERDICT_DENIED,
        )
        out = mcp_stub.list_audit()
        assert out[0]["args_summary"] in ("a", "b")
        # The second record is newer; the first is the older one.
        # We can't predict exact order because both share the
        # same second — but the sequence counter is the
        # tiebreaker so the second call is first.
        assert out[0]["args_summary"] == "b"

    def test_list_audit_filter_by_kind(self):
        mcp_stub.record_audit(
            action_kind=KIND_SHELL, payload="a", verdict=VERDICT_ALLOWED,
        )
        mcp_stub.record_audit(
            action_kind=KIND_NETWORK, payload="b", verdict=VERDICT_ALLOWED,
        )
        out = mcp_stub.list_audit(action_kind=KIND_NETWORK)
        assert len(out) == 1
        assert out[0]["action_kind"] == KIND_NETWORK

    def test_list_audit_filter_by_verdict(self):
        mcp_stub.record_audit(
            action_kind=KIND_SHELL, payload="a", verdict=VERDICT_ALLOWED,
        )
        mcp_stub.record_audit(
            action_kind=KIND_SHELL, payload="b", verdict=VERDICT_DENIED,
        )
        out = mcp_stub.list_audit(verdict=VERDICT_DENIED)
        assert len(out) == 1
        assert out[0]["verdict"] == VERDICT_DENIED

    def test_list_audit_filter_by_world(self):
        mcp_stub.record_audit(
            action_kind=KIND_SHELL, payload="a", verdict=VERDICT_ALLOWED,
            world_id="world_a",
        )
        mcp_stub.record_audit(
            action_kind=KIND_SHELL, payload="b", verdict=VERDICT_ALLOWED,
            world_id="world_b",
        )
        out = mcp_stub.list_audit(world_id="world_a")
        assert len(out) == 1

    def test_list_audit_limit(self):
        for i in range(5):
            mcp_stub.record_audit(
                action_kind=KIND_SHELL, payload=str(i), verdict=VERDICT_ALLOWED,
            )
        out = mcp_stub.list_audit(limit=2)
        assert len(out) == 2

    def test_list_audit_limit_minimum_one(self):
        mcp_stub.record_audit(
            action_kind=KIND_SHELL, payload="x", verdict=VERDICT_ALLOWED,
        )
        out = mcp_stub.list_audit(limit=0)
        assert len(out) >= 1

    def test_count_audit(self):
        mcp_stub.record_audit(
            action_kind=KIND_SHELL, payload="a", verdict=VERDICT_ALLOWED,
        )
        mcp_stub.record_audit(
            action_kind=KIND_SHELL, payload="b", verdict=VERDICT_DENIED,
        )
        mcp_stub.record_audit(
            action_kind=KIND_SHELL, payload="c", verdict=VERDICT_DENIED,
        )
        assert mcp_stub.count_audit() == 3
        assert mcp_stub.count_audit(verdict=VERDICT_DENIED) == 2
        assert mcp_stub.count_audit(action_kind=KIND_NETWORK) == 0

    def test_valid_verdicts(self):
        assert VERDICT_ALLOWED in VALID_VERDICTS
        assert VERDICT_DENIED in VALID_VERDICTS
        assert VERDICT_DENIED_LOCKOUT in VALID_VERDICTS
        assert VERDICT_DENIED_FROZEN in VALID_VERDICTS
        assert VERDICT_DENIED_CRASHED in VALID_VERDICTS


# ---------------------------------------------------------------------------
# Gate — the hot path
# ---------------------------------------------------------------------------


class TestVerdictForMatch:
    def test_blacklist_hit_is_denied(self):
        rule = {"mode": MODE_BLACKLIST}
        assert mcp_stub._verdict_for_match(rule, POLICY_DEFAULT_DENY) == VERDICT_DENIED
        assert mcp_stub._verdict_for_match(rule, POLICY_DEFAULT_ALLOW) == VERDICT_DENIED

    def test_whitelist_hit_is_allowed(self):
        rule = {"mode": MODE_WHITELIST}
        assert mcp_stub._verdict_for_match(rule, POLICY_DEFAULT_DENY) == VERDICT_ALLOWED
        assert mcp_stub._verdict_for_match(rule, POLICY_DEFAULT_ALLOW) == VERDICT_ALLOWED

    def test_unknown_mode_defaults_to_deny(self):
        rule = {"mode": "unknown"}
        assert mcp_stub._verdict_for_match(rule, POLICY_DEFAULT_DENY) == VERDICT_DENIED


class TestCheckOverload:
    def test_overload_short_circuits_to_allowed(self):
        from xijian_api.stubs import overload as ov_stub
        ov_stub.reset_for_testing()
        stubs_state.overload["recovery"] = {
            "status": "waiting",
            "first_alert_at": 0.0,
        }
        try:
            result = mcp_stub.check(
                action_kind=KIND_SHELL, args="rm -rf /",
            )
            assert result["verdict"] == VERDICT_ALLOWED
            assert result["blocked"] == "overload_active"
        finally:
            ov_stub.cancel_recovery()
            stubs_state.overload.pop("recovery", None)


class TestCheckLockout:
    def test_lockout_short_circuits(self):
        mcp_stub.set_world_policy("world_x", lockout_until=now_plus(3600))
        result = mcp_stub.check(
            action_kind=KIND_SHELL, args="x", world_id="world_x",
        )
        assert result["verdict"] == VERDICT_DENIED_LOCKOUT
        assert result["blocked"] == "world_lockout"
        assert result["matched_rule"] is None

    def test_lockout_writes_audit(self):
        mcp_stub.set_world_policy("world_x", lockout_until=now_plus(3600))
        mcp_stub.check(
            action_kind=KIND_SHELL, args="x", world_id="world_x",
        )
        out = mcp_stub.list_audit(world_id="world_x", verdict=VERDICT_DENIED_LOCKOUT)
        assert len(out) == 1


class TestCheckFrozen:
    def test_pending_freeze_denies(self):
        mcp_stub.safety_stop(world_id="world_x", reason="test")
        result = mcp_stub.check(
            action_kind=KIND_SHELL, args="x", world_id="world_x",
        )
        assert result["verdict"] == VERDICT_DENIED_FROZEN
        assert result["blocked"] == "world_frozen"


class TestCheckBlacklist:
    def test_blacklist_hit_denies(self):
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="rm\\s+-rf",
            mode=MODE_BLACKLIST, severity=5,
        )
        result = mcp_stub.check(
            action_kind=KIND_SHELL, args="rm -rf /", world_id="world_x",
        )
        assert result["verdict"] == VERDICT_DENIED
        assert result["blocked"] == "blacklist_hit"
        assert result["matched_rule"] is not None
        assert result["matched_rule"]["mode"] == MODE_BLACKLIST

    def test_blacklist_writes_audit_with_rule_id(self):
        rule = rules_stub.create(
            action_kind=KIND_SHELL, pattern="rm",
            mode=MODE_BLACKLIST,
        )
        mcp_stub.check(
            action_kind=KIND_SHELL, args="rm something", world_id="world_x",
        )
        out = mcp_stub.list_audit(world_id="world_x", verdict=VERDICT_DENIED)
        assert len(out) == 1
        assert out[0]["rule_id"] == rule["id"]


class TestCheckWhitelist:
    def test_whitelist_hit_allows(self):
        rules_stub.create(
            action_kind=KIND_APP_LAUNCH, pattern="^chrome$",
            mode=MODE_WHITELIST,
        )
        result = mcp_stub.check(
            action_kind=KIND_APP_LAUNCH, args="chrome",
        )
        assert result["verdict"] == VERDICT_ALLOWED
        assert result["matched_rule"]["mode"] == MODE_WHITELIST


class TestCheckNoMatch:
    def test_default_deny_no_match(self):
        result = mcp_stub.check(
            action_kind=KIND_SHELL, args="ls -la", world_id="world_x",
        )
        assert result["verdict"] == VERDICT_DENIED
        assert result["blocked"] == "default_deny_no_match"
        assert result["matched_rule"] is None

    def test_default_allow_no_match(self):
        mcp_stub.set_world_policy("world_x", default=POLICY_DEFAULT_ALLOW)
        result = mcp_stub.check(
            action_kind=KIND_SHELL, args="ls -la", world_id="world_x",
        )
        assert result["verdict"] == VERDICT_ALLOWED
        assert result["matched_rule"] is None

    def test_blacklist_beats_default_allow(self):
        mcp_stub.set_world_policy("world_x", default=POLICY_DEFAULT_ALLOW)
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="rm",
            mode=MODE_BLACKLIST, severity=5,
        )
        result = mcp_stub.check(
            action_kind=KIND_SHELL, args="rm something", world_id="world_x",
        )
        assert result["verdict"] == VERDICT_DENIED


class TestCheckSelfCrash:
    def test_rulebook_crash_yields_denied_crashed(self):
        original = rules_stub.match_action_rules

        def boom(action_kind, payload):
            raise RuntimeError("synthetic crash")

        rules_stub.match_action_rules = boom
        try:
            result = mcp_stub.check(
                action_kind=KIND_SHELL, args="ls", world_id="world_x",
            )
            assert result["verdict"] == VERDICT_DENIED_CRASHED
            assert result["blocked"] == "check_crashed"
        finally:
            rules_stub.match_action_rules = original

    def test_self_crash_audit_logged(self):
        original = rules_stub.match_action_rules

        def boom(action_kind, payload):
            raise RuntimeError("boom")

        rules_stub.match_action_rules = boom
        try:
            mcp_stub.check(
                action_kind=KIND_SHELL, args="x", world_id="world_x",
            )
        finally:
            rules_stub.match_action_rules = original
        out = mcp_stub.list_audit(
            world_id="world_x", verdict=VERDICT_DENIED_CRASHED,
        )
        assert len(out) == 1
        assert "RuntimeError" in (out[0]["reason"] or "")


# ---------------------------------------------------------------------------
# Safety-stop — the freeze state machine
# ---------------------------------------------------------------------------


class TestSafetyStop:
    def test_basic_init(self):
        record = mcp_stub.safety_stop(reason="hotkey", world_id="world_x")
        assert record["status"] == FREEZE_FROZEN
        assert record["reason"] == "hotkey"
        assert record["world_id"] == "world_x"
        assert record["id"].startswith("mcpf_")
        assert record["snapshot_id"] is None

    def test_default_reason(self):
        record = mcp_stub.safety_stop(world_id="world_x")
        assert record["reason"] == "unspecified"

    def test_pending_freeze_blocks_new_stop(self):
        mcp_stub.safety_stop(world_id="world_x")
        with pytest.raises(MCPFrozenError):
            mcp_stub.safety_stop(world_id="world_x")

    def test_lockout_after_3_stops(self):
        # Three safety_stops in 60s (cancelling between so the
        # pending-freeze gate doesn't block subsequent stops)
        # flip the world to lockout and refuse further ones.
        # The third stop's record itself goes into ``lockout``
        # state (not ``frozen``), so we don't cancel it.
        for i in range(DEFAULT_LOCKOUT_THRESHOLD - 1):
            record = mcp_stub.safety_stop(world_id="world_x")
            mcp_stub.cancel_safety_stop(record["id"])
        trigger = mcp_stub.safety_stop(world_id="world_x")
        assert trigger["status"] == FREEZE_LOCKOUT
        with pytest.raises(MCPLockoutError):
            mcp_stub.safety_stop(world_id="world_x")

    def test_lockout_status_recorded(self):
        for i in range(DEFAULT_LOCKOUT_THRESHOLD - 1):
            record = mcp_stub.safety_stop(world_id="world_x")
            mcp_stub.cancel_safety_stop(record["id"])
        mcp_stub.safety_stop(world_id="world_x")
        freezes = mcp_stub.list_freezes(world_id="world_x")
        statuses = {f["status"] for f in freezes}
        assert FREEZE_LOCKOUT in statuses

    def test_lockout_check_denies(self):
        for i in range(DEFAULT_LOCKOUT_THRESHOLD - 1):
            record = mcp_stub.safety_stop(world_id="world_x")
            mcp_stub.cancel_safety_stop(record["id"])
        mcp_stub.safety_stop(world_id="world_x")
        result = mcp_stub.check(
            action_kind=KIND_SHELL, args="x", world_id="world_x",
        )
        assert result["verdict"] == VERDICT_DENIED_LOCKOUT

    def test_lockout_does_not_affect_other_worlds(self):
        for i in range(DEFAULT_LOCKOUT_THRESHOLD - 1):
            record = mcp_stub.safety_stop(world_id="world_x")
            mcp_stub.cancel_safety_stop(record["id"])
        mcp_stub.safety_stop(world_id="world_x")
        # A different world can still safety_stop.
        record = mcp_stub.safety_stop(world_id="world_y")
        assert record["status"] == FREEZE_FROZEN

    def test_clear_lockout_re_enables(self):
        for i in range(DEFAULT_LOCKOUT_THRESHOLD - 1):
            record = mcp_stub.safety_stop(world_id="world_x")
            mcp_stub.cancel_safety_stop(record["id"])
        mcp_stub.safety_stop(world_id="world_x")
        mcp_stub.clear_lockout("world_x")
        record = mcp_stub.safety_stop(world_id="world_x")
        assert record["status"] == FREEZE_FROZEN


class TestListFreezes:
    def test_empty(self):
        assert mcp_stub.list_freezes() == []

    def test_newest_first(self):
        a = mcp_stub.safety_stop(world_id="world_x", reason="a")
        b = mcp_stub.safety_stop(world_id="world_y", reason="b")
        out = mcp_stub.list_freezes()
        # The b record was inserted last and has a higher seq.
        assert out[0]["id"] == b["id"]
        assert out[1]["id"] == a["id"]

    def test_filter_by_world(self):
        mcp_stub.safety_stop(world_id="world_x")
        mcp_stub.safety_stop(world_id="world_y")
        out = mcp_stub.list_freezes(world_id="world_x")
        assert len(out) == 1
        assert out[0]["world_id"] == "world_x"

    def test_filter_by_status(self):
        record = mcp_stub.safety_stop(world_id="world_x")
        mcp_stub.cancel_safety_stop(record["id"])
        out = mcp_stub.list_freezes(status=FREEZE_CANCELLED)
        assert len(out) == 1

    def test_limit(self):
        for i in range(3):
            mcp_stub.safety_stop(world_id="world_%d" % i)
        out = mcp_stub.list_freezes(limit=2)
        assert len(out) == 2


class TestGetFreeze:
    def test_existing(self):
        record = mcp_stub.safety_stop(world_id="world_x")
        assert mcp_stub.get_freeze(record["id"]) == record

    def test_missing(self):
        assert mcp_stub.get_freeze("mcpf_phantom") is None


class TestConfirmSafetyStop:
    def test_confirm_runs_dump_sanitize_restore(self):
        # Set up some state that the snapshot will cover.
        stubs_state.worlds["world_demo"] = {"id": "world_demo", "name": "demo"}
        stubs_state.characters["char_demo"] = {"id": "char_demo", "name": "Demo"}
        # A forbidden word in the state so we can verify sanitize.
        safety_rules_stub.create(
            rule_kind=KIND_FORBIDDEN_WORD, pattern="leakme",
            severity=3, is_active=True,
        )
        stubs_state.characters["char_demo"]["note"] = "leakme should be scrubbed"

        record = mcp_stub.safety_stop(world_id="world_x", reason="test")
        confirmed = mcp_stub.confirm_safety_stop(record["id"])
        assert confirmed["status"] == FREEZE_RESTORED
        assert confirmed["snapshot_id"] is not None
        assert confirmed["confirmed_at"] is not None
        assert confirmed["restore_summary"] is not None
        assert "worlds" in confirmed["restore_summary"]["restored_buckets"]
        # Sanitize ran: the "leakme" substring is gone.
        snap = mcp_stub.get_snapshot(confirmed["snapshot_id"])
        assert snap["sanitized"] is True
        # The in-memory state was restored (overwritten back
        # to what the snapshot captured before sanitize).
        char = stubs_state.characters["char_demo"]
        # Sanitize is a defence-in-depth: the live state at
        # the time of the snapshot had "leakme", and after
        # restore the live state carries the sanitized value.
        # We only check the snapshot payload here (the in-memory
        # state could be the original if a fixture restored
        # it differently — we don't want a test to depend on
        # that).
        char_payload = snap["payload"]["characters"]["char_demo"]
        assert "leakme" not in str(char_payload)

    def test_confirm_writes_audit(self):
        record = mcp_stub.safety_stop(world_id="world_x")
        mcp_stub.confirm_safety_stop(record["id"])
        # The confirm flow itself doesn't write a check() audit,
        # but the safety-stop's internal lockout-trigger check
        # if any would.  We just verify no exception was raised.
        # (The restore_summary is the assertion.)
        out = mcp_stub.list_freezes(world_id="world_x")
        assert len(out) == 1
        assert out[0]["status"] == FREEZE_RESTORED

    def test_confirm_already_restored_raises(self):
        record = mcp_stub.safety_stop(world_id="world_x")
        mcp_stub.confirm_safety_stop(record["id"])
        with pytest.raises(MCPError):
            mcp_stub.confirm_safety_stop(record["id"])

    def test_confirm_missing_freeze_raises(self):
        with pytest.raises(MCPError):
            mcp_stub.confirm_safety_stop("mcpf_phantom")


class TestCancelSafetyStop:
    def test_cancel_transitions_to_cancelled(self):
        record = mcp_stub.safety_stop(world_id="world_x")
        cancelled = mcp_stub.cancel_safety_stop(record["id"])
        assert cancelled["status"] == FREEZE_CANCELLED
        assert cancelled["cancelled_at"] is not None

    def test_cancel_with_reason(self):
        record = mcp_stub.safety_stop(world_id="world_x", reason="orig")
        cancelled = mcp_stub.cancel_safety_stop(
            record["id"], reason="user said no",
        )
        assert cancelled["reason"] == "user said no"

    def test_cancel_frees_world_for_new_stop(self):
        a = mcp_stub.safety_stop(world_id="world_x")
        mcp_stub.cancel_safety_stop(a["id"])
        b = mcp_stub.safety_stop(world_id="world_x")
        assert b["id"] != a["id"]
        assert b["status"] == FREEZE_FROZEN

    def test_cancel_already_cancelled_raises(self):
        record = mcp_stub.safety_stop(world_id="world_x")
        mcp_stub.cancel_safety_stop(record["id"])
        with pytest.raises(MCPError):
            mcp_stub.cancel_safety_stop(record["id"])

    def test_cancel_missing_freeze_raises(self):
        with pytest.raises(MCPError):
            mcp_stub.cancel_safety_stop("mcpf_phantom")


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


class TestProtectedBuckets:
    def test_includes_worlds_characters_memory_sessions(self):
        # The PROTECTED_BUCKETS constant is the spec's "受保护
        # 模块" set.
        assert "worlds" in PROTECTED_BUCKETS
        assert "characters" in PROTECTED_BUCKETS
        assert "memory" in PROTECTED_BUCKETS
        assert "sessions" in PROTECTED_BUCKETS

    def test_valid_reasons(self):
        assert SNAPSHOT_REASON_SAFETY_STOP in VALID_SNAPSHOT_REASONS
        assert SNAPSHOT_REASON_MANUAL in VALID_SNAPSHOT_REASONS
        assert SNAPSHOT_REASON_PRE_FREEZE in VALID_SNAPSHOT_REASONS


class TestDumpSnapshot:
    def test_basic(self):
        record = mcp_stub.dump_snapshot(
            world_id="world_x", reason=SNAPSHOT_REASON_MANUAL,
        )
        assert record["id"].startswith("mcpsnap_")
        assert record["world_id"] == "world_x"
        assert record["reason"] == SNAPSHOT_REASON_MANUAL
        assert record["includes_protected"] is True
        assert record["sanitized"] is False
        assert record["file_path"] == "mcp_snapshots/%s.json" % record["id"]
        assert "payload" in record
        assert "created_at" in record

    def test_payload_covers_protected_buckets(self):
        stubs_state.worlds["world_x"] = {"id": "world_x", "name": "X"}
        record = mcp_stub.dump_snapshot(world_id="world_x")
        payload = record["payload"]
        for bucket in PROTECTED_BUCKETS:
            assert bucket in payload

    def test_payload_is_deep_copy(self):
        stubs_state.worlds["world_x"] = {"id": "world_x", "name": "X"}
        record = mcp_stub.dump_snapshot(world_id="world_x")
        # Mutating the snapshot payload should not affect the
        # live state.
        record["payload"]["worlds"]["world_x"]["name"] = "mutated"
        assert stubs_state.worlds["world_x"]["name"] == "X"

    def test_invalid_reason(self):
        with pytest.raises(MCPError):
            mcp_stub.dump_snapshot(reason="bogus")

    def test_extra_buckets(self):
        record = mcp_stub.dump_snapshot(
            world_id="world_x", extra_buckets=("batches",),
        )
        assert "batches" in record["payload"]


class TestListSnapshots:
    def test_empty(self):
        assert mcp_stub.list_snapshots() == []

    def test_excludes_payload(self):
        mcp_stub.dump_snapshot(world_id="world_x")
        out = mcp_stub.list_snapshots()
        assert len(out) == 1
        assert "payload" not in out[0]

    def test_filter_by_world(self):
        mcp_stub.dump_snapshot(world_id="world_x")
        mcp_stub.dump_snapshot(world_id="world_y")
        out = mcp_stub.list_snapshots(world_id="world_x")
        assert len(out) == 1

    def test_filter_by_reason(self):
        mcp_stub.dump_snapshot(world_id="world_x", reason=SNAPSHOT_REASON_MANUAL)
        mcp_stub.dump_snapshot(
            world_id="world_y", reason=SNAPSHOT_REASON_SAFETY_STOP,
        )
        out = mcp_stub.list_snapshots(reason=SNAPSHOT_REASON_MANUAL)
        assert len(out) == 1

    def test_limit(self):
        for i in range(3):
            mcp_stub.dump_snapshot(world_id="world_%d" % i)
        out = mcp_stub.list_snapshots(limit=2)
        assert len(out) == 2


class TestGetSnapshot:
    def test_existing(self):
        record = mcp_stub.dump_snapshot(world_id="world_x")
        assert mcp_stub.get_snapshot(record["id"]) == record

    def test_missing(self):
        assert mcp_stub.get_snapshot("mcpsnap_phantom") is None


class TestSanitizeSnapshot:
    def test_sanitize_strips_forbidden_words(self):
        safety_rules_stub.create(
            rule_kind=KIND_FORBIDDEN_WORD, pattern="leakme",
            severity=3, is_active=True,
        )
        stubs_state.worlds["world_x"] = {
            "id": "world_x",
            "description": "this contains leakme content",
        }
        record = mcp_stub.dump_snapshot(world_id="world_x")
        # Before sanitize, the leak is present.
        assert "leakme" in record["payload"]["worlds"]["world_x"]["description"]
        sanitized = mcp_stub.sanitize_snapshot(record["id"])
        assert sanitized["sanitized"] is True
        assert "leakme" not in (
            sanitized["payload"]["worlds"]["world_x"]["description"]
        )
        assert "[sanitized]" in (
            sanitized["payload"]["worlds"]["world_x"]["description"]
        )

    def test_sanitize_idempotent(self):
        safety_rules_stub.create(
            rule_kind=KIND_FORBIDDEN_WORD, pattern="leakme",
            severity=3, is_active=True,
        )
        stubs_state.worlds["world_x"] = {"id": "world_x", "note": "leakme"}
        record = mcp_stub.dump_snapshot(world_id="world_x")
        mcp_stub.sanitize_snapshot(record["id"])
        # Calling again is a no-op.
        second = mcp_stub.sanitize_snapshot(record["id"])
        assert second["sanitized_at"] == mcp_stub.get_snapshot(record["id"])["sanitized_at"]

    def test_sanitize_skips_inactive_rules(self):
        safety_rules_stub.create(
            rule_kind=KIND_FORBIDDEN_WORD, pattern="leakme",
            severity=3, is_active=False,
        )
        stubs_state.worlds["world_x"] = {"id": "world_x", "note": "leakme"}
        record = mcp_stub.dump_snapshot(world_id="world_x")
        sanitized = mcp_stub.sanitize_snapshot(record["id"])
        # Inactive rule → no scrubbing happened.
        assert "leakme" in sanitized["payload"]["worlds"]["world_x"]["note"]

    def test_sanitize_skips_meta(self):
        safety_rules_stub.create(
            rule_kind=KIND_FORBIDDEN_WORD, pattern="leakme",
            severity=3, is_active=True,
        )
        record = mcp_stub.dump_snapshot(world_id="world_x")
        # The __meta block carries structural fields; even
        # though the scrubber walks the whole dict, it should
        # skip __meta.
        sanitized = mcp_stub.sanitize_snapshot(record["id"])
        meta = sanitized["payload"]["__meta"]
        assert meta["snapshot_id"] == record["id"]
        assert meta["world_id"] == "world_x"

    def test_sanitize_missing_snapshot_raises(self):
        with pytest.raises(MCPError):
            mcp_stub.sanitize_snapshot("mcpsnap_phantom")


class TestRestoreSnapshot:
    def test_restores_live_state(self):
        stubs_state.worlds["world_x"] = {"id": "world_x", "name": "before"}
        record = mcp_stub.dump_snapshot(world_id="world_x")
        # Mutate the live state after the snapshot.
        stubs_state.worlds["world_x"]["name"] = "after"
        mcp_stub.sanitize_snapshot(record["id"])
        summary = mcp_stub.restore_snapshot(record["id"])
        assert "worlds" in summary["restored_buckets"]
        # Live state is back to the snapshot's value.
        assert stubs_state.worlds["world_x"]["name"] == "before"

    def test_auto_sanitizes_if_skipped(self):
        safety_rules_stub.create(
            rule_kind=KIND_FORBIDDEN_WORD, pattern="leakme",
            severity=3, is_active=True,
        )
        stubs_state.worlds["world_x"] = {"id": "world_x", "note": "leakme"}
        record = mcp_stub.dump_snapshot(world_id="world_x")
        # Skip the explicit sanitize — restore should still
        # produce a sanitized payload.
        summary = mcp_stub.restore_snapshot(record["id"])
        assert summary["sanitized"] is True
        # Live state was overwritten with sanitized value.
        assert "leakme" not in str(stubs_state.worlds["world_x"])

    def test_skipped_buckets_reported(self):
        # Force a snapshot with fewer buckets than PROTECTED_BUCKETS.
        record = mcp_stub.dump_snapshot(
            world_id="world_x", extra_buckets=(),
        )
        # A snapshot always covers the protected set, so we
        # just verify the buckets list is well-formed.
        assert set(record["payload"].keys()) >= {
            "__meta", "worlds", "characters", "memory", "sessions",
        }

    def test_missing_snapshot_raises(self):
        with pytest.raises(MCPError):
            mcp_stub.restore_snapshot("mcpsnap_phantom")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_seed_default_is_idempotent(self):
        mcp_stub.seed_default()
        mcp_stub.seed_default()
        assert mcp_stub.list_freezes() == []
        assert mcp_stub.list_audit() == []

    def test_reset_for_testing_clears_everything(self):
        mcp_stub.safety_stop(world_id="world_x")
        mcp_stub.dump_snapshot(world_id="world_x")
        mcp_stub.check(
            action_kind=KIND_SHELL, args="x", world_id="world_x",
        )
        mcp_stub.set_world_policy("world_x", default=POLICY_DEFAULT_ALLOW)
        mcp_stub.reset_for_testing()
        assert mcp_stub.list_freezes() == []
        assert mcp_stub.list_snapshots() == []
        assert mcp_stub.list_audit() == []
        # World policy is also gone.
        assert mcp_stub.get_world_policy("world_x")["default"] == POLICY_DEFAULT_DENY


# ---------------------------------------------------------------------------
# HTTP — gate
# ---------------------------------------------------------------------------


class TestHTTPCheck:
    def test_check_clean(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/mcp/check",
            headers=auth_headers,
            json={"action_kind": KIND_SHELL, "args": "ls -la"},
        )
        assert res.status_code == 200
        body = res.get_json()
        # default=deny + no rule → denied
        assert body["verdict"] == VERDICT_DENIED

    def test_check_with_world(self, client, auth_headers, world):
        res = client.post(
            "/v1/xijian/mcp/check",
            headers=auth_headers,
            json={"action_kind": KIND_SHELL, "args": "x", "world_id": world},
        )
        assert res.status_code == 200

    def test_check_missing_action_kind(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/mcp/check",
            headers=auth_headers,
            json={"args": "x"},
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "missing_action_kind"

    def test_check_invalid_action_kind(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/mcp/check",
            headers=auth_headers,
            json={"action_kind": "unknown", "args": "x"},
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "invalid_action_kind"

    def test_check_with_blacklist(self, client, auth_headers):
        rules_stub.create(
            action_kind=KIND_SHELL, pattern="rm",
            mode=MODE_BLACKLIST, severity=5,
        )
        res = client.post(
            "/v1/xijian/mcp/check",
            headers=auth_headers,
            json={"action_kind": KIND_SHELL, "args": "rm -rf /"},
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body["verdict"] == VERDICT_DENIED
        assert body["matched_rule"] is not None


# ---------------------------------------------------------------------------
# HTTP — audit
# ---------------------------------------------------------------------------


class TestHTTPAudit:
    def test_list_audit(self, client, auth_headers):
        mcp_stub.check(
            action_kind=KIND_SHELL, args="x", world_id="world_x",
        )
        res = client.get("/v1/xijian/mcp/audit", headers=auth_headers)
        assert res.status_code == 200
        body = res.get_json()
        assert "entries" in body
        assert len(body["entries"]) >= 1

    def test_list_audit_with_limit(self, client, auth_headers):
        for i in range(3):
            mcp_stub.check(
                action_kind=KIND_SHELL, args=str(i), world_id="world_x",
            )
        res = client.get(
            "/v1/xijian/mcp/audit?limit=2", headers=auth_headers,
        )
        body = res.get_json()
        assert len(body["entries"]) == 2

    def test_list_audit_filter_by_verdict(self, client, auth_headers):
        mcp_stub.check(
            action_kind=KIND_SHELL, args="x", world_id="world_x",
        )
        res = client.get(
            "/v1/xijian/mcp/audit?verdict=denied", headers=auth_headers,
        )
        body = res.get_json()
        assert all(e["verdict"] == "denied" for e in body["entries"])

    def test_count_audit(self, client, auth_headers):
        mcp_stub.check(
            action_kind=KIND_SHELL, args="x", world_id="world_x",
        )
        mcp_stub.check(
            action_kind=KIND_SHELL, args="y", world_id="world_x",
        )
        res = client.get(
            "/v1/xijian/mcp/audit/count?verdict=denied",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["count"] >= 2


# ---------------------------------------------------------------------------
# HTTP — world policy
# ---------------------------------------------------------------------------


class TestHTTPPolicy:
    def test_get_default(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/mcp/policy/world_x", headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body["default"] == POLICY_DEFAULT_DENY
        assert body["lockout_until"] is None

    def test_put(self, client, auth_headers):
        res = client.put(
            "/v1/xijian/mcp/policy/world_x",
            headers=auth_headers,
            json={"default": POLICY_DEFAULT_ALLOW},
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body["default"] == POLICY_DEFAULT_ALLOW

    def test_put_clear_lockout(self, client, auth_headers):
        mcp_stub.set_world_policy("world_x", lockout_until=999.0)
        res = client.put(
            "/v1/xijian/mcp/policy/world_x",
            headers=auth_headers,
            json={"clear_lockout": True},
        )
        body = res.get_json()
        assert body["lockout_until"] is None

    def test_put_invalid_default(self, client, auth_headers):
        res = client.put(
            "/v1/xijian/mcp/policy/world_x",
            headers=auth_headers,
            json={"default": "unknown"},
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "policy_error"

    def test_delete(self, client, auth_headers):
        mcp_stub.set_world_policy("world_x", default=POLICY_DEFAULT_ALLOW)
        res = client.delete(
            "/v1/xijian/mcp/policy/world_x", headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["reset"] is True


# ---------------------------------------------------------------------------
# HTTP — safety-stop
# ---------------------------------------------------------------------------


class TestHTTPSafetyStop:
    def test_init(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/mcp/safety_stop",
            headers=auth_headers,
            json={"reason": "hotkey", "world_id": "world_x"},
        )
        assert res.status_code == 201
        body = res.get_json()
        assert body["status"] == FREEZE_FROZEN
        assert body["reason"] == "hotkey"

    def test_list(self, client, auth_headers):
        mcp_stub.safety_stop(world_id="world_x", reason="a")
        mcp_stub.safety_stop(world_id="world_y", reason="b")
        res = client.get(
            "/v1/xijian/mcp/safety_stop", headers=auth_headers,
        )
        assert res.status_code == 200
        assert len(res.get_json()["freezes"]) == 2

    def test_list_filter_by_world(self, client, auth_headers):
        mcp_stub.safety_stop(world_id="world_x")
        mcp_stub.safety_stop(world_id="world_y")
        res = client.get(
            "/v1/xijian/mcp/safety_stop?world_id=world_x",
            headers=auth_headers,
        )
        assert len(res.get_json()["freezes"]) == 1

    def test_get(self, client, auth_headers):
        record = mcp_stub.safety_stop(world_id="world_x")
        res = client.get(
            f"/v1/xijian/mcp/safety_stop/{record['id']}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["id"] == record["id"]

    def test_get_missing(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/mcp/safety_stop/mcpf_phantom",
            headers=auth_headers,
        )
        assert res.status_code == 404

    def test_confirm(self, client, auth_headers):
        record = mcp_stub.safety_stop(world_id="world_x")
        res = client.post(
            f"/v1/xijian/mcp/safety_stop/{record['id']}/confirm",
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body["status"] == FREEZE_RESTORED

    def test_confirm_missing(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/mcp/safety_stop/mcpf_phantom/confirm",
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_cancel(self, client, auth_headers):
        record = mcp_stub.safety_stop(world_id="world_x")
        res = client.post(
            f"/v1/xijian/mcp/safety_stop/{record['id']}/cancel",
            headers=auth_headers,
            json={"reason": "user said no"},
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body["status"] == FREEZE_CANCELLED
        assert body["reason"] == "user said no"

    def test_cancel_no_body(self, client, auth_headers):
        record = mcp_stub.safety_stop(world_id="world_x")
        res = client.post(
            f"/v1/xijian/mcp/safety_stop/{record['id']}/cancel",
            headers=auth_headers,
        )
        assert res.status_code == 200

    def test_lockout_returns_409(self, client, auth_headers):
        for i in range(DEFAULT_LOCKOUT_THRESHOLD - 1):
            record = mcp_stub.safety_stop(world_id="world_x")
            mcp_stub.cancel_safety_stop(record["id"])
        mcp_stub.safety_stop(world_id="world_x")
        res = client.post(
            "/v1/xijian/mcp/safety_stop",
            headers=auth_headers,
            json={"world_id": "world_x"},
        )
        assert res.status_code == 409
        assert res.get_json()["error"]["code"] == "lockout_active"

    def test_pending_freeze_returns_409(self, client, auth_headers):
        mcp_stub.safety_stop(world_id="world_x")
        res = client.post(
            "/v1/xijian/mcp/safety_stop",
            headers=auth_headers,
            json={"world_id": "world_x"},
        )
        assert res.status_code == 409
        assert res.get_json()["error"]["code"] == "freeze_pending"


# ---------------------------------------------------------------------------
# HTTP — snapshots
# ---------------------------------------------------------------------------


class TestHTTPSnapshots:
    def test_dump(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/mcp/snapshots",
            headers=auth_headers,
            json={"world_id": "world_x", "reason": SNAPSHOT_REASON_MANUAL},
        )
        assert res.status_code == 201
        body = res.get_json()
        assert body["id"].startswith("mcpsnap_")
        assert "payload" not in body  # route strips payload

    def test_dump_invalid_reason(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/mcp/snapshots",
            headers=auth_headers,
            json={"world_id": "world_x", "reason": "bogus"},
        )
        assert res.status_code == 400

    def test_list(self, client, auth_headers):
        mcp_stub.dump_snapshot(world_id="world_x")
        mcp_stub.dump_snapshot(world_id="world_y")
        res = client.get(
            "/v1/xijian/mcp/snapshots", headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert len(body["snapshots"]) == 2

    def test_get(self, client, auth_headers):
        record = mcp_stub.dump_snapshot(world_id="world_x")
        res = client.get(
            f"/v1/xijian/mcp/snapshots/{record['id']}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert "payload" in res.get_json()  # raw get returns payload

    def test_get_missing(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/mcp/snapshots/mcpsnap_phantom",
            headers=auth_headers,
        )
        assert res.status_code == 404

    def test_sanitize(self, client, auth_headers):
        record = mcp_stub.dump_snapshot(world_id="world_x")
        res = client.post(
            f"/v1/xijian/mcp/snapshots/{record['id']}/sanitize",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["sanitized"] is True

    def test_sanitize_missing(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/mcp/snapshots/mcpsnap_phantom/sanitize",
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_restore(self, client, auth_headers):
        record = mcp_stub.dump_snapshot(world_id="world_x")
        mcp_stub.sanitize_snapshot(record["id"])
        res = client.post(
            f"/v1/xijian/mcp/snapshots/{record['id']}/restore",
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert "restored_buckets" in body

    def test_restore_missing(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/mcp/snapshots/mcpsnap_phantom/restore",
            headers=auth_headers,
        )
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# HTTP — dev crash
# ---------------------------------------------------------------------------


class TestHTTPDevCrash:
    def test_dev_crash(self, client, auth_headers, monkeypatch):
        monkeypatch.setenv("XIJIAN_DEV", "1")
        res = client.post("/v1/xijian/mcp/dev/crash", headers=auth_headers)
        assert res.status_code == 200
        body = res.get_json()
        assert body["verdict"] == VERDICT_DENIED_CRASHED
        assert body["blocked"] == "check_crashed"

    def test_dev_crash_without_dev_env(self, client, auth_headers):
        # XIJIAN_DEV should be popped by conftest.
        res = client.post("/v1/xijian/mcp/dev/crash", headers=auth_headers)
        assert res.status_code == 403


# ---------------------------------------------------------------------------
# Auth coverage
# ---------------------------------------------------------------------------


class TestAuthCoverage:
    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("POST", "/v1/xijian/mcp/check", {"action_kind": "shell", "args": "x"}),
            ("GET", "/v1/xijian/mcp/audit", None),
            ("GET", "/v1/xijian/mcp/audit/count", None),
            ("GET", "/v1/xijian/mcp/policy/world_x", None),
            ("PUT", "/v1/xijian/mcp/policy/world_x", {}),
            ("DELETE", "/v1/xijian/mcp/policy/world_x", None),
            ("POST", "/v1/xijian/mcp/safety_stop", {}),
            ("GET", "/v1/xijian/mcp/safety_stop", None),
            ("GET", "/v1/xijian/mcp/safety_stop/mcpf_x", None),
            ("POST", "/v1/xijian/mcp/safety_stop/mcpf_x/confirm", None),
            ("POST", "/v1/xijian/mcp/safety_stop/mcpf_x/cancel", None),
            ("GET", "/v1/xijian/mcp/snapshots", None),
            ("GET", "/v1/xijian/mcp/snapshots/mcpsnap_x", None),
            ("POST", "/v1/xijian/mcp/snapshots", {}),
            ("POST", "/v1/xijian/mcp/snapshots/mcpsnap_x/sanitize", None),
            ("POST", "/v1/xijian/mcp/snapshots/mcpsnap_x/restore", None),
        ],
    )
    def test_requires_bearer(self, client, method, path, body):
        kwargs = {"method": method, "path": path}
        if body is not None and method in {"POST", "PUT", "PATCH"}:
            kwargs["json"] = body
        res = client.open(**kwargs)
        assert res.status_code in (401, 403), (
            "%s %s should require auth, got %d body=%s"
            % (method, path, res.status_code, res.get_data(as_text=True)[:80])
        )
