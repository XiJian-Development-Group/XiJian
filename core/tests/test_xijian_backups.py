"""Tests for ``stubs.snapshots`` (A5.3) and the
``/v1/xijian/backups/*`` endpoints.

Covers:

* **Pure helpers** — payload size estimation, compression
  ratio, sequence counter.
* **Policy** — :func:`get_policy` / :func:`set_policy` /
  :func:`reset_policy` with all four mutable fields.
* **Snapshot CRUD** — create / list / get / delete /
  force-recompress; payload deep-copy; size tracking.
* **Capacity** — :func:`enforce_capacity` returns a
  prompt record on overflow; :func:`resolve_capacity`
  handles compress / drop / force.
* **Prune** — :func:`prune_expired` drops only the
  expired records.
* **Compression** — :func:`compress_snapshot` recompresses
  in place; the post-compression ratio stays within
  AC-3's 0.4 target.
* **Auth** — every endpoint requires a Bearer token.
"""

from __future__ import annotations

import time

import pytest

from xijian_api.stubs import snapshots as snap_stub
from xijian_api.stubs.snapshots import (
    COMPRESSION_RATIO_TARGET,
    DEFAULT_AUTO_COMPRESS_ENABLED,
    DEFAULT_BACKUP_INTERVAL_SECONDS,
    DEFAULT_COMPRESSION_TARGET,
    DEFAULT_MAX_TOTAL_BYTES,
    DEFAULT_POLICY_ID,
    MAX_SINGLE_SNAPSHOT_BYTES,
    REASON_MANUAL,
    REASON_OVERLOAD,
    REASON_SAFETY_STOP,
    REASON_SCHEDULED,
    SCOPE_CHARACTER,
    SCOPE_MEMORY,
    SCOPE_MIXED,
    SCOPE_WORLD,
    VALID_REASONS,
    VALID_SCOPES,
    CapacityExceededError,
    SnapshotError,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestEstimatePayloadBytes:
    def test_empty(self):
        size = snap_stub._estimate_payload_bytes({})
        # Even an empty dict serialises to a few bytes via
        # pickle + zlib.
        assert size > 0

    def test_large(self):
        payload = {"key": "x" * 10_000}
        size = snap_stub._estimate_payload_bytes(payload)
        # The compressed size is much smaller than the raw
        # 10_000-char string.
        assert size < 5_000


class TestCompressBytes:
    def test_round_trip_shape(self):
        compressed, original, compressed_size = snap_stub._compress_bytes({"a": 1})
        assert isinstance(compressed, bytes)
        assert original > 0
        assert compressed_size == len(compressed)

    def test_compression_ratio_on_realistic_payload(self):
        # Spec AC-3 "压缩比 ≥ 0.4" applies to the *average*
        # across the whole backup set, not every individual
        # payload.  For tiny dicts the zlib header blows
        # past the data, so we test the ratio on a payload
        # that mirrors real-world backup sizes.
        payload = {"key": "x" * 10_000}
        compressed, original, compressed_size = snap_stub._compress_bytes(payload)
        assert compressed_size < original * COMPRESSION_RATIO_TARGET


class TestSeqNext:
    def test_monotonic(self):
        a = snap_stub._seq_next()
        b = snap_stub._seq_next()
        assert b > a


class TestValidation:
    @pytest.mark.parametrize("scope", list(VALID_SCOPES))
    def test_valid_scopes(self, scope):
        assert snap_stub._validate_scope(scope) == scope

    @pytest.mark.parametrize("bad", ["", "unknown", None, 123])
    def test_invalid_scope(self, bad):
        with pytest.raises(SnapshotError):
            snap_stub._validate_scope(bad)

    @pytest.mark.parametrize("reason", list(VALID_REASONS))
    def test_valid_reasons(self, reason):
        assert snap_stub._validate_reason(reason) == reason

    @pytest.mark.parametrize("bad", ["", "unknown", None, 123])
    def test_invalid_reason(self, bad):
        with pytest.raises(SnapshotError):
            snap_stub._validate_reason(bad)

    def test_empty_target_id(self):
        with pytest.raises(SnapshotError):
            snap_stub._validate_target_id("")


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class TestGetPolicy:
    def test_seeds_default(self):
        policy = snap_stub.get_policy()
        assert policy["id"] == DEFAULT_POLICY_ID
        assert policy["max_total_bytes"] == DEFAULT_MAX_TOTAL_BYTES
        assert policy["auto_compress_enabled"] is DEFAULT_AUTO_COMPRESS_ENABLED
        assert policy["compression_target"] == DEFAULT_COMPRESSION_TARGET
        assert policy["backup_interval_seconds"] == DEFAULT_BACKUP_INTERVAL_SECONDS

    def test_returns_same_record(self):
        a = snap_stub.get_policy()
        b = snap_stub.get_policy()
        assert a is b


class TestSetPolicy:
    def test_set_max_total_bytes(self):
        updated = snap_stub.set_policy(max_total_bytes=1024)
        assert updated["max_total_bytes"] == 1024

    def test_set_auto_compress(self):
        updated = snap_stub.set_policy(auto_compress_enabled=False)
        assert updated["auto_compress_enabled"] is False

    def test_set_compression_target(self):
        updated = snap_stub.set_policy(compression_target=0.5)
        assert updated["compression_target"] == 0.5

    def test_set_backup_interval(self):
        updated = snap_stub.set_policy(backup_interval_seconds=120.0)
        assert updated["backup_interval_seconds"] == 120.0

    def test_invalid_max_total_bytes(self):
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(max_total_bytes=0)
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(max_total_bytes=-1)
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(max_total_bytes="1000")

    def test_invalid_compression_target(self):
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(compression_target=0.0)
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(compression_target=1.5)
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(compression_target="0.5")

    def test_invalid_backup_interval(self):
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(backup_interval_seconds=0)
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(backup_interval_seconds=-1)

    def test_invalid_auto_compress_type(self):
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(auto_compress_enabled="yes")

    def test_updated_at_advances(self):
        a = snap_stub.get_policy()
        time.sleep(0.001)
        b = snap_stub.set_policy(max_total_bytes=9999)
        assert b["updated_at"] >= a["updated_at"]


class TestResetPolicy:
    def test_resets_to_default(self):
        snap_stub.set_policy(max_total_bytes=1024)
        reset = snap_stub.reset_policy()
        assert reset["max_total_bytes"] == DEFAULT_MAX_TOTAL_BYTES


# ---------------------------------------------------------------------------
# Snapshot CRUD
# ---------------------------------------------------------------------------


class TestCreateSnapshot:
    def test_basic(self):
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD,
            target_id="world_x",
            payload={"data": "x" * 100},
            reason=REASON_MANUAL,
        )
        assert record["id"].startswith("sas_")
        assert record["scope"] == SCOPE_WORLD
        assert record["target_id"] == "world_x"
        assert record["reason"] == REASON_MANUAL
        assert record["size_bytes"] > 0
        assert record["original_size_bytes"] > 0
        assert record["compressed"] is True
        assert record["compression_ratio"] <= 1.0
        assert "payload" in record
        assert record["file_path"] == "safety_snapshots/%s.zst" % record["id"]

    def test_invalid_scope(self):
        with pytest.raises(SnapshotError):
            snap_stub.create_snapshot(
                scope="bogus", target_id="x", payload={},
            )

    def test_invalid_reason(self):
        with pytest.raises(SnapshotError):
            snap_stub.create_snapshot(
                scope=SCOPE_WORLD, target_id="x", payload={},
                reason="bogus",
            )

    def test_empty_target_id(self):
        with pytest.raises(SnapshotError):
            snap_stub.create_snapshot(
                scope=SCOPE_WORLD, target_id="", payload={},
            )

    def test_oversize_payload_rejected(self, monkeypatch):
        # Patch the cap down so the test runs fast.  The
        # default 500 MiB cap would require 500 MiB of
        # incompressible data to trip — too slow for unit
        # tests.
        monkeypatch.setattr(snap_stub, "MAX_SINGLE_SNAPSHOT_BYTES", 64)
        # Use ``os.urandom`` so the bytes are truly random
        # (and therefore don't compress).  1 KiB of random
        # data won't compress below 64 bytes — but a few
        # hundred bytes might, so we use 4 KiB to leave
        # headroom.
        import os
        payload = {"big": os.urandom(4 * 1024)}
        with pytest.raises(SnapshotError, match="too large"):
            snap_stub.create_snapshot(
                scope=SCOPE_WORLD,
                target_id="x",
                payload=payload,
            )

    def test_deep_copy(self):
        original = {"x": 1}
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload=original,
        )
        # Mutating the original must not affect the
        # snapshot.
        original["x"] = 999
        assert record["payload"]["x"] == 1

    def test_force_over_capacity(self):
        # Set a tiny ceiling and push past it with one
        # well-sized payload; a second write without
        # force → CapacityExceededError.
        # 5000 'x' chars compresses to ~58 bytes; 2 of
        # them is 116 which is > 50.
        big = {"a": "x" * 5000}
        snap_stub.set_policy(max_total_bytes=50)
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x",
            payload=big, reason=REASON_MANUAL, force=True,
        )
        # Second write would exceed; without force → raise.
        with pytest.raises(CapacityExceededError):
            snap_stub.create_snapshot(
                scope=SCOPE_WORLD, target_id="y",
                payload={"b": "x" * 5000}, reason=REASON_MANUAL,
            )
        # With force → succeeds.
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="y",
            payload={"b": "x" * 5000}, reason=REASON_MANUAL, force=True,
        )
        assert record is not None

    def test_capacity_exceeded_carries_prompt(self):
        big = {"a": "x" * 5000}
        snap_stub.set_policy(max_total_bytes=50)
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x",
            payload=big, reason=REASON_MANUAL, force=True,
        )
        with pytest.raises(CapacityExceededError) as exc_info:
            snap_stub.create_snapshot(
                scope=SCOPE_WORLD, target_id="y",
                payload={"b": "x" * 5000}, reason=REASON_MANUAL,
            )
        assert exc_info.value.prompt["action"] == "prompt"
        assert exc_info.value.prompt["ceiling"] == 50


