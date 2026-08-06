"""Tests for ``stubs.snapshots`` (A5.3) and the
``/v1/xijian/backups/*`` endpoints.
(``stubs.snapshots`` (A5.3) 和 ``/v1/xijian/backups/*`` 端点的测试。)

Covers:
(覆盖范围：)

* **Pure helpers** — payload size estimation, compression
  ratio, sequence counter.
* (**纯辅助函数** — 负载大小估算、压缩比、序列计数器。)
* **Policy** — :func:`get_policy` / :func:`set_policy` /
  :func:`reset_policy` with all four mutable fields.
* (**策略** — :func:`get_policy` / :func:`set_policy` /
  :func:`reset_policy`，包含所有四个可变字段。)
* **Snapshot CRUD** — create / list / get / delete /
  force-recompress; payload deep-copy; size tracking.
* (**快照 CRUD** — 创建 / 列出 / 获取 / 删除 /
  强制重新压缩；负载深拷贝；大小追踪。)
* **Capacity** — :func:`enforce_capacity` returns a
  prompt record on overflow; :func:`resolve_capacity`
  handles compress / drop / force.
* (**容量** — :func:`enforce_capacity` 在溢出时返回
  提示记录；:func:`resolve_capacity` 处理压缩/删除/强制。)
* **Prune** — :func:`prune_expired` drops only the
  expired records.
* (**修剪** — :func:`prune_expired` 仅删除已过期的记录。)
* **Compression** — :func:`compress_snapshot` recompresses
  in place; the post-compression ratio stays within
  AC-3's 0.4 target.
* (**压缩** — :func:`compress_snapshot` 原地重新压缩；
  压缩后的比率保持在 AC-3 的 0.4 目标内。)
* **Auth** — every endpoint requires a Bearer token.
* (**认证** — 每个端点都需要 Bearer token。)
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
# (纯辅助函数)


class TestEstimatePayloadBytes:
    """Tests for payload size estimation.
    (负载大小估算的测试。)
    """

    def test_empty(self):
        """Empty dict still has a small size.
        (空字典仍有一个小尺寸。)
        """
        size = snap_stub._estimate_payload_bytes({})
        # Even an empty dict serialises to a few bytes via
        # pickle + zlib.
        # (即使是空字典，通过 pickle + zlib 序列化也会产生几个字节。)
        assert size > 0

    def test_large(self):
        """Large payload compresses well.
        (大负载压缩效果好。)
        """
        payload = {"key": "x" * 10_000}
        size = snap_stub._estimate_payload_bytes(payload)
        # The compressed size is much smaller than the raw
        # 10_000-char string.
        # (压缩后的大小远小于原始 10_000 字符字符串。)
        assert size < 5_000


class TestCompressBytes:
    """Tests for byte-level compression.
    (字节级压缩的测试。)
    """

    def test_round_trip_shape(self):
        """Compression returns proper shape.
        (压缩返回正确的格式。)
        """
        compressed, original, compressed_size = snap_stub._compress_bytes({"a": 1})
        assert isinstance(compressed, bytes)
        assert original > 0
        assert compressed_size == len(compressed)

    def test_compression_ratio_on_realistic_payload(self):
        """Realistic payload achieves target compression ratio.
        (实际负载达到目标压缩比。)
        """
        # Spec AC-3 "压缩比 ≥ 0.4" applies to the *average*
        # across the whole backup set, not every individual
        # payload.  For tiny dicts the zlib header blows
        # past the data, so we test the ratio on a payload
        # that mirrors real-world backup sizes.
        # (规格 AC-3 "压缩比 ≥ 0.4" 适用于整个备份集的*平均*值，
        # 而非每个单独负载。对于小字典，zlib 头部超过数据本身，
        # 因此我们在模拟真实备份大小的负载上测试比率。)
        payload = {"key": "x" * 10_000}
        compressed, original, compressed_size = snap_stub._compress_bytes(payload)
        assert compressed_size < original * COMPRESSION_RATIO_TARGET


class TestSeqNext:
    """Tests for the monotonic sequence counter.
    (单调序列计数器的测试。)
    """

    def test_monotonic(self):
        """Sequence numbers are monotonic.
        (序列号是单调递增的。)
        """
        a = snap_stub._seq_next()
        b = snap_stub._seq_next()
        assert b > a


class TestValidation:
    """Tests for input validation utilities.
    (输入验证工具的测试。)
    """

    @pytest.mark.parametrize("scope", list(VALID_SCOPES))
    def test_valid_scopes(self, scope):
        """Valid scopes pass validation.
        (有效的作用域通过验证。)
        """
        assert snap_stub._validate_scope(scope) == scope

    @pytest.mark.parametrize("bad", ["", "unknown", None, 123])
    def test_invalid_scope(self, bad):
        """Invalid scopes raise SnapshotError.
        (无效的作用域抛出 SnapshotError。)
        """
        with pytest.raises(SnapshotError):
            snap_stub._validate_scope(bad)

    @pytest.mark.parametrize("reason", list(VALID_REASONS))
    def test_valid_reasons(self, reason):
        """Valid reasons pass validation.
        (有效的原因通过验证。)
        """
        assert snap_stub._validate_reason(reason) == reason

    @pytest.mark.parametrize("bad", ["", "unknown", None, 123])
    def test_invalid_reason(self, bad):
        """Invalid reasons raise SnapshotError.
        (无效的原因抛出 SnapshotError。)
        """
        with pytest.raises(SnapshotError):
            snap_stub._validate_reason(bad)

    def test_empty_target_id(self):
        """Empty target id raises SnapshotError.
        (空的 target id 抛出 SnapshotError。)
        """
        with pytest.raises(SnapshotError):
            snap_stub._validate_target_id("")


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------
# (策略)


class TestGetPolicy:
    """Tests for getting the backup policy.
    (获取备份策略的测试。)
    """

    def test_seeds_default(self):
        """Get policy returns seeded defaults.
        (获取策略返回播种的默认值。)
        """
        policy = snap_stub.get_policy()
        assert policy["id"] == DEFAULT_POLICY_ID
        assert policy["max_total_bytes"] == DEFAULT_MAX_TOTAL_BYTES
        assert policy["auto_compress_enabled"] is DEFAULT_AUTO_COMPRESS_ENABLED
        assert policy["compression_target"] == DEFAULT_COMPRESSION_TARGET
        assert policy["backup_interval_seconds"] == DEFAULT_BACKUP_INTERVAL_SECONDS

    def test_returns_same_record(self):
        """Get policy returns the same record reference.
        (获取策略返回相同的记录引用。)
        """
        a = snap_stub.get_policy()
        b = snap_stub.get_policy()
        assert a is b


class TestSetPolicy:
    """Tests for setting the backup policy.
    (设置备份策略的测试。)
    """

    def test_set_max_total_bytes(self):
        """Set max total bytes.
        (设置最大总字节数。)
        """
        updated = snap_stub.set_policy(max_total_bytes=1024)
        assert updated["max_total_bytes"] == 1024

    def test_set_auto_compress(self):
        """Set auto-compress enabled flag.
        (设置自动压缩启用标志。)
        """
        updated = snap_stub.set_policy(auto_compress_enabled=False)
        assert updated["auto_compress_enabled"] is False

    def test_set_compression_target(self):
        """Set compression target ratio.
        (设置压缩目标比率。)
        """
        updated = snap_stub.set_policy(compression_target=0.5)
        assert updated["compression_target"] == 0.5

    def test_set_backup_interval(self):
        """Set backup interval in seconds.
        (设置备份间隔秒数。)
        """
        updated = snap_stub.set_policy(backup_interval_seconds=120.0)
        assert updated["backup_interval_seconds"] == 120.0

    def test_invalid_max_total_bytes(self):
        """Invalid max_total_bytes raises SnapshotError.
        (无效的 max_total_bytes 抛出 SnapshotError。)
        """
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(max_total_bytes=0)
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(max_total_bytes=-1)
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(max_total_bytes="1000")

    def test_invalid_compression_target(self):
        """Invalid compression_target raises SnapshotError.
        (无效的 compression_target 抛出 SnapshotError。)
        """
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(compression_target=0.0)
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(compression_target=1.5)
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(compression_target="0.5")

    def test_invalid_backup_interval(self):
        """Invalid backup_interval_seconds raises SnapshotError.
        (无效的 backup_interval_seconds 抛出 SnapshotError。)
        """
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(backup_interval_seconds=0)
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(backup_interval_seconds=-1)

    def test_invalid_auto_compress_type(self):
        """Invalid type for auto_compress_enabled raises SnapshotError.
        (auto_compress_enabled 的无效类型抛出 SnapshotError。)
        """
        with pytest.raises(SnapshotError):
            snap_stub.set_policy(auto_compress_enabled="yes")

    def test_updated_at_advances(self):
        """updated_at advances after setting policy.
        (设置策略后 updated_at 前进。)
        """
        a = snap_stub.get_policy()
        time.sleep(0.001)
        b = snap_stub.set_policy(max_total_bytes=9999)
        assert b["updated_at"] >= a["updated_at"]


class TestResetPolicy:
    """Tests for resetting the backup policy.
    (重置备份策略的测试。)
    """

    def test_resets_to_default(self):
        """Reset returns to default max_total_bytes.
        (重置恢复到默认的 max_total_bytes。)
        """
        snap_stub.set_policy(max_total_bytes=1024)
        reset = snap_stub.reset_policy()
        assert reset["max_total_bytes"] == DEFAULT_MAX_TOTAL_BYTES


# ---------------------------------------------------------------------------
# Snapshot CRUD
# ---------------------------------------------------------------------------
# (快照 CRUD)


class TestCreateSnapshot:
    """Tests for creating snapshots.
    (创建快照的测试。)
    """

    def test_basic(self):
        """Basic snapshot creation with all fields.
        (包含所有字段的基本快照创建。)
        """
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
        """Invalid scope raises SnapshotError.
        (无效作用域抛出 SnapshotError。)
        """
        with pytest.raises(SnapshotError):
            snap_stub.create_snapshot(
                scope="bogus", target_id="x", payload={},
            )

    def test_invalid_reason(self):
        """Invalid reason raises SnapshotError.
        (无效原因抛出 SnapshotError。)
        """
        with pytest.raises(SnapshotError):
            snap_stub.create_snapshot(
                scope=SCOPE_WORLD, target_id="x", payload={},
                reason="bogus",
            )

    def test_empty_target_id(self):
        """Empty target ID raises SnapshotError.
        (空目标 ID 抛出 SnapshotError。)
        """
        with pytest.raises(SnapshotError):
            snap_stub.create_snapshot(
                scope=SCOPE_WORLD, target_id="", payload={},
            )

    def test_oversize_payload_rejected(self, monkeypatch):
        """Payload exceeding max size is rejected.
        (超过最大大小的负载被拒绝。)
        """
        # Patch the cap down so the test runs fast.  The
        # default 500 MiB cap would require 500 MiB of
        # incompressible data to trip — too slow for unit
        # tests.
        # (降低上限使测试快速运行。默认 500 MiB 上限需要
        # 500 MiB 不可压缩数据才能触发 —— 对单元测试来说太慢。)
        monkeypatch.setattr(snap_stub, "MAX_SINGLE_SNAPSHOT_BYTES", 64)
        # Use ``os.urandom`` so the bytes are truly random
        # (and therefore don't compress).  1 KiB of random
        # data won't compress below 64 bytes — but a few
        # hundred bytes might, so we use 4 KiB to leave
        # headroom.
        # (使用 ``os.urandom`` 以便字节真正随机（因此不压缩）。
        # 1 KiB 随机数据不会压缩到 64 字节以下 —— 但几百字节可能，
        # 所以我们使用 4 KiB 预留余量。)
        import os
        payload = {"big": os.urandom(4 * 1024)}
        with pytest.raises(SnapshotError, match="too large"):
            snap_stub.create_snapshot(
                scope=SCOPE_WORLD,
                target_id="x",
                payload=payload,
            )

    def test_deep_copy(self):
        """Payload is deep-copied; mutations don't affect snapshot.
        (负载被深拷贝；变更不影响快照。)
        """
        original = {"x": 1}
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload=original,
        )
        # Mutating the original must not affect the
        # snapshot.
        # (变更原始值不得影响快照。)
        original["x"] = 999
        assert record["payload"]["x"] == 1

    def test_force_over_capacity(self):
        """Force flag bypasses capacity check.
        (force 标志绕过容量检查。)
        """
        # Set a tiny ceiling and push past it with one
        # well-sized payload; a second write without
        # force → CapacityExceededError.
        # (设置小上限并用一个大小合适的负载超出；第二个写入
        # 没有 force → CapacityExceededError。)
        big = {"a": "x" * 5000}
        snap_stub.set_policy(max_total_bytes=50)
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x",
            payload=big, reason=REASON_MANUAL, force=True,
        )
        # Second write would exceed; without force → raise.
        # (第二次写入会超出；无 force → 抛出。)
        with pytest.raises(CapacityExceededError):
            snap_stub.create_snapshot(
                scope=SCOPE_WORLD, target_id="y",
                payload={"b": "x" * 5000}, reason=REASON_MANUAL,
            )
        # With force → succeeds.
        # (有 force → 成功。)
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="y",
            payload={"b": "x" * 5000}, reason=REASON_MANUAL, force=True,
        )
        assert record is not None

    def test_capacity_exceeded_carries_prompt(self):
        """CapacityExceededError carries a prompt record.
        (CapacityExceededError 携带提示记录。)
        """
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
    """Tests for getting a single snapshot.
    (获取单个快照的测试。)
    """

    def test_existing(self):
        """Get existing snapshot returns the record.
        (获取现有快照返回记录。)
        """
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        assert snap_stub.get_snapshot(record["id"]) == record

    def test_missing(self):
        """Get missing snapshot returns None.
        (获取缺失的快照返回 None。)
        """
        assert snap_stub.get_snapshot("sas_phantom") is None


class TestListSnapshots:
    """Tests for listing snapshots.
    (列出快照的测试。)
    """

    def test_empty(self):
        """List returns empty list when no snapshots.
        (无快照时列表返回空列表。)
        """
        assert snap_stub.list_snapshots() == []

    def test_newest_first(self):
        """Snapshots are ordered newest first.
        (快照按最新优先排序。)
        """
        a = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={"a": 1},
        )
        b = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="y", payload={"b": 1},
        )
        out = snap_stub.list_snapshots()
        # b is newer, so it should come first.
        # (b 更新，所以它应在前。)
        assert out[0]["id"] == b["id"]
        assert out[1]["id"] == a["id"]

    def test_filter_by_scope(self):
        """List filtered by scope.
        (按作用域过滤的列表。)
        """
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
        """List filtered by target_id.
        (按 target_id 过滤的列表。)
        """
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
        """List filtered by reason.
        (按原因过滤的列表。)
        """
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
        """List respects the limit parameter.
        (列表遵循限制参数。)
        """
        for i in range(3):
            snap_stub.create_snapshot(
                scope=SCOPE_WORLD, target_id="t%d" % i, payload={},
            )
        out = snap_stub.list_snapshots(limit=2)
        assert len(out) == 2


class TestDeleteSnapshot:
    """Tests for deleting snapshots.
    (删除快照的测试。)
    """

    def test_existing(self):
        """Delete existing snapshot returns True.
        (删除现有快照返回 True。)
        """
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        assert snap_stub.delete_snapshot(record["id"]) is True
        assert snap_stub.get_snapshot(record["id"]) is None

    def test_missing(self):
        """Delete missing snapshot returns False.
        (删除缺失快照返回 False。)
        """
        assert snap_stub.delete_snapshot("sas_phantom") is False


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------
# (压缩)


class TestCompressSnapshot:
    """Tests for snapshot compression.
    (快照压缩的测试。)
    """

    def test_recompress(self):
        """Recompression reduces size and meets ratio target.
        (重新压缩减少大小并满足比率目标。)
        """
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
        # (重新压缩后的大小仍在同一范围内（无增长）。
        # AC-3 比率目标 ≤ 0.4 在全新压缩上满足。)
        assert new["size_bytes"] <= before
        assert new["compression_ratio"] <= COMPRESSION_RATIO_TARGET

    def test_missing(self):
        """Compress missing snapshot returns None.
        (压缩缺失快照返回 None。)
        """
        assert snap_stub.compress_snapshot("sas_phantom") is None

    def test_records_compressed_at(self):
        """Compress records compressed_at timestamp.
        (压缩记录 compressed_at 时间戳。)
        """
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={"a": 1},
        )
        new = snap_stub.compress_snapshot(record["id"])
        assert "compressed_at" in new


# ---------------------------------------------------------------------------
# Capacity
# ---------------------------------------------------------------------------
# (容量)


class TestEnforceCapacity:
    """Tests for capacity enforcement.
    (容量执行的测试。)
    """

    def test_under_capacity(self):
        """Under capacity returns prompt with zero overage.
        (在容量内返回超限为零的提示。)
        """
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
        """Over capacity returns prompt with positive overage.
        (超出容量返回正超限的提示。)
        """
        snap_stub.set_policy(max_total_bytes=50)
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x",
            # Large enough that even zstd (AC-3, much tighter than the
            # old zlib stub) can't squeeze it under the 50-byte
            # ceiling — the whole point of this test is a real overage.
            # (足够大，即使 zstd (AC-3，比旧 zlib 存根紧凑得多) 也无法
            # 将其压缩到 50 字节上限以下 —— 本测试的重点是真实超限。)
            payload={"a": "x" * 200_000},
            reason=REASON_MANUAL, force=True,
        )
        prompt = snap_stub.enforce_capacity()
        assert prompt["overage"] > 0
        assert prompt["compress_available"] is True

    def test_includes_oldest(self):
        """Enforce includes oldest snapshots in prompt.
        (执行提示中包含最旧的快照。)
        """
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        prompt = snap_stub.enforce_capacity(incoming_bytes=100)
        assert len(prompt["oldest"]) >= 1
        assert "id" in prompt["oldest"][0]


class TestResolveCapacity:
    """Tests for capacity resolution actions.
    (容量解决方案的测试。)
    """

    def test_compress(self):
        """Compress action compresses snapshots.
        (压缩操作压缩快照。)
        """
        snap_stub.set_policy(max_total_bytes=50, auto_compress_enabled=True)
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x",
            payload={"a": "x" * 200},
            reason=REASON_MANUAL, force=True,
        )
        summary = snap_stub.resolve_capacity(action="compress")
        assert summary["action"] == "compress"

    def test_drop(self):
        """Drop action removes oldest snapshots.
        (删除操作移除最旧的快照。)
        """
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
        """Force action is a no-op returning metadata.
        (强制操作为无操作，返回元数据。)
        """
        summary = snap_stub.resolve_capacity(action="force")
        assert summary["action"] == "force"
        assert "ceiling" in summary

    def test_invalid_action(self):
        """Invalid action raises SnapshotError.
        (无效操作抛出 SnapshotError。)
        """
        with pytest.raises(SnapshotError):
            snap_stub.resolve_capacity(action="bogus")


# ---------------------------------------------------------------------------
# Prune
# ---------------------------------------------------------------------------
# (修剪)


class TestPrune:
    """Tests for pruning expired snapshots.
    (修剪过期快照的测试。)
    """

    def test_no_expired(self):
        """No expired snapshots → 0 removed.
        (无过期快照 → 移除 0 个。)
        """
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        assert snap_stub.prune_expired() == 0

    def test_with_expired(self):
        """Expired snapshots are pruned; non-expired remain.
        (过期快照被修剪；未过期的保留。)
        """
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
        # (未过期的一个仍然存在。)
        assert len(snap_stub.list_snapshots()) == 1

    def test_no_expires_at(self):
        """A snapshot with no expires_at is never pruned.
        (没有 expires_at 的快照永远不会被修剪。)
        """
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        assert snap_stub.prune_expired() == 0


# ---------------------------------------------------------------------------
# Total bytes
# ---------------------------------------------------------------------------
# (总字节数)


class TestGetTotalBytes:
    """Tests for tracking total bytes across snapshots.
    (跨快照跟踪总字节数的测试。)
    """

    def test_empty(self):
        """Empty bucket returns zero total bytes.
        (空桶返回零总字节数。)
        """
        assert snap_stub.get_total_bytes() == 0

    def test_accumulates(self):
        """Total bytes accumulates across snapshots.
        (总字节数跨快照累积。)
        """
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
# (生命周期)


class TestLifecycle:
    """Tests for module lifecycle (seed, reset).
    (模块生命周期（种子初始化、重置）的测试。)
    """

    def test_seed_default_is_idempotent(self):
        """Seed default is idempotent.
        (默认种子初始化是幂等的。)
        """
        snap_stub.seed_default()
        snap_stub.seed_default()
        policy = snap_stub.get_policy()
        assert policy["id"] == DEFAULT_POLICY_ID

    def test_reset_clears_everything(self):
        """Reset clears all snapshots and resets policy.
        (重置清除所有快照并重置策略。)
        """
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        snap_stub.set_policy(max_total_bytes=1024)
        snap_stub.reset_for_testing()
        assert snap_stub.list_snapshots() == []
        # After reset, the policy is re-seeded with the
        # default value.
        # (重置后，策略用默认值重新播种。)
        assert snap_stub.get_policy()["max_total_bytes"] == DEFAULT_MAX_TOTAL_BYTES


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
# (HTTP 端点测试)


class TestHTTPSnapshots:
    """HTTP tests for snapshot CRUD endpoints.
    (快照 CRUD 端点的 HTTP 测试。)
    """

    def test_create(self, client, auth_headers):
        """Create snapshot via HTTP returns 201.
        (通过 HTTP 创建快照返回 201。)
        """
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
        """Create snapshot exceeding capacity returns 409.
        (创建超出容量的快照返回 409。)
        """
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
            },
        )
        assert res.status_code == 409
        body = res.get_json()
        assert body["error"]["code"] == "capacity_exceeded"
        assert body["error"]["action"] == "prompt"

    def test_create_force(self, client, auth_headers):
        """Create snapshot with force bypasses capacity check.
        (使用 force 创建快照绕过容量检查。)
        """
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
        """Create snapshot with invalid scope returns 400.
        (使用无效作用域创建快照返回 400。)
        """
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
        """List snapshots via HTTP.
        (通过 HTTP 列出快照。)
        """
        snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="x", payload={},
        )
        res = client.get(
            "/v1/xijian/backups/snapshots", headers=auth_headers,
        )
        assert res.status_code == 200
        assert len(res.get_json()["snapshots"]) == 1

    def test_list_filter_by_scope(self, client, auth_headers):
        """List snapshots filtered by scope via HTTP.
        (通过 HTTP 按作用域过滤列出快照。)
        """
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
        """Get snapshot by id via HTTP.
        (通过 HTTP 按 id 获取快照。)
        """
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
        """Get missing snapshot via HTTP returns 404.
        (通过 HTTP 获取缺失快照返回 404。)
        """
        res = client.get(
            "/v1/xijian/backups/snapshots/sas_phantom",
            headers=auth_headers,
        )
        assert res.status_code == 404

    def test_delete(self, client, auth_headers):
        """Delete snapshot via HTTP returns 200.
        (通过 HTTP 删除快照返回 200。)
        """
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
        """Delete missing snapshot via HTTP returns 404.
        (通过 HTTP 删除缺失快照返回 404。)
        """
        res = client.delete(
            "/v1/xijian/backups/snapshots/sas_phantom",
            headers=auth_headers,
        )
        assert res.status_code == 404

    def test_compress(self, client, auth_headers):
        """Compress snapshot via HTTP returns 200.
        (通过 HTTP 压缩快照返回 200。)
        """
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
        """Compress missing snapshot via HTTP returns 404.
        (通过 HTTP 压缩缺失快照返回 404。)
        """
        res = client.post(
            "/v1/xijian/backups/snapshots/sas_phantom/compress",
            headers=auth_headers,
        )
        assert res.status_code == 404


class TestHTTPCapacity:
    """HTTP tests for capacity endpoints.
    (容量端点的 HTTP 测试。)
    """

    def test_get(self, client, auth_headers):
        """Get capacity via HTTP.
        (通过 HTTP 获取容量。)
        """
        res = client.get(
            "/v1/xijian/backups/capacity", headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert "current_total" in body
        assert "ceiling" in body
        assert body["ceiling"] == DEFAULT_MAX_TOTAL_BYTES

    def test_resolve_compress(self, client, auth_headers):
        """Resolve via compress action.
        (通过压缩操作解决容量问题。)
        """
        res = client.post(
            "/v1/xijian/backups/capacity/resolve",
            headers=auth_headers,
            json={"action": "compress"},
        )
        assert res.status_code == 200
        assert res.get_json()["action"] == "compress"

    def test_resolve_drop(self, client, auth_headers):
        """Resolve via drop action.
        (通过删除操作解决容量问题。)
        """
        res = client.post(
            "/v1/xijian/backups/capacity/resolve",
            headers=auth_headers,
            json={"action": "drop", "incoming_bytes": 1000},
        )
        assert res.status_code == 200
        assert res.get_json()["action"] == "drop"

    def test_resolve_force(self, client, auth_headers):
        """Resolve via force action.
        (通过强制操作解决容量问题。)
        """
        res = client.post(
            "/v1/xijian/backups/capacity/resolve",
            headers=auth_headers,
            json={"action": "force"},
        )
        assert res.status_code == 200
        assert res.get_json()["action"] == "force"

    def test_resolve_invalid_action(self, client, auth_headers):
        """Resolve with invalid action returns 400.
        (使用无效操作解决返回 400。)
        """
        res = client.post(
            "/v1/xijian/backups/capacity/resolve",
            headers=auth_headers,
            json={"action": "bogus"},
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "invalid_action"


class TestHTTPPrune:
    """HTTP tests for prune endpoint.
    (修剪端点的 HTTP 测试。)
    """

    def test_prune_dry_run(self, client, auth_headers):
        """Dry-run prune returns would_drop count without removing.
        (模拟运行修剪返回 would_drop 计数而不实际删除。)
        """
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
        """Real prune removes expired snapshots.
        (实际修剪移除过期快照。)
        """
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
        """Prune with no expired snapshots drops 0.
        (无过期快照的修剪删除 0 个。)
        """
        res = client.post(
            "/v1/xijian/backups/prune", headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["dropped"] == 0


class TestHTTPPolicy:
    """HTTP tests for policy endpoint.
    (策略端点的 HTTP 测试。)
    """

    def test_get_default(self, client, auth_headers):
        """Get default policy.
        (获取默认策略。)
        """
        res = client.get(
            "/v1/xijian/backups/policy", headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body["max_total_bytes"] == DEFAULT_MAX_TOTAL_BYTES

    def test_put(self, client, auth_headers):
        """Update policy via HTTP PUT.
        (通过 HTTP PUT 更新策略。)
        """
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
        """Update policy with invalid values returns 400.
        (使用无效值更新策略返回 400。)
        """
        res = client.put(
            "/v1/xijian/backups/policy",
            headers=auth_headers,
            json={"max_total_bytes": 0},
        )
        assert res.status_code == 400

    def test_delete(self, client, auth_headers):
        """Delete (reset) policy via HTTP returns defaults.
        (通过 HTTP 删除（重置）策略返回默认值。)
        """
        snap_stub.set_policy(max_total_bytes=1024)
        res = client.delete(
            "/v1/xijian/backups/policy", headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["max_total_bytes"] == DEFAULT_MAX_TOTAL_BYTES


# ---------------------------------------------------------------------------
# Auth coverage
# ---------------------------------------------------------------------------
# (认证覆盖)


class TestAuthCoverage:
    """All backup endpoints require Bearer auth.
    (所有备份端点都需要 Bearer 认证。)
    """

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
        """Test that endpoint requires Bearer auth.
        (测试端点需要 Bearer 认证。)
        """
        kwargs = {"method": method, "path": path}
        if body is not None and method in {"POST", "PUT", "PATCH"}:
            kwargs["json"] = body
        res = client.open(**kwargs)
        assert res.status_code in (401, 403), (
            "%s %s should require auth, got %d body=%s"
            % (method, path, res.status_code, res.get_data(as_text=True)[:80])
        )


# ---------------------------------------------------------------------------
# A5.3 AC-3 — zstd 压缩（真实 zstandard，而非 zlib 模仿）
# ---------------------------------------------------------------------------


class TestZstdCompression:
    """安装 ``zstandard`` 时，压缩器必须是 zstd（规格 AC-3）——
    旧的 stub 使用 zlib 却带 ``.zst`` 扩展名。"""

    def test_zstd_available_in_test_env(self):
        # CI / 测试环境（anaconda python）自带 zstandard；
        # 缺少时 stub 会降级并发出警告，因此在这类主机上
        # 我们跳过而不是失败。
        if not snap_stub._ZSTD_AVAILABLE:
            pytest.skip("zstandard not installed in this environment")
        import zstandard  # noqa: F401
        assert snap_stub._ZSTD_AVAILABLE is True

    def test_compressor_is_actually_zstd(self):
        if not snap_stub._ZSTD_AVAILABLE:
            pytest.skip("zstandard not installed in this environment")
        # 压缩后的字节必须是真正的 zstd 帧（魔数 0x28B52FFD）。
        compressed, _orig, _size = snap_stub._compress_bytes({"a": 1})
        assert compressed[:4] == b"\x28\xb5\x2f\xfd"

    def test_zstd_ratio_well_below_target(self):
        payload = {"key": "x" * 10_000}
        compressed, original, compressed_size = snap_stub._compress_bytes(payload)
        assert compressed_size < original * snap_stub.COMPRESSION_RATIO_TARGET
        # 真实 zstd 在同一载荷形态上的压缩率应优于旧的 zlib stub
        # （仅供参考，非硬性门槛）。
        import zlib
        zlib_size = len(zlib.compress(
            __import__("pickle").dumps(payload, protocol=__import__("pickle").HIGHEST_PROTOCOL),
            level=6,
        ))
        assert compressed_size <= zlib_size


# ---------------------------------------------------------------------------
# A5.3 AC-1 — 每小时定时自动备份 (scheduled backups)
# ---------------------------------------------------------------------------


class TestScheduledBackup:
    """后台调度器线程 + 它所驱动的一次性扫描。"""

    def test_run_scheduled_backup_creates_scheduled_snapshot(self):
        before = snap_stub.list_snapshots(reason=snap_stub.REASON_SCHEDULED)
        result = snap_stub.run_scheduled_backup()
        assert result["created"] is True
        assert result["reason"] == snap_stub.REASON_SCHEDULED
        after = snap_stub.list_snapshots(reason=snap_stub.REASON_SCHEDULED)
        assert len(after) == len(before) + 1
        snap = after[0]
        assert snap["scope"] == snap_stub.SCOPE_MIXED
        assert "worlds" in snap["payload"]

    def test_policy_interval_used_by_scheduler(self):
        snap_stub.set_policy(backup_interval_seconds=7200)
        assert snap_stub._current_interval() == 7200.0
        status = snap_stub.scheduler_status()
        assert status["policy_interval_s"] == 7200.0

    def test_scheduler_start_stop_lifecycle(self, monkeypatch):
        monkeypatch.setenv("XIJIAN_BACKUP_SCHEDULER", "1")
        started = snap_stub.start_scheduler()
        try:
            assert started["started"] is True
            assert snap_stub.scheduler_status()["running"] is True
            # 幂等。
            again = snap_stub.start_scheduler()
            assert again["started"] is False
            assert again["reason"] == "already_running"
        finally:
            stopped = snap_stub.stop_scheduler()
            assert stopped["stopped"] is True
            assert snap_stub.scheduler_status()["running"] is False

    def test_scheduler_thread_fires_scheduled_backup(self, monkeypatch):
        monkeypatch.setenv("XIJIAN_BACKUP_SCHEDULER", "1")
        monkeypatch.setenv("XIJIAN_BACKUP_INTERVAL_SECONDS", "1")
        snap_stub.start_scheduler()
        try:
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if snap_stub.list_snapshots(reason=snap_stub.REASON_SCHEDULED):
                    break
                time.sleep(0.1)
            assert snap_stub.list_snapshots(reason=snap_stub.REASON_SCHEDULED), \
                "scheduler thread should have written a scheduled snapshot"
        finally:
            snap_stub.stop_scheduler()

    def test_env_zero_disables_scheduler(self, monkeypatch):
        monkeypatch.setenv("XIJIAN_BACKUP_SCHEDULER", "0")
        result = snap_stub.start_scheduler()
        assert result == {"started": False, "reason": "disabled_by_env"}


class TestEmergencyDumpHandler:
    """A5.4 ``emergency_dump`` 动作 → 强制持久化的归档快照。"""

    def test_handler_writes_force_snapshot(self):
        before = len(snap_stub.list_snapshots(reason=snap_stub.REASON_OVERLOAD))
        snap_stub._emergency_dump_handler({
            "id": "evt_1", "tier": "strict",
            "triggered_metrics": ["soc_temp"], "action": "emergency_dump",
        })
        after = snap_stub.list_snapshots(reason=snap_stub.REASON_OVERLOAD)
        assert len(after) == before + 1
        assert after[0]["target_id"] == "evt_1"

    def test_handler_installed(self):
        from xijian_api.stubs import overload as ov_stub
        from xijian_api.stubs import snapshots as snap_stub_mod
        # 重新安装（conftest 已保持接线，但显式调用更稳妥）。
        snap_stub_mod.install_overload_handler()
        handlers = ov_stub.list_action_handlers()
        assert handlers[ov_stub.ACTION_EMERGENCY_DUMP]
