"""Tests for A5.3 real zstd compression (T0-3).

Covers the AC-3 "压缩采用 zstd，平均压缩比 ≥ 0.4" acceptance:

* **Round-trip consistency** — a ≥10 MB payload survives
  ``create_snapshot`` → ``decompress_snapshot`` byte-for-byte.
* **Compression ratio** — the post-compression size lands under
  :data:`COMPRESSION_RATIO_TARGET` (0.4 × original) on realistic
  compressible data.
* **Backend selection** — the policy ``compression_backend`` knob
  (``zstd`` / ``zlib`` / ``auto``) drives the compressor; the record
  stores which backend was used so the round-trip path can decode.
* **Configurable per-snapshot cap** — ``max_single_snapshot_bytes``
  replaces the module-level :data:`MAX_SINGLE_SNAPSHOT_BYTES` default.
* **Config wiring** — ``[snapshots]`` in ``config.toml`` /
  :class:`xijian_api.config.SnapshotsConfig` parse correctly.
"""

from __future__ import annotations

import os

import pytest

from xijian_api.stubs import snapshots as snap_stub
from xijian_api.stubs.snapshots import (
    COMPRESSION_BACKEND_AUTO,
    COMPRESSION_BACKEND_ZLIB,
    COMPRESSION_BACKEND_ZSTD,
    COMPRESSION_RATIO_TARGET,
    MAX_SINGLE_SNAPSHOT_BYTES,
    REASON_MANUAL,
    SCOPE_WORLD,
    SnapshotError,
)


#: A payload comfortably above 10 MB after pickle serialisation.
#: 序列化后轻松超过 10 MB 的负载。
def _big_payload(mb: int = 12) -> dict:
    """Build a JSON-shaped payload of roughly ``mb`` MiB."""
    return {
        "world": "w1",
        "chars": "x" * (mb * 1024 * 1024),
        "items": [{"id": i, "name": f"item-{i}"} for i in range(2000)],
    }


class TestCompressionBackendSelection:
    """Policy ``compression_backend`` drives the compressor."""

    def test_default_policy_is_auto(self):
        policy = snap_stub.get_policy()
        assert policy["compression_backend"] == COMPRESSION_BACKEND_AUTO

    def test_set_zlib_backend(self):
        updated = snap_stub.set_policy(compression_backend=COMPRESSION_BACKEND_ZLIB)
        assert updated["compression_backend"] == COMPRESSION_BACKEND_ZLIB
        assert snap_stub._policy_compression_backend() == COMPRESSION_BACKEND_ZLIB

    def test_set_zstd_backend(self):
        updated = snap_stub.set_policy(compression_backend=COMPRESSION_BACKEND_ZSTD)
        assert updated["compression_backend"] == COMPRESSION_BACKEND_ZSTD

    def test_invalid_backend_rejected(self):
        with pytest.raises(SnapshotError, match="compression_backend"):
            snap_stub.set_policy(compression_backend="brotli")

    def test_create_snapshot_records_backend(self):
        snap_stub.set_policy(compression_backend=COMPRESSION_BACKEND_ZLIB)
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="w1", payload={"a": 1},
        )
        assert record["compression_backend"] == COMPRESSION_BACKEND_ZLIB
        assert record["compressed"] is True


class TestRoundTripConsistency:
    """≥10 MB payload survives compress → decompress unchanged."""

    def test_big_payload_round_trip(self):
        payload = _big_payload()
        record = snap_stub.create_snapshot(
            scope=SCOPE_WORLD, target_id="w1", payload=payload,
            reason=REASON_MANUAL,
        )
        restored = snap_stub.decompress_snapshot(record["id"])
        assert restored is not None
        assert restored["payload"] == payload

    def test_big_payload_compression_ratio(self):
        payload = _big_payload()
        compressed, original, compressed_size = snap_stub._compress_bytes(payload)
        # AC-3: 平均压缩比 ≥ 0.4 — post-compression ≤ 0.4 × original.
        assert original > 10 * 1024 * 1024
        assert compressed_size < original * COMPRESSION_RATIO_TARGET

    def test_zstd_and_zlib_both_round_trip(self):
        payload = _big_payload(mb=2)
        for backend in (COMPRESSION_BACKEND_ZSTD, COMPRESSION_BACKEND_ZLIB):
            snap_stub.set_policy(compression_backend=backend)
            record = snap_stub.create_snapshot(
                scope=SCOPE_WORLD, target_id="w1", payload=payload,
            )
            restored = snap_stub.decompress_snapshot(record["id"])
            assert restored["payload"] == payload
            assert restored["_roundtrip_backend"] == backend

    def test_decompress_bytes_helper(self):
        payload = {"k": "v" * 1000}
        compressed, _, _ = snap_stub._compress_bytes(payload, backend=COMPRESSION_BACKEND_ZSTD)
        assert snap_stub.decompress_bytes(compressed, backend=COMPRESSION_BACKEND_ZSTD) == payload


class TestConfigurableSingleSnapshotCap:
    """``max_single_snapshot_bytes`` replaces the module default."""

    def test_policy_cap_overrides_module_constant(self):
        snap_stub.set_policy(max_single_snapshot_bytes=1024)
        policy = snap_stub.get_policy()
        assert policy["max_single_snapshot_bytes"] == 1024
        # A payload that fits the module default but not the new cap.
        payload = {"big": os.urandom(4096)}
        with pytest.raises(SnapshotError, match="too large"):
            snap_stub.create_snapshot(
                scope=SCOPE_WORLD, target_id="x", payload=payload,
            )

    def test_module_constant_still_default_when_unset(self):
        policy = snap_stub.get_policy()
        assert policy["max_single_snapshot_bytes"] is None
        # Sanity: the module default itself is a positive value.
        assert MAX_SINGLE_SNAPSHOT_BYTES > 0

    def test_invalid_cap_rejected(self):
        with pytest.raises(SnapshotError, match="max_single_snapshot_bytes"):
            snap_stub.set_policy(max_single_snapshot_bytes=0)
        with pytest.raises(SnapshotError, match="max_single_snapshot_bytes"):
            snap_stub.set_policy(max_single_snapshot_bytes="lots")


class TestSnapshotsConfigParsing:
    """``[snapshots]`` config section parses into SnapshotsConfig."""

    def test_defaults(self):
        from xijian_api.config import Config
        cfg = Config.from_dict({})
        assert cfg.snapshots.compression_backend == COMPRESSION_BACKEND_AUTO
        assert cfg.snapshots.max_single_snapshot_bytes is None

    def test_explicit_values(self):
        from xijian_api.config import Config
        cfg = Config.from_dict({
            "snapshots": {
                "compression_backend": "zstd",
                "max_single_snapshot_bytes": 1048576,
            },
        })
        assert cfg.snapshots.compression_backend == "zstd"
        assert cfg.snapshots.max_single_snapshot_bytes == 1048576

    def test_invalid_backend_rejected(self):
        from xijian_api.config import Config
        with pytest.raises(ValueError, match="compression_backend"):
            Config.from_dict({"snapshots": {"compression_backend": "brotli"}})

    def test_config_toml_has_snapshots_section(self):
        # The shipped config.toml must carry the [snapshots] section
        # so production picks up the AC-3 zstd default.
        repo_core = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        toml_path = os.path.join(repo_core, "config.toml")
        assert os.path.exists(toml_path)
        with open(toml_path, "r", encoding="utf-8") as fp:
            content = fp.read()
        assert "[snapshots]" in content
        assert "compression_backend" in content