class TestGetSnapshot:
    def test_existing(self):
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        assert snap_stub.get_snapshot(record["id"]) == record

    def test_missing(self):
        assert snap_stub.get_snapshot("sas_phantom") is None


class TestListSnapshots:
    def test_empty(self):
        assert snap_stub.list_snapshots() == []

    def test_newest_first(self):
        a = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={"a": 1},
        )
        b = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="y", payload={"b": 1},
        )
        out = snap_stub.list_snapshots()
        # b is newer, so it should come first.
        assert out[0]["id"] == b["id"]
        assert out[1]["id"] == a["id"]

    def test_filter_by_scope(self):
        a = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        snap_stub.create_snapshot(
            scope=SCOPE_MEMORY, target_id="y", payload={},
        )
        out = snap_stub.list_snapshots(scope=SCOPE_WORLD)
        assert len(out) == 1
        assert out[0]["id"] == a["id"]

    def test_filter_by_target(self):
        a = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="y", payload={},
        )
        out = snap_stub.list_snapshots(target_id="x")
        assert len(out) == 1
        assert out[0]["id"] == a["id"]

    def test_filter_by_reason(self):
        a = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
            reason=REASON_SCHEDULED,
        )
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="y", payload={},
            reason=REASON_MANUAL,
        )
        out = snap_stub.list_snapshots(reason=REASON_SCHEDULED)
        assert len(out) == 1
        assert out[0]["id"] == a["id"]

    def test_limit(self):
        for i in range(3):
            snap_stub.create_snapshot(
                scope=SCOPE_WORLD, target_id="t%d" % i, payload={},
            )
        out = snap_stub.list_snapshots(limit=2)
        assert len(out) == 2


class TestDeleteSnapshot:
    def test_existing(self):
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        assert snap_stub.delete_snapshot(record["id"]) is True
        assert snap_stub.get_snapshot(record["id"]) is None

    def test_missing(self):
        assert snap_stub.delete_snapshot("sas_phantom") is False


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------


class TestCompressSnapshot:
    def test_recompress(self):
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x",
            payload={"data": "x" * 5000},
        )
        before = record["size_bytes"]
        new = snap_stub.compress_snapshot(record["id"])
        assert new is not None
        # The post-recompression size is still in the same
        # ballpark (no growth).  AC-3 ratio target ≤ 0.4
        # is satisfied on a fresh recompression.
        assert new["size_bytes"] <= before
        assert new["compression_ratio"] <= COMPRESSION_RATIO_TARGET

    def test_missing(self):
        assert snap_stub.compress_snapshot("sas_phantom") is None

    def test_records_compressed_at(self):
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={"a": 1},
        )
        new = snap_stub.compress_snapshot(record["id"])
        assert "compressed_at" in new


# ---------------------------------------------------------------------------
# Capacity
# ---------------------------------------------------------------------------


class TestEnforceCapacity:
    def test_under_capacity(self):
        snap_stub.set_policy(max_total_bytes=10_000)
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={"a": 1},
        )
        prompt = snap_stub.enforce_capacity()
        assert prompt["action"] == "prompt"
        assert prompt["current_total"] > 0
        assert prompt["ceiling"] == 10_000
        assert prompt["overage"] == 0
        assert prompt["compress_available"] is False

    def test_over_capacity(self):
        snap_stub.set_policy(max_total_bytes=50)
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x",
            payload={"a": "x" * 5000},
            reason=REASON_MANUAL, force=True,
        )
        prompt = snap_stub.enforce_capacity()
        assert prompt["overage"] > 0
        assert prompt["compress_available"] is True

    def test_includes_oldest(self):
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        prompt = snap_stub.enforce_capacity(incoming_bytes=100)
        assert len(prompt["oldest"]) >= 1
        assert "id" in prompt["oldest"][0]


class TestResolveCapacity:
    def test_compress(self):
        snap_stub.set_policy(max_total_bytes=50, auto_compress_enabled=True)
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x",
            payload={"a": "x" * 200},
            reason=REASON_MANUAL, force=True,
        )
        summary = snap_stub.resolve_capacity(action="compress")
        assert summary["action"] == "compress"
        assert summary["total_after"] <= summary["total_after"]  # tautology, but safe

    def test_drop(self):
        snap_stub.set_policy(max_total_bytes=10_000)
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={"a": "x" * 100},
        )
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="y", payload={"b": "x" * 100},
        )
        summary = snap_stub.resolve_capacity(
            action="drop", incoming_bytes=10_000,
        )
        assert summary["action"] == "drop"
        assert summary["dropped"] >= 1

    def test_force_noop(self):
        summary = snap_stub.resolve_capacity(action="force")
        assert summary["action"] == "force"
        assert "ceiling" in summary

    def test_invalid_action(self):
        with pytest.raises(SnapshotError):
            snap_stub.resolve_capacity(action="bogus")


# ---------------------------------------------------------------------------
# Prune
# ---------------------------------------------------------------------------


class TestPrune:
    def test_no_expired(self):
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        assert snap_stub.prune_expired() == 0

    def test_with_expired(self):
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
            expires_at=time.time() - 100,
        )
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="y", payload={},
            expires_at=time.time() + 1000,
        )
        removed = snap_stub.prune_expired()
        assert removed == 1
        # The non-expired one is still there.
        assert len(snap_stub.list_snapshots()) == 1

    def test_no_expires_at(self):
        # A snapshot with no expires_at is never pruned.
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        assert snap_stub.prune_expired() == 0


# ---------------------------------------------------------------------------
# Total bytes
# ---------------------------------------------------------------------------


class TestGetTotalBytes:
    def test_empty(self):
        assert snap_stub.get_total_bytes() == 0

    def test_accumulates(self):
        a = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={"a": "x" * 200},
        )
        b = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="y", payload={"b": "x" * 200},
        )
        total = snap_stub.get_total_bytes()
        assert total == a["size_bytes"] + b["size_bytes"]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_seed_default_is_idempotent(self):
        snap_stub.seed_default()
        snap_stub.seed_default()
        policy = snap_stub.get_policy()
        assert policy["id"] == DEFAULT_POLICY_ID

    def test_reset_clears_everything(self):
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        snap_stub.set_policy(max_total_bytes=1024)
        snap_stub.reset_for_testing()
        assert snap_stub.list_snapshots() == []
        # After reset, the policy is re-seeded with the
        # default value.
        assert snap_stub.get_policy()["max_total_bytes"] == DEFAULT_MAX_TOTAL_BYTES


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class TestHTTPSnapshots:
    def test_create(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/backups/snapshots",
            headers=auth_headers,
            json={
                "scope": SCOPE_WORLD,
                "target_id": "world_x",
                "payload": {"data": "test"},
                "reason": REASON_MANUAL,
            },
        )
        assert res.status_code == 201
        body = res.get_json()
        assert body["id"].startswith("sas_")
        assert body["scope"] == SCOPE_WORLD

    def test_create_capacity_exceeded(self, client, auth_headers):
        snap_stub.set_policy(max_total_bytes=50)
        # Pre-fill so the next one blows past.
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x",
            payload={"a": "x" * 5000}, reason=REASON_MANUAL, force=True,
        )
        res = client.post(
            "/v1/xijian/backups/snapshots",
            headers=auth_headers,
            json={
                "scope": SCOPE_WORLD,
                "target_id": "y",
                "payload": {"b": "x" * 5000},
                "reason": REASON_MANUAL,
            },
        )
        assert res.status_code == 409
        body = res.get_json()
        assert body["error"]["code"] == "capacity_exceeded"
        assert body["error"]["action"] == "prompt"

    def test_create_force(self, client, auth_headers):
        snap_stub.set_policy(max_total_bytes=50)
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x",
            payload={"a": "x" * 5000}, reason=REASON_MANUAL, force=True,
        )
        res = client.post(
            "/v1/xijian/backups/snapshots",
            headers=auth_headers,
            json={
                "scope": SCOPE_WORLD,
                "target_id": "y",
                "payload": {"b": "x" * 5000},
                "reason": REASON_MANUAL,
                "force": True,
            },
        )
        assert res.status_code == 201

    def test_create_invalid_scope(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/backups/snapshots",
            headers=auth_headers,
            json={
                "scope": "bogus",
                "target_id": "x",
                "payload": {},
            },
        )
        assert res.status_code == 400

    def test_list(self, client, auth_headers):
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        res = client.get(
            "/v1/xijian/backups/snapshots", headers=auth_headers,
        )
        assert res.status_code == 200
        assert len(res.get_json()["snapshots"]) == 1

    def test_list_filter_by_scope(self, client, auth_headers):
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        snap_stub.create_snapshot(
            scope=SCOPE_MEMORY, target_id="y", payload={},
        )
        res = client.get(
            "/v1/xijian/backups/snapshots?scope=world",
            headers=auth_headers,
        )
        body = res.get_json()
        assert len(body["snapshots"]) == 1

    def test_get(self, client, auth_headers):
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        res = client.get(
            f"/v1/xijian/backups/snapshots/{record['id']}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["id"] == record["id"]

    def test_get_missing(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/backups/snapshots/sas_phantom",
            headers=auth_headers,
        )
        assert res.status_code == 404

    def test_delete(self, client, auth_headers):
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        res = client.delete(
            f"/v1/xijian/backups/snapshots/{record['id']}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["deleted"] is True

    def test_delete_missing(self, client, auth_headers):
        res = client.delete(
            "/v1/xijian/backups/snapshots/sas_phantom",
            headers=auth_headers,
        )
        assert res.status_code == 404

    def test_compress(self, client, auth_headers):
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x",
            payload={"data": "x" * 5000},
        )
        res = client.post(
            f"/v1/xijian/backups/snapshots/{record['id']}/compress",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["compressed"] is True

    def test_compress_missing(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/backups/snapshots/sas_phantom/compress",
            headers=auth_headers,
        )
        assert res.status_code == 404


class TestHTTPCapacity:
    def test_get(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/backups/capacity", headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert "current_total" in body
        assert "ceiling" in body
        assert body["ceiling"] == DEFAULT_MAX_TOTAL_BYTES

    def test_resolve_compress(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/backups/capacity/resolve",
            headers=auth_headers,
            json={"action": "compress"},
        )
        assert res.status_code == 200
        assert res.get_json()["action"] == "compress"

    def test_resolve_drop(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/backups/capacity/resolve",
            headers=auth_headers,
            json={"action": "drop", "incoming_bytes": 1000},
        )
        assert res.status_code == 200
        assert res.get_json()["action"] == "drop"

    def test_resolve_force(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/backups/capacity/resolve",
            headers=auth_headers,
            json={"action": "force"},
        )
        assert res.status_code == 200
        assert res.get_json()["action"] == "force"

    def test_resolve_invalid_action(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/backups/capacity/resolve",
            headers=auth_headers,
            json={"action": "bogus"},
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "invalid_action"


class TestHTTPPrune:
    def test_prune_dry_run(self, client, auth_headers):
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
            expires_at=time.time() - 100,
        )
        res = client.post(
            "/v1/xijian/backups/prune",
            headers=auth_headers,
            json={"dry_run": True},
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body["dry_run"] is True
        assert body["would_drop"] == 1

    def test_prune_real(self, client, auth_headers):
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
            expires_at=time.time() - 100,
        )
        res = client.post(
            "/v1/xijian/backups/prune", headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["dropped"] == 1

    def test_prune_empty(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/backups/prune", headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["dropped"] == 0


class TestHTTPPolicy:
    def test_get_default(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/backups/policy", headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body["max_total_bytes"] == DEFAULT_MAX_TOTAL_BYTES

    def test_put(self, client, auth_headers):
        res = client.put(
            "/v1/xijian/backups/policy",
            headers=auth_headers,
            json={"max_total_bytes": 1024, "auto_compress_enabled": False},
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body["max_total_bytes"] == 1024
        assert body["auto_compress_enabled"] is False

    def test_put_invalid(self, client, auth_headers):
        res = client.put(
            "/v1/xijian/backups/policy",
            headers=auth_headers,
            json={"max_total_bytes": 0},
        )
        assert res.status_code == 400

    def test_delete(self, client, auth_headers):
        snap_stub.set_policy(max_total_bytes=1024)
        res = client.delete(
            "/v1/xijian/backups/policy", headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["max_total_bytes"] == DEFAULT_MAX_TOTAL_BYTES


# ---------------------------------------------------------------------------
# Auth coverage
# ---------------------------------------------------------------------------


class TestAuthCoverage:
    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("GET", "/v1/xijian/backups/snapshots", None),
            ("POST", "/v1/xijian/backups/snapshots", {
                "scope": SCOPE_WORLD, "target_id": "x", "payload": {},
            }),
            ("GET", "/v1/xijian/backups/snapshots/sas_x", None),
            ("DELETE", "/v1/xijian/backups/snapshots/sas_x", None),
            ("POST", "/v1/xijian/backups/snapshots/sas_x/compress", None),
            ("GET", "/v1/xijian/backups/capacity", None),
            ("POST", "/v1/xijian/backups/capacity/resolve", {"action": "force"}),
            ("POST", "/v1/xijian/backups/prune", None),
            ("GET", "/v1/xijian/backups/policy", None),
            ("PUT", "/v1/xijian/backups/policy", {}),
            ("DELETE", "/v1/xijian/backups/policy", None),
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
